from decimal import Decimal

from django.contrib import messages
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .. import services as svc
from ..context import NAV
from ..models import Order, Rider
from ..permissions import role_required
from ..roles import ROLE_INFO


@role_required("manager")
def roles_matrix(request):
    ids = [r[0] for r in ROLE_INFO]
    pages = [{"label": l, "group": g, "roles": set(ids) if roles is None else set(roles) | {"owner"}} for l, url, ic, roles, g in NAV]
    return render(request, "rms/roles_matrix.html", {"roles": ROLE_INFO, "pages": pages, "ids": ids})


def _rider(request):
    return Rider.objects.filter(user=request.user).select_related("branch").first()


@role_required("rider")
def rider_home(request):
    r = _rider(request)
    if not r:
        return render(request, "rms/rider_home.html", {"rider": None})
    today = timezone.localdate()
    mine = Order.objects.filter(rider=r).select_related("branch", "table")
    todo = list(mine.filter(status=Order.READY).prefetch_related("lines"))
    road = list(mine.filter(status=Order.OUT).prefetch_related("lines"))
    done = mine.filter(status=Order.COMPLETED, ready_at__date=today)
    agg = done.aggregate(n=Count("id"), cash=Sum("total"))
    cash = Decimal("0")
    for o in done:
        cash += sum((p.amount for p in o.payments.filter(method="cash")), Decimal("0")) if hasattr(o, "payments") else 0
    n = agg["n"] or 0
    return render(request, "rms/rider_home.html", {
        "rider": r, "todo": todo, "road": road, "done": done.order_by("-ready_at")[:15], "n_done": n,
        "earn": (r.per_delivery_fee or 0) * n, "cash": cash, "branch": r.branch})


@role_required("rider")
@require_POST
def rider_action(request, pk):
    r = _rider(request)
    o = get_object_or_404(Order, pk=pk, rider=r) if r else None
    if not o:
        messages.error(request, "That delivery is not assigned to you.")
        return redirect("rider_home")
    act = request.POST.get("action")
    try:
        if act == "pickup" and o.status == Order.READY:
            svc.set_status(o, Order.OUT, request.user)
            messages.success(request, f"{o.number} picked up - drive safe.")
        elif act == "deliver" and o.status == Order.OUT:
            svc.set_status(o, Order.COMPLETED, request.user, request.POST.get("method") or "cash")
            messages.success(request, f"{o.number} delivered{' - cash collected' if o.total else ''}.")
        else:
            messages.error(request, "Nothing to do for this order right now.")
    except svc.OrderError as e:
        messages.error(request, str(e))
    return redirect("rider_home")


@role_required("waiter", "cashier", "kitchen", "kds", "storekeeper", "rider")
def my_slip(request):
    u = request.user
    if u.role == "rider":
        r = _rider(request)
        return redirect("slip", kind="rider", pk=r.pk) if r else redirect("rider_home")
    return redirect("slip", kind="waiter" if u.role == "waiter" else "staff", pk=u.pk)
