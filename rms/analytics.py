"""Analytics engine: staff performance, cost per item, menu engineering, sales / demand forecast and insights.

No external services are needed. Forecasting is a classic statistical model (weekday seasonality x linear trend) with a
back-test so the screen can show how accurate it was on the last 7 days.
"""
import json
import math
import os
import urllib.request
from collections import defaultdict
from datetime import timedelta
from decimal import ROUND_CEILING, Decimal

from django.db.models import Avg, Count, DecimalField, ExpressionWrapper, F, Max, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import Branch, BranchItem, MenuItem, Order, OrderItem, Recipe, Stock, VoidLog

D0 = Decimal("0")
LINE_TOTAL = ExpressionWrapper(F("qty") * F("price"), output_field=DecimalField(max_digits=14, decimal_places=2))


def _aware(d):
    return timezone.make_aware(timezone.datetime.combine(d, timezone.datetime.min.time()), timezone.get_current_timezone())


def period(request, default_days=30):
    """(d1, d2, start, end) from ?from=&to= (inclusive dates, branch-local)."""
    today = timezone.localdate()
    try:
        d1 = timezone.datetime.fromisoformat(request.GET.get("from") or str(today - timedelta(days=default_days - 1))).date()
        d2 = timezone.datetime.fromisoformat(request.GET.get("to") or str(today)).date()
    except ValueError:
        d1, d2 = today - timedelta(days=default_days - 1), today
    if d2 < d1:
        d1, d2 = d2, d1
    return d1, d2, _aware(d1), _aware(d2 + timedelta(days=1))


def single_branch(request):
    """Cost / price analytics only make sense in one currency: returns (branch, all_branches_for_picker)."""
    if request.branch:
        return request.branch, []
    branches = list(Branch.objects.filter(is_active=True))
    sel = request.GET.get("b")
    chosen = next((b for b in branches if str(b.pk) == str(sel)), None)
    return chosen or getattr(request, "ctx_branch", None) or branches[0], branches


# ----------------------------------------------------------------------------- costs
def base_costs():
    """{item_id: ingredient cost of one portion} in head-office currency."""
    out = defaultdict(Decimal)
    for r in Recipe.objects.select_related("ingredient"):
        out[r.item_id] += r.quantity * r.ingredient.unit_cost
    return out


def food_cost_by(orders, key):
    """Ingredient cost of the food on `orders` grouped by an Order field (e.g. 'waiter_id'), in each branch's currency."""
    base = base_costs()
    factor = {b.pk: b.cost_factor for b in Branch.objects.all()}
    rows = (OrderItem.objects.filter(order__in=orders, is_void=False, item__isnull=False)
            .values(f"order__{key}", "order__branch_id", "item_id").annotate(q=Sum("qty")))
    out = defaultdict(Decimal)
    for r in rows:
        out[r[f"order__{key}"]] += base.get(r["item_id"], D0) * factor.get(r["order__branch_id"], 1) * r["q"]
    return out


def money_round(v, branch):
    v = Decimal(v)
    return v.quantize(Decimal("1")) if branch.currency_code == "PKR" else v.quantize(Decimal("0.01"))


# ----------------------------------------------------------------------------- staff
def _done(request_scope_qs, start, end):
    return request_scope_qs.filter(status=Order.COMPLETED, created_at__gte=start, created_at__lt=end)


def _sum_block(done):
    a = done.aggregate(n=Count("id"), total=Sum("total"), tax=Sum("tax"), tips=Sum("tip"), fee=Sum("delivery_fee"),
                       svc=Sum("service_charge"), tables=Count("table_id", distinct=True))
    cost = sum(food_cost_by(done, "branch_id").values(), D0)
    revenue = (a["total"] or D0) - (a["tax"] or D0)
    return {"n": a["n"] or 0, "gross": a["total"] or D0, "revenue": revenue, "tips": a["tips"] or D0,
            "fees": a["fee"] or D0, "service": a["svc"] or D0, "tables": a["tables"] or 0, "cost": cost,
            "profit": revenue - cost, "avg": (a["total"] or D0) / a["n"] if a["n"] else D0}


def pay_breakdown(obj, kind, days, revenue, deliveries, tips):
    """What the person earned personally in the period: prorated salary + commission + per-delivery pay + tips."""
    salary = obj.monthly_salary * Decimal(days) / Decimal(30)
    commission = revenue * obj.commission_percent / 100
    per_del = obj.per_delivery_fee * deliveries if kind == "rider" else D0
    return {"salary_part": salary, "commission": commission, "delivery_pay": per_del,
            "total_income": salary + commission + per_del + tips}


def staff_profile_data(kind, obj, d1, d2, start, end):
    field = "waiter" if kind == "waiter" else "rider"
    mine = Order.objects.filter(**{field: obj})
    done = _done(mine, start, end)
    s = _sum_block(done)
    s["cancelled"] = mine.filter(status=Order.CANCELLED, created_at__gte=start, created_at__lt=end).count()
    s["active"] = mine.filter(status__in=Order.ACTIVE).count()
    s["items"] = OrderItem.objects.filter(order__in=done, is_void=False).aggregate(q=Sum("qty"))["q"] or 0
    if kind == "waiter":
        s["voids"] = VoidLog.objects.filter(user=obj, created_at__gte=start, created_at__lt=end).exclude(kind=VoidLog.REFUND).count()
    else:
        mins = []
        for r, b in done.exclude(ready_at=None).exclude(billed_at=None).values_list("ready_at", "billed_at")[:800]:
            mins.append(max(0, (b - r).total_seconds() / 60))
        s["avg_minutes"] = round(sum(mins) / len(mins)) if mins else None
        s["out"] = mine.filter(status=Order.OUT).count()
    days_n = max(1, (d2 - d1).days + 1)
    s.update(pay_breakdown(obj, kind, days_n, s["revenue"], s["n"], s["tips"]))
    s["tip_pct"] = round(float(s["tips"]) / float(s["revenue"]) * 100, 1) if s["revenue"] else 0
    s["earn_per_order"] = s["revenue"] / s["n"] if s["n"] else D0

    # per day bars (max 31 days)
    tz = timezone.get_current_timezone()
    rows = {r["d"]: r for r in done.annotate(d=TruncDate("created_at", tzinfo=tz)).values("d")
            .annotate(n=Count("id"), rev=Sum("total"), tips=Sum("tip"))}
    days = []
    span = min((d2 - d1).days + 1, 31)
    for i in range(span):
        d = d2 - timedelta(days=span - 1 - i)
        r = rows.get(d)
        days.append({"label": d.strftime("%d %b"), "n": r["n"] if r else 0, "value": r["rev"] if r else D0,
                     "tips": r["tips"] if r else D0})
    mx = max([float(x["value"]) for x in days] + [1])
    for x in days:
        x["pct"] = round(float(x["value"]) / mx * 100, 1)

    top = list(OrderItem.objects.filter(order__in=done, is_void=False).values("name")
               .annotate(q=Sum("qty"), rev=Sum(LINE_TOTAL)).order_by("-q")[:8])
    recent = mine.select_related("table").order_by("-created_at")[:12]

    # rank inside the branch
    peers = (_done(Order.objects.filter(branch=obj.branch, **{f"{field}__isnull": False}), start, end)
             .values(f"{field}_id").annotate(t=Sum("total")).order_by("-t"))
    ids = [p[f"{field}_id"] for p in peers]
    rank = ids.index(obj.pk) + 1 if obj.pk in ids else None
    best_day = max(days, key=lambda x: x["value"]) if days and any(x["n"] for x in days) else None
    tables = []
    if kind == "waiter":
        from .models import Feedback
        ratings = defaultdict(list)
        for tid, r in Feedback.objects.filter(order__in=done, service_rating__gt=0).values_list("order__table_id", "service_rating"):
            ratings[tid].append(r)
        for r in (done.filter(table__isnull=False).values("table_id", "table__name", "table__area")
                  .annotate(n=Count("id"), total=Sum("total"), tax=Sum("tax"), tips=Sum("tip"), last=Max("created_at")).order_by("-total")):
            rt = ratings.get(r["table_id"], [])
            tables.append({"name": r["table__name"], "area": r["table__area"], "visits": r["n"], "earned": (r["total"] or D0) - (r["tax"] or D0),
                           "tips": r["tips"] or D0, "last": r["last"], "rating": round(sum(rt) / len(rt), 1) if rt else None})
        mt = max([float(t["earned"]) for t in tables] + [1])
        for t in tables:
            t["pct"] = round(float(t["earned"]) / mt * 100)
    from . import feedback as fbk
    fb = fbk.waiter_feedback(obj, start, end)
    return {"s": s, "tables": tables, "fb": fb, "days": days, "days_rev": [d for d in reversed(days) if d["n"]], "top": top, "recent": recent, "rank": rank, "of": len(ids), "best_day": best_day}


def staff_board(kind, request_scope, start, end):
    """Leaderboard rows for every waiter / rider in scope. request_scope = function(qs, field) -> qs."""
    from .models import Rider, User
    field = "waiter" if kind == "waiter" else "rider"
    people = (User.objects.filter(role="waiter", is_active=True) if kind == "waiter" else Rider.objects.filter(is_active=True))
    people = request_scope(people.select_related("branch"))
    done = _done(request_scope(Order.objects.filter(**{f"{field}__isnull": False})), start, end)
    agg = {r[f"{field}_id"]: r for r in done.values(f"{field}_id").annotate(
        n=Count("id"), total=Sum("total"), tax=Sum("tax"), tips=Sum("tip"), fee=Sum("delivery_fee"),
        tables=Count("table_id", distinct=True))}
    cost = food_cost_by(done, f"{field}_id")
    active = {r[f"{field}_id"]: r["n"] for r in request_scope(Order.objects.filter(status__in=Order.ACTIVE, **{f"{field}__isnull": False}))
              .values(f"{field}_id").annotate(n=Count("id"))}
    rows = []
    for p in people:
        a = agg.get(p.pk)
        revenue = ((a["total"] or D0) - (a["tax"] or D0)) if a else D0
        c = cost.get(p.pk, D0)
        tips_v = (a["tips"] or D0) if a else D0
        pay = pay_breakdown(p, kind, max(1, round((end - start).total_seconds() / 86400)), revenue, a["n"] if a else 0, tips_v)
        rows.append({"p": p, "branch": p.branch, "income": pay["total_income"], "n": a["n"] if a else 0, "tables": a["tables"] if a else 0,
                     "revenue": revenue, "profit": revenue - c, "tips": (a["tips"] or D0) if a else D0,
                     "fees": (a["fee"] or D0) if a else D0, "active": active.get(p.pk, 0),
                     "avg": ((a["total"] or D0) / a["n"]) if a and a["n"] else D0})
    rows.sort(key=lambda r: (-float(r["revenue"]), r["p"].pk))
    top = max([float(r["revenue"]) for r in rows] + [1])
    for i, r in enumerate(rows, 1):
        r["rank"], r["pct"] = i, round(float(r["revenue"]) / top * 100)
    return rows


# ----------------------------------------------------------------------------- menu cost + engineering
CLASS_INFO = {
    "star": ("Star", "Popular and profitable — keep it, promote it, protect quality."),
    "plow": ("Plowhorse", "Popular but low margin — raise price a little or trim portion / cost."),
    "puzzle": ("Puzzle", "Profitable but slow — push it with waiters, combos and better menu placement."),
    "dog": ("Dog", "Slow and low margin — rework the recipe or remove it."),
    "nocost": ("No recipe", "Add a recipe so cost and margin can be calculated."),
}


def recipe_preview(item, branch, base=None):
    f = branch.cost_factor
    lines, total = [], D0
    for r in item.recipe.select_related("ingredient"):
        unit_cost = r.ingredient.unit_cost * f
        c = r.quantity * unit_cost
        total += c
        lines.append({"name": r.ingredient.name, "unit": r.ingredient.unit, "qty": r.quantity.normalize(),
                      "unit_cost": unit_cost, "cost": c})
    for l in lines:
        l["pct"] = round(float(l["cost"] / total * 100)) if total else 0
    return lines, total


def menu_rows(branch, start, end, target=30):
    from . import services as svc
    base = base_costs()
    f = branch.cost_factor
    off = set(BranchItem.objects.filter(branch=branch, is_available=False).values_list("item_id", flat=True))
    sold = {r["item_id"]: r for r in OrderItem.objects.filter(
        order__branch=branch, order__created_at__gte=start, order__created_at__lt=end, is_void=False)
        .exclude(order__status=Order.CANCELLED).values("item_id").annotate(q=Sum("qty"), rev=Sum(LINE_TOTAL))}
    from .models import ItemFeedback
    rated = {r["item_id"]: r for r in ItemFeedback.objects.filter(
        feedback__branch=branch, feedback__created_at__gte=start, feedback__created_at__lt=end, rating__gt=0)
        .values("item_id").annotate(avg=Avg("rating"), n=Count("id"))}
    rows = []
    for it in MenuItem.objects.filter(is_active=True).select_related("category"):
        if it.pk in off:
            continue
        price = Decimal(svc.effective_price(it, branch))
        has_recipe = it.pk in base
        cost = base[it.pk] * f if has_recipe else None
        sr = sold.get(it.pk)
        q, rev = (sr["q"], sr["rev"]) if sr else (0, D0)
        margin = price - cost if cost is not None else None
        sug = None
        if cost:
            raw = cost / (Decimal(target) / 100)
            step = Decimal("5") if branch.currency_code == "PKR" else Decimal("0.5")
            sug = (raw / step).to_integral_value(rounding=ROUND_CEILING) * step
        rows.append({"id": it.pk, "name": it.name, "sku": it.sku, "category": it.category.name if it.category_id else "",
                     "price": price, "cost": cost, "margin": margin,
                     "margin_pct": round(float(margin / price * 100), 1) if margin is not None and price else None,
                     "cost_pct": round(float(cost / price * 100), 1) if cost is not None and price else None,
                     "q": q, "rev": rev, "profit": (rev - cost * q) if cost is not None else None,
                     "suggested": sug, "rating": round(float(rated[it.pk]["avg"]), 1) if it.pk in rated else None,
                     "rating_n": rated[it.pk]["n"] if it.pk in rated else 0, "img": it.image_url})
    # menu engineering (popularity vs margin)
    sellers = [r for r in rows if r["cost"] is not None and r["q"] > 0]
    total_q = sum(r["q"] for r in sellers)
    avg_margin = (sum(r["profit"] for r in sellers) / total_q) if total_q else D0
    pop_cut = (0.7 * total_q / len(sellers)) if sellers else 0
    for r in rows:
        if r["cost"] is None:
            r["klass"] = "nocost"
        else:
            pop = r["q"] >= pop_cut and r["q"] > 0
            good = r["margin"] >= avg_margin
            r["klass"] = "star" if pop and good else "plow" if pop else "puzzle" if good else "dog"
        r["klass_label"] = CLASS_INFO[r["klass"]][0]
    return rows, avg_margin


# ----------------------------------------------------------------------------- forecasting
def _mean(v):
    return sum(v) / len(v) if v else 0.0


def forecast_series(dates, values, horizon=7):
    """Weekday seasonality x linear trend. dates: consecutive ascending dates (training), values: floats.
    Returns (list of {date, value, low, high}, confidence label)."""
    n = len(values)
    if n == 0:
        return [], "None"
    last = dates[-1]
    overall = _mean(values)
    by_wd = defaultdict(list)
    for d, v in zip(dates, values):
        by_wd[d.weekday()].append(v)
    use_season = n >= 14 and overall > 0
    factor = {wd: (_mean(by_wd[wd]) / overall if use_season and by_wd.get(wd) else 1.0) for wd in range(7)}
    m = min(n, 28)
    ys = [v / (factor[d.weekday()] or 1) for d, v in zip(dates[-m:], values[-m:])]
    xs = list(range(m))
    mx, my = _mean(xs), _mean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den and m >= 10 else 0.0
    # damp the trend so a lucky week does not explode the forecast
    slope = max(min(slope, my * 0.03), -my * 0.03) if my else 0.0
    icpt = my - slope * mx
    resid = [y - (icpt + slope * x) for x, y in zip(xs, ys)]
    sd = math.sqrt(sum(r * r for r in resid) / max(1, len(resid) - 2))
    out = []
    for h in range(1, horizon + 1):
        d = last + timedelta(days=h)
        lvl = icpt + slope * (m - 1 + h)
        v = max(0.0, lvl * factor[d.weekday()])
        band = 1.28 * sd * factor[d.weekday()]
        out.append({"date": d, "value": v, "low": max(0.0, v - band), "high": v + band})
    cv = sd / my if my else 1
    conf = "High" if n >= 28 and cv < 0.3 else "Medium" if n >= 14 else "Low"
    return out, conf


def backtest(dates, values, hold=7):
    """Forecast the last `hold` days from the data before them; returns accuracy % (100 - MAPE) or None."""
    if len(values) < 14 + hold:
        return None
    fc, _ = forecast_series(dates[:-hold], values[:-hold], hold)
    errs = [abs(f["value"] - a) / a for f, a in zip(fc, values[-hold:]) if a > 0]
    return round(max(0.0, 100 - _mean(errs) * 100), 1) if errs else None


def daily_series(branch, days=56):
    """Completed sales per local day (today excluded: incomplete). Returns dates, sales(list float), orders(list int)."""
    tz = timezone.get_current_timezone()
    today = timezone.localdate()
    start = _aware(today - timedelta(days=days))
    end = _aware(today)
    rows = {r["d"]: r for r in Order.objects.filter(branch=branch, status=Order.COMPLETED, created_at__gte=start, created_at__lt=end)
            .annotate(d=TruncDate("created_at", tzinfo=tz)).values("d").annotate(n=Count("id"), t=Sum("total"), tax=Sum("tax"))}
    dates = [today - timedelta(days=days - i) for i in range(days)]
    first = next((i for i, d in enumerate(dates) if d in rows), None)     # drop leading days before the first sale
    if first is None:
        return [], [], []
    dates = dates[first:]
    sales = [float((rows[d]["t"] - rows[d]["tax"]) if d in rows else 0) for d in dates]
    orders = [rows[d]["n"] if d in rows else 0 for d in dates]
    return dates, sales, orders


def hour_profile(branch, days=28):
    start = _aware(timezone.localdate() - timedelta(days=days))
    tz = timezone.get_current_timezone()
    counts = [0] * 24
    for dt in Order.objects.filter(branch=branch, created_at__gte=start).exclude(status=Order.CANCELLED).values_list("created_at", flat=True):
        counts[dt.astimezone(tz).hour] += 1
    return counts


def ingredient_forecast(branch, item_units_7d):
    """Expected ingredient need for the next 7 days vs stock on hand."""
    f = branch.cost_factor
    need = defaultdict(Decimal)
    for r in Recipe.objects.filter(item_id__in=item_units_7d.keys()).select_related("ingredient"):
        need[r.ingredient_id] += r.quantity * Decimal(str(item_units_7d[r.item_id]))
    stock = {s.ingredient_id: s for s in Stock.objects.filter(branch=branch).select_related("ingredient", "ingredient__default_supplier")}
    rows = []
    for ing_id, n7 in need.items():
        st = stock.get(ing_id)
        if not st:
            continue
        per_day = n7 / 7
        cover = float(st.quantity / per_day) if per_day > 0 else 99
        short = max(n7 - st.quantity, D0)
        status = "order" if cover < 2 else "soon" if cover < 4 else "ok"
        rows.append({"id": ing_id, "supplier": st.ingredient.default_supplier_id, "cost": st.ingredient.unit_cost,
                     "name": st.ingredient.name, "unit": st.ingredient.unit, "need": n7, "stock": st.quantity,
                     "per_day": per_day, "cover": cover, "short": short, "status": status,
                     "buy_cost": short * st.ingredient.unit_cost * f})
    rows.sort(key=lambda r: r["cover"])
    return rows


def build_ai(branch, target=30):
    """Everything the AI Insights screen shows."""
    from . import services as svc
    today = timezone.localdate()
    dates, sales, orders = daily_series(branch)
    fc_sales, conf = forecast_series(dates, sales, 7) if dates else ([], "None")
    fc_orders, _ = forecast_series(dates, [float(o) for o in orders], 7) if dates else ([], "None")
    acc = backtest(dates, sales) if dates else None

    # chart: last 14 days actual + 7 forecast
    hist = list(zip(dates, sales))[-14:]
    mx = max([v for _, v in hist] + [f["high"] for f in fc_sales] + [1])
    bars = [{"label": d.strftime("%d %b"), "value": round(v), "pct": round(v / mx * 100, 1), "fc": False} for d, v in hist]
    bars += [{"label": f["date"].strftime("%a %d"), "value": round(f["value"]), "pct": round(f["value"] / mx * 100, 1), "fc": True,
              "low": round(f["low"]), "high": round(f["high"])} for f in fc_sales]

    # item demand next 7 days
    start28, end = _aware(today - timedelta(days=28)), _aware(today)
    sold = list(OrderItem.objects.filter(order__branch=branch, order__status=Order.COMPLETED, order__created_at__gte=start28,
                                         order__created_at__lt=end, is_void=False, item__isnull=False)
                .values("item_id", "name").annotate(q=Sum("qty")).order_by("-q"))
    total_q = sum(r["q"] for r in sold)
    n_orders = sum(orders[-28:]) or 1
    exp_orders7 = sum(f["value"] for f in fc_orders)
    exp_units7 = exp_orders7 * (total_q / n_orders) if total_q else 0
    item_units = {r["item_id"]: (r["q"] / total_q * exp_units7 if total_q else 0) for r in sold}
    demand = [{"name": r["name"], "last28": r["q"], "next7": round(item_units[r["item_id"]]),
               "daily": round(item_units[r["item_id"]] / 7, 1)} for r in sold[:12]]
    ing = ingredient_forecast(branch, item_units)

    hours = hour_profile(branch)
    hmx = max(hours + [1])
    hour_bars = [{"h": h, "label": f"{h % 12 or 12}{'a' if h < 12 else 'p'}", "n": n, "pct": round(n / hmx * 100)} for h, n in enumerate(hours)
                 if 10 <= h <= 23]

    rows, avg_margin = menu_rows(branch, start28, _aware(today + timedelta(days=1)), target)
    insights = _insights(branch, dates, sales, orders, fc_sales, fc_orders, conf, hours, rows, ing, today)
    from . import feedback as fbk
    from .models import Feedback
    fa = fbk.analyze(Feedback.objects.filter(branch=branch), start28, _aware(today + timedelta(days=1)))
    insights += [dict(i, title="Feedback · " + i["title"]) for i in fa["insights"][:6]]
    return {"conf": conf, "accuracy": acc, "bars": bars, "fc": fc_sales, "fc_orders": fc_orders, "demand": demand, "ing": ing,
            "hours": hour_bars, "menu": rows, "insights": insights, "days_of_data": len(dates),
            "week_total": sum(f["value"] for f in fc_sales), "week_low": sum(f["low"] for f in fc_sales),
            "week_high": sum(f["high"] for f in fc_sales), "week_orders": round(exp_orders7),
            "tomorrow": fc_sales[0] if fc_sales else None}


def _insights(branch, dates, sales, orders, fc, fc_orders, conf, hours, rows, ing, today):
    cur = branch.currency_symbol
    fmt = lambda v: f"{cur} {v:,.0f}"
    out = []
    add = lambda lvl, title, text: out.append({"level": lvl, "title": title, "text": text})
    if len(sales) >= 14:
        a, b = sum(sales[-7:]), sum(sales[-14:-7])
        if b > 0:
            ch = (a - b) / b * 100
            add("good" if ch >= 3 else "bad" if ch <= -3 else "info", "Weekly trend",
                f"Last 7 days sales {fmt(a)} — {'up' if ch >= 0 else 'down'} {abs(ch):.1f}% vs the 7 days before ({fmt(b)}).")
    if fc:
        best = max(fc, key=lambda f: f["value"])
        add("info", "Forecast", f"Next 7 days expected sales ≈ {fmt(sum(f['value'] for f in fc))} "
            f"(~{round(sum(f['value'] for f in fc_orders))} orders). Busiest day ahead: {best['date']:%A} ≈ {fmt(best['value'])}. Confidence: {conf}.")
    if len(dates) >= 14:
        wd = defaultdict(list)
        for d, v in zip(dates, sales):
            wd[d.weekday()].append(v)
        avgs = {k: _mean(v) for k, v in wd.items()}
        hi, lo = max(avgs, key=avgs.get), min(avgs, key=avgs.get)
        names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        add("info", "Best / slowest weekday", f"{names[hi]} is your best day (avg {fmt(avgs[hi])}); {names[lo]} is the slowest "
            f"(avg {fmt(avgs[lo])}). Plan staff and prep accordingly, run offers on {names[lo]}.")
    if sum(hours):
        top = sorted(range(24), key=lambda h: -hours[h])[:3]
        lab = lambda h: f"{h % 12 or 12}{'am' if h < 12 else 'pm'}"
        add("info", "Peak hours", "Busiest hours: " + ", ".join(f"{lab(h)}–{lab((h + 1) % 24)}" for h in sorted(top)) +
            ". Make sure all chefs and waiters are on the floor then.")
    for r in ing:
        if r["status"] == "order":
            add("bad", f"Stock will run out: {r['name']}", f"Only {r['stock'].normalize():f} {r['unit']} left, expected use "
                f"{r['per_day']:.1f} {r['unit']}/day → about {r['cover']:.1f} days of cover. Order ≈ {r['short']:.1f} {r['unit']} now.")
    soon = [r["name"] for r in ing if r["status"] == "soon"]
    if soon:
        add("warn", "Reorder soon", "Within ~4 days: " + ", ".join(soon[:6]) + ".")
    stars = [r["name"] for r in rows if r["klass"] == "star"][:4]
    if stars:
        add("good", "Menu stars", ", ".join(stars) + " — popular and profitable. Keep stock and quality consistent.")
    for r in [r for r in rows if r["klass"] == "plow"][:3]:
        add("warn", f"Low margin bestseller: {r['name']}", f"Sold {r['q']} in 28 days but food cost is {r['cost_pct']}% of price. "
            f"Raising the price to ≈ {fmt(r['suggested'])} would bring cost to 30%.")
    for r in [r for r in rows if r["klass"] == "dog"][:2]:
        add("warn", f"Weak item: {r['name']}", f"Sold only {r['q']} in 28 days with {r['margin_pct']}% margin — rework or remove.")
    high = [r for r in rows if r["cost_pct"] and r["cost_pct"] > 40]
    if high:
        add("warn", "High food cost", ", ".join(f"{r['name']} ({r['cost_pct']}%)" for r in high[:4]) + " are above 40% food cost.")
    nocost = [r["name"] for r in rows if r["klass"] == "nocost"]
    if nocost:
        add("info", "Recipes missing", f"{len(nocost)} item(s) have no recipe so cost can't be calculated: " + ", ".join(nocost[:5]) + ".")
    # void / cancel rate
    start = _aware(today - timedelta(days=28))
    n_all = Order.objects.filter(branch=branch, created_at__gte=start).count()
    n_can = Order.objects.filter(branch=branch, created_at__gte=start, status=Order.CANCELLED).count()
    if n_all and n_can / n_all > 0.05:
        add("bad", "High cancellations", f"{n_can} of {n_all} orders ({n_can / n_all * 100:.1f}%) were cancelled in 28 days — check Voids & Cancels for patterns.")
    # staff + channels
    done = Order.objects.filter(branch=branch, status=Order.COMPLETED, created_at__gte=start)
    w = (done.filter(waiter__isnull=False).values("waiter__first_name", "waiter__username")
         .annotate(n=Count("id"), t=Sum("total"), tips=Sum("tip")).order_by("-t"))
    w = list(w)
    if len(w) >= 2:
        add("info", "Top waiter (28 days)", f"{w[0]['waiter__first_name'] or w[0]['waiter__username']} brought {fmt(w[0]['t'])} from {w[0]['n']} tables "
            f"and earned {fmt(w[0]['tips'] or 0)} in tips.")
    ch = list(done.values("source__name", "source__commission_percent").annotate(t=Sum("total")).order_by("-t"))
    comm = sum(float(c["t"]) * float(c["source__commission_percent"] or 0) / 100 for c in ch if c["source__name"])
    tot = sum(float(c["t"]) for c in ch)
    if comm and tot:
        add("warn", "Delivery app commission", f"Aggregator commission cost ≈ {fmt(comm)} ({comm / tot * 100:.1f}% of sales) in 28 days. "
            "Push your own website / WhatsApp ordering to keep more margin.")
    return out


# ----------------------------------------------------------------------------- optional LLM narrative
def llm_summary_payload(branch, ai):
    return {"branch": branch.name, "currency": branch.currency_code, "forecast_next_7_days_sales": round(ai["week_total"]),
            "forecast_orders": ai["week_orders"], "confidence": ai["conf"], "backtest_accuracy_pct": ai["accuracy"],
            "insights": [f"{i['title']}: {i['text']}" for i in ai["insights"]],
            "menu": [{"name": r["name"], "price": float(r["price"]), "cost": float(r["cost"]) if r["cost"] is not None else None,
                      "sold_28d": r["q"], "class": r["klass_label"]} for r in ai["menu"]][:25],
            "stock_cover": [{"item": r["name"], "days": round(r["cover"], 1)} for r in ai["ing"][:10]]}


def ask_claude(payload, language="English", task=None):
    """Optional: send the numbers (no customer data) to the Anthropic API. Needs ANTHROPIC_API_KEY in the environment."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None, "ANTHROPIC_API_KEY is not set on the server. Add it to the environment to enable the AI advisor (the built-in forecasts and insights work without it)."
    body = {"model": os.environ.get("RESTOPRO_AI_MODEL", "claude-sonnet-5-5"), "max_tokens": 900,
            "messages": [{"role": "user", "content": (
                (task or "Using only these numbers, give 6 short, concrete, prioritised actions for the owner (pricing, menu, staffing, "
                          "purchasing, promotions).") + f" You are a restaurant business consultant. Reply in {language}. Data:\n"
                + json.dumps(payload, default=str))}]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(), method="POST",
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            data = json.load(r)
        return "".join(b.get("text", "") for b in data.get("content", [])), None
    except Exception as e:      # network / key / quota problems must never break the page
        return None, f"AI request failed: {e}"
