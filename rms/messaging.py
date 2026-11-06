"""Outgoing messages: customers, suppliers, staff, apps.
Provider is chosen in Settings -> Automations. Secrets live in environment variables, never in the database:
  Twilio : TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_SMS_FROM (+1555...), TWILIO_WHATSAPP_FROM (whatsapp:+1415...)
  Webhook: MESSAGE_WEBHOOK_URL (gets JSON {channel,to,subject,body,kind})
  Email  : EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, EMAIL_USE_TLS, DEFAULT_FROM_EMAIL
With provider "log" (default) or missing keys nothing leaves the server; the message is only recorded in the log."""
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from django.core.mail import send_mail

from .models import MessageLog, Setting

DIAL = {"PK": "92", "IN": "91", "BD": "880", "AE": "971", "SA": "966", "QA": "974", "KW": "965", "OM": "968", "BH": "973",
        "GB": "44", "US": "1", "CA": "1", "TR": "90", "EG": "20", "MY": "60", "SG": "65", "ID": "62", "LK": "94", "NP": "977",
        "AU": "61", "DE": "49", "FR": "33"}


def e164(phone, country="PK"):
    """'0300 1234567' / '+92 300 1234567' / '3001234567' -> '+923001234567' ('' if it does not look like a number)."""
    raw = (phone or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 7:
        return ""
    if raw.startswith("+"):
        return "+" + digits
    if raw.startswith("00"):
        return "+" + digits[2:]
    dial = DIAL.get((country or "PK").upper(), "92")
    if digits.startswith("0"):
        return "+" + dial + digits.lstrip("0")
    return "+" + (digits if digits.startswith(dial) and len(digits) > 10 else dial + digits)


def site_url():
    base = os.environ.get("SITE_URL") or os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") or ""
    if base and not base.startswith("http"):
        base = "https://" + base
    return base.rstrip("/")


def _post(url, data, headers=None, form=False, auth=None, timeout=8):
    body = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
    h = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json", **(headers or {})}
    if auth:
        h["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status


def _deliver(provider, channel, to, subject, body, kind):
    """Returns (status, error)."""
    if channel == "email":
        if not os.environ.get("EMAIL_HOST"):
            return "logged", "EMAIL_HOST not configured"
        send_mail(subject or "Notification", body, None, [to], fail_silently=False)
        return "sent", ""
    if provider == "twilio":
        sid, tok = os.environ.get("TWILIO_ACCOUNT_SID"), os.environ.get("TWILIO_AUTH_TOKEN")
        frm = os.environ.get("TWILIO_WHATSAPP_FROM" if channel == "whatsapp" else "TWILIO_SMS_FROM")
        if not (sid and tok and frm):
            return "logged", "Twilio keys missing"
        _post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
              {"From": frm, "To": ("whatsapp:" if channel == "whatsapp" else "") + to, "Body": body}, form=True, auth=f"{sid}:{tok}")
        return "sent", ""
    if provider == "webhook":
        url = os.environ.get("MESSAGE_WEBHOOK_URL")
        if not url:
            return "logged", "MESSAGE_WEBHOOK_URL missing"
        _post(url, {"channel": channel, "to": to, "subject": subject, "body": body, "kind": kind})
        return "sent", ""
    return "logged", ""


def send(to, body, *, channel="whatsapp", kind="", subject="", branch=None, order=None):
    """Never raises: a failed message must not break an order. Returns the MessageLog row (or None if `to` is empty)."""
    if not to:
        return None
    provider = Setting.get().msg_provider
    try:
        status, err = _deliver(provider, channel, to, subject, body, kind)
    except (urllib.error.URLError, OSError, ValueError) as e:
        status, err = "failed", str(e)[:250]
    except Exception as e:                                     # noqa: BLE001 - provider SDK / mail errors
        status, err = "failed", f"{type(e).__name__}: {e}"[:250]
    return MessageLog.objects.create(channel=channel, kind=kind, to=to[:120], subject=subject[:160], body=body,
                                     status=status, error=err, branch=branch, order=order)


def send_phone(phone, body, *, country="PK", prefer="whatsapp", **kw):
    """WhatsApp when the provider supports it, else SMS."""
    num = e164(phone, country)
    if not num:
        return None
    ch = prefer if Setting.get().msg_provider in ("twilio", "log", "webhook") else "sms"
    return send(num, body, channel=ch, **kw)


def sync_webhook(url, payload, secret):
    """Signed menu push to a delivery-app channel. Returns (ok, error)."""
    import hashlib
    import hmac
    raw = json.dumps(payload, sort_keys=True)
    sig = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(url, data=raw.encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Restopro-Signature": sig})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return 200 <= r.status < 300, ""
    except Exception as e:                                     # noqa: BLE001
        return False, str(e)[:200]
