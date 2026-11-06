"""Public/online ordering: REST-style API for delivery apps + own website ordering page."""
import json
from decimal import Decimal, InvalidOperation

from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from .. import services as svc
from ..models import Branch, Category, Channel, MenuItem, Order, Setting, Table


# ------------------------------------------------------------------ API helpers
def _channel_from_request(request):
    key = request.headers.get("X-API-Key", "")
    if not key:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            key = auth[7:].strip()
    if not key:
        return None
    return Channel.objects.filter(api_key=key, is_active=True).first()


def _err(msg, status=400, **extra):
    return JsonResponse({"ok": False, "error": msg, **extra}, status=status)


def _order_json(o):
    return {"ok": True, "order_number": o.number, "id": o.pk, "status": o.status, "order_type": o.order_type,
            "branch": o.branch.code, "subtotal": float(o.subtotal), "tax": float(o.tax),
            "delivery_fee": float(o.delivery_fee), "total": float(o.total), "paid": o.is_paid}


@csrf_exempt
@require_http_methods(["GET"])
def api_menu(request):
    ch = _channel_from_request(request)
    if not ch:
        return _err("Invalid or missing API key", 401)
    branch = Branch.objects.filter(code=request.GET.get("branch", "").upper(), is_active=True).first()
    if not branch:
        return _err("Unknown branch code. Use ?branch=CODE", 404)
    cats = {c.pk: c.name for c in Category.objects.filter(is_active=True)}
    items = [{**m, "category": cats.get(m["category"])} for m in svc.branch_menu(branch)]
    return JsonResponse({"ok": True, "branch": branch.code, "currency": branch.currency_code, "items": items})


@csrf_exempt
@require_http_methods(["POST"])
def api_orders(request):
    """POST JSON:
    {"branch": "KHI", "external_id": "FP-123", "order_type": "delivery|takeaway|dine_in",
     "customer": {"name": "", "phone": "", "address": ""}, "notes": "", "discount": 0,
     "paid": true, "payment_method": "online",
     "items": [{"sku": "BRG-01", "qty": 2, "notes": ""}, {"name": "Custom item", "qty": 1, "price": 350}]}"""
    ch = _channel_from_request(request)
    if not ch:
        return _err("Invalid or missing API key", 401)
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return _err("Body must be valid JSON")
    branch = Branch.objects.filter(code=str(data.get("branch", "")).upper(), is_active=True).first()
    if not branch:
        return _err("Unknown or missing 'branch' code", 422)
    ext = str(data.get("external_id", ""))[:80]
    if ext:
        dup = Order.objects.filter(source=ch, external_ref=ext).select_related("branch").first()
        if dup:
            return JsonResponse({**_order_json(dup), "duplicate": True}, status=200)
    cust = data.get("customer") or {}
    otype = data.get("order_type", Order.DELIVERY)
    table = None
    if otype == Order.DINE_IN and data.get("table"):
        table = Table.objects.filter(branch=branch, name=str(data["table"])).first()
    menu_items = list(MenuItem.objects.filter(is_active=True))
    by_sku = {m.sku.lower(): m for m in menu_items if m.sku}
    by_name = {m.name.lower(): m for m in menu_items}
    avail = {m["id"]: m for m in svc.branch_menu(branch)}
    lines, missing = [], []
    for l in data.get("items", []):
        try:
            qty = int(l.get("qty", 1))
            m = by_sku.get(str(l.get("sku", "")).lower()) or by_name.get(str(l.get("name", "")).lower())
            if m:
                if m.id not in avail:
                    missing.append(f"{m.name} (unavailable in {branch.code})")
                    continue
                lines.append({"item": m, "name": m.name, "qty": qty, "price": avail[m.id]["price"],
                              "notes": l.get("notes", "")})
            elif l.get("price") is not None and l.get("name"):
                lines.append({"item": None, "name": str(l["name"]), "qty": qty, "price": Decimal(str(l["price"])),
                              "notes": l.get("notes", "")})
            else:
                missing.append(str(l.get("sku") or l.get("name") or "?"))
        except (ValueError, TypeError, InvalidOperation):
            return _err("Invalid item line", 422)
    if missing:
        return _err("Some items could not be matched", 422, unmatched=missing)
    try:
        order = svc.create_order(
            branch=branch, order_type=otype, items=lines, source=ch, table=table, customer_name=str(cust.get("name", "")),
            phone=str(cust.get("phone", "")), address=str(cust.get("address", "")), notes=str(data.get("notes", "")),
            discount=Decimal(str(data.get("discount") or 0)), external_ref=ext, paid=bool(data.get("paid")),
            method=data.get("payment_method", "online"))
    except svc.OrderError as e:
        return _err(str(e), 422)
    except (InvalidOperation, ValueError):
        return _err("Invalid numeric value", 422)
    return JsonResponse(_order_json(order), status=201)


@csrf_exempt
@require_http_methods(["GET"])
def api_order_status(request, number):
    ch = _channel_from_request(request)
    if not ch:
        return _err("Invalid or missing API key", 401)
    o = get_object_or_404(Order.objects.select_related("branch"), number=number, source=ch)
    return JsonResponse(_order_json(o))


# ------------------------------------------------------------------ own website
def _site_branch(code):
    b = Branch.objects.filter(code=code.upper(), is_active=True, online_ordering=True).first()
    if not b:
        raise Http404("Branch not found")
    return b


def site_branches(request):
    from ..models import Setting
    s = Setting.get()
    return render(request, "rms/site_branches.html", {
        "setting": s, "branches": Branch.objects.filter(is_active=True, online_ordering=True) if s.online_ordering_enabled else []})


def site_menu(request, code):
    from ..models import Setting
    s = Setting.get()
    if not s.online_ordering_enabled:
        raise Http404("Online ordering is disabled")
    b = _site_branch(code)
    return render(request, "rms/site_menu.html", {
        "b": b, "setting": s, "categories": Category.objects.filter(is_active=True),
        "menu": svc.branch_menu(b)})


@require_POST
def site_coupon(request, code):
    b = _site_branch(code)
    try:
        data = json.loads(request.body)
        menu = {m["id"]: m for m in svc.branch_menu(b)}
        objs = MenuItem.objects.in_bulk([int(l["id"]) for l in data["items"]])
        sub = sum(svc.build_line(objs[int(l["id"])], menu[int(l["id"])], dict(l, qty=min(int(l["qty"]), 50)))["price"] * min(int(l["qty"]), 50)
                  for l in data["items"])
        c, amt = svc.coupon_discount(data.get("code"), b, sub)
    except svc.OrderError as e:
        return _err(str(e))
    except Exception:
        return _err("Invalid request")
    return JsonResponse({"ok": True, "amount": float(amt), "code": c.code})


@require_POST
def site_submit(request, code):
    b = _site_branch(code)
    try:
        data = json.loads(request.body)
        menu = {m["id"]: m for m in svc.branch_menu(b)}
        objs = MenuItem.objects.in_bulk([int(l["id"]) for l in data["items"]])
        lines = []
        for l in data["items"]:
            m = menu.get(int(l["id"]))
            if not m:
                return _err("An item is no longer available")
            lines.append(svc.build_line(objs[int(l["id"])], m, dict(l, qty=min(int(l["qty"]), 50), notes="")))
        otype = data.get("order_type")
        if otype not in (Order.DELIVERY, Order.TAKEAWAY):
            return _err("Choose delivery or takeaway")
        if not str(data.get("phone", "")).strip():
            return _err("Phone number is required")
        ch, _ = Channel.objects.get_or_create(code="website", defaults={"name": "Website", "kind": "website"})
        order = svc.create_order(branch=b, order_type=otype, items=lines, source=ch,
                                 customer_name=str(data.get("name", "")).strip(), phone=str(data.get("phone", "")).strip(),
                                 address=str(data.get("address", "")).strip(), notes=str(data.get("notes", "")),
                                 coupon_code=str(data.get("coupon", "")))
    except svc.OrderError as e:
        return _err(str(e))
    except (KeyError, ValueError, TypeError, MenuItem.DoesNotExist):
        return _err("Invalid order")
    if not order.customer_phone:
        return _err("Phone number is required")
    return JsonResponse({"ok": True, "number": order.number, "total": float(order.total), "track": f"/track/{order.number}/"})


def track(request, number):
    o = get_object_or_404(Order.objects.select_related("branch"), number=number)
    from django.urls import reverse
    if o.order_type == Order.DELIVERY:
        steps = [("new", "Received"), ("preparing", "Cooking"), ("ready", "Ready"), ("out_for_delivery", "On the way"), ("completed", "Delivered")]
    else:
        steps = [("new", "Received"), ("preparing", "Cooking"), ("ready", "Ready"), ("served", "Served"), ("completed", "Done")]
    keys = [k for k, _ in steps]
    step_i = keys.index(o.status) if o.status in keys else (len(keys) - 1 if o.status == "completed" else 0)
    return render(request, "rms/track.html", {"o": o, "lines": o.lines.all(), "setting": None, "steps": steps, "step_i": step_i,
                                              "fb_url": reverse("feedback_form", args=[o.ensure_feedback_token()]),
                                              "pay_available": Setting.get().payment_gateway != "none" and o.due > 0
                                              and o.status != Order.CANCELLED})
