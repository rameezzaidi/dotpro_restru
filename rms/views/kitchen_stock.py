from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db.models import Avg, ExpressionWrapper, F, DurationField
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .. import services as svc
from ..models import (Ingredient, Order, PurchaseLine, PurchaseOrder, Stock, StockRequest, User)
from ..permissions import role_required, scope

KITCHEN = ("manager", "kitchen", "kds")


def chef_stats(request, chefs):
    """Per chef: orders finished today (marked ready), average cooking time, still cooking."""
    start, end = svc.day_bounds(timezone.localdate())
    base = scope(Order.objects.filter(chef__in=[c.pk for c in chefs]), request)
    rows = []
    for c in chefs:
        done = base.filter(chef=c, ready_at__gte=start, ready_at__lt=end)
        avg = done.filter(started_at__isnull=False).aggregate(
            a=Avg(ExpressionWrapper(F("ready_at") - F("started_at"), output_field=DurationField())))["a"]
        rows.append({"chef": c, "done": done.count(), "avg_min": round(avg.total_seconds() / 60, 1) if avg else None,
                     "cooking": base.filter(chef=c, status=Order.PREPARING).count(),
                     "total": base.filter(chef=c, ready_at__isnull=False).count()})
    return rows


def stock_panel(branch):
    """Lowest-stock ingredients of the branch, with a suggested quantity to ask for."""
    rows = []
    for st in Stock.objects.filter(branch=branch).select_related("ingredient"):
        lvl = st.level
        out, low = st.quantity <= 0, bool(lvl and st.quantity <= lvl)
        sug = max((lvl * 2 - st.quantity) if lvl else Decimal(1), lvl or Decimal(1))
        ratio = (st.quantity / lvl) if lvl else Decimal(99)
        rows.append({"st": st, "out": out, "low": low, "suggest": sug.quantize(Decimal("0.1")), "ratio": ratio})
    rows.sort(key=lambda r: (not r["out"], not r["low"], r["ratio"], r["st"].ingredient.name))
    return rows


@role_required(*KITCHEN)
@require_POST
def request_stock(request):
    b = request.branch
    ing = Ingredient.objects.filter(pk=request.POST.get("ingredient") or 0).first()
    try:
        qty = Decimal(request.POST.get("qty") or 0)
    except InvalidOperation:
        qty = Decimal(0)
    if not (b and ing and qty > 0):
        messages.error(request, "Choose an ingredient and a quantity.")
        return redirect("kitchen")
    dup = StockRequest.objects.filter(branch=b, ingredient=ing, status=StockRequest.PENDING).first()
    if dup:
        dup.qty = max(dup.qty, qty)
        dup.urgent = dup.urgent or bool(request.POST.get("urgent"))
        dup.save(update_fields=["qty", "urgent"])
        svc.notify(svc.supply_users(b), f"Stock request updated: {ing.name}",
                   f"{request.user.display_name} ({b.code}) now needs {dup.qty.normalize():f} {ing.unit}{' - URGENT' if dup.urgent else ''}",
                   "/stock/requests/", "urgent" if dup.urgent else "info")
        messages.info(request, f"A request for {ing.name} is already waiting - updated it.")
    else:
        r = StockRequest.objects.create(branch=b, ingredient=ing, qty=qty, urgent=bool(request.POST.get("urgent")),
                                        note=request.POST.get("note", "")[:200], requested_by=request.user)
        svc.notify(svc.supply_users(b), ("URGENT stock request: " if r.urgent else "New stock request: ") + ing.name,
                   f"{request.user.display_name} ({b.code}) needs {qty.normalize():f} {ing.unit}" + (f" - {r.note}" if r.note else ""),
                   "/stock/requests/", "urgent" if r.urgent else "info")
        messages.success(request, f"Request sent to the manager: {qty.normalize():f} {ing.unit} {ing.name}.")
    return redirect("kitchen")


@role_required("manager", "storekeeper")
def stock_requests(request):
    qs = scope(StockRequest.objects.select_related("branch", "ingredient", "requested_by", "decided_by", "po"), request)
    have = {(x.branch_id, x.ingredient_id): x.quantity for x in Stock.objects.all()}
    pend = [r for r in qs if r.status == StockRequest.PENDING]
    for r in pend:
        r.have = have.get((r.branch_id, r.ingredient_id), 0)
    return render(request, "rms/stock_requests.html", {
        "pending": pend, "history": [r for r in qs if r.status != StockRequest.PENDING][:150]})


@role_required("manager", "storekeeper")
@require_POST
def stock_request_action(request, pk):
    r = get_object_or_404(scope(StockRequest.objects.select_related("ingredient", "branch"), request), pk=pk)
    if r.status != StockRequest.PENDING:
        return redirect("stock_requests")
    act = request.POST.get("action")
    r.decided_by, r.decided_at, r.decision_note = request.user, timezone.now(), request.POST.get("note", "")[:200]
    if act == "reject":
        r.status = StockRequest.REJECTED
        r.save()
        svc.notify([r.requested_by], f"Stock request rejected: {r.ingredient.name}",
                   f"{request.user.display_name}: {r.decision_note or 'no reason given'}", "/kitchen/", "bad")
        messages.info(request, "Request rejected.")
        return redirect("stock_requests")
    try:
        qty = Decimal(request.POST.get("qty") or r.qty)
    except InvalidOperation:
        qty = r.qty
    r.qty, r.status = qty, StockRequest.APPROVED
    sup = r.ingredient.default_supplier
    if sup:
        po = PurchaseOrder.objects.filter(branch=r.branch, supplier=sup, status=PurchaseOrder.DRAFT).first()
        if not po:
            po = PurchaseOrder.objects.create(number=svc.next_po_number(r.branch), branch=r.branch, supplier=sup,
                                              created_by=request.user, status=PurchaseOrder.DRAFT, notes="From kitchen stock request")
        line = PurchaseLine.objects.filter(po=po, ingredient=r.ingredient).first()
        if line:
            line.qty += qty
            line.save(update_fields=["qty"])
        else:
            PurchaseLine.objects.create(po=po, ingredient=r.ingredient, qty=qty, unit_cost=r.ingredient.unit_cost)
        r.po = po
        messages.success(request, f"Approved - added to draft {po.number} ({sup.name}). Review it, then send.")
    else:
        messages.warning(request, f"Approved, but {r.ingredient.name} has no default supplier - create a purchase order or transfer manually.")
    r.save()
    svc.notify([r.requested_by], f"Stock approved: {r.ingredient.name}",
               f"{qty.normalize():f} {r.ingredient.unit} approved by {request.user.display_name}" + (f" - on {r.po.number}" if r.po else " - no supplier set, manager will arrange"),
               "/kitchen/", "ok")
    return redirect("stock_requests")


# ------------------------------------------------------------------ bell icon feed
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from ..models import Notification


@login_required
def notif_feed(request):
    try:
        from ..automation import tick
        tick()                      # throttled to once a minute; keeps alerts live without a separate scheduler
    except Exception:               # noqa: BLE001
        pass
    qs = Notification.objects.filter(user=request.user)
    rows = [{"id": n.pk, "title": n.title, "body": n.body, "url": n.url, "kind": n.kind, "read": n.is_read,
             "at": timezone.localtime(n.created_at).strftime("%d %b %H:%M")} for n in qs[:15]]
    return JsonResponse({"unread": qs.filter(is_read=False).count(), "items": rows})


@login_required
@require_POST
def notif_read(request):
    qs = Notification.objects.filter(user=request.user, is_read=False)
    if request.POST.get("id"):
        qs = qs.filter(pk=request.POST["id"])
    qs.update(is_read=True)
    return JsonResponse({"ok": True})
