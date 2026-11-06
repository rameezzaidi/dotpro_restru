import json
import os
from decimal import Decimal, InvalidOperation
from xml.sax.saxutils import escape

from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .. import ai
from .. import analytics as an
from .. import messaging
from .. import services as svc
from ..models import AILog, Branch, BranchItem, Feedback, MenuItem, Setting
from ..permissions import role_required

STAFF = ("manager", "cashier", "waiter")


def _branch(request):
    b, picker = an.single_branch(request)
    return b, picker


@role_required("manager")
def ai_home(request):
    st = Setting.get()
    branch, picker = _branch(request)
    advice = ai.menu_advice(branch)
    ctx = {"all_branches": Branch.objects.filter(is_active=True), "abranch": branch, "picker": picker, "st": st, "has_key": bool(ai.api_key()), "model": ai.model_name(),
           "usage": ai.usage(), "advice": advice, "advice_text": ai.menu_summary(branch, advice) if request.GET.get("summary") else None,
           "replies": ai.reply_drafts(branch) if request.GET.get("replies") else None,
           "prep": ai.prep_plan(branch) if request.GET.get("prep") else None,
           "wa_url": request.build_absolute_uri("/ai/whatsapp/"), "recent": AILog.objects.all()[:12],
           "wa_key": bool(os.environ.get("WHATSAPP_WEBHOOK_KEY"))}
    return render(request, "rms/ai_home.html", ctx)


@role_required("manager")
@require_POST
def ai_settings(request):
    st = Setting.get()
    for f in ("ai_enabled", "ai_upsell", "ai_anomaly", "ai_whatsapp"):
        setattr(st, f, request.POST.get(f) == "on")
    st.ai_wa_branch = Branch.objects.filter(pk=request.POST.get("ai_wa_branch") or 0).first()
    st.save()
    messages.success(request, "AI settings saved.")
    return redirect("ai_home")


@role_required("manager")
@require_POST
def ai_copilot(request):
    branch, _ = _branch(request)
    return JsonResponse(ai.copilot(branch, request.POST.get("q", "")))


@role_required(*STAFF)
@require_POST
def ai_order_staff(request):
    if not request.branch:
        return JsonResponse({"ok": False, "error": "Select a branch first"}, status=400)
    res = ai.parse_order(request.POST.get("text", ""), svc.branch_menu(request.branch))
    return JsonResponse({"ok": True, **res})


@role_required(*STAFF)
def ai_upsell_staff(request):
    if not request.branch or not Setting.get().ai_upsell:
        return JsonResponse({"items": []})
    ids = [i for i in request.GET.get("ids", "").split(",") if i]
    return JsonResponse({"items": ai.upsell(request.branch, ids)})


def _site_branch(code):
    b = Branch.objects.filter(code=code.upper(), is_active=True, online_ordering=True).first()
    if not b or not Setting.get().online_ordering_enabled:
        raise Http404
    return b


@require_POST
def ai_order_site(request, code):
    b = _site_branch(code)
    res = ai.parse_order(request.POST.get("text", "")[:400], svc.branch_menu(b))
    return JsonResponse({"ok": True, **res})


def ai_upsell_site(request, code):
    b = _site_branch(code)
    if not Setting.get().ai_upsell:
        return JsonResponse({"items": []})
    return JsonResponse({"items": ai.upsell(b, [i for i in request.GET.get("ids", "").split(",") if i])})


@role_required("manager")
@require_POST
def ai_apply_price(request):
    branch, _ = _branch(request)
    try:
        price = Decimal(request.POST.get("price", ""))
        if price <= 0:
            raise InvalidOperation
    except InvalidOperation:
        messages.error(request, "Invalid price.")
        return redirect("ai_home")
    item = MenuItem.objects.filter(pk=request.POST.get("item") or 0).first()
    if not item:
        raise Http404
    BranchItem.objects.update_or_create(branch=branch, item=item, defaults={"price": price})
    messages.success(request, f"{item.name} is now {branch.currency_symbol}{price:,.0f} at {branch.name}.")
    return redirect(f"/ai/?b={branch.pk}" if not request.branch else "/ai/")


@role_required("manager")
@require_POST
def ai_send_reply(request):
    branch, _ = _branch(request)
    fb = Feedback.objects.filter(pk=request.POST.get("fb") or 0, branch=branch).select_related("order").first()
    text = request.POST.get("text", "").strip()[:900]
    if not fb or not text or not fb.order.customer_phone:
        return JsonResponse({"ok": False, "error": "Nothing to send"}, status=400)
    row = messaging.send_phone(fb.order.customer_phone, text, country=branch.country, kind="feedback_reply", branch=branch, order=fb.order)
    return JsonResponse({"ok": True, "status": getattr(row, "status", "")})


@csrf_exempt
def wa_webhook(request):
    """Twilio WhatsApp inbound webhook. Set ?key=<WHATSAPP_WEBHOOK_KEY> on the URL when that env var is set."""
    need = os.environ.get("WHATSAPP_WEBHOOK_KEY", "")
    if need and request.GET.get("key") != need:
        return HttpResponse(status=403)
    if request.method != "POST" or not Setting.get().ai_whatsapp:
        return HttpResponse("<Response/>", content_type="text/xml")
    phone = request.POST.get("From", "").replace("whatsapp:", "").strip()
    body = request.POST.get("Body", "")
    if not phone:
        return HttpResponse("<Response/>", content_type="text/xml")
    reply = ai.wa_reply(phone, body)
    return HttpResponse(f"<Response><Message>{escape(reply)}</Message></Response>", content_type="text/xml")
