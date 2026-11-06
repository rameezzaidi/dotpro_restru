"""Profit & Loss and the promo-code check."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .. import analytics as an, services as svc
from ..models import Branch, Expense, Order, Rider, User
from ..permissions import role_required, scope

D0 = Decimal("0")


def _range(request):
    today = timezone.localdate()
    try:
        d1 = timezone.datetime.fromisoformat(request.GET.get("from") or str(today.replace(day=1))).date()
        d2 = timezone.datetime.fromisoformat(request.GET.get("to") or str(today)).date()
    except ValueError:
        d1, d2 = today.replace(day=1), today
    if d2 < d1:
        d1, d2 = d2, d1
    return d1, d2


def pnl_data(request, d1, d2):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(timezone.datetime.combine(d1, timezone.datetime.min.time()), tz)
    end = timezone.make_aware(timezone.datetime.combine(d2 + timedelta(days=1), timezone.datetime.min.time()), tz)
    done = scope(Order.objects.filter(status=Order.COMPLETED), request).filter(created_at__gte=start, created_at__lt=end)
    a = done.aggregate(total=Sum("total"), tax=Sum("tax"), disc=Sum("discount"))
    revenue = (a["total"] or D0) - (a["tax"] or D0)
    cogs = sum(an.food_cost_by(done, "branch_id").values(), D0)
    exp_qs = scope(Expense.objects.filter(date__gte=d1, date__lte=d2), request)
    by_cat = []
    for code, label in Expense.CATS:
        v = exp_qs.filter(category=code).aggregate(s=Sum("amount"))["s"] or D0
        if v:
            by_cat.append({"label": label, "amount": v})
    expenses = sum((r["amount"] for r in by_cat), D0)
    days = (d2 - d1).days + 1
    sal_users = scope(User.objects.filter(is_active=True, monthly_salary__gt=0), request).aggregate(s=Sum("monthly_salary"))["s"] or D0
    sal_riders = scope(Rider.objects.filter(is_active=True, monthly_salary__gt=0), request).aggregate(s=Sum("monthly_salary"))["s"] or D0
    salaries = ((sal_users + sal_riders) * days / 30).quantize(Decimal("0.01"))
    gross = revenue - cogs
    net = gross - expenses - salaries
    return {"orders": done.count(), "revenue": revenue, "discounts": a["disc"] or D0, "cogs": cogs, "gross": gross,
            "gross_pct": (gross / revenue * 100) if revenue else D0, "expenses": expenses, "by_cat": by_cat,
            "salaries": salaries, "net": net, "net_pct": (net / revenue * 100) if revenue else D0, "days": days}


@role_required("manager")
def profit_loss(request):
    d1, d2 = _range(request)
    mixed = request.branch_all and Branch.objects.filter(is_active=True).values("currency_code").distinct().count() > 1
    return render(request, "rms/pnl.html", {"p": pnl_data(request, d1, d2), "d1": d1, "d2": d2, "mixed": mixed})


@role_required("manager", "cashier", "waiter")
@require_POST
def coupon_check(request):
    import json
    try:
        data = json.loads(request.body)
        sub = Decimal(str(data.get("subtotal") or 0))
        c, amt = svc.coupon_discount(data.get("code"), request.branch, sub)
    except svc.OrderError as e:
        return JsonResponse({"ok": False, "error": str(e)})
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid request"})
    return JsonResponse({"ok": True, "amount": float(amt), "code": c.code})
