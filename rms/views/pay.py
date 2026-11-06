"""Public online-payment pages + Stripe webhook."""
import json
import os

from django.contrib import messages
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .. import gateway as gw
from ..models import Order, PaymentLink
from ..permissions import role_required


def pay_page(request, token):
    link = get_object_or_404(PaymentLink.objects.select_related("order", "order__branch"), token=token)
    try:
        gw.stripe_check(link)
        link.refresh_from_db()
    except gw.GatewayError:
        pass
    return render(request, "rms/pay.html", {"link": link, "o": link.order})


@require_POST
def pay_start(request, token):
    link = get_object_or_404(PaymentLink.objects.select_related("order", "order__branch"), token=token)
    if link.status != PaymentLink.PENDING:
        return redirect("pay_page", token=token)
    if link.provider == "sandbox":
        return redirect("pay_sandbox", token=token)
    try:
        url = gw.stripe_start(link, request.build_absolute_uri(reverse("pay_page", args=[token])),
                              request.build_absolute_uri(reverse("pay_page", args=[token])))
    except gw.GatewayError as e:
        messages.error(request, str(e))
        return redirect("pay_page", token=token)
    return redirect(url)


def pay_sandbox(request, token):
    link = get_object_or_404(PaymentLink.objects.select_related("order", "order__branch"), token=token, provider="sandbox")
    if link.status != PaymentLink.PENDING:
        return redirect("pay_page", token=token)
    if request.method == "POST":
        if request.POST.get("outcome") == "success":
            gw.mark_paid(link, f"sbx_{link.token[:8]}")
        else:
            gw.mark_failed(link)
        return redirect("pay_page", token=token)
    return render(request, "rms/pay_sandbox.html", {"link": link, "o": link.order})


@csrf_exempt
@require_POST
def stripe_webhook(request):
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if not secret or not gw.verify_stripe_signature(request.body, request.META.get("HTTP_STRIPE_SIGNATURE", ""), secret):
        return HttpResponseBadRequest("bad signature")
    try:
        ev = json.loads(request.body)
    except ValueError:
        return HttpResponseBadRequest("bad json")
    if ev.get("type") == "checkout.session.completed":
        s = ev["data"]["object"]
        link = PaymentLink.objects.filter(token=s.get("client_reference_id"), provider="stripe").first()
        if link and s.get("payment_status") == "paid":
            gw.mark_paid(link, s.get("payment_intent") or s.get("id", ""))
    return HttpResponse("ok")


@require_POST
def track_pay(request, number):
    """'Pay online' button on the public order-tracking page."""
    o = get_object_or_404(Order.objects.select_related("branch"), number=number)
    try:
        link = gw.create_link(o)
    except gw.GatewayError as e:
        messages.error(request, str(e))
        return redirect("track", number=number)
    return redirect("pay_page", token=link.token)


@role_required("manager", "cashier")
@require_POST
def order_pay_link(request, pk):
    o = get_object_or_404(Order, pk=pk)
    try:
        link = gw.create_link(o)
        messages.success(request, "Payment link created — share it with the customer.")
    except gw.GatewayError as e:
        messages.error(request, str(e))
    return redirect("order_detail", pk=pk)
