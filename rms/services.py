"""Business logic: pricing, order creation, status flow, stock."""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import F as models_F, Q, Sum, Count
from django.utils import timezone

from .models import (Branch, BranchItem, Channel, DayClose, Order, OrderItem, MenuItem, Payment, PurchaseOrder,
                     Customer, LoyaltyTxn, Recipe, Rider, Setting, Shift, Stock, StockMovement, StockTransfer, Table, VoidLog, D0)

TWO = Decimal("0.01")


def q2(v):
    return Decimal(v).quantize(TWO, rounding=ROUND_HALF_UP)


# ------------------------------------------------------------------ menu
def stock_info(branch):
    """Per dish: how many portions the branch stock still allows and which ingredients are running low.
    {item_id: {"max": int, "low": [{"name","left","unit","level","out"}], "limiting": name}}"""
    stock = {s.ingredient_id: s for s in Stock.objects.filter(branch=branch).select_related("ingredient")}
    info = {}
    for r in Recipe.objects.filter(quantity__gt=0).select_related("ingredient"):
        st = stock.get(r.ingredient_id)
        have = st.quantity if st else D0
        level = st.level if st else r.ingredient.default_reorder_level
        can = int(max(have, D0) // r.quantity)
        d = info.setdefault(r.item_id, {"max": can, "low": [], "limiting": r.ingredient.name})
        if can < d["max"]:
            d["max"], d["limiting"] = can, r.ingredient.name
        if have <= 0 or (level and have <= level):
            d["low"].append({"name": r.ingredient.name, "left": float(have), "unit": r.ingredient.unit,
                             "level": float(level or 0), "out": have <= 0})
    return info


def stock_limits(branch):
    return {k: v["max"] for k, v in stock_info(branch).items()}


def branch_menu(branch):
    """List of dicts for POS / website: only active items available in the branch.
    `max_qty` = portions the stock allows (None = no recipe / not limited) so tiles can show 'Sold out';
    `low` = ingredients of this dish that are at / below their alert level (POS shows a warning)."""
    overrides = {b.item_id: b for b in BranchItem.objects.filter(branch=branch)}
    info = stock_info(branch)
    block = Setting.get().block_if_out_of_stock
    from .automation import active_promo
    promo = active_promo(branch)
    out = []
    for it in MenuItem.objects.filter(is_active=True, category__is_active=True).select_related("category").prefetch_related(
            "modifier_groups__options"):
        ov = overrides.get(it.id)
        if ov and not ov.is_available:
            continue
        price = ov.price if ov and ov.price is not None else it.price
        base = price
        if promo:
            price = (price * (100 - promo) / 100).quantize(Decimal("0.01"))
        d = info.get(it.id)
        out.append({"id": it.id, "name": it.name, "sku": it.sku, "price": float(price), "base_price": float(base), "promo": float(promo),
                    "category": it.category_id,
                    "desc": it.description, "veg": it.is_veg, "img": it.image_url,
                    "cat_name": it.category.name, "max_qty": d["max"] if (d and block) else None,
                    "low": d["low"] if d else [], "limiting": d["limiting"] if d else "",
                    "mods": [{"id": g.pk, "name": g.name, "required": g.required, "multi": g.multi,
                              "options": [{"id": o.pk, "name": o.name, "price": float(o.price)} for o in g.options.all() if o.is_active]}
                             for g in sorted(it.modifier_groups.all(), key=lambda g: g.pk)]})
    return out


def resolve_mods(item, option_ids):
    """Check the chosen modifier options for a menu item. Returns (price_delta, text). Raises OrderError when a required
    group is missing, a single-choice group has several picks, or an option does not belong to the item."""
    from .models import ModifierOption
    ids = {int(i) for i in (option_ids or [])}
    groups = list(item.modifier_groups.order_by("id").prefetch_related("options"))
    chosen = list(ModifierOption.objects.filter(pk__in=ids, is_active=True, group__in=groups).select_related("group"))
    if len(chosen) != len(ids):
        raise OrderError(f"Invalid choice for {item.name}")
    delta, parts = D0, []
    for g in groups:
        picks = [o for o in chosen if o.group_id == g.pk]
        if g.required and not picks:
            raise OrderError(f"Choose {g.name} for {item.name}")
        if len(picks) > 1 and not g.multi:
            raise OrderError(f"Only one {g.name} allowed for {item.name}")
        for o in picks:
            delta += o.price
            parts.append(o.name)
    return delta, ", ".join(parts)


def build_line(item, menu_row, raw):
    """One cart line from the browser ({id, qty, notes, mods:[option ids]}) with modifier price + text applied."""
    delta, text = resolve_mods(item, raw.get("mods"))
    note = (raw.get("notes") or "").strip()
    notes = (f"+ {text}" if text else "") + (" · " if text and note else "") + note
    return {"item": item, "name": menu_row["name"], "qty": int(raw["qty"]), "price": Decimal(str(menu_row["price"])) + delta,
            "notes": notes[:160]}


def effective_price(item, branch):
    from .automation import active_promo
    ov = BranchItem.objects.filter(branch=branch, item=item).first()
    price = ov.price if ov and ov.price is not None else item.price
    promo = active_promo(branch)
    return (price * (100 - promo) / 100).quantize(Decimal("0.01")) if promo else price


# ------------------------------------------------------------------ orders
def next_order_number(branch):
    today = timezone.localdate()
    prefix = f"{branch.code}-{today:%y%m%d}-"
    n = Order.objects.filter(number__startswith=prefix).count() + 1
    while Order.objects.filter(number=f"{prefix}{n:04d}").exists():
        n += 1
    return f"{prefix}{n:04d}"


def recalc(order):
    b = order.branch
    sub = sum((l.qty * l.price for l in order.lines.all() if not l.is_void), D0)
    disc = min(order.discount or D0, sub)
    base = sub - disc
    svc = q2(base * b.service_charge_percent / 100) if order.order_type == Order.DINE_IN else D0
    tax = q2((base + svc) * b.tax_percent / 100)
    fee = order.delivery_fee if order.order_type == Order.DELIVERY else D0
    order.subtotal, order.discount, order.service_charge, order.tax = q2(sub), q2(disc), svc, tax
    order.delivery_fee = q2(fee)
    order.total = q2(base + svc + tax + fee)
    order.save(update_fields=["subtotal", "discount", "service_charge", "tax", "delivery_fee", "total"])
    return order


class OrderError(Exception):
    pass


def coupon_discount(code, branch, subtotal):
    """Validate a promo code for this branch and subtotal. Returns (coupon, amount) or raises OrderError."""
    from .models import Coupon
    code = (code or "").strip().upper()
    c = Coupon.objects.filter(code=code, is_active=True).first()
    if not c or (c.branch_id and c.branch_id != branch.pk):
        raise OrderError("This promo code is not valid")
    today = timezone.localdate()
    if (c.valid_from and today < c.valid_from) or (c.valid_to and today > c.valid_to):
        raise OrderError("This promo code has expired or is not active yet")
    if c.usage_limit and c.used >= c.usage_limit:
        raise OrderError("This promo code has been fully used")
    if subtotal < c.min_order:
        raise OrderError(f"Minimum order for this code is {c.min_order:g}")
    amt = q2(subtotal * c.value / 100) if c.kind == c.PERCENT else c.value
    if c.kind == c.PERCENT and c.max_discount:
        amt = min(amt, c.max_discount)
    return c, min(q2(amt), subtotal)


ONLINE_CODES = ("website", "whatsapp")


@transaction.atomic
def create_order(*, branch, order_type, items, source=None, table=None, waiter=None, rider=None, user=None,
                 customer_name="", phone="", address="", notes="", discount=D0, external_ref="",
                 paid=False, method="", delivery_fee=None, coupon_code=""):
    """items: list of dicts {item: MenuItem|None, name, qty, price, notes}"""
    if not items:
        raise OrderError("Cart is empty")
    if order_type not in dict(Order.TYPES):
        raise OrderError("Invalid order type")
    if order_type == Order.DINE_IN and not table:
        raise OrderError("Select a table for dine-in")
    if order_type == Order.DELIVERY and not (address and phone):
        raise OrderError("Delivery needs customer phone and address")
    if source is None:
        source = Channel.objects.filter(code="pos").first()
    ensure_day_open(branch)
    order = Order(number=next_order_number(branch), branch=branch, order_type=order_type, source=source,
                  table=table if order_type == Order.DINE_IN else None, waiter=waiter, rider=rider, created_by=user,
                  customer_name=customer_name[:100], customer_phone=phone[:30], customer_address=address[:255],
                  notes=notes[:255], discount=Decimal(discount or 0), external_ref=external_ref[:80],
                  delivery_fee=branch.delivery_fee if delivery_fee is None else Decimal(delivery_fee))
    if source is not None and source.code in ONLINE_CODES and Setting.get().online_manual_accept:
        order.acceptance = "pending"           # waits for Accept / Reject; the kitchen does not see it yet
    if coupon_code:
        sub = sum((Decimal(str(i["price"])) * max(1, int(i["qty"])) for i in items), D0)
        order.coupon, cd = coupon_discount(coupon_code, branch, sub)
        order.discount = min(sub, order.discount + cd)
    check_ingredients(branch, items)       # refuse the order if a recipe ingredient is short
    order.customer = link_customer(customer_name, phone, address)
    order.save()
    for it in items:
        OrderItem.objects.create(order=order, item=it.get("item"), name=it["name"][:120],
                                 qty=max(1, int(it["qty"])), price=Decimal(str(it["price"])),
                                 notes=(it.get("notes") or "")[:160])
    recalc(order)
    deduct_stock_for_order(order, user)    # ingredients leave the stock the moment the order is placed
    if order.table_id:
        Table.objects.filter(pk=order.table_id).update(status=Table.OCCUPIED)
    if paid:
        mark_paid(order, method or "cash", user=user)
    dispatch_kitchen(branch)          # auto-send to a free chef (timer starts) or wait in the queue
    order.refresh_from_db()
    from .automation import on_order_created
    on_order_created(order)
    return order


def mark_paid(order, method="cash", user=None, tendered=None):
    """Collect whatever is still due in one payment (kept for POS 'Pay now', API and 'complete & collect')."""
    method = method if method in dict(Order.METHODS) else "cash"
    if order.due <= 0:
        sync_payment_state(order)
        return order
    add_payment(order, method, order.due, tendered=tendered, user=user)
    return order


def generate_bill(order):
    """Give a completed order its bill (invoice) number: INV-<BRANCH>-000001, sequential per branch. Idempotent."""
    if order.bill_number:
        return order.bill_number
    prefix = f"INV-{order.branch.code}-"
    last = (Order.objects.filter(branch=order.branch, bill_number__startswith=prefix)
            .order_by("-bill_number").values_list("bill_number", flat=True).first())
    n = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    order.bill_number = f"{prefix}{n:06d}"
    order.billed_at = timezone.now()
    order.save(update_fields=["bill_number", "billed_at"])
    return order.bill_number


def _free_table_if_idle(order, cancelled=False):
    """Dine-in table: after a finished meal it goes to CLEANING (staff tap 'Clean'), after a cancel it is free."""
    if order.table_id:
        busy = Order.objects.filter(table_id=order.table_id, status__in=Order.ACTIVE).exclude(pk=order.pk).exists()
        if not busy:
            new = Table.FREE if cancelled else Table.CLEANING
            Table.objects.filter(pk=order.table_id, status__in=(Table.OCCUPIED, Table.BILL)).update(status=new)


@transaction.atomic
def set_status(order, status, user=None, method=""):
    if status not in dict(Order.STATUS):
        raise OrderError("Invalid status")
    if order.status in (Order.COMPLETED, Order.CANCELLED):
        raise OrderError("Order is already closed")
    if status == Order.OUT:
        if order.order_type != Order.DELIVERY:
            raise OrderError("Only delivery orders can go out for delivery")
        if not order.rider_id:
            raise OrderError("Assign a rider first")
        Rider.objects.filter(pk=order.rider_id).update(status=Rider.BUSY)
    if status == Order.CANCELLED and order.status == Order.NEW:
        restore_unstarted_stock(order, user)
    elif status != Order.NEW and status != Order.CANCELLED:
        deduct_stock_for_order(order, user)       # normally already done at order creation (legacy orders only)
    if status == Order.COMPLETED and order.due > 0:
        mark_paid(order, method or "cash", user=user)
    elif status == Order.COMPLETED:
        sync_payment_state(order)
    was_preparing = order.status == Order.PREPARING
    order.status = status
    now = timezone.now()
    if status == Order.PREPARING and not order.started_at:
        order.started_at = now
    if status in (Order.READY, Order.SERVED, Order.OUT, Order.COMPLETED):
        if not order.started_at:
            order.started_at = order.created_at
        if not order.ready_at:
            order.ready_at = now
    order.save(update_fields=["status", "updated_at", "started_at", "ready_at"])
    if was_preparing and status != Order.PREPARING:
        dispatch_kitchen(order.branch)      # a chef slot is free: next queued order moves in automatically
    if status == Order.COMPLETED:
        generate_bill(order)                 # bill is created automatically the moment the order completes
        award_points(order)
    if status in (Order.COMPLETED, Order.CANCELLED):
        _free_table_if_idle(order, cancelled=(status == Order.CANCELLED))
        if order.rider_id and not Order.objects.filter(rider_id=order.rider_id, status=Order.OUT).exclude(pk=order.pk).exists():
            Rider.objects.filter(pk=order.rider_id, status=Rider.BUSY).update(status=Rider.AVAILABLE)
    from .automation import on_status
    on_status(order, status)
    return order


# ------------------------------------------------------------------ kitchen queue
@transaction.atomic
def dispatch_kitchen(branch):
    """Give waiting orders (status NEW, no chef) to the least-loaded chef who has a free slot (capacity per branch,
    default 3). FIFO. If the branch has no chef accounts the orders simply start immediately."""
    from .models import User
    waiting = list(Order.objects.filter(branch=branch, status=Order.NEW, chef__isnull=True).exclude(acceptance="pending").order_by("created_at", "id"))
    if not waiting:
        return 0
    chefs = list(User.objects.filter(branch=branch, role=User.KITCHEN, is_active=True))
    started = 0
    if not chefs:
        for o in waiting:
            set_status(o, Order.PREPARING)
            started += 1
        return started
    cap = max(1, branch.kitchen_capacity)
    loads = {c.pk: Order.objects.filter(chef=c, status=Order.PREPARING).count() for c in chefs}
    for o in waiting:
        free = [c for c in chefs if loads[c.pk] < cap]
        if not free:
            break
        chef = min(free, key=lambda c: (loads[c.pk], c.pk))
        o.chef = chef
        o.save(update_fields=["chef"])
        set_status(o, Order.PREPARING)
        loads[chef.pk] += 1
        started += 1
    return started


def queue_position(order):
    """1-based place in the kitchen queue (None if not waiting)."""
    if order.status != Order.NEW or order.chef_id or order.acceptance == "pending":
        return None
    return Order.objects.filter(branch=order.branch, status=Order.NEW, chef__isnull=True,
                                created_at__lt=order.created_at).exclude(acceptance="pending").count() + 1


# ------------------------------------------------------------------ stock
def adjust_stock(branch, ingredient, delta, reason, ref="", note="", user=None):
    stock, _ = Stock.objects.select_for_update().get_or_create(branch=branch, ingredient=ingredient)
    stock.quantity = stock.quantity + Decimal(delta)
    stock.save(update_fields=["quantity"])
    StockMovement.objects.create(branch=branch, ingredient=ingredient, change=Decimal(delta), reason=reason,
                                 ref=ref, note=note, user=user)
    return stock


def _needs(pairs):
    """pairs: iterable of (item_id, qty) -> {ingredient: total quantity needed}"""
    need = {}
    for item_id, qty in pairs:
        if not item_id:
            continue
        for r in Recipe.objects.filter(item_id=item_id).select_related("ingredient"):
            need[r.ingredient] = need.get(r.ingredient, D0) + r.quantity * int(qty)
    return need


def check_ingredients(branch, items):
    """Raise OrderError naming the short ingredient(s) if the branch cannot make the cart (when the setting is on)."""
    from .models import Setting
    if not Setting.get().block_if_out_of_stock:
        return
    need = _needs((it["item"].pk if it.get("item") else None, it["qty"]) for it in items)
    short = []
    for ing, qty in need.items():
        have = Stock.objects.filter(branch=branch, ingredient=ing).values_list("quantity", flat=True).first() or D0
        if have < qty:
            short.append(f"{ing.name} (need {qty.normalize():f} {ing.unit}, have {have.normalize():f})")
    if short:
        raise OrderError("Not enough stock: " + ", ".join(short))


def deduct_stock_for_order(order, user=None):
    """Remove the recipe ingredients of every not-yet-deducted line from the branch stock (idempotent per line)."""
    lines = [l for l in order.lines.all() if not l.is_void and not l.deducted]
    need = _needs((l.item_id, l.qty) for l in lines)
    for ing, qty in need.items():
        adjust_stock(order.branch, ing, -qty, "sale", ref=order.number, user=user)
    if lines:
        OrderItem.objects.filter(pk__in=[l.pk for l in lines]).update(deducted=True)
    if not order.stock_deducted:
        order.stock_deducted = True
        order.save(update_fields=["stock_deducted"])


def restore_lines_stock(order, lines, user=None, note="Cancelled"):
    """Put the ingredients of these (deducted) lines back into stock."""
    lines = [l for l in lines if l.deducted]
    need = _needs((l.item_id, l.qty) for l in lines)
    for ing, qty in need.items():
        adjust_stock(order.branch, ing, qty, "cancel_return", ref=order.number, note=note, user=user)
    if lines:
        OrderItem.objects.filter(pk__in=[l.pk for l in lines]).update(deducted=False)


def restore_unstarted_stock(order, user=None):
    """Order cancelled while still waiting for the kitchen: return the ingredients of the round not yet cooked."""
    lines = [l for l in order.lines.all() if not l.is_void and l.round == order.kot_round]
    restore_lines_stock(order, lines, user, "Order cancelled")


@transaction.atomic
def receive_purchase_order(po, user=None, quantities=None, invoice_no=""):
    """Goods receiving (GRN). quantities = {line_id: qty received now}; None = everything still pending.
    Partial deliveries are allowed: the PO stays 'Partially received' until every line is complete."""
    if po.status == PurchaseOrder.RECEIVED:
        raise OrderError("Already received")
    if po.status == PurchaseOrder.CANCELLED:
        raise OrderError("Cancelled PO cannot be received")
    got_any = False
    for l in po.lines.select_related("ingredient"):
        qty = l.pending_qty if quantities is None else Decimal(str(quantities.get(l.pk, 0) or 0))
        if qty < 0:
            raise OrderError("Quantity cannot be negative")
        if qty > l.pending_qty:
            raise OrderError(f"{l.ingredient.name}: only {l.pending_qty.normalize():f} {l.ingredient.unit} pending")
        if qty == 0:
            continue
        got_any = True
        adjust_stock(po.branch, l.ingredient, qty, "purchase", ref=po.number, user=user)
        l.received_qty += qty
        l.save(update_fields=["received_qty"])
        if l.unit_cost:
            l.ingredient.unit_cost = l.unit_cost
            l.ingredient.save(update_fields=["unit_cost"])
    if not got_any:
        raise OrderError("Enter the quantity received for at least one line")
    done = all(l.pending_qty <= 0 for l in po.lines.all())
    po.status = PurchaseOrder.RECEIVED if done else PurchaseOrder.PARTIAL
    if invoice_no:
        po.invoice_no = invoice_no[:60]
    if done:
        po.received_at = timezone.now()
    po.save(update_fields=["status", "received_at", "invoice_no"])


def reorder_suggestions(branch):
    """Ingredients at/below their alert level, with a suggested quantity (back up to 2x the level)."""
    out = []
    for st in Stock.objects.filter(branch=branch).select_related("ingredient", "ingredient__default_supplier"):
        lvl = st.level
        if lvl > 0 and st.quantity <= lvl:
            qty = max(lvl * 2 - st.quantity, lvl)
            out.append({"stock": st, "ingredient": st.ingredient, "supplier": st.ingredient.default_supplier,
                        "qty": qty.quantize(Decimal("0.001")), "cost": st.ingredient.unit_cost})
    return out


@transaction.atomic
def create_reorder_pos(branch, user=None):
    """One DRAFT purchase order per supplier from the low-stock list. Ingredients without a default supplier are skipped."""
    from .models import PurchaseLine
    groups, skipped = {}, []
    for r in reorder_suggestions(branch):
        if not r["supplier"]:
            skipped.append(r["ingredient"].name)
            continue
        groups.setdefault(r["supplier"], []).append(r)
    made = []
    for sup, rows in groups.items():
        # don't duplicate: skip ingredients already on an open draft/ordered PO for this supplier
        open_ing = set(PurchaseLine.objects.filter(po__branch=branch, po__supplier=sup,
                                                   po__status__in=(PurchaseOrder.DRAFT, PurchaseOrder.ORDERED, PurchaseOrder.PARTIAL)
                                                   ).values_list("ingredient_id", flat=True))
        rows = [r for r in rows if r["ingredient"].pk not in open_ing]
        if not rows:
            continue
        po = PurchaseOrder.objects.create(number=next_po_number(branch), branch=branch, supplier=sup, created_by=user,
                                          status=PurchaseOrder.DRAFT, notes="Auto-created from low stock")
        for r in rows:
            PurchaseLine.objects.create(po=po, ingredient=r["ingredient"], qty=r["qty"], unit_cost=r["cost"])
        made.append(po)
    return made, skipped


def create_pos_from_lines(branch, user, lines):
    """lines: [(Ingredient, qty)] -> one DRAFT PO per default supplier. Skips ingredients already on an open PO or without
    a supplier. Returns (made_pos, skipped_names)."""
    from .models import PurchaseLine
    groups, skipped = {}, []
    for ing, qty in lines:
        if not ing.default_supplier_id:
            skipped.append(ing.name)
            continue
        groups.setdefault(ing.default_supplier, []).append((ing, qty))
    made = []
    for sup, rows in groups.items():
        open_ing = set(PurchaseLine.objects.filter(po__branch=branch, po__supplier=sup, po__status__in=(
            PurchaseOrder.DRAFT, PurchaseOrder.ORDERED, PurchaseOrder.PARTIAL)).values_list("ingredient_id", flat=True))
        rows = [(i, q) for i, q in rows if i.pk not in open_ing and q > 0]
        if not rows:
            continue
        po = PurchaseOrder.objects.create(number=next_po_number(branch), branch=branch, supplier=sup, created_by=user,
                                          status=PurchaseOrder.DRAFT, notes="Smart order (forecast)")
        for i, q in rows:
            PurchaseLine.objects.create(po=po, ingredient=i, qty=q, unit_cost=i.unit_cost)
        made.append(po)
    return made, skipped


@transaction.atomic
def transfer_stock(from_branch, to_branch, ingredient, qty, note="", user=None):
    qty = Decimal(qty)
    if from_branch.pk == to_branch.pk:
        raise OrderError("Source and destination must differ")
    if qty <= 0:
        raise OrderError("Quantity must be positive")
    src = Stock.objects.filter(branch=from_branch, ingredient=ingredient).first()
    if not src or src.quantity < qty:
        raise OrderError("Not enough stock in source branch")
    t = StockTransfer.objects.create(from_branch=from_branch, to_branch=to_branch, ingredient=ingredient,
                                     quantity=qty, note=note, created_by=user)
    ref = f"TR-{t.pk}"
    adjust_stock(from_branch, ingredient, -qty, "transfer_out", ref=ref, note=f"to {to_branch.code}", user=user)
    adjust_stock(to_branch, ingredient, qty, "transfer_in", ref=ref, note=f"from {from_branch.code}", user=user)
    return t


def next_po_number(branch):
    n = PurchaseOrder.objects.filter(branch=branch).count() + 1
    while PurchaseOrder.objects.filter(number=f"PO-{branch.code}-{n:04d}").exists():
        n += 1
    return f"PO-{branch.code}-{n:04d}"



# ================================================================== standard restaurant flow
def effective_role(user):
    return "owner" if (user is None or user.is_superuser) else user.role


def ensure_day_open(branch):
    if DayClose.objects.filter(branch=branch, date=timezone.localdate()).exists():
        raise OrderError("The business day is closed for this branch. A manager must reopen it (Day close page).")


# ---------------------------------------------------------------- payments
def current_shift(branch, user):
    if not user or not getattr(user, "pk", None):
        return None
    return Shift.objects.filter(branch=branch, user=user, closed_at__isnull=True).first()


def sync_payment_state(order):
    """Recompute paid flag / main method / tip from the payment rows (call after any payment or total change)."""
    pays = list(Payment.objects.filter(order=order))
    paid = sum((p.amount for p in pays), D0)
    order.is_paid = paid >= order.total
    methods = {p.method for p in pays if p.amount > 0}
    order.payment_method = "" if not methods else (next(iter(methods)) if len(methods) == 1 else "mixed")
    order.paid_at = (pays[-1].created_at if pays else None) if order.is_paid else None
    order.save(update_fields=["is_paid", "payment_method", "paid_at", "tip", "updated_at"])
    return order


@transaction.atomic
def add_payment(order, method, amount=None, *, tendered=None, tip=0, user=None, note=""):
    """Record one payment. Several payments (cash + card, or one per guest) can settle a single bill.
    Cash: `tendered` is what the customer handed over; change is calculated."""
    order = Order.objects.select_for_update().get(pk=order.pk) if order.pk else order
    if order.status == Order.CANCELLED:
        raise OrderError("Cancelled order cannot be paid")
    if method not in dict(Order.METHODS):
        raise OrderError("Invalid payment method")
    due = order.due
    amount = due if amount in (None, "") else q2(amount)
    if due <= 0:
        raise OrderError("Nothing left to pay")
    if amount <= 0:
        raise OrderError("Amount must be greater than zero")
    if amount > due:
        raise OrderError(f"Amount is more than the due balance ({due})")
    shift = current_shift(order.branch, user)
    if user and not user.is_superuser and user.role == "cashier" and Setting.get().require_shift and not shift:
        raise OrderError("Open your shift (Shifts page) before taking payments")
    tip = q2(tip or 0)
    if tip < 0:
        raise OrderError("Tip cannot be negative")
    if method == "cash":
        tendered = amount if tendered in (None, "") else q2(tendered)
        if tendered < amount:
            raise OrderError("Cash received is less than the amount")
        change = tendered - amount
    else:
        tendered, change = amount, D0
    p = Payment.objects.create(order=order, method=method, amount=amount, tendered=tendered, change=change, tip=tip,
                               shift=shift, user=user if getattr(user, "pk", None) else None, note=note[:120])
    if tip:
        order.tip = (order.tip or D0) + tip
    sync_payment_state(order)
    return p


def refund_payments(order, user=None, amount=None, note="Refund"):
    """Give money back: negative payment rows mirroring the original methods (newest first)."""
    paid = order.paid_total
    left = paid if amount is None else min(q2(amount), paid)
    if left <= 0:
        return D0
    given = D0
    shift = current_shift(order.branch, user)
    for p in sorted([x for x in order.payments.all() if x.amount > 0], key=lambda x: x.created_at, reverse=True):
        if left <= 0:
            break
        take = min(p.amount, left)
        Payment.objects.create(order=order, method=p.method, amount=-take, tendered=D0, change=D0, shift=shift,
                               user=user if getattr(user, "pk", None) else None, note=note[:120])
        left -= take
        given += take
    sync_payment_state(order)
    return given


# ---------------------------------------------------------------- dine-in flow
@transaction.atomic
def add_items(order, items, user=None):
    """Guest orders more: a new KOT round. The order goes back to the kitchen (new items only are highlighted),
    ingredients for the new items are checked + deducted, totals/payment state are recomputed."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status in (Order.COMPLETED, Order.CANCELLED):
        raise OrderError("This order is closed")
    if order.status == Order.OUT:
        raise OrderError("Order is already out for delivery")
    if not items:
        raise OrderError("Nothing to add")
    ensure_day_open(order.branch)
    check_ingredients(order.branch, items)
    new_round = order.status != Order.NEW            # still waiting in the queue -> items join the same ticket
    if new_round:
        order.kot_round += 1
        order.status, order.chef, order.started_at, order.ready_at = Order.NEW, None, None, None
        order.bill_requested_at = None
        order.save(update_fields=["kot_round", "status", "chef", "started_at", "ready_at", "bill_requested_at", "updated_at"])
    for it in items:
        OrderItem.objects.create(order=order, item=it.get("item"), name=it["name"][:120], qty=max(1, int(it["qty"])),
                                 price=Decimal(str(it["price"])), notes=(it.get("notes") or "")[:160], round=order.kot_round)
    recalc(order)
    sync_payment_state(order)
    deduct_stock_for_order(order, user)
    if order.table_id:
        Table.objects.filter(pk=order.table_id, status__in=(Table.BILL, Table.FREE, Table.RESERVED)).update(status=Table.OCCUPIED)
    dispatch_kitchen(order.branch)
    order.refresh_from_db()
    return order


def request_bill(order):
    if order.order_type != Order.DINE_IN:
        raise OrderError("Bill request is for dine-in orders")
    if order.status not in Order.ACTIVE:
        raise OrderError("Order is closed")
    order.bill_requested_at = timezone.now()
    order.save(update_fields=["bill_requested_at"])
    if order.table_id:
        Table.objects.filter(pk=order.table_id, status=Table.OCCUPIED).update(status=Table.BILL)


@transaction.atomic
def move_table(order, table, user=None):
    if order.order_type != Order.DINE_IN or order.status not in Order.ACTIVE:
        raise OrderError("Only an open dine-in order can be moved")
    if table.branch_id != order.branch_id:
        raise OrderError("Table belongs to another branch")
    if table.pk == order.table_id:
        raise OrderError("Order is already on that table")
    if table.status not in (Table.FREE, Table.RESERVED) or Order.objects.filter(table=table, status__in=Order.ACTIVE).exists():
        raise OrderError(f"Table {table.name} is not free")
    old = order.table
    order.table = table
    order.save(update_fields=["table", "updated_at"])
    Table.objects.filter(pk=table.pk).update(status=Table.BILL if order.bill_requested_at else Table.OCCUPIED)
    if old and not Order.objects.filter(table=old, status__in=Order.ACTIVE).exists():
        Table.objects.filter(pk=old.pk).update(status=Table.CLEANING)


# ---------------------------------------------------------------- void / cancel with reason + approval
def _need_reason(reason):
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise OrderError("A reason is required")
    return reason[:200]


@transaction.atomic
def void_line(order, line, reason, user, approver=None):
    """Remove one item from an open order. Not yet cooked -> stock returns. Already cooked -> counts as wastage and
    needs a manager (own role or an approver's credentials). Everything is logged."""
    reason = _need_reason(reason)
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status in (Order.COMPLETED, Order.CANCELLED):
        raise OrderError("Order is closed")
    if line.order_id != order.pk or line.is_void:
        raise OrderError("Item not found on this order")
    role = effective_role(user)
    if role in ("waiter", "kitchen", "kds"):
        raise OrderError("Waiters / chefs cannot void items — ask a cashier or manager")
    cooked = not (order.status == Order.NEW and line.round == order.kot_round)
    approved = None
    if cooked or order.paid_total > 0:
        if role not in ("owner", "manager"):
            if not approver:
                raise OrderError("Manager approval required (the item was already sent to the kitchen or the bill has payments)")
            approved = approver
        else:
            approved = user
    line.is_void, line.void_reason = True, reason
    line.save(update_fields=["is_void", "void_reason"])
    if not cooked:
        restore_lines_stock(order, [line], user, f"Void: {line.name}")
    VoidLog.objects.create(branch=order.branch, order=order, order_number=order.number, kind=VoidLog.ITEM,
                           item_name=line.name, qty=line.qty, amount=line.qty * line.price, reason=reason,
                           after_cooking=cooked, user=user if getattr(user, "pk", None) else None,
                           approved_by=approved if approved is not user else None)
    if not [l for l in order.lines.all() if not l.is_void]:
        return cancel_order(order, f"All items voided ({reason})", user, approver, _logged=True)
    recalc(order)
    over = order.paid_total - order.total
    if over > 0:
        refund_payments(order, user, over, note="Refund for voided item")
    else:
        sync_payment_state(order)
    return order


@transaction.atomic
def cancel_order(order, reason, user, approver=None, _logged=False):
    reason = _need_reason(reason)
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status in (Order.COMPLETED, Order.CANCELLED):
        raise OrderError("Order is already closed")
    role = effective_role(user)
    if role not in ("owner", "manager", "cashier"):
        raise OrderError("Only manager / cashier can cancel")
    cooked = order.status != Order.NEW
    paid = order.paid_total
    approved = None
    if (cooked or paid > 0) and not _logged:
        if role not in ("owner", "manager"):
            if not approver:
                raise OrderError("Manager approval required (order is in the kitchen or already paid)")
            approved = approver
    refunded = D0
    if paid > 0:
        refunded = refund_payments(order, user, note=f"Cancelled: {reason}"[:120])
    order.cancel_reason = reason
    order.save(update_fields=["cancel_reason"])
    set_status(order, Order.CANCELLED, user)
    restore_redeemed(order)
    if not _logged:
        VoidLog.objects.create(branch=order.branch, order=order, order_number=order.number, kind=VoidLog.ORDER,
                               qty=sum(l.qty for l in order.lines.all() if not l.is_void), amount=order.total,
                               reason=reason, after_cooking=cooked, user=user if getattr(user, "pk", None) else None,
                               approved_by=approved)
        if refunded:
            VoidLog.objects.create(branch=order.branch, order=order, order_number=order.number, kind=VoidLog.REFUND,
                                   amount=refunded, reason=reason, user=user if getattr(user, "pk", None) else None,
                                   approved_by=approved)
    return order


# ---------------------------------------------------------------- split bill by items
@transaction.atomic
def split_order(order, qty_map, user=None):
    """Move chosen items (qty_map {line_id: qty}) to a new bill (<number>-S1 ...) on the same table, so each guest pays
    separately. Must be done before any payment is taken."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status in (Order.COMPLETED, Order.CANCELLED):
        raise OrderError("Order is closed")
    if order.payments.exists():
        raise OrderError("Split the bill before taking any payment")
    lines = {l.pk: l for l in order.lines.all() if not l.is_void}
    moves = {}
    for lid, q in qty_map.items():
        l = lines.get(int(lid))
        q = int(q or 0)
        if not l or q <= 0:
            continue
        if q > l.qty:
            raise OrderError(f"{l.name}: only {l.qty} on the bill")
        moves[l.pk] = q
    if not moves:
        raise OrderError("Select the items for the new bill")
    if sum(moves.values()) >= sum(l.qty for l in lines.values()):
        raise OrderError("Leave at least one item on the original bill")
    n = order.splits.count() + 1
    child = Order.objects.create(
        number=f"{order.number}-S{n}", branch=order.branch, order_type=order.order_type, source=order.source,
        table=order.table, waiter=order.waiter, chef=order.chef, started_at=order.started_at, ready_at=order.ready_at,
        created_by=user if getattr(user, "pk", None) else order.created_by, customer_name=order.customer_name,
        customer_phone=order.customer_phone, customer_address=order.customer_address, notes=order.notes,
        status=order.status, stock_deducted=order.stock_deducted, kot_round=order.kot_round, parent=order, customer=order.customer,
        created_at=order.created_at, delivery_fee=D0, bill_requested_at=order.bill_requested_at)
    for lid, q in moves.items():
        l = lines[lid]
        if q == l.qty:
            l.order = child
            l.save(update_fields=["order"])
        else:
            OrderItem.objects.create(order=child, item=l.item, name=l.name, qty=q, price=l.price, notes=l.notes,
                                     round=l.round, deducted=l.deducted)
            l.qty -= q
            l.save(update_fields=["qty"])
    recalc(order)
    recalc(child)
    sync_payment_state(order)
    sync_payment_state(child)
    return child


# ---------------------------------------------------------------- shifts & day close
@transaction.atomic
def open_shift(branch, user, opening_cash):
    if current_shift(branch, user):
        raise OrderError("You already have an open shift in this branch")
    opening = q2(opening_cash or 0)
    if opening < 0:
        raise OrderError("Opening cash cannot be negative")
    return Shift.objects.create(branch=branch, user=user, opening_cash=opening)


def shift_totals(shift):
    pays = list(shift.payments.select_related("order"))
    by_method, tips, cash_in = {}, D0, D0
    for p in pays:
        by_method[p.method] = by_method.get(p.method, D0) + p.amount
        tips += p.tip
        if p.method == "cash":
            cash_in += p.amount + p.tip
    return {"by_method": by_method, "tips": tips, "cash_in": cash_in, "count": len({p.order_id for p in pays}),
            "refunds": sum((-p.amount for p in pays if p.amount < 0), D0),
            "sales": sum((p.amount for p in pays), D0)}


@transaction.atomic
def close_shift(shift, counted_cash, note=""):
    if not shift.is_open:
        raise OrderError("Shift is already closed")
    counted = q2(counted_cash if counted_cash not in (None, "") else -1)
    if counted < 0:
        raise OrderError("Enter the cash you counted in the drawer")
    t = shift_totals(shift)
    shift.expected_cash = shift.opening_cash + t["cash_in"]
    shift.counted_cash = counted
    shift.difference = counted - shift.expected_cash
    shift.closed_at = timezone.now()
    shift.note = note[:255]
    shift.save()
    return shift


def day_bounds(date):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(timezone.datetime.combine(date, timezone.datetime.min.time()), tz)
    return start, start + timedelta(days=1)


def day_summary(branch, date):
    start, end = day_bounds(date)
    done = Order.objects.filter(branch=branch, status=Order.COMPLETED, billed_at__gte=start, billed_at__lt=end)
    agg = done.aggregate(n=Count("id"), total=Sum("total"), tax=Sum("tax"), svc=Sum("service_charge"),
                         disc=Sum("discount"), fee=Sum("delivery_fee"), tips=Sum("tip"))
    by_type = {r["order_type"]: [r["n"], r["t"]] for r in done.values("order_type").annotate(n=Count("id"), t=Sum("total"))}
    pays = Payment.objects.filter(order__branch=branch, created_at__gte=start, created_at__lt=end)
    by_method = {r["method"]: r["t"] for r in pays.values("method").annotate(t=Sum("amount"))}
    voids = VoidLog.objects.filter(branch=branch, created_at__gte=start, created_at__lt=end)
    v = voids.exclude(kind=VoidLog.REFUND).aggregate(n=Count("id"), t=Sum("amount"))
    return {
        "orders": agg["n"] or 0, "sales": agg["total"] or D0, "tax": agg["tax"] or D0, "service": agg["svc"] or D0,
        "discount": agg["disc"] or D0, "delivery_fee": agg["fee"] or D0, "tips": agg["tips"] or D0,
        "by_type": by_type, "by_method": by_method, "void_count": v["n"] or 0, "void_amount": v["t"] or D0,
        "cancelled": Order.objects.filter(branch=branch, status=Order.CANCELLED, updated_at__gte=start, updated_at__lt=end).count(),
        "open_orders": Order.objects.filter(branch=branch, status__in=Order.ACTIVE).count(),
        "open_shifts": Shift.objects.filter(branch=branch, closed_at__isnull=True).count(),
    }


def close_day(branch, date, user):
    if DayClose.objects.filter(branch=branch, date=date).exists():
        raise OrderError("Day is already closed")
    s = day_summary(branch, date)
    if s["open_orders"]:
        raise OrderError(f"{s['open_orders']} order(s) are still open — complete or cancel them first")
    if s["open_shifts"]:
        raise OrderError(f"{s['open_shifts']} cashier shift(s) are still open — close them first")
    clean = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in s.items() if k not in ("by_type", "by_method")}
    clean["by_type"] = {k: [n, str(t or 0)] for k, (n, t) in s["by_type"].items()}
    clean["by_method"] = {k: str(v or 0) for k, v in s["by_method"].items()}
    return DayClose.objects.create(branch=branch, date=date, closed_by=user, summary=clean)


def order_stock_alerts(order):
    """After an order: ingredients of its dishes that are now low / out. Returns readable warning strings."""
    ids = {l.item_id for l in order.lines.all() if l.item_id and not l.is_void}
    need = {r.ingredient_id for r in Recipe.objects.filter(item_id__in=ids)}
    out = []
    for st in Stock.objects.filter(branch=order.branch, ingredient_id__in=need).select_related("ingredient"):
        if st.quantity <= 0:
            out.append(f"{st.ingredient.name} is OUT of stock")
        elif st.level and st.quantity <= st.level:
            out.append(f"{st.ingredient.name} is low: {st.quantity.normalize():f} {st.ingredient.unit} left (alert level {st.level.normalize():f})")
    return out


def low_ingredients(branch):
    """All ingredients at/below alert level in this branch (for the POS banner)."""
    rows = []
    for st in Stock.objects.filter(branch=branch).select_related("ingredient"):
        if st.quantity <= 0 or (st.level and st.quantity <= st.level):
            rows.append(st)
    return sorted(rows, key=lambda s: (s.quantity > 0, s.ingredient.name))


# ---------------------------------------------------------------- customers & loyalty
def norm_phone(phone):
    return "".join(ch for ch in (phone or "") if ch.isdigit())[-10:]      # national number: 0300… / +92 300… match


def link_customer(name, phone, address=""):
    """Find or create the customer from the phone typed on the order (needs 7+ digits)."""
    ph = norm_phone(phone)
    if len(ph) < 7:
        return None
    c, created = Customer.objects.get_or_create(phone=ph, defaults={"name": (name or "")[:100], "address": (address or "")[:255]})
    changed = []
    if not c.name and name:
        c.name = name[:100]
        changed.append("name")
    if address and not c.address:
        c.address = address[:255]
        changed.append("address")
    if changed:
        c.save(update_fields=changed)
    return c


def points_for(order):
    cfg = Setting.get()
    if not cfg.loyalty_enabled:
        return 0
    return int((order.total * cfg.loyalty_points_per_100 / 100).to_integral_value(rounding="ROUND_FLOOR"))


@transaction.atomic
def award_points(order):
    """Visit + spend + points are booked once, when the order completes."""
    c = order.customer
    if not c or order.loyalty_txns.filter(reason__startswith="Earned").exists():
        return 0
    c = Customer.objects.select_for_update().get(pk=c.pk)
    pts = points_for(order)
    c.visits += 1
    c.total_spent += order.total
    c.last_visit = timezone.now()
    if pts:
        c.points += pts
        c.lifetime_points += pts
        LoyaltyTxn.objects.create(customer=c, order=order, points=pts, reason=f"Earned on {order.number}")
    c.save()
    return pts


@transaction.atomic
def redeem_points(order, points, user=None):
    cfg = Setting.get()
    if not cfg.loyalty_enabled:
        raise OrderError("Loyalty is switched off in Settings")
    order = Order.objects.select_for_update().get(pk=order.pk)
    if not order.customer_id:
        raise OrderError("This order has no customer (enter the phone number on the order)")
    if order.status not in Order.ACTIVE:
        raise OrderError("Order is closed")
    if order.paid_total > 0:
        raise OrderError("Redeem points before taking payment")
    c = Customer.objects.select_for_update().get(pk=order.customer_id)
    try:
        pts = int(points)
    except (TypeError, ValueError):
        raise OrderError("Enter the number of points")
    if pts <= 0:
        raise OrderError("Enter the number of points")
    if pts > c.points:
        raise OrderError(f"Customer has only {c.points} points")
    value_each = cfg.loyalty_point_value
    room = order.subtotal - order.discount
    max_pts = int(room / value_each) if value_each > 0 else 0
    pts = min(pts, max_pts)
    if pts <= 0:
        raise OrderError("Nothing left to discount on this bill")
    value = q2(pts * value_each)
    order.discount = order.discount + value
    order.save(update_fields=["discount"])
    recalc(order)
    c.points -= pts
    c.save(update_fields=["points"])
    LoyaltyTxn.objects.create(customer=c, order=order, points=-pts, reason=f"Redeemed on {order.number} (discount {value})")
    return pts, value


def restore_redeemed(order):
    """Cancelled order: points the customer spent on it go back."""
    spent = -sum(t.points for t in order.loyalty_txns.filter(points__lt=0))
    refunded = sum(t.points for t in order.loyalty_txns.filter(reason__startswith="Refund"))
    back = spent - refunded
    if back > 0 and order.customer_id:
        Customer.objects.filter(pk=order.customer_id).update(points=models_F("points") + back)
        LoyaltyTxn.objects.create(customer_id=order.customer_id, order=order, points=back, reason=f"Refund: {order.number} cancelled")


# ---------------------------------------------------------------- notifications (bell icon)
def notify(users, title, body="", url="", kind="info"):
    from .models import Notification
    seen = set()
    rows = []
    for u in users:
        if u and u.pk not in seen and u.is_active:
            seen.add(u.pk)
            rows.append(Notification(user=u, title=title[:120], body=body[:255], url=url, kind=kind))
    Notification.objects.bulk_create(rows)
    return len(rows)


def managers_for_branch(branch):
    """Everyone who handles this branch: its managers, master-branch managers and owners."""
    from django.db.models import Q
    from .models import User
    return list(User.objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(role=User.OWNER) |
        Q(role=User.MANAGER, branch=branch) | Q(role=User.MANAGER, branch__is_master=True)).distinct())


def supply_users(branch):
    """Who handles stock for this branch: its managers/owners plus the branch storekeepers."""
    from .models import User
    return managers_for_branch(branch) + list(User.objects.filter(role=User.STOREKEEPER, branch=branch, is_active=True))


def notify_rider(order):
    """Tell the rider's login (if any) about a delivery assigned to them."""
    r = order.rider
    if r and r.user_id and r.user.is_active:
        notify([r.user], f"New delivery {order.number}", f"{order.customer_name or 'Customer'} - {order.customer_address or order.customer_phone}"[:200], "/deliveries/", "info")


# ------------------------------------------------------------------ online order acceptance
def accept_online(order, user=None, eta=30):
    """Restaurant confirms a website / WhatsApp order: it now reaches the kitchen queue."""
    order = Order.objects.get(pk=order.pk)
    if order.acceptance != "pending" or order.status != Order.NEW:
        raise OrderError("This order is not waiting for acceptance")
    order.acceptance, order.accept_eta = "accepted", max(5, min(int(eta or 30), 240))
    order.save(update_fields=["acceptance", "accept_eta"])
    dispatch_kitchen(order.branch)
    order.refresh_from_db()
    from .automation import on_online_decision
    on_online_decision(order, True)
    return order


def reject_online(order, user, reason):
    order = Order.objects.get(pk=order.pk)
    if order.acceptance != "pending" or order.status != Order.NEW:
        raise OrderError("This order is not waiting for acceptance")
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise OrderError("Please give a reason (the customer will see it)")
    cancel_order(order, reason, user)
    order.refresh_from_db()
    order.acceptance = "rejected"
    order.save(update_fields=["acceptance"])
    from .automation import on_online_decision
    on_online_decision(order, False)
    return order
