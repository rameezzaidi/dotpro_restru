from datetime import timedelta

from django.contrib import messages
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..models import Branch, Channel, Order, OrderItem, User
from ..permissions import role_required, master_required, scope


# ------------------------------------------------------------------ channels & integrations
@role_required("manager")
def channels_list(request):
    rows = []
    for ch in Channel.objects.all():
        rows.append({"ch": ch, "count": ch.orders.count()})
    base = request.build_absolute_uri("/")[:-1]
    return render(request, "rms/channels.html", {"rows": rows, "base": base,
                                                 "branch_codes": Branch.objects.filter(is_active=True)})


@master_required
@require_POST
def channel_regen(request, pk):
    ch = get_object_or_404(Channel, pk=pk)
    ch.api_key = ""
    ch.save()
    messages.success(request, f"New API key generated for {ch.name}. Update it in the connected app.")
    return redirect("channels")


@master_required
@require_POST
def channel_delete_safe(request, pk):
    ch = get_object_or_404(Channel, pk=pk)
    if ch.code in ("pos", "website"):
        messages.error(request, "Built-in channels cannot be deleted.")
    else:
        ch.delete()
        messages.success(request, "Channel deleted.")
    return redirect("channels")


# ------------------------------------------------------------------ reports
@role_required("manager")
def reports(request):
    today = timezone.localdate()
    try:
        d1 = timezone.datetime.fromisoformat(request.GET.get("from") or str(today - timedelta(days=29))).date()
        d2 = timezone.datetime.fromisoformat(request.GET.get("to") or str(today)).date()
    except ValueError:
        d1, d2 = today - timedelta(days=29), today
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(timezone.datetime.combine(d1, timezone.datetime.min.time()), tz)
    end = timezone.make_aware(timezone.datetime.combine(d2 + timedelta(days=1), timezone.datetime.min.time()), tz)

    qs = scope(Order.objects.exclude(status=Order.CANCELLED), request).filter(created_at__gte=start, created_at__lt=end)
    branches = Branch.objects.filter(is_active=True)
    if not request.branch_all:
        branches = branches.filter(pk=request.branch.pk)
    mixed = len({b.currency_code for b in branches}) > 1

    totals = qs.aggregate(n=Count("id"), rev=Sum("total"), tax=Sum("tax"), disc=Sum("discount"))
    by_day = list(qs.annotate(d=TruncDate("created_at", tzinfo=tz)).values("d").annotate(n=Count("id"), rev=Sum("total")).order_by("d"))
    by_type = []
    for code, label in Order.TYPES:
        r = qs.filter(order_type=code).aggregate(n=Count("id"), rev=Sum("total"))
        by_type.append({"label": label, "n": r["n"], "rev": r["rev"] or 0})
    by_source = []
    for ch in Channel.objects.all():
        r = qs.filter(source=ch).aggregate(n=Count("id"), rev=Sum("total"))
        if r["n"]:
            rev = r["rev"] or 0
            by_source.append({"label": ch.name, "n": r["n"], "rev": rev, "commission": rev * ch.commission_percent / 100})
    by_branch = []
    for b in branches:
        r = qs.filter(branch=b).aggregate(n=Count("id"), rev=Sum("total"))
        by_branch.append({"branch": b, "n": r["n"], "rev": r["rev"] or 0})
    line_total = ExpressionWrapper(F("qty") * F("price"), output_field=DecimalField(max_digits=14, decimal_places=2))
    top_items = (OrderItem.objects.filter(order__in=qs).values("name")
                 .annotate(q=Sum("qty"), rev=Sum(line_total)).order_by("-q")[:10])
    waiters = (qs.filter(waiter__isnull=False).values("waiter__first_name", "waiter__username")
               .annotate(n=Count("id"), rev=Sum("total")).order_by("-n")[:10])
    riders = (qs.filter(rider__isnull=False, status=Order.COMPLETED).values("rider__name")
              .annotate(n=Count("id")).order_by("-n")[:10])
    return render(request, "rms/reports.html", {
        "f": {"from": str(d1), "to": str(d2)}, "mixed": mixed, "totals": totals, "by_day": by_day, "by_type": by_type,
        "by_source": by_source, "by_branch": by_branch, "top_items": top_items, "waiters": waiters, "riders": riders})
