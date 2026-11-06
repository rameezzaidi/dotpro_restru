from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, F, Q, Sum
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from ..countries import LANGUAGES
from ..forms import SettingForm
from ..models import Branch, Order, OrderItem, Stock, Table, User
from ..permissions import master_required, role_required, scope


def home(request):
    return redirect("dashboard" if request.user.is_authenticated else "login")


def _day_bounds(day):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time()), tz)
    return start, start + timedelta(days=1)


@login_required
def dashboard(request):
    from ..roles import LANDING
    role = "owner" if request.user.is_superuser else request.user.role
    if role in LANDING:
        return redirect(LANDING[role])
    is_mgr = role in ("owner", "manager")
    today = timezone.localdate()
    start, end = _day_bounds(today)
    orders = scope(Order.objects.exclude(status=Order.CANCELLED), request)
    if role == User.WAITER:                      # a waiter only sees his own orders
        orders = orders.filter(Q(waiter=request.user) | Q(created_by=request.user))
    todays = orders.filter(created_at__gte=start, created_at__lt=end)

    branches = Branch.objects.filter(is_active=True)
    if not request.branch_all:
        branches = branches.filter(pk=request.branch.pk) if request.branch else branches.none()
    mixed = len({b.currency_code for b in branches}) > 1

    agg = todays.aggregate(n=Count("id"), rev=Sum("total"))
    by_type = {r["order_type"]: r for r in todays.values("order_type").annotate(n=Count("id"), rev=Sum("total"))}
    type_rows = [{"label": label, "n": by_type.get(code, {}).get("n", 0), "rev": by_type.get(code, {}).get("rev") or 0}
                 for code, label in Order.TYPES]
    by_source = list(todays.values("source__name").annotate(n=Count("id"), rev=Sum("total")).order_by("-n"))

    # 7-day chart (revenue, or order count when currencies are mixed)
    series, peak = [], 0
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        s, e = _day_bounds(d)
        qs = orders.filter(created_at__gte=s, created_at__lt=e)
        val = qs.count() if mixed else (qs.aggregate(x=Sum("total"))["x"] or 0)
        series.append({"label": d.strftime("%a"), "value": val})
        peak = max(peak, val)
    for p in series:
        p["pct"] = int((p["value"] / peak) * 100) if peak else 0

    low = scope(Stock.objects.select_related("ingredient", "branch").filter(
        Q(reorder_level__gt=0, quantity__lte=F("reorder_level")) |
        Q(reorder_level=0, ingredient__default_reorder_level__gt=0, quantity__lte=F("ingredient__default_reorder_level"))), request)[:8]
    tables = scope(Table.objects.all(), request)
    open_orders = orders.filter(status__in=Order.ACTIVE).count()

    top_items = (OrderItem.objects.filter(order__in=todays).values("name")
                 .annotate(q=Sum("qty")).order_by("-q")[:6])

    branch_rows = []
    if request.branch_all:
        for b in branches:
            bq = todays.filter(branch=b).aggregate(n=Count("id"), rev=Sum("total"))
            branch_rows.append({"branch": b, "n": bq["n"], "rev": bq["rev"] or 0,
                                "open": orders.filter(branch=b, status__in=Order.ACTIVE).count()})

    low_fb = []
    if request.user.is_superuser or request.user.role in ("owner", "manager"):
        from ..models import Feedback
        low_fb = list(scope(Feedback.objects.filter(created_at__gte=timezone.now() - timedelta(days=3)), request)
                      .filter(Q(service_rating__range=(1, 2)) | Q(items__rating__range=(1, 2))).distinct()
                      .select_related("order", "branch").prefetch_related("items")[:5])
    return render(request, "rms/dashboard.html", {
        "low_fb": low_fb, "is_mgr": is_mgr, "role": role,
        "stats": {"orders": agg["n"], "revenue": agg["rev"] or Decimal(0), "open": open_orders,
                  "tables_busy": tables.filter(status=Table.OCCUPIED).count(), "tables_total": tables.count(),
                  "low": len(low)},
        "mixed": mixed, "type_rows": type_rows, "by_source": by_source, "series": series, "low_stock": low,
        "top_items": top_items, "branch_rows": branch_rows,
        "recent": todays.select_related("branch", "source", "table")[:8],
    })


@login_required
def switch_branch(request, branch_id):
    if not request.user.can_access_all:
        messages.error(request, "You can only work in your own branch.")
        return redirect("dashboard")
    if branch_id == "all":
        request.session["branch"] = "all"
    elif Branch.objects.filter(pk=branch_id, is_active=True).exists():
        request.session["branch"] = str(branch_id)
    nxt = request.GET.get("next") or request.META.get("HTTP_REFERER") or "/"
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        nxt = "/"
    return HttpResponseRedirect(nxt)


@login_required
def set_language(request, code):
    if request.setting.language_selection_enabled and code in LANGUAGES:
        request.session["lang"] = code
        if request.user.language != code:
            request.user.language = code
            request.user.save(update_fields=["language"])
    nxt = request.META.get("HTTP_REFERER") or "/"
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        nxt = "/"
    return HttpResponseRedirect(nxt)


@master_required
def settings_view(request):
    from ..models import Setting
    obj = Setting.get()
    if request.method == "POST":
        form = SettingForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Settings saved.")
            return redirect("settings")
    else:
        form = SettingForm(instance=obj)
    return render(request, "rms/crud_form.html", {"title": "Settings", "form": form, "back": "/", "no_delete": True})
