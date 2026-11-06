import os
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .. import analytics as an
from .. import automation as au
from .. import services as svc
from ..forms import AUTOMATION_FIELDS, AutomationForm
from ..models import (AutomationEvent, Branch, DailyReport, Ingredient, MessageLog, PriceRule, Rider, Setting, User)
from ..permissions import master_required, role_required, scope


def _env_status():
    tw = all(os.environ.get(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN")) and (
        os.environ.get("TWILIO_SMS_FROM") or os.environ.get("TWILIO_WHATSAPP_FROM"))
    return [("Twilio SMS / WhatsApp keys", bool(tw)), ("Message webhook URL", bool(os.environ.get("MESSAGE_WEBHOOK_URL"))),
            ("Email server", bool(os.environ.get("EMAIL_HOST"))), ("Site URL (links in messages)", bool(au.messaging.site_url())),
            ("Cron secret (scheduled runs)", bool(os.environ.get("CRON_SECRET")))]


def _suggestions(branches):
    """Quiet hours (<40% of the peak hour, 11:00-22:00) where a happy-hour discount could bring guests in."""
    out = []
    for b in branches:
        h = an.hour_profile(b)
        peak = max(h + [1])
        if peak < 8:
            continue
        quiet = [i for i in range(11, 22) if h[i] < peak * 0.4]
        runs, cur = [], []
        for i in quiet:
            if cur and i != cur[-1] + 1:
                runs.append(cur)
                cur = []
            cur.append(i)
        if cur:
            runs.append(cur)
        for r in runs:
            out.append({"branch": b, "start": r[0], "end": r[-1] + 1, "pct": 10})
    return out


@master_required
def automation_home(request):
    st = Setting.get()
    form = AutomationForm(request.POST or None, instance=st)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Automation settings saved.")
        return redirect("automation")
    branches = [request.branch] if request.branch else list(Branch.objects.filter(is_active=True))
    last = AutomationEvent.objects.filter(key="last_run").first()
    sections = [(title, [form[f] for f in fs]) for title, fs in AUTOMATION_FIELDS]
    return render(request, "rms/automation.html", {
        "form": form, "sections": sections, "env": _env_status(), "last": last,
        "msgs": MessageLog.objects.all()[:60], "rules": PriceRule.objects.select_related("branch"),
        "suggest": _suggestions(branches), "all_branches": Branch.objects.filter(is_active=True),
        "day_names": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]})


@master_required
@require_POST
def automation_action(request):
    a = request.POST.get("action")
    if a == "run":
        out = au.tick(force=True)
        messages.success(request, "Automations ran: " + (", ".join(f"{k}={v}" for k, v in out.items()) or "nothing due"))
    elif a == "sync":
        n = au.job_menu_sync(force=True)
        messages.success(request, f"Menu pushed to delivery apps ({n} branch feeds).")
    elif a in ("rule_add", "rule_suggest"):
        try:
            br = Branch.objects.filter(pk=request.POST.get("branch") or 0).first()
            PriceRule.objects.create(
                name=(request.POST.get("name") or "Happy hour")[:60], branch=br, start_hour=int(request.POST.get("start", 15)) % 24,
                end_hour=min(24, max(1, int(request.POST.get("end", 18)))), percent=Decimal(request.POST.get("percent") or 10),
                days="".join(sorted(set(request.POST.getlist("days")))) or "0123456", is_active=True)
            messages.success(request, "Price rule added - it applies automatically in those hours.")
        except (ValueError, ArithmeticError):
            messages.error(request, "Check the hours and percent.")
    elif a in ("rule_toggle", "rule_delete"):
        r = get_object_or_404(PriceRule, pk=request.POST.get("id"))
        if a == "rule_delete":
            r.delete()
        else:
            r.is_active = not r.is_active
            r.save(update_fields=["is_active"])
    return redirect("automation")


@role_required("manager")
def daily_reports(request):
    if request.method == "POST":
        for b in ([request.branch] if request.branch else Branch.objects.filter(is_active=True)):
            au.build_daily_report(b)
        messages.success(request, "Today's report built / refreshed.")
        return redirect("daily_reports")
    return render(request, "rms/daily_reports.html", {"reports": scope(DailyReport.objects.select_related("branch"), request)[:60]})


@role_required("manager", "storekeeper")
def smart_order(request):
    branch, picker = an.single_branch(request)
    ai = an.build_ai(branch)
    rows = list(ai["ing"])
    for r in rows:
        r["need_order"] = r["status"] == "order" or r["short"] > 0
        r["buy"] = (r["short"] * Decimal("1.1")).quantize(Decimal("0.1")) if r["short"] > 0 else (r["per_day"] * 3).quantize(Decimal("0.1"))
    if request.method == "POST":
        ings = {i.pk: i for i in Ingredient.objects.select_related("default_supplier").filter(pk__in=[int(x) for x in request.POST.getlist("pick")])}
        lines = []
        for pk, ing in ings.items():
            try:
                q = Decimal(request.POST.get(f"qty_{pk}") or 0)
            except ArithmeticError:
                q = Decimal(0)
            lines.append((ing, q))
        made, skipped = svc.create_pos_from_lines(branch, request.user, lines)
        if made:
            messages.success(request, "Draft purchase orders created: " + ", ".join(p.number for p in made))
        else:
            messages.info(request, "Nothing new to order (already on an open PO, or no quantity).")
        if skipped:
            messages.warning(request, "No default supplier for: " + ", ".join(skipped))
        return redirect("smart_order" if not made else "purchase_orders")
    return render(request, "rms/smart_order.html", {"rows": rows, "branch": branch, "picker": picker, "ai": ai})


def _month(request):
    raw = request.GET.get("month", "")
    try:
        y, m = (int(x) for x in raw.split("-"))
        return date(y, m, 1)
    except (ValueError, TypeError):
        t = timezone.localdate()
        return date(t.year - (t.month == 1), (t.month - 2) % 12 + 1, 1)


@role_required("manager")
def slips(request):
    m = _month(request)
    rows = []
    for u in scope(User.objects.filter(role="waiter", is_active=True).select_related("branch"), request):
        rows.append(au.slip_data("waiter", u, m.year, m.month))
    for r in scope(Rider.objects.filter(is_active=True).select_related("branch"), request):
        rows.append(au.slip_data("rider", r, m.year, m.month))
    for u in scope(User.objects.filter(role__in=au.STAFF_SALARY_ROLES, is_active=True, monthly_salary__gt=0).select_related("branch"), request):
        rows.append(au.slip_data("staff", u, m.year, m.month))
    return render(request, "rms/slips.html", {"rows": rows, "month": m, "month_str": f"{m:%Y-%m}",
                                              "total": {b: sum(x["total"] for x in rows if x["branch"] == b) for b in {x["branch"] for x in rows}}})


@role_required("manager", "waiter", "cashier", "kitchen", "kds", "storekeeper", "rider")
def slip(request, kind, pk):
    if kind == "waiter":
        obj = get_object_or_404(User, pk=pk, role="waiter")
    elif kind == "staff":
        obj = get_object_or_404(User, pk=pk, role__in=au.STAFF_SALARY_ROLES)
    else:
        obj = get_object_or_404(Rider, pk=pk)
    u = request.user
    if u.role != "manager" and not u.is_superuser and u.role != "owner":       # staff may only open their own slip
        own = obj if kind in ("waiter", "staff") else getattr(obj, "user", None)
        if not own or own.pk != u.pk:
            return redirect("dashboard")
    if u.role == "manager" and not request.branch_all and obj.branch_id != request.branch.pk:
        return redirect("dashboard")
    m = _month(request)
    return render(request, "rms/slip.html", {"d": au.slip_data(kind, obj, m.year, m.month), "setting": Setting.get(), "month": m})


@csrf_exempt
def cron_tick(request):
    """Vercel Cron / Windows Task Scheduler: GET /cron/tick/?key=CRON_SECRET (or Authorization: Bearer CRON_SECRET)."""
    secret = os.environ.get("CRON_SECRET", "")
    given = request.GET.get("key", "") or request.headers.get("Authorization", "").replace("Bearer ", "")
    if not secret or given != secret:
        return HttpResponse("forbidden", status=403)
    return JsonResponse({"ok": True, "ran": au.tick(force=True)})
