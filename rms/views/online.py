from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .. import services as svc
from ..models import Order
from ..permissions import role_required, scope

ROLES = ("manager", "cashier")
REASONS = ["Kitchen is too busy", "Item sold out", "Outside delivery area", "Closing soon", "Could not reach the customer"]


def _pending(request):
    return scope(Order.objects.filter(acceptance="pending", status=Order.NEW), request)


@role_required(*ROLES)
def online_orders(request):
    pend = list(_pending(request).select_related("branch", "source").prefetch_related("lines").order_by("created_at"))
    now = timezone.now()
    for o in pend:
        o.waited = int((now - o.created_at).total_seconds() // 60)
    from datetime import timedelta
    recent = scope(Order.objects.filter(acceptance__in=("accepted", "rejected"), created_at__gte=now - timedelta(hours=24)), request) \
        .select_related("branch", "source").order_by("-created_at")[:25]
    return render(request, "rms/online_orders.html", {"pending": pend, "recent": recent, "reasons": REASONS, "etas": (15, 20, 30, 45, 60)})


@role_required(*ROLES)
def online_count(request):
    return JsonResponse({"n": _pending(request).count()})


@role_required(*ROLES)
@require_POST
def online_action(request, pk):
    o = get_object_or_404(scope(Order.objects, request), pk=pk)
    try:
        if request.POST.get("action") == "accept":
            svc.accept_online(o, request.user, request.POST.get("eta") or 30)
            messages.success(request, f"{o.number} accepted - sent to the kitchen.")
        else:
            reason = request.POST.get("reason", "")
            if reason == "other" or not reason:
                reason = request.POST.get("other", "")
            svc.reject_online(o, request.user, reason)
            messages.info(request, f"{o.number} rejected and the customer was told.")
    except svc.OrderError as e:
        messages.error(request, str(e))
    return redirect("online_orders")
