"""Customer CRM + loyalty points."""
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .. import services as svc
from ..models import Customer, Feedback, ItemFeedback, LoyaltyTxn, Order, OrderItem
from ..permissions import role_required, scope


@role_required("manager", "cashier")
def customers(request):
    q = (request.GET.get("q") or "").strip()
    seg = request.GET.get("seg", "")
    now = timezone.now()
    qs = Customer.objects.all()
    if not request.branch_all:
        ids = Order.objects.filter(branch=request.branch, customer__isnull=False).values("customer_id")
        qs = qs.filter(pk__in=ids)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=svc.norm_phone(q) or "~~~"))
    if seg == "vip":
        qs = qs.filter(visits__gte=15)
    elif seg == "lapsed":
        qs = qs.filter(last_visit__lt=now - timedelta(days=30), visits__gte=2)
    elif seg == "new":
        qs = qs.filter(created_at__gte=now - timedelta(days=7))
    elif seg == "points":
        qs = qs.filter(points__gt=0).order_by("-points")
    sort = request.GET.get("sort", "")
    qs = qs.order_by({"spent": "-total_spent", "visits": "-visits", "points": "-points"}.get(sort, "-last_visit"))
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    base = Customer.objects.all()
    stats = {"total": base.count(), "vip": base.filter(visits__gte=15).count(),
             "lapsed": base.filter(last_visit__lt=now - timedelta(days=30), visits__gte=2).count(),
             "points": base.aggregate(p=Sum("points"))["p"] or 0}
    return render(request, "rms/customers.html", {"page": page, "q": q, "seg": seg, "sort": sort, "stats": stats})


@role_required("manager", "cashier")
def customer_detail(request, pk):
    c = get_object_or_404(Customer, pk=pk)
    orders = scope(Order.objects.filter(customer=c).select_related("branch", "table"), request)
    if not request.branch_all and not orders.exists():
        return redirect("customers")
    done = orders.filter(status=Order.COMPLETED)
    fav = list(OrderItem.objects.filter(order__in=done, is_void=False).values("name").annotate(q=Count("id"), n=Sum("qty")).order_by("-n")[:6])
    rated = ItemFeedback.objects.filter(feedback__order__customer=c, rating__gt=0).aggregate(a=Avg("rating"), n=Count("id"))
    sess = Feedback.objects.filter(order__customer=c, service_rating__gt=0).aggregate(a=Avg("service_rating"))
    first_branch = done.first().branch if done.exists() else None
    cfg = svc.Setting.get()
    return render(request, "rms/customer_detail.html", {
        "c": c, "orders": orders.order_by("-created_at")[:20], "fav": fav, "ledger": c.ledger.select_related("order")[:25],
        "avg": (c.total_spent / c.visits) if c.visits else Decimal(0), "rated": rated, "service_avg": sess["a"],
        "cbranch": first_branch or request.ctx_branch, "cfg": cfg,
        "worth": (c.points * cfg.loyalty_point_value)})


@role_required("manager", "cashier")
@require_POST
def customer_action(request, pk):
    c = get_object_or_404(Customer, pk=pk)
    act = request.POST.get("action")
    if act == "save":
        c.name = request.POST.get("name", "")[:100]
        c.email = request.POST.get("email", "")[:254]
        c.address = request.POST.get("address", "")[:255]
        c.notes = request.POST.get("notes", "")[:255]
        c.save()
        messages.success(request, "Customer saved.")
    elif act == "adjust":
        if request.user.role not in ("owner", "manager") and not request.user.is_superuser:
            messages.error(request, "Only a manager can adjust points.")
        else:
            try:
                pts = int(request.POST.get("points") or 0)
            except ValueError:
                pts = 0
            reason = (request.POST.get("reason") or "").strip()[:60]
            if not pts or not reason:
                messages.error(request, "Enter points (+/-) and a reason.")
            elif c.points + pts < 0:
                messages.error(request, "Customer cannot go below 0 points.")
            else:
                c.points += pts
                if pts > 0:
                    c.lifetime_points += pts
                c.save()
                LoyaltyTxn.objects.create(customer=c, points=pts, reason=f"Manual: {reason}")
                messages.success(request, f"{pts:+d} points applied.")
    return redirect("customer_detail", pk=c.pk)
