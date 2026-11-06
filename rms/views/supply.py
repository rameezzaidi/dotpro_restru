from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .. import services as svc
from ..models import (Branch, BranchItem, Ingredient, MenuItem, PurchaseLine, PurchaseOrder, Recipe, Stock,
                      StockMovement, StockTransfer, Supplier)
from ..permissions import master_required, role_required, scope

SUP_ROLES = ("manager", "storekeeper")


def _dec(v, default=None):
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return default


# ------------------------------------------------------------------ stock
@role_required("manager", "storekeeper", "kitchen", "kds")
def stock(request):
    q = request.GET.get("q", "").strip()
    low_only = request.GET.get("low") == "1"
    ings = Ingredient.objects.all()
    if q:
        ings = ings.filter(name__icontains=q)
    if request.branch_all:
        branches = list(Branch.objects.filter(is_active=True))
        data = {(s.branch_id, s.ingredient_id): s for s in Stock.objects.filter(branch__in=branches)}
        rows = []
        for ing in ings:
            cells = [data.get((b.pk, ing.pk)) for b in branches]
            if low_only and not any(c and c.is_low for c in cells):
                continue
            rows.append({"ing": ing, "cells": cells})
        return render(request, "rms/stock_matrix.html", {"rows": rows, "branches": branches, "q": q, "low": low_only})
    b = request.branch
    data = {s.ingredient_id: s for s in Stock.objects.filter(branch=b)}
    rows = []
    for ing in ings:
        s = data.get(ing.pk)
        if low_only and not (s and s.is_low):
            continue
        rows.append({"ing": ing, "stock": s})
    return render(request, "rms/stock.html", {"rows": rows, "q": q, "low": low_only})


@role_required(*SUP_ROLES)
@require_POST
def stock_adjust(request, ingredient_id):
    if not request.branch:
        messages.error(request, "Select a branch first.")
        return redirect("stock")
    ing = get_object_or_404(Ingredient, pk=ingredient_id)
    delta = _dec(request.POST.get("delta"))
    reason = request.POST.get("reason", "adjustment")
    if delta is None or delta == 0:
        messages.error(request, "Enter a quantity (use negative to remove).")
        return redirect("stock")
    if reason == "wastage":
        delta = -abs(delta)
    svc.adjust_stock(request.branch, ing, delta, reason if reason in ("adjustment", "wastage", "opening") else "adjustment",
                     note=request.POST.get("note", "")[:200], user=request.user)
    messages.success(request, f"{ing.name}: stock updated.")
    return redirect("stock")


@role_required(*SUP_ROLES)
@require_POST
def stock_reorder(request, ingredient_id):
    if not request.branch:
        return redirect("stock")
    ing = get_object_or_404(Ingredient, pk=ingredient_id)
    lvl = _dec(request.POST.get("reorder_level"), Decimal(0))
    s, _ = Stock.objects.get_or_create(branch=request.branch, ingredient=ing)
    s.reorder_level = max(lvl, Decimal(0))
    s.save(update_fields=["reorder_level"])
    messages.success(request, "Reorder level saved.")
    return redirect("stock")


@role_required(*SUP_ROLES, "kitchen", "kds")
def stock_movements(request):
    qs = scope(StockMovement.objects.select_related("branch", "ingredient", "user"), request)
    return render(request, "rms/movements.html", {"moves": qs[:200]})


# ------------------------------------------------------------------ purchase orders
@role_required(*SUP_ROLES)
def purchase_orders(request):
    qs = scope(PurchaseOrder.objects.select_related("branch", "supplier").prefetch_related("lines"), request)
    return render(request, "rms/po_list.html", {"pos": qs[:200]})


@role_required(*SUP_ROLES)
def po_create(request):
    branches = Branch.objects.filter(is_active=True) if request.user.can_access_all else Branch.objects.filter(pk=request.user.branch_id)
    if request.method == "POST":
        branch = branches.filter(pk=request.POST.get("branch") or 0).first()
        supplier = Supplier.objects.filter(pk=request.POST.get("supplier") or 0).first()
        ings = request.POST.getlist("ingredient")
        qtys = request.POST.getlist("qty")
        costs = request.POST.getlist("cost")
        lines = []
        for i, qty, cost in zip(ings, qtys, costs):
            ing = Ingredient.objects.filter(pk=i or 0).first()
            qd = _dec(qty)
            if ing and qd and qd > 0:
                lines.append((ing, qd, _dec(cost, Decimal(0)) or Decimal(0)))
        if not (branch and supplier and lines):
            messages.error(request, "Choose branch, supplier and at least one ingredient line.")
        else:
            po = PurchaseOrder.objects.create(number=svc.next_po_number(branch), branch=branch, supplier=supplier,
                                              notes=request.POST.get("notes", "")[:255], created_by=request.user,
                                              status=PurchaseOrder.ORDERED)
            for ing, qd, cd in lines:
                PurchaseLine.objects.create(po=po, ingredient=ing, qty=qd, unit_cost=cd)
            messages.success(request, f"{po.number} created.")
            return redirect("po_detail", pk=po.pk)
    return render(request, "rms/po_form.html", {
        "branches": branches, "suppliers": Supplier.objects.filter(is_active=True),
        "ingredients": Ingredient.objects.all(), "default_branch": request.branch})


@role_required(*SUP_ROLES)
def po_detail(request, pk):
    po = get_object_or_404(scope(PurchaseOrder.objects.select_related("branch", "supplier"), request), pk=pk)
    return render(request, "rms/po_detail.html", {"po": po, "lines": po.lines.select_related("ingredient")})


@role_required(*SUP_ROLES)
@require_POST
def po_action(request, pk):
    po = get_object_or_404(scope(PurchaseOrder.objects, request), pk=pk)
    action = request.POST.get("action")
    try:
        if action == "send" and po.status == PurchaseOrder.DRAFT:
            po.status = PurchaseOrder.ORDERED
            po.save(update_fields=["status"])
            from ..automation import on_po_sent
            on_po_sent(po)
            messages.success(request, f"{po.number} approved and marked as ordered from {po.supplier.name}.")
        elif action == "receive":
            qty = None
            if any(k.startswith("recv_") for k in request.POST):
                qty = {int(k[5:]): _dec(v, Decimal(0)) for k, v in request.POST.items() if k.startswith("recv_") and k[5:].isdigit()}
            svc.receive_purchase_order(po, request.user, qty, request.POST.get("invoice_no", ""))
            po.refresh_from_db()
            messages.success(request, "Goods received — stock updated." if po.status == PurchaseOrder.RECEIVED
                             else "Partial delivery received — the rest is still pending.")
        elif action == "cancel" and po.status in (PurchaseOrder.DRAFT, PurchaseOrder.ORDERED):
            po.status = PurchaseOrder.CANCELLED
            po.save(update_fields=["status"])
        elif action == "cancel":
            messages.error(request, "A PO with received goods cannot be cancelled.")
    except svc.OrderError as e:
        messages.error(request, str(e))
    return redirect("po_detail", pk=po.pk)


@role_required(*SUP_ROLES)
@require_POST
def po_from_low(request):
    """One click: draft purchase orders (one per supplier) for everything at/below its reorder level."""
    if not request.branch:
        messages.error(request, "Select a branch first.")
        return redirect("stock")
    made, skipped = svc.create_reorder_pos(request.branch, request.user)
    if made:
        messages.success(request, "Draft purchase orders created: " + ", ".join(p.number for p in made) +
                         ". Review them, then approve & send.")
    else:
        messages.info(request, "Nothing to order — no low-stock ingredient without an open purchase order.")
    if skipped:
        messages.warning(request, "No default supplier set for: " + ", ".join(skipped))
    return redirect("purchase_orders" if made else "stock")


# ------------------------------------------------------------------ transfers
@role_required(*SUP_ROLES)
def transfers(request):
    from django.db.models import Q
    qs = StockTransfer.objects.select_related("from_branch", "to_branch", "ingredient", "created_by")
    if not request.branch_all:
        qs = qs.filter(Q(from_branch=request.branch) | Q(to_branch=request.branch))
    all_b = Branch.objects.filter(is_active=True)
    from_b = all_b if request.user.can_access_all else all_b.filter(pk=request.user.branch_id)
    if request.method == "POST":
        try:
            src = from_b.get(pk=request.POST.get("from_branch"))
            dst = all_b.get(pk=request.POST.get("to_branch"))
            ing = Ingredient.objects.get(pk=request.POST.get("ingredient"))
            qty = _dec(request.POST.get("quantity"), Decimal(0))
            svc.transfer_stock(src, dst, ing, qty, request.POST.get("note", "")[:200], request.user)
            messages.success(request, f"Transferred {qty} {ing.unit} of {ing.name}: {src.code} → {dst.code}")
            return redirect("transfers")
        except (Branch.DoesNotExist, Ingredient.DoesNotExist):
            messages.error(request, "Pick valid branches and ingredient.")
        except svc.OrderError as e:
            messages.error(request, str(e))
    return render(request, "rms/transfers.html", {
        "transfers": qs[:100], "from_branches": from_b, "to_branches": all_b, "ingredients": Ingredient.objects.all(),
        "default_from": request.branch})


# ------------------------------------------------------------------ recipes & branch menu
@master_required
def recipe(request, pk):
    """Recipe editor: ingredients per 1 portion of a dish, with live cost / margin and what each branch can still make."""
    item = get_object_or_404(MenuItem.objects.select_related("category"), pk=pk)
    if request.method == "POST":
        ings = request.POST.getlist("ingredient")
        qtys = request.POST.getlist("qty")
        Recipe.objects.filter(item=item).delete()
        seen = set()
        for i, q in zip(ings, qtys):
            ing = Ingredient.objects.filter(pk=i or 0).first()
            qd = _dec(q)
            if ing and qd and qd > 0 and ing.pk not in seen:
                Recipe.objects.create(item=item, ingredient=ing, quantity=qd)
                seen.add(ing.pk)
        messages.success(request, "Recipe saved. These ingredients are checked and deducted from stock whenever this dish is ordered.")
        return redirect("recipes")
    src = item
    copy_id = request.GET.get("copy")
    if copy_id:
        src = MenuItem.objects.filter(pk=copy_id).first() or item
    lines = list(Recipe.objects.filter(item=src).select_related("ingredient"))
    ingredients = list(Ingredient.objects.all())
    avail = []
    for b in Branch.objects.filter(is_active=True):
        d = svc.stock_info(b).get(item.pk)
        avail.append({"branch": b, "max": d["max"] if d else None, "limiting": d["limiting"] if d else "",
                      "low": d["low"] if d else []})
    return render(request, "rms/recipe.html", {
        "item": item, "lines": lines, "ingredients": ingredients, "avail": avail, "copied": bool(copy_id and src != item),
        "src": src, "others": MenuItem.objects.exclude(pk=item.pk).filter(recipe__isnull=False).distinct().order_by("name"),
        "cats": Ingredient.CATEGORIES,
        "ing_json": {i.pk: {"unit": i.unit, "cost": float(i.unit_cost)} for i in ingredients}})


@role_required("manager")
def branch_menu(request):
    if not request.branch:
        messages.warning(request, "Select a specific branch to manage its menu.")
        return redirect("dashboard")
    b = request.branch
    if request.method == "POST":
        for it in MenuItem.objects.all():
            avail = request.POST.get(f"avail_{it.pk}") == "on"
            raw = request.POST.get(f"price_{it.pk}", "").strip()
            price = _dec(raw) if raw else None
            bi, _ = BranchItem.objects.get_or_create(branch=b, item=it)
            bi.is_available, bi.price = avail, price
            bi.save()
        messages.success(request, f"Branch menu for {b.name} saved.")
        return redirect("branch_menu")
    ov = {x.item_id: x for x in BranchItem.objects.filter(branch=b)}
    rows = [{"item": it, "ov": ov.get(it.pk)} for it in MenuItem.objects.select_related("category")]
    return render(request, "rms/branch_menu.html", {"rows": rows})
