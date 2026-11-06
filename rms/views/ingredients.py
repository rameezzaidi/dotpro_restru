"""Ingredient module: stock-aware ingredient list, ingredient detail, recipes overview."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .. import services as svc
from ..models import Branch, Ingredient, MenuItem, Recipe, Stock, StockMovement, D0
from ..permissions import role_required


def _dec(v, default=None):
    try:
        return Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        return default


def _branches(request):
    qs = Branch.objects.filter(is_active=True)
    if request.branch_all:
        return qs
    return qs.filter(pk=request.branch.pk)


def _status(qty, level):
    if qty is None:
        return "none"
    if qty <= 0:
        return "out"
    if level and qty <= level:
        return "low"
    return "ok"


@role_required("manager", "storekeeper")
def ingredient_list(request):
    q = request.GET.get("q", "").strip()
    cat = request.GET.get("cat", "")
    flt = request.GET.get("show", "")                 # low | out | unused
    ings = Ingredient.objects.select_related("default_supplier").annotate(dishes=Count("recipe", distinct=True))
    if q:
        ings = ings.filter(name__icontains=q)
    if cat:
        ings = ings.filter(category=cat)
    branches = list(_branches(request))
    stocks = {}
    for s in Stock.objects.filter(branch__in=branches):
        stocks.setdefault(s.ingredient_id, []).append(s)
    rows, counts, value = [], {"low": 0, "out": 0, "ok": 0}, D0
    for ing in ings:
        ss = stocks.get(ing.pk, [])
        if request.branch_all:
            qty = sum((s.quantity for s in ss), D0) if ss else None
            lows = [s for s in ss if _status(s.quantity, s.level) in ("low", "out")]
            status = "none" if not ss else ("out" if all(s.quantity <= 0 for s in ss) else ("low" if lows else "ok"))
            level = None
            note = f"{len(lows)} of {len(branches)} branches low" if lows else ""
        else:
            s = ss[0] if ss else None
            qty = s.quantity if s else None
            level = s.level if s else ing.default_reorder_level
            status = _status(qty, level)
            note = ""
            if qty:
                value += max(qty, D0) * ing.unit_cost
        if flt == "low" and status not in ("low", "out"):
            continue
        if flt == "out" and status != "out":
            continue
        if flt == "unused" and ing.dishes:
            continue
        if status in counts:
            counts[status] += 1
        pct = 100 if not level else max(4, min(100, int((qty or 0) / (level * 2) * 100)))
        rows.append({"ing": ing, "qty": qty, "level": level, "status": status, "pct": pct, "note": note})
    return render(request, "rms/ingredients.html", {
        "rows": rows, "q": q, "cat": cat, "show": flt, "categories": Ingredient.CATEGORIES, "counts": counts,
        "value": value, "total": Ingredient.objects.count()})


@role_required("manager", "storekeeper")
def ingredient_detail(request, pk):
    ing = get_object_or_404(Ingredient.objects.select_related("default_supplier"), pk=pk)
    branches = list(_branches(request))
    if request.method == "POST":
        item = MenuItem.objects.filter(pk=request.POST.get("item") or 0).first()
        qty = _dec(request.POST.get("qty"))
        if not request.user.can_access_all:
            messages.error(request, "Only the owner / master-branch manager can change recipes.")
        elif not item or not qty or qty <= 0:
            messages.error(request, "Choose a dish and a quantity per portion.")
        else:
            Recipe.objects.update_or_create(item=item, ingredient=ing, defaults={"quantity": qty})
            messages.success(request, f"{ing.name} added to {item.name}.")
        return redirect("ingredient_detail", pk=ing.pk)
    stocks = {s.branch_id: s for s in Stock.objects.filter(ingredient=ing, branch__in=branches)}
    brows = []
    for b in branches:
        s = stocks.get(b.pk)
        brows.append({"branch": b, "stock": s, "status": _status(s.quantity if s else None, s.level if s else ing.default_reorder_level)})
    cur = request.branch
    cur_stock = stocks.get(cur.pk) if cur else None
    dishes = []
    for r in Recipe.objects.filter(ingredient=ing).select_related("item", "item__category"):
        can = None
        if cur_stock is not None:
            can = int(max(cur_stock.quantity, D0) // r.quantity) if r.quantity else None
        dishes.append({"r": r, "can": can, "cost": r.quantity * ing.unit_cost})
    week = timezone.now() - timedelta(days=7)
    mv = StockMovement.objects.filter(ingredient=ing, branch__in=branches)
    used = -(mv.filter(reason="sale", created_at__gte=week).aggregate(t=Sum("change"))["t"] or D0)
    return render(request, "rms/ingredient_detail.html", {
        "ing": ing, "brows": brows, "dishes": dishes, "used_week": used, "moves": mv.select_related("branch", "user")[:25],
        "all_items": MenuItem.objects.exclude(recipe__ingredient=ing).order_by("name"),
        "can_edit": request.user.can_access_all, "cur": cur})


@role_required("manager")
def recipes_overview(request):
    b = request.branch
    show = request.GET.get("show", "")
    info = svc.stock_info(b) if b else {}
    overrides = {}
    if b:
        from ..models import BranchItem
        overrides = {x.item_id: x for x in BranchItem.objects.filter(branch=b)}
    rows, missing, lowc = [], 0, 0
    for it in MenuItem.objects.select_related("category").prefetch_related("recipe__ingredient").filter(is_active=True):
        n = len(it.recipe.all())
        cost = it.recipe_cost
        ov = overrides.get(it.pk)
        price = ov.price if ov and ov.price is not None else it.price
        d = info.get(it.pk)
        if not n:
            status = "none"; missing += 1
        elif d and d["max"] <= 0:
            status = "out"; lowc += 1
        elif d and d["low"]:
            status = "low"; lowc += 1
        else:
            status = "ok"
        if show == "missing" and status != "none":
            continue
        if show == "low" and status not in ("low", "out"):
            continue
        rows.append({"item": it, "n": n, "cost": cost, "price": price, "status": status,
                     "margin": price - cost if n else None,
                     "fc": (cost / price * 100) if n and price else None,
                     "max": d["max"] if d else None, "limiting": d["limiting"] if d else "",
                     "low": d["low"] if d else []})
    return render(request, "rms/recipes.html", {"rows": rows, "show": show, "missing": missing, "lowc": lowc,
                                                "can_edit": request.user.can_access_all})
