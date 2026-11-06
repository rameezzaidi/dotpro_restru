import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import JsonResponse, Http404, HttpResponse
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .. import services as svc
from ..models import Branch, Category, Channel, MenuItem, Order, Rider, Setting, Table, User
from ..permissions import role_required, scope

POS_ROLES = ("manager", "cashier", "waiter")


def _need_branch(request):
    if not request.branch:
        messages.warning(request, "Select a specific branch from the top bar to use the POS.")
        return redirect("dashboard")
    return None


# ------------------------------------------------------------------ POS
@role_required(*POS_ROLES)
def pos(request):
    bad = _need_branch(request)
    if bad:
        return bad
    b = request.branch
    waiters = User.objects.filter(role=User.WAITER, branch=b, is_active=True)
    add_order = None
    if request.GET.get("add"):
        add_order = Order.objects.filter(pk=request.GET.get("add") or 0, branch=b, status__in=Order.ACTIVE).exclude(
            status=Order.OUT).select_related("table").first()
        if not add_order:
            messages.error(request, "That order is closed or not found.")
            return redirect("orders")
    ctx = {
        "add_order": add_order, "low_list": svc.low_ingredients(b),
        "categories": Category.objects.filter(is_active=True),
        "menu": svc.branch_menu(b),
        "tables": Table.objects.filter(branch=b),
        "waiters": waiters,
        "riders": Rider.objects.filter(branch=b, is_active=True).exclude(status=Rider.OFFLINE),
        "preselect_table": request.GET.get("table", ""),
        "tax": float(b.tax_percent), "service": float(b.service_charge_percent), "delivery_fee": float(b.delivery_fee),
        "channels": Channel.objects.filter(is_active=True).exclude(kind="website"),
    }
    return render(request, "rms/pos.html", ctx)


def _cart_lines(b, data):
    ids = [int(l["id"]) for l in data.get("items", [])]
    menu = {m["id"]: m for m in svc.branch_menu(b)}
    objs = MenuItem.objects.in_bulk(ids)
    lines = []
    for l in data["items"]:
        m = menu.get(int(l["id"]))
        if not m:
            raise svc.OrderError("Item not available in this branch")
        lines.append(svc.build_line(objs[int(l["id"])], m, l))
    return lines


@role_required(*POS_ROLES)
@require_POST
def order_add_items(request, pk):
    """Guest orders more: new items go to the kitchen as the next KOT round of the same order."""
    order = get_object_or_404(scope(Order.objects, request), pk=pk)
    try:
        data = json.loads(request.body)
        order = svc.add_items(order, _cart_lines(order.branch, data), request.user)
        for w in svc.order_stock_alerts(order):
            messages.warning(request, "Stock alert: " + w)
    except svc.OrderError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except (KeyError, ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Invalid order data"}, status=400)
    printed = _auto_kot(request, order)
    return JsonResponse({"ok": True, "url": f"/orders/{order.pk}/" + ("" if printed else "?kot=1"), "round": order.kot_round})


@role_required(*POS_ROLES)
@require_POST
def pos_submit(request):
    if not request.branch:
        return JsonResponse({"ok": False, "error": "Select a branch first"}, status=400)
    b = request.branch
    try:
        data = json.loads(request.body)
    except ValueError:
        return JsonResponse({"ok": False, "error": "Bad request"}, status=400)
    try:
        lines = _cart_lines(b, data)
        order_type = data.get("order_type")
        table = Table.objects.filter(pk=data.get("table_id") or 0, branch=b).first()
        rider = Rider.objects.filter(pk=data.get("rider_id") or 0, branch=b).first()
        if request.user.role == User.WAITER:
            waiter = request.user
        else:
            waiter = User.objects.filter(pk=data.get("waiter_id") or 0, branch=b, role=User.WAITER).first()
        source = Channel.objects.filter(pk=data.get("source_id") or 0, is_active=True).first() \
            or Channel.objects.filter(code="pos").first()
        discount = Decimal(str(data.get("discount") or 0))
        if discount < 0:
            raise InvalidOperation
        order = svc.create_order(
            branch=b, order_type=order_type, items=lines, source=source, table=table, waiter=waiter, rider=rider,
            user=request.user, customer_name=data.get("customer_name", ""), phone=data.get("phone", ""),
            address=data.get("address", ""), notes=data.get("notes", ""), discount=discount, coupon_code=str(data.get("coupon") or ""),
            paid=bool(data.get("pay_now")), method=data.get("method", "cash"))
    except svc.OrderError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except (KeyError, ValueError, TypeError, InvalidOperation, MenuItem.DoesNotExist):
        return JsonResponse({"ok": False, "error": "Invalid order data"}, status=400)
    for w in svc.order_stock_alerts(order):
        messages.warning(request, "Stock alert: " + w)
    printed = _auto_kot(request, order)
    return JsonResponse({"ok": True, "id": order.pk, "number": order.number, "total": float(order.total),
                         "url": f"/orders/{order.pk}/" + ("" if printed else "?kot=1"), "receipt": f"/orders/{order.pk}/receipt/"})


# ------------------------------------------------------------------ orders
@role_required(*POS_ROLES)
def orders(request):
    qs = scope(Order.objects.select_related("branch", "source", "table", "waiter", "rider"), request)
    today = timezone.localdate()
    f_from = request.GET.get("from") or str(today)
    f_to = request.GET.get("to") or str(today)
    status = request.GET.get("status", "")
    otype = request.GET.get("type", "")
    source = request.GET.get("source", "")
    q = request.GET.get("q", "").strip()
    try:
        d1 = timezone.datetime.fromisoformat(f_from).date()
        d2 = timezone.datetime.fromisoformat(f_to).date()
    except ValueError:
        d1 = d2 = today
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(timezone.datetime.combine(d1, timezone.datetime.min.time()), tz)
    end = timezone.make_aware(timezone.datetime.combine(d2 + timedelta(days=1), timezone.datetime.min.time()), tz)
    if not q:
        qs = qs.filter(created_at__gte=start, created_at__lt=end)
    if status == "active":
        qs = qs.filter(status__in=Order.ACTIVE)
    elif status:
        qs = qs.filter(status=status)
    if otype:
        qs = qs.filter(order_type=otype)
    if source:
        qs = qs.filter(source_id=source)
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(customer_phone__icontains=q) | Q(customer_name__icontains=q)
                       | Q(external_ref__icontains=q))
    if request.user.role == User.WAITER:
        qs = qs.filter(Q(waiter=request.user) | Q(created_by=request.user))
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return render(request, "rms/orders.html", {
        "page": page, "f": {"from": str(d1), "to": str(d2), "status": status, "type": otype, "source": source, "q": q},
        "statuses": Order.STATUS, "types": Order.TYPES, "channels": Channel.objects.all()})


@role_required(*POS_ROLES, "kitchen")
def order_detail(request, pk):
    order = get_object_or_404(scope(Order.objects.select_related("branch", "source", "table", "waiter", "rider", "parent"), request), pk=pk)
    role = "owner" if request.user.is_superuser else request.user.role
    lines = list(order.lines.all())
    live = [l for l in lines if not l.is_void]
    from django.conf import settings as dj
    free_tables = Table.objects.filter(branch=order.branch, status__in=(Table.FREE, Table.RESERVED)).exclude(pk=order.table_id or 0)
    return render(request, "rms/order_detail.html", {
        "o": order, "lines": lines, "live_lines": live, "queue_pos": svc.queue_position(order),
        "cust_worth": (order.customer.points * Setting.get().loyalty_point_value) if order.customer_id else 0,
        "earn_pts": svc.points_for(order), "cfg": Setting.get(),
        "pay_link": order.pay_links.filter(status="pending").first(),
        "pay_url": (request.build_absolute_uri(reverse("pay_page", args=[order.pay_links.filter(status="pending").first().token]))
                    if order.pay_links.filter(status="pending").exists() else ""),
        "pay_paid": order.pay_links.filter(status="paid").first(),
        "fb": getattr(order, "feedback", None) if hasattr(order, "feedback") else None,
        "fb_url": request.build_absolute_uri(reverse("feedback_form", args=[order.ensure_feedback_token()])),
        "payments": list(order.payments.select_related("user")), "splits": list(order.splits.all()),
        "riders": Rider.objects.filter(branch=order.branch, is_active=True).exclude(status=Rider.OFFLINE),
        "waiters": User.objects.filter(role=User.WAITER, branch=order.branch, is_active=True),
        "methods": Order.METHODS, "free_tables": free_tables, "can_manage": role in ("owner", "manager"),
        "can_cashier": role in ("owner", "manager", "cashier"), "shift": svc.current_shift(order.branch, request.user),
        "auto_kot": bool(request.GET.get("kot")) and Setting.get().auto_print_kot,
        "reasons": VOID_REASONS})


@role_required(*POS_ROLES, "kitchen")
def kot(request, pk):
    """Kitchen order ticket: items of one KOT round (default = the latest), big and printable."""
    order = get_object_or_404(scope(Order.objects.select_related("branch", "table", "waiter"), request), pk=pk)
    try:
        rnd = int(request.GET.get("round") or order.kot_round)
    except ValueError:
        rnd = order.kot_round
    lines = [l for l in order.lines.all() if l.round == rnd]
    return render(request, "rms/kot.html", {"o": order, "lines": lines, "round": rnd})


@role_required(*POS_ROLES)
def receipt(request, pk):
    order = get_object_or_404(scope(Order.objects.select_related("branch", "table", "waiter"), request), pk=pk)
    return render(request, "rms/receipt.html", {"o": order, "lines": [l for l in order.lines.all() if not l.is_void],
                                                "payments": list(order.payments.all()),
                                                "fb_url": request.build_absolute_uri(reverse("feedback_form", args=[order.ensure_feedback_token()]))})


@role_required("owner", "manager", "cashier")
def bills(request):
    """All generated bills (completed orders) with totals, filterable by date / type / payment / search."""
    qs = scope(Order.objects.select_related("branch", "table", "waiter").exclude(bill_number=""), request)
    today = timezone.localdate()
    f_from = request.GET.get("from") or str(today)
    f_to = request.GET.get("to") or str(today)
    otype, method = request.GET.get("type", ""), request.GET.get("method", "")
    q = request.GET.get("q", "").strip()
    try:
        d1 = timezone.datetime.fromisoformat(f_from).date()
        d2 = timezone.datetime.fromisoformat(f_to).date()
    except ValueError:
        d1 = d2 = today
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(timezone.datetime.combine(d1, timezone.datetime.min.time()), tz)
    end = timezone.make_aware(timezone.datetime.combine(d2 + timedelta(days=1), timezone.datetime.min.time()), tz)
    if q:
        qs = qs.filter(Q(bill_number__icontains=q) | Q(number__icontains=q) | Q(customer_name__icontains=q)
                       | Q(customer_phone__icontains=q))
    else:
        qs = qs.filter(billed_at__gte=start, billed_at__lt=end)
    if otype:
        qs = qs.filter(order_type=otype)
    if method:
        qs = qs.filter(payment_method=method)
    qs = qs.order_by("-billed_at", "-id")
    agg = qs.aggregate(n=Count("id"), total=Sum("total"), tax=Sum("tax"))
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    return render(request, "rms/bills.html", {
        "page": page, "agg": agg, "types": Order.TYPES, "methods": Order.METHODS,
        "f": {"from": str(d1), "to": str(d2), "type": otype, "method": method, "q": q}})


VOID_REASONS = ["Customer changed mind", "Wrong item punched", "Item not available", "Long waiting time",
                "Customer complaint / quality", "Duplicate order", "Staff mistake", "Other"]


def _approver(request, order):
    """Manager override: a cashier types a manager's username + password to approve a void / cancel."""
    uname = request.POST.get("approver_user", "").strip()
    if not uname:
        return None
    from django.contrib.auth import authenticate
    a = authenticate(request, username=uname, password=request.POST.get("approver_pass", ""))
    if not a or not (a.is_superuser or a.role in ("owner", "manager")):
        raise svc.OrderError("Manager username / password is not valid")
    if not a.can_access_all and a.branch_id != order.branch_id:
        raise svc.OrderError("That manager belongs to another branch")
    return a


def _reason(request):
    r, extra = request.POST.get("reason", "").strip(), request.POST.get("reason_text", "").strip()
    return f"{r}: {extra}" if r == "Other" and extra else (extra if not r else r + (f" — {extra}" if extra else ""))


@role_required(*POS_ROLES, "kitchen", "kds")
@require_POST
def order_action(request, pk):
    order = get_object_or_404(scope(Order.objects, request), pk=pk)
    action = request.POST.get("action")
    nxt = request.POST.get("next") or f"/orders/{order.pk}/"
    role = "owner" if request.user.is_superuser else request.user.role
    try:
        if action == "advance" and order.acceptance == "pending":
            raise svc.OrderError("This online order is waiting for acceptance - open Online Orders and Accept or Reject it")
        if action == "advance":
            target = order.next_status
            if not target:
                raise svc.OrderError("Nothing to advance")
            if role in (User.KITCHEN, User.KDS) and target not in (Order.PREPARING, Order.READY):
                raise svc.OrderError("Kitchen can only mark orders preparing / ready")
            if role == User.KITCHEN and order.chef_id not in (None, request.user.pk):
                raise svc.OrderError("This order belongs to another chef")
            if role == User.WAITER and target not in (Order.SERVED,):
                raise svc.OrderError("Waiters can mark an order served and request the bill — the cashier takes payment")
            svc.set_status(order, target, request.user, request.POST.get("method", ""))
            if target == Order.COMPLETED:
                messages.success(request, f"Bill {order.bill_number} generated.")
                return redirect(f"/orders/{order.pk}/receipt/")
        elif role in (User.KITCHEN, User.KDS):
            raise svc.OrderError("Kitchen can only mark orders preparing / ready")
        elif action == "cancel":
            svc.cancel_order(order, _reason(request), request.user, _approver(request, order))
            messages.success(request, "Order cancelled and logged.")
        elif action == "void_line":
            line = get_object_or_404(order.lines, pk=request.POST.get("line") or 0)
            svc.void_line(order, line, _reason(request), request.user, _approver(request, order))
            messages.success(request, f"{line.name} voided and logged.")
        elif action == "request_bill":
            svc.request_bill(order)
            messages.success(request, "Bill requested — cashier can collect payment.")
        elif action == "move_table":
            t = get_object_or_404(Table, pk=request.POST.get("table") or 0, branch=order.branch)
            svc.move_table(order, t, request.user)
            messages.success(request, f"Order moved to table {t.name}.")
        elif action == "split":
            qty = {k[4:]: v for k, v in request.POST.items() if k.startswith("qty_") and v}
            child = svc.split_order(order, qty, request.user)
            messages.success(request, f"New bill {child.number} created.")
            return redirect(f"/orders/{child.pk}/")
        elif action == "redeem":
            if role == User.WAITER:
                raise svc.OrderError("Only the cashier can redeem points")
            pts, value = svc.redeem_points(order, request.POST.get("points"), request.user)
            messages.success(request, f"{pts} points redeemed — discount {value}.")
        elif action == "assign_rider":
            rider = get_object_or_404(Rider, pk=request.POST.get("rider"), branch=order.branch)
            if order.order_type != Order.DELIVERY:
                raise svc.OrderError("Not a delivery order")
            order.rider = rider
            order.save(update_fields=["rider"])
            svc.notify_rider(order)
            messages.success(request, f"Rider {rider.name} assigned.")
        elif action == "assign_waiter":
            w = User.objects.filter(pk=request.POST.get("waiter") or 0, role=User.WAITER, branch=order.branch).first()
            order.waiter = w
            order.save(update_fields=["waiter"])
        elif action == "pay":
            if role == User.WAITER:
                raise svc.OrderError("Payment is taken by the cashier")
            p = svc.add_payment(order, request.POST.get("method", "cash"), request.POST.get("amount") or None,
                                tendered=request.POST.get("tendered") or None, tip=request.POST.get("tip") or 0,
                                user=request.user)
            order.refresh_from_db()
            msg = f"Payment {p.amount} recorded."
            if p.change:
                msg += f" Give change: {p.change}."
            msg += " Fully paid." if order.is_paid else f" Balance due: {order.due}."
            messages.success(request, msg)
        else:
            raise svc.OrderError("Unknown action")
    except svc.OrderError as e:
        messages.error(request, str(e))
    return redirect(nxt)


# ------------------------------------------------------------------ kitchen
def _target_secs(o):
    mins = [l.item.prep_minutes for l in o.lines.all() if l.item_id]
    return (max(mins) if mins else 15) * 60


@role_required("manager", "kitchen", "kds", "cashier")
def kitchen(request):
    """Chef board: one column per chef (max N orders each), a shared waiting queue and a Ready column."""
    base = scope(Order.objects.select_related("branch", "table", "source", "chef")
                 .prefetch_related("lines__item"), request)
    is_chef = request.user.role == User.KITCHEN and not request.user.is_superuser
    chefs = scope(User.objects.filter(role=User.KITCHEN, is_active=True).select_related("branch"), request)
    if is_chef:
        chefs = chefs.filter(pk=request.user.pk)
    cooking = [o for o in base.filter(status=Order.PREPARING).order_by("started_at", "id")]
    queue = [o for o in base.filter(status=Order.NEW).exclude(acceptance="pending").order_by("created_at", "id")]
    ready_qs = base.filter(status=Order.READY).order_by("ready_at", "id")
    if is_chef:
        ready_qs = ready_qs.filter(chef=request.user)
        queue = [o for o in queue if o.branch_id == request.user.branch_id]
        cooking = [o for o in cooking if o.chef_id == request.user.pk]
    ready = list(ready_qs)
    for o in cooking + queue + ready:
        o.target = _target_secs(o)
    for i, o in enumerate(queue):
        o.pos = i + 1
    columns = []
    for c in chefs:
        mine = [o for o in cooking if o.chef_id == c.pk]
        columns.append({"chef": c, "orders": mine, "cap": c.branch.kitchen_capacity if c.branch_id else 3})
    unassigned = [o for o in cooking if not o.chef_id]
    is_kds = request.user.role == User.KDS and not request.user.is_superuser
    from .kitchen_stock import chef_stats, stock_panel
    from ..models import StockRequest
    kb = request.branch
    stats = chef_stats(request, list(chefs))
    stock_rows = stock_panel(kb) if kb else []
    my_reqs = list(StockRequest.objects.filter(branch=kb).select_related("ingredient", "po", "decided_by")[:8]) if kb else []
    return render(request, "rms/kitchen.html", {
        "stats": stats, "stock_rows": stock_rows[:14], "stock_low": sum(1 for r in stock_rows if r["low"]),
        "all_ings": [r["st"] for r in stock_rows], "my_reqs": my_reqs, "kb": kb,
        "can_ctl": not is_chef and not is_kds, "is_kds": is_kds,
        "columns": columns, "queue": queue, "ready": ready, "unassigned": unassigned, "is_chef": is_chef,
        "total_active": len(cooking) + len(queue)})


# ------------------------------------------------------------------ tables floor
@role_required(*POS_ROLES)
def tables(request):
    from ..models import Reservation
    qs = scope(Table.objects.select_related("branch"), request)
    active = {}
    for o in Order.objects.filter(table__in=qs, status__in=Order.ACTIVE, order_type=Order.DINE_IN).order_by("created_at"):
        active[o.table_id] = o
    start, end = svc.day_bounds(timezone.localdate())
    nxt = {}
    for r in Reservation.objects.filter(table__in=qs, status=Reservation.BOOKED, at__gte=start, at__lt=end).order_by("at"):
        nxt.setdefault(r.table_id, r)
    areas = {}
    for t in qs:
        t.active_order = active.get(t.pk)
        t.next_res = nxt.get(t.pk)
        areas.setdefault((t.branch if request.branch_all else None, t.area), []).append(t)
    c = {st: qs.filter(status=st).count() for st, _ in Table.STATUS}
    return render(request, "rms/tables.html", {
        "areas": [{"branch": k[0], "area": k[1], "tables": v} for k, v in areas.items()], "counts": c})


@role_required(*POS_ROLES)
def table_open(request, pk):
    t = get_object_or_404(scope(Table.objects, request), pk=pk)
    o = Order.objects.filter(table=t, status__in=Order.ACTIVE, order_type=Order.DINE_IN).order_by("created_at").first()
    if o:
        return redirect("order_detail", pk=o.pk)
    if request.branch is None or request.branch.pk != t.branch_id:
        request.session["branch"] = str(t.branch_id) if request.user.can_access_all else request.session.get("branch")
    return redirect(f"/pos/?table={t.pk}")


@role_required(*POS_ROLES)
@require_POST
def table_status(request, pk):
    """Reserve / release a free table, or tap 'Clean' when a cleaned table is ready for the next guests."""
    t = get_object_or_404(scope(Table.objects, request), pk=pk)
    st = request.POST.get("status")
    if st not in (Table.FREE, Table.RESERVED):
        messages.error(request, "Invalid table status.")
    elif Order.objects.filter(table=t, status__in=Order.ACTIVE).exists():
        messages.error(request, "Table has an open order.")
    else:
        t.status = st
        t.save(update_fields=["status"])
    return redirect("tables")



# ------------------------------------------------------------------ thermal (ESC/POS) printing
def _escpos_bytes(request, order, kind):
    from .. import escpos
    b = order.branch
    if kind == "kot":
        try:
            rnd = int(request.GET.get("round") or request.POST.get("round") or order.kot_round)
        except ValueError:
            rnd = order.kot_round
        lines = [l for l in order.lines.all() if l.round == rnd and not l.is_void]
        host, port = b.kitchen_printer_host or b.receipt_printer_host, b.kitchen_printer_port if b.kitchen_printer_host else b.receipt_printer_port
        return host, port, escpos.build_kot(order, lines, rnd, b.paper_chars)
    fb = request.build_absolute_uri(reverse("feedback_form", args=[order.ensure_feedback_token()]))
    return b.receipt_printer_host, b.receipt_printer_port, escpos.build_receipt(
        order, [l for l in order.lines.all() if not l.is_void], list(order.payments.all()), b.paper_chars, fb)


def _auto_kot(request, order):
    """Send the new KOT round straight to the kitchen printer (if auto-print is on and a printer IP is set)."""
    from .. import escpos
    order = Order.objects.select_related("branch", "table", "waiter").get(pk=order.pk)
    b = order.branch
    if not (Setting.get().auto_print_kot and (b.kitchen_printer_host or b.receipt_printer_host)):
        return False
    try:
        host, port, data = _escpos_bytes(request, order, "kot")
        escpos.send(host, port, data)
        messages.success(request, "Kitchen ticket printed.")
        return True
    except escpos.PrinterError as e:
        messages.warning(request, f"Kitchen printer: {e}")
        return False


@role_required("manager", "cashier", "waiter")
@require_POST
def order_print(request, pk, kind):
    from .. import escpos
    order = get_object_or_404(scope(Order.objects.select_related("branch", "table", "waiter"), request), pk=pk)
    if kind not in ("receipt", "kot"):
        raise Http404()
    if kind == "receipt" and request.user.role == "waiter":
        messages.error(request, "Only the cashier prints the bill.")
        return redirect("order_detail", pk=pk)
    try:
        host, port, data = _escpos_bytes(request, order, kind)
        escpos.send(host, port, data)
        messages.success(request, "Sent to the thermal printer.")
    except escpos.PrinterError as e:
        messages.error(request, str(e))
    return redirect("order_detail", pk=pk)


@role_required("manager", "cashier", "waiter")
def order_escpos(request, pk, kind):
    """Download the raw ESC/POS bytes (for a local print agent / RawBT on Android)."""
    order = get_object_or_404(scope(Order.objects.select_related("branch", "table", "waiter"), request), pk=pk)
    if kind not in ("receipt", "kot"):
        raise Http404()
    _, _, data = _escpos_bytes(request, order, kind)
    resp = HttpResponse(data, content_type="application/octet-stream")
    resp["Content-Disposition"] = f'attachment; filename="{order.number}-{kind}.bin"'
    return resp
