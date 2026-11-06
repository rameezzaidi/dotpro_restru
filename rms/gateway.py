"""Online payment links. Providers: 'sandbox' (offline test gateway) and 'stripe' (Checkout; STRIPE_SECRET_KEY /
STRIPE_WEBHOOK_SECRET in the environment). A successful payment becomes a normal 'online' Payment on the order."""
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from . import services as svc
from .models import Order, PaymentLink, Setting


class GatewayError(Exception):
    pass


def provider():
    return Setting.get().payment_gateway


def create_link(order):
    prov = provider()
    if prov == "none":
        raise GatewayError("Online payment is switched off (Settings → Payment gateway).")
    if order.status == Order.CANCELLED:
        raise GatewayError("Order is cancelled")
    if order.due <= 0:
        raise GatewayError("Nothing left to pay on this order")
    old = order.pay_links.filter(status=PaymentLink.PENDING, provider=prov, amount=order.due).first()
    if old:
        return old
    return PaymentLink.objects.create(order=order, token=secrets.token_urlsafe(12), provider=prov, amount=order.due)


@transaction.atomic
def mark_paid(link, ref=""):
    link = PaymentLink.objects.select_for_update().get(pk=link.pk)
    if link.status == PaymentLink.PAID:
        return link
    order = Order.objects.get(pk=link.order_id)
    amount = min(link.amount, order.due)
    if amount > 0:
        svc.add_payment(order, "online", amount, note=f"{link.provider}:{ref or link.provider_ref}"[:120])
    link.status, link.paid_at = PaymentLink.PAID, timezone.now()
    if ref:
        link.provider_ref = ref[:120]
    link.save()
    return link


def mark_failed(link):
    if link.status == PaymentLink.PENDING:
        link.status = PaymentLink.FAILED
        link.save(update_fields=["status"])


# ----------------------------------------------------------------------------- Stripe
def _stripe_key():
    key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not key:
        raise GatewayError("STRIPE_SECRET_KEY is not set on the server.")
    return key


def _stripe(method, path, form=None):
    data = urllib.parse.urlencode(form).encode() if form else None
    req = urllib.request.Request("https://api.stripe.com" + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + _stripe_key()})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.load(r)
    except Exception as e:
        raise GatewayError(f"Stripe request failed: {e}")


def stripe_start(link, success_url, cancel_url):
    o = link.order
    minor = int((link.amount * 100).to_integral_value())
    s = _stripe("POST", "/v1/checkout/sessions", {
        "mode": "payment", "success_url": success_url, "cancel_url": cancel_url, "client_reference_id": link.token,
        "line_items[0][quantity]": 1, "line_items[0][price_data][currency]": o.branch.currency_code.lower(),
        "line_items[0][price_data][unit_amount]": minor, "line_items[0][price_data][product_data][name]": f"{o.branch.name} · order {o.number}"})
    link.provider_ref = s["id"]
    link.save(update_fields=["provider_ref"])
    return s["url"]


def stripe_check(link):
    """On return from Stripe: ask Stripe if the session is paid (works even without the webhook)."""
    if link.provider == "stripe" and link.provider_ref and link.status == PaymentLink.PENDING:
        s = _stripe("GET", f"/v1/checkout/sessions/{link.provider_ref}")
        if s.get("payment_status") == "paid":
            mark_paid(link, s.get("payment_intent") or s["id"])
    return link


def verify_stripe_signature(payload, header, secret, tolerance=300, now=None):
    """Stripe-Signature: t=timestamp,v1=hex(hmac_sha256(secret, f'{t}.{payload}'))"""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        t = int(parts["t"])
    except (KeyError, ValueError, AttributeError):
        return False
    if abs((now or time.time()) - t) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, v) for k, v in (p.split("=", 1) for p in header.split(",")) if k == "v1")
