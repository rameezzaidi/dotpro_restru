import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from rms import services as svc
from rms.countries import COUNTRIES
from rms.models import (Branch, BranchItem, Category, Channel, Ingredient, MenuItem, Order, Recipe, Rider, Setting,
                        Stock, StockMovement, Supplier, Table, User)

MENU = {
    "Burgers & Wraps": [("BRG-01", "Zinger Burger", 650, "Crispy chicken, mayo, lettuce", False),
                        ("BRG-02", "Beef Smash Burger", 850, "Double patty, cheddar, house sauce", False),
                        ("WRP-01", "Chicken Tikka Wrap", 520, "Tikka, mint chutney, onion", False)],
    "Pizza & Pasta": [("PZA-01", "Chicken Fajita Pizza (M)", 1450, "Fajita chicken, peppers, mozzarella", False),
                      ("PZA-02", "Margherita Pizza (M)", 1200, "Tomato, basil, mozzarella", True),
                      ("PST-01", "Creamy Alfredo Pasta", 990, "Penne, white sauce, grilled chicken", False)],
    "Desi / BBQ": [("DSI-01", "Chicken Karahi (Half)", 1800, "Tomato-ginger karahi, fresh coriander", False),
                   ("DSI-02", "Beef Biryani", 750, "Basmati, tender beef, raita", False),
                   ("BBQ-01", "Chicken Malai Boti (8 pcs)", 1100, "Creamy marinated boti", False),
                   ("BBQ-02", "Seekh Kabab (4 pcs)", 900, "Charcoal grilled", False),
                   ("DSI-03", "Garlic Naan", 90, "Fresh from the tandoor", True)],
    "Sides": [("SID-01", "Loaded Fries", 450, "Cheese sauce, jalapeño", True),
              ("SID-02", "Garden Salad", 350, "Seasonal veggies", True)],
    "Drinks": [("DRK-01", "Fresh Lime Soda", 220, "Sweet or salted", True),
               ("DRK-02", "Mint Margarita", 320, "Mint, lemon, soda", True),
               ("DRK-03", "Soft Drink 345ml", 120, "", True),
               ("DRK-04", "Doodh Patti Chai", 150, "Kadak desi chai", True)],
    "Desserts": [("DST-01", "Chocolate Lava Cake", 480, "Warm, molten centre", True),
                 ("DST-02", "Kheer", 280, "Slow-cooked rice pudding", True)],
}

ING_CAT = {"Chicken": "meat", "Beef": "meat", "Cheese (mozzarella)": "dairy", "Flour / Dough": "bakery",
           "Rice (basmati)": "grocery", "Burger buns": "bakery", "Cooking oil": "oil", "Tomato": "veg", "Potato": "veg",
           "Milk": "dairy", "Lemon": "veg", "Soft drink bottles": "beverage", "Tea leaves": "beverage",
           "Chocolate": "grocery", "Pasta (penne)": "grocery"}
INGREDIENTS = [("Chicken", "kg", 750), ("Beef", "kg", 1500), ("Cheese (mozzarella)", "kg", 1400),
               ("Flour / Dough", "kg", 120), ("Rice (basmati)", "kg", 330), ("Burger buns", "pcs", 45),
               ("Cooking oil", "l", 520), ("Tomato", "kg", 160), ("Potato", "kg", 90), ("Milk", "l", 190),
               ("Lemon", "kg", 300), ("Soft drink bottles", "pcs", 65), ("Tea leaves", "kg", 1400),
               ("Chocolate", "kg", 1800), ("Pasta (penne)", "kg", 380)]

RECIPES = {
    "BRG-01": [("Chicken", "0.18"), ("Burger buns", "1"), ("Cooking oil", "0.03")],
    "BRG-02": [("Beef", "0.2"), ("Burger buns", "1"), ("Cheese (mozzarella)", "0.03")],
    "WRP-01": [("Chicken", "0.15"), ("Flour / Dough", "0.08")],
    "PZA-01": [("Chicken", "0.15"), ("Cheese (mozzarella)", "0.2"), ("Flour / Dough", "0.3"), ("Tomato", "0.08")],
    "PZA-02": [("Cheese (mozzarella)", "0.2"), ("Flour / Dough", "0.3"), ("Tomato", "0.1")],
    "PST-01": [("Pasta (penne)", "0.15"), ("Chicken", "0.12"), ("Milk", "0.1")],
    "DSI-01": [("Chicken", "0.5"), ("Tomato", "0.2"), ("Cooking oil", "0.08")],
    "DSI-02": [("Rice (basmati)", "0.2"), ("Beef", "0.2"), ("Cooking oil", "0.05")],
    "BBQ-01": [("Chicken", "0.3")], "BBQ-02": [("Beef", "0.2")], "DSI-03": [("Flour / Dough", "0.1")],
    "SID-01": [("Potato", "0.25"), ("Cooking oil", "0.06"), ("Cheese (mozzarella)", "0.04")],
    "SID-02": [("Tomato", "0.1")], "DRK-01": [("Lemon", "0.05"), ("Soft drink bottles", "1")],
    "DRK-02": [("Lemon", "0.04")], "DRK-03": [("Soft drink bottles", "1")],
    "DRK-04": [("Milk", "0.2"), ("Tea leaves", "0.01")],
    "DST-01": [("Chocolate", "0.08"), ("Flour / Dough", "0.05")], "DST-02": [("Milk", "0.3"), ("Rice (basmati)", "0.03")],
}


class Command(BaseCommand):
    help = "Create demo data: 3 branches (master + 2), users, menu, tables, riders, supply chain, orders."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true",
                            help="WIPE all existing data (users, orders, everything) and re-create the demo data")



    # ------------------------------------------------------------------ older history (fast, no stock) for trends / forecasts
    def bulk_history(self, branches, items, waiters, cashiers, chans, days_from=34, days_to=7):
        from zoneinfo import ZoneInfo
        from rms.models import OrderItem, Payment
        weights = {"BRG-01": 10, "DRK-03": 9, "DSI-03": 8, "SID-01": 6, "DRK-04": 6, "BRG-02": 5, "PZA-01": 5, "DSI-02": 5,
                   "BBQ-01": 4, "WRP-01": 4, "DRK-01": 4, "PST-01": 3, "BBQ-02": 3, "DRK-02": 3, "DST-01": 3,
                   "PZA-02": 2, "DSI-01": 2, "SID-02": 2, "DST-02": 2}
        by_id = {m.pk: m for m in items.values()}
        today = timezone.localdate()
        for b in branches:
            zi = ZoneInfo(b.timezone)
            menu = svc.branch_menu(b)
            w = [weights.get(by_id[m["id"]].sku, 2) for m in menu]
            tables = list(Table.objects.filter(branch=b))
            riders = list(Rider.objects.filter(branch=b))
            made = []
            for d in range(days_from, days_to - 1, -1):
                date = today - timedelta(days=d)
                base = {4: 1.45, 5: 1.5, 6: 1.2}.get(date.weekday(), 1.0)          # Fri/Sat/Sun busier
                growth = 1 + (days_from - d) * 0.006                                # slight upward trend
                for k in range(max(3, int(random.randint(6, 10) * base * growth))):
                    otype = random.choices(["dine_in", "takeaway", "delivery"], [5, 2, 3])[0]
                    hour = random.choices(range(11, 24), [1, 3, 5, 3, 1, 1, 2, 3, 5, 6, 4, 2, 1])[0]
                    at = timezone.datetime.combine(date, timezone.datetime.min.time().replace(hour=hour, minute=random.randint(0, 59)), tzinfo=zi)
                    picks, seen = [], set()
                    for _ in range(random.randint(1, 4)):
                        m = random.choices(menu, w)[0]
                        if m["id"] not in seen:
                            seen.add(m["id"])
                            picks.append(m)
                    src = chans["pos"] if otype != "delivery" else random.choice([chans["foodpanda"], chans["website"], chans["whatsapp"]])
                    o = Order(number=f"{b.code}-{date:%y%m%d}-{k + 1:04d}", branch=b, order_type=otype, source=src,
                              table=random.choice(tables) if otype == "dine_in" else None,
                              waiter=random.choice(waiters[b]) if otype == "dine_in" else None,
                              rider=random.choice(riders) if otype == "delivery" else None, created_by=cashiers[b],
                              customer_name=random.choice(["Ali", "Sana", "Hassan", "Mariam", "Zain"]),
                              customer_phone="0300-555" + str(random.randint(1000, 9999)),
                              customer_address="House 12, Street 4" if otype == "delivery" else "",
                              delivery_fee=b.delivery_fee if otype == "delivery" else 0, status=Order.COMPLETED,
                              stock_deducted=True, kot_round=1)
                    made.append((o, at, picks))
            Order.objects.bulk_create([o for o, _, _ in made])
            lines = []
            for o, at, picks in made:
                for m in picks:
                    lines.append(OrderItem(order=o, item_id=m["id"], name=m["name"], qty=random.choices([1, 2, 3], [6, 3, 1])[0],
                                           price=m["price"], deducted=True))
            OrderItem.objects.bulk_create(lines)
            pays = []
            for n, (o, at, picks) in enumerate(made, 1):
                svc.recalc(o)
                method = random.choice(["cash", "card", "online"])
                started = at + timedelta(minutes=random.randint(1, 4))
                ready = at + timedelta(minutes=random.randint(9, 26))
                Order.objects.filter(pk=o.pk).update(
                    created_at=at, started_at=started, ready_at=ready, billed_at=at + timedelta(minutes=random.randint(25, 60)),
                    bill_number=f"INV-{b.code}-{n:06d}", is_paid=True, payment_method=method, paid_at=at + timedelta(minutes=40))
                pays.append(Payment(order=o, method=method, amount=o.total, tendered=o.total, user=cashiers[b], created_at=at + timedelta(minutes=40)))
            Payment.objects.bulk_create(pays)


    # ------------------------------------------------------------------ customer feedback (per item, waiter, rider)
    def add_feedback(self, items, waiters):
        from rms.models import Feedback, ItemFeedback, OrderItem
        prof = {"BRG-01": (4.5, []), "BRG-02": (3.3, ["Patty thora dry tha", "Burger was dry and a bit salty", "Namak zyada tha"]),
                "WRP-01": (3.0, ["Wrap was bland, portion small", "Bilkul pheeka tha", "Very small portion for the price"]),
                "PZA-01": (4.4, []), "PST-01": (4.0, []), "DSI-01": (4.2, []),
                "DSI-02": (3.4, ["Biryani thandi thi", "Rice was cold and oily", "Bohat mehngi hai for this quantity"]),
                "BBQ-01": (4.6, []), "BBQ-02": (4.3, []), "DSI-03": (3.8, ["Naan thanda tha", "Naan was cold"]),
                "SID-01": (4.0, []), "SID-02": (2.9, ["Salad was not fresh", "Baasi lag raha tha", "Salad stale and watery"]),
                "DRK-03": (3.7, ["Drink was not cold", "Thanda nahi tha"]), "DRK-04": (4.6, []), "DST-01": (4.5, [])}
        pos = ["Delicious, loved it", "Bohat mazedar!", "Tasty and fresh", "Zabardast", "Perfectly cooked, hot and fresh", "Best in town", "Lajawab taste"]
        neu = ["Theek tha", "Okay, nothing special", "Average"]
        svc_pos = ["Staff was polite and friendly", "Great service, very quick", "Waiter was helpful and attentive", "Bohat acha behaviour"]
        svc_neg = ["Waiter was rude and ignored us", "Service was slow, we waited too long", "Bohat dair lagi order aane mein", "Staff was careless"]
        rid_neg = ["Delivery late thi aur khana thanda tha", "Rider was rude and late", "Order took too long, food cold"]
        rid_pos = ["Quick delivery, food was hot", "Rider was polite and on time"]
        by_id = {m.pk: m.sku for m in items.values()}
        wscore = {}
        for b, ws in waiters.items():
            for i, w in enumerate(ws):
                wscore[w.pk] = 4.6 if i == 0 else 3.4
        rscore = {}
        for b in Branch.objects.all():
            for i, r in enumerate(Rider.objects.filter(branch=b)):
                rscore[r.pk] = 4.4 if i == 0 else 3.3
        rate = lambda mu: max(1, min(5, int(round(random.gauss(mu, 0.9)))))
        orders = list(Order.objects.filter(status=Order.COMPLETED, feedback__isnull=True).select_related("waiter", "rider"))
        batch = []
        for o in orders:
            if random.random() > 0.45:
                continue
            if o.order_type == "dine_in" and o.waiter_id:
                mu = wscore.get(o.waiter_id, 4.2)
            elif o.order_type == "delivery" and o.rider_id:
                mu = rscore.get(o.rider_id, 4.0)
            else:
                mu = 4.2
            sr = rate(mu) if random.random() < 0.9 else 0
            comment = ""
            if random.random() < 0.35:
                if o.order_type == "delivery":
                    comment = random.choice(rid_neg if sr <= 2 else rid_pos) if sr != 3 else ""
                else:
                    comment = random.choice(svc_neg if sr <= 2 else svc_pos) if sr != 3 else ""
            batch.append((Feedback(order=o, branch_id=o.branch_id, service_rating=sr, comment=comment, source="link",
                                   created_at=o.created_at + timedelta(minutes=random.randint(60, 240))), o))
        Feedback.objects.bulk_create([f for f, _ in batch])
        fmap = {f.order_id: f for f in Feedback.objects.filter(order_id__in=[o.pk for _, o in batch])}
        rows = []
        for o in orders:
            fb = fmap.get(o.pk)
            if not fb:
                continue
            seen = set()
            for l in OrderItem.objects.filter(order=o, is_void=False, item__isnull=False):
                if l.item_id in seen or random.random() < 0.15:
                    continue
                seen.add(l.item_id)
                sku = by_id.get(l.item_id)
                mu, bad = prof.get(sku, (4.2, []))
                r = rate(mu)
                c = ""
                if random.random() < 0.4:
                    if r <= 2 and bad:
                        c = random.choice(bad)
                    elif r >= 4:
                        c = random.choice(pos)
                    elif r == 3:
                        c = random.choice(neu)
                rows.append(ItemFeedback(feedback=fb, item_id=l.item_id, name=l.name, rating=r, comment=c))
        ItemFeedback.objects.bulk_create(rows)

    # ------------------------------------------------------------------ customers + loyalty
    def add_customers(self):
        from rms.models import Customer, LoyaltyTxn
        first = ["Ali", "Sana", "Hassan", "Mariam", "Zain", "Bilal", "Hina", "Omer", "Ayesha", "Fahad", "Noor", "Saad", "Iqra", "Danish", "Rabia",
                 "Usman", "Maryam", "Hamza", "Areeba", "Talha", "Kiran", "Adeel", "Sidra", "Waqas", "Laiba"]
        last = ["Khan", "Ahmed", "Malik", "Sheikh", "Raza", "Qureshi", "Siddiqui", "Butt", "Chaudhry", "Hashmi"]
        Customer.objects.all().delete()
        pool = []
        for i in range(48):
            pool.append(Customer(name=f"{random.choice(first)} {random.choice(last)}", phone=svc.norm_phone(f"03{random.randint(0, 4)}{random.randint(1000000, 9999999)}"),
                                 created_at=timezone.now() - timedelta(days=random.randint(5, 40))))
        Customer.objects.bulk_create(pool, ignore_conflicts=True)
        pool = list(Customer.objects.all())
        weights = [max(1, int(40 / (i + 1) ** 0.6)) for i in range(len(pool))]          # a few regulars, many occasional
        rate = Decimal(str(Setting.get().loyalty_points_per_100))
        stats = {c.pk: {"v": 0, "spent": Decimal(0), "last": None, "pts": 0} for c in pool}
        txns = []
        for o in Order.objects.exclude(status=Order.CANCELLED).order_by("created_at"):
            c = random.choices(pool, weights)[0]
            Order.objects.filter(pk=o.pk).update(customer=c, customer_name=c.name, customer_phone=c.phone)
            if o.status == Order.COMPLETED:
                st = stats[c.pk]
                st["v"] += 1
                st["spent"] += o.total
                st["last"] = o.created_at
                pts = int(o.total * rate / 100)
                st["pts"] += pts
                if pts:
                    txns.append(LoyaltyTxn(customer=c, order=o, points=pts, reason=f"Earned on {o.number}", created_at=o.created_at))
        for c in pool:
            st = stats[c.pk]
            redeemed = 0
            if st["pts"] > 400 and random.random() < 0.5:
                redeemed = 100
                txns.append(LoyaltyTxn(customer=c, points=-redeemed, reason="Redeemed (discount applied on a bill)", created_at=timezone.now() - timedelta(days=3)))
            Customer.objects.filter(pk=c.pk).update(visits=st["v"], total_spent=st["spent"], last_visit=st["last"], points=st["pts"] - redeemed,
                                                    lifetime_points=st["pts"])
        LoyaltyTxn.objects.bulk_create(txns)
        Customer.objects.filter(visits=0).delete()
    # ------------------------------------------------------------------ tips (waiters: dine-in, riders: delivery)
    def add_tips(self):
        from rms.models import Payment
        for o in Order.objects.filter(status=Order.COMPLETED, tip=0).select_related("branch"):
            chance = {"dine_in": 0.6, "delivery": 0.45, "takeaway": 0.1}[o.order_type]
            if random.random() > chance:
                continue
            pct = Decimal(random.choice([5, 8, 10, 12, 15])) / 100
            tip = (o.subtotal * pct).quantize(Decimal("1")) if o.branch.currency_code == "PKR" else (o.subtotal * pct).quantize(Decimal("0.5"))
            if tip <= 0:
                continue
            p = Payment.objects.filter(order=o).order_by("-id").first()
            if p:
                Payment.objects.filter(pk=p.pk).update(tip=tip)
                Order.objects.filter(pk=o.pk).update(tip=tip)

    # ------------------------------------------------------------------ past days: shifts + day close
    def past_ops(self, branches, cashiers, admin, now):
        from zoneinfo import ZoneInfo
        from rms.models import DayClose, Payment, Shift
        self.add_tips()
        for o in Order.objects.filter(status="completed", created_at__gte=now - timedelta(days=7)).only("id", "created_at"):
            t = o.created_at + timedelta(minutes=random.randint(25, 60))
            Order.objects.filter(pk=o.pk).update(billed_at=t)
            Payment.objects.filter(order_id=o.pk).update(created_at=t)
        diffs = [0, 0, -200, 150, 0, -50]
        for b in branches:
            zi = ZoneInfo(b.timezone)
            for i, days in enumerate(range(6, 0, -1)):
                date = timezone.localdate(now) - timedelta(days=days)
                start, end = svc.day_bounds(date)
                sh = Shift.objects.create(branch=b, user=cashiers[b], opening_cash=5000)
                Payment.objects.filter(order__branch=b, created_at__gte=start, created_at__lt=end).update(shift=sh)
                t = svc.shift_totals(sh)
                svc.close_shift(sh, Decimal(5000) + t["cash_in"] + diffs[i],
                                note="Short in drawer" if diffs[i] < 0 else ("Extra cash" if diffs[i] > 0 else ""))
                Shift.objects.filter(pk=sh.pk).update(opened_at=start + timedelta(hours=9), closed_at=start + timedelta(hours=23))
                dc = svc.close_day(b, date, admin)
                DayClose.objects.filter(pk=dc.pk).update(closed_at=start + timedelta(hours=23, minutes=30))

    # ------------------------------------------------------------------ supply chain: POs, transfers, wastage
    def supply_ops(self, khi, lhe, dxb, sups, ings, admin, managers):
        from rms.models import PurchaseLine, PurchaseOrder
        now = timezone.now()

        def mk_po(b, sup, rows, status, ago, notes="", invoice=""):
            po = PurchaseOrder.objects.create(number=svc.next_po_number(b), branch=b, supplier=sup, created_by=managers[b],
                                              status=PurchaseOrder.DRAFT, notes=notes)
            for n, q in rows:
                PurchaseLine.objects.create(po=po, ingredient=ings[n], qty=Decimal(q), unit_cost=ings[n].unit_cost)
            PurchaseOrder.objects.filter(pk=po.pk).update(created_at=now - timedelta(days=ago))
            po.refresh_from_db()
            if status != PurchaseOrder.DRAFT:
                po.status = PurchaseOrder.ORDERED
                po.save(update_fields=["status"])
            return po

        for b, tag in ((khi, "FF"), (lhe, "FF")):
            po = mk_po(b, sups[0], [("Tomato", "40"), ("Potato", "50"), ("Lemon", "10")], "ordered", 5, "Weekly veg order")
            svc.receive_purchase_order(po, managers[b], None, invoice_no=f"{tag}-INV-{random.randint(1000, 9999)}")
            po = mk_po(b, sups[1], [("Chicken", "60"), ("Beef", "40")], "ordered", 2, "Meat - part delivery expected")
            first = po.lines.order_by("id").first()
            svc.receive_purchase_order(po, managers[b], {first.pk: Decimal("35")}, invoice_no="KMC-" + str(random.randint(100, 999)))
            mk_po(b, sups[2], [("Flour / Dough", "80"), ("Rice (basmati)", "50"), ("Cooking oil", "30")], "ordered", 1, "Dry goods")
            po = mk_po(b, sups[0], [("Milk", "25")], "draft", 4, "Supplier could not deliver")
            po.status = PurchaseOrder.CANCELLED
            po.save(update_fields=["status"])
        # head office (Karachi) acts as the central store -> stock transfers to other branches
        for n in ("Chicken", "Burger buns", "Cooking oil"):
            svc.adjust_stock(khi, ings[n], Decimal("100"), "adjustment", note="Central store top-up", user=managers[khi])
        svc.transfer_stock(khi, lhe, ings["Chicken"], Decimal("10"), note="Weekend rush support", user=managers[khi])
        svc.transfer_stock(khi, lhe, ings["Burger buns"], Decimal("60"), note="Buns for Lahore", user=managers[khi])
        svc.transfer_stock(khi, dxb, ings["Cooking oil"], Decimal("8"), note="Opening support", user=managers[khi])
        # wastage + count corrections
        svc.adjust_stock(khi, ings["Tomato"], Decimal("-3"), "wastage", note="Spoiled - fridge failure", user=managers[khi])
        svc.adjust_stock(lhe, ings["Milk"], Decimal("-2"), "wastage", note="Expired", user=managers[lhe])
        svc.adjust_stock(khi, ings["Potato"], Decimal("5"), "adjustment", note="Stock count correction", user=managers[khi])
        svc.adjust_stock(dxb, ings["Lemon"], Decimal("-1"), "wastage", note="Dropped / unusable", user=managers[dxb])

    # ------------------------------------------------------------------ today: every order / payment / table state
    def today_ops(self, branches, cashiers, waiters, chans, items, managers):
        khi, lhe, dxb = branches
        pos = chans["pos"]
        for st in Stock.objects.select_related("ingredient"):            # restock so today's orders never push stock negative
            if st.quantity < st.level * 3:
                svc.adjust_stock(st.branch, st.ingredient, st.level * 4, "purchase", note="Daily restock", user=managers[st.branch])
        for b in branches:
            svc.open_shift(b, cashiers[b], 10000)

        def lines(b, spec):
            out = []
            for sku, q in spec:
                it = items[sku]
                out.append({"item": it, "name": it.name, "qty": q, "price": svc.effective_price(it, b)})
            return out

        def table(b):
            return Table.objects.filter(branch=b, status="free").order_by("name").first()

        def order(b, spec, otype="dine_in", **kw):
            t = table(b) if otype == "dine_in" else None
            if otype == "dine_in" and not t:
                otype = "takeaway"
            return svc.create_order(branch=b, order_type=otype, items=lines(b, spec), source=kw.pop("source", pos), table=t,
                                    waiter=waiters[b][1] if t else None, user=cashiers[b],
                                    customer_name=kw.pop("name", "Walk-in"), **kw)

        for b in (khi, lhe, dxb):
            c, m = cashiers[b], managers[b]
            # cash with change + tip -> completed
            o = order(b, [("BRG-01", 2), ("DRK-03", 2)], "takeaway", name="Bilal")
            svc.add_payment(o, "cash", tendered=Decimal(o.total) + 500, tip=Decimal("50"), user=c)
            svc.set_status(o, Order.COMPLETED, c)
            # mixed payment (card + cash), table goes to 'cleaning'
            o = order(b, [("PZA-01", 1), ("PST-01", 1), ("DRK-01", 2)], name="Table of friends")
            half = (Decimal(o.total) / 2).quantize(Decimal("0.01"))
            svc.add_payment(o, "card", half, user=c, tip=Decimal("100"))
            svc.add_payment(o, "cash", tendered=Decimal(o.total) - half + 200, user=c)
            svc.set_status(o, Order.COMPLETED, c)
            if b is dxb:
                continue
            # served, guest asked for the bill -> table 'bill'
            o = order(b, [("DSI-02", 2), ("BBQ-02", 1), ("DRK-04", 3)], name="Family Khan")
            svc.set_status(o, Order.READY, c)
            svc.set_status(o, Order.SERVED, c)
            svc.request_bill(o)
            # served, still eating -> table 'occupied'
            o = order(b, [("WRP-01", 2), ("SID-01", 1)], name="Couple")
            svc.set_status(o, Order.READY, c)
            svc.set_status(o, Order.SERVED, c)
            # second KOT round (guest ordered more)
            o = order(b, [("DSI-01", 1), ("DSI-03", 4)], name="Guest - round 2")
            svc.add_items(o, lines(b, [("DRK-02", 2), ("DST-01", 1)]), c)
            # split bill: one guest pays on his own (card), the other bill stays open
            o = order(b, [("BRG-02", 2), ("SID-02", 1), ("DRK-03", 2)], name="Office lunch")
            line = o.lines.filter(item__sku="BRG-02").first()
            child = svc.split_order(o, {line.pk: 1}, c)
            svc.add_payment(child, "card", user=c)
            svc.set_status(child, Order.COMPLETED, c)
            # void one item (manager approval), cancelled paid order (refund), cancelled unpaid order
            o = order(b, [("PZA-02", 1), ("DST-02" if b is not lhe else "DST-01", 2)], "takeaway", name="Void demo")
            svc.void_line(o, o.lines.order_by("-id").first(), "Customer changed mind", c, approver=m)
            o = order(b, [("BBQ-01", 1), ("DRK-03", 1)], "takeaway", name="Refund demo")
            svc.add_payment(o, "card", user=c)
            svc.cancel_order(o, "Wrong order taken", c, approver=m)
            o = order(b, [("SID-01", 1)], "takeaway", name="Dup")
            svc.cancel_order(o, "Duplicate order", c, approver=m)
            # delivery: one out on the road, one waiting for the rider
            riders = list(Rider.objects.filter(branch=b))
            o = order(b, [("PZA-01", 2), ("DRK-03", 2)], "delivery", name="Hina", phone="0301-5551234", address="Flat 7, Block C",
                      source=chans["foodpanda"], external_ref=f"foodpanda-{random.randint(10000, 99999)}")
            Order.objects.filter(pk=o.pk).update(rider=riders[0])
            o.refresh_from_db()
            svc.set_status(o, Order.READY, c)
            svc.set_status(o, Order.OUT, c)
            order(b, [("BRG-01", 3)], "delivery", name="Omer", phone="0302-5559876", address="House 44, Phase 5", source=chans["website"])

    # ------------------------------------------------------------------ low / out of stock so alerts are visible
    def stock_alerts(self, khi, lhe, dxb, ings, admin, managers):
        def set_qty(b, name, qty):
            st = Stock.objects.get(branch=b, ingredient=ings[name])
            svc.adjust_stock(b, ings[name], Decimal(qty) - st.quantity, "adjustment", note="Physical count", user=managers[b])
        set_qty(lhe, "Cheese (mozzarella)", "0.5")
        set_qty(lhe, "Beef", "1.2")
        set_qty(dxb, "Chocolate", "0")
        set_qty(dxb, "Tea leaves", "0.1")
        set_qty(khi, "Lemon", "1")
        for b in (khi, lhe, dxb):
            svc.create_reorder_pos(b, managers[b])

    def handle(self, *args, **opts):
        if Branch.objects.exists():
            if not opts["force"]:
                self.stdout.write(self.style.WARNING(
                    "Data already exists. Run with --force to WIPE everything and re-create the demo data."))
                return
            from django.core.management import call_command
            call_command("flush", interactive=False, verbosity=0)     # clean slate, tables stay
        random.seed(7)
        s = Setting.get()
        s.business_name, s.default_country, s.default_language = "Restopro Foods", "PK", "en"
        s.block_if_out_of_stock = False          # demo history/live orders must not be refused; switched back on at the end
        s.msg_customer_events = s.auto_late_alerts = s.auto_assign_rider = s.online_manual_accept = False   # no messages / alerts for fake history; restored at the end
        s.save()

        # channels
        chans = {}
        for code, name, kind, comm in [("pos", "POS / Counter", "pos", 0), ("website", "Website", "website", 0),
                                       ("foodpanda", "Foodpanda", "aggregator", 25), ("whatsapp", "WhatsApp", "phone", 0),
                                       ("phone", "Phone Call", "phone", 0)]:
            chans[code], _ = Channel.objects.get_or_create(code=code, defaults={"name": name, "kind": kind,
                                                                                 "commission_percent": comm})

        # branches
        def mk_branch(name, code, cc, master=False, tax=0, svc_pct=0, fee=0, addr=""):
            c = COUNTRIES[cc]
            return Branch.objects.create(name=name, code=code, country=cc, timezone=c["tz"], currency_code=c["cur"],
                                         currency_symbol=c["sym"], language="en" if cc == "PK" else c["lang"],
                                         tax_percent=tax, service_charge_percent=svc_pct, delivery_fee=fee,
                                         is_master=master, address=addr, phone="+92 21 111 222" if cc == "PK" else "+971 4 555 0101")
        khi = mk_branch("Karachi — Head Office", "KHI", "PK", True, 15, 5, 150, "Khayaban-e-Shahbaz, DHA Phase 6, Karachi")
        lhe = mk_branch("Lahore — Gulberg", "LHE", "PK", False, 16, 5, 150, "MM Alam Road, Gulberg III, Lahore")
        dxb = mk_branch("Dubai — Marina", "DXB", "AE", False, 5, 10, 8, "Marina Walk, Dubai")
        branches = [khi, lhe, dxb]

        # users
        def mk_user(username, role, branch, first, pw="demo1234", **kw):
            u = User(username=username, role=role, branch=branch, first_name=first, **kw)
            u.set_password(pw)
            u.save()
            return u
        admin = mk_user("admin", "owner", None, "Owner", pw="admin123", is_superuser=True, is_staff=True)
        mk_user("khi_manager", "manager", khi, "Hamza (Master Mgr)")
        mk_user("lhe_manager", "manager", lhe, "Sara")
        mk_user("dxb_manager", "manager", dxb, "Omar")
        cashiers = {b: mk_user(f"{b.code.lower()}_cashier", "cashier", b, f"Cashier {b.code}") for b in branches}
        waiters = {b: [mk_user(f"{b.code.lower()}_waiter{i}", "waiter", b, n) for i, n in ((1, "Bilal"), (2, "Ayesha"))] for b in branches}
        mk_user("khi_kitchen", "kitchen", khi, "Chef Imran")
        mk_user("khi_kitchen2", "kitchen", khi, "Chef Waseem")
        mk_user("lhe_kitchen", "kitchen", lhe, "Chef Adeel")
        mk_user("khi_kds", "kds", khi, "Kitchen Display KHI")
        mk_user("lhe_kds", "kds", lhe, "Kitchen Display LHE")
        mk_user("dxb_kitchen", "kitchen", dxb, "Chef Rahim")
        mk_user("dxb_kds", "kds", dxb, "Kitchen Display DXB")
        for b, nm in ((khi, "Store KHI"), (lhe, "Store LHE"), (dxb, "Store DXB")):
            mk_user(f"{b.code.lower()}_store", "storekeeper", b, nm, phone="0300-1000" + str(random.randint(100, 999)))

        # menu
        items = {}
        for i, (cat, rows) in enumerate(MENU.items()):
            c = Category.objects.create(name=cat, sort_order=i)
            for sku, name, price, desc, veg in rows:
                items[sku] = MenuItem.objects.create(category=c, name=name, sku=sku, price=price, description=desc, is_veg=veg,
                                                     prep_minutes=random.choice([8, 12, 15, 20]))
        from rms.models import ModifierGroup, ModifierOption
        def mod_group(name, options, multi, skus):
            g = ModifierGroup.objects.create(name=name, multi=multi, required=False)
            for n, (opt, price) in enumerate(options):
                ModifierOption.objects.create(group=g, name=opt, price=price, sort_order=n)
            for sku in skus:
                if sku in items:
                    items[sku].modifier_groups.add(g)
        food = [k for k in items if k.startswith(("BRG", "PZA"))]
        mod_group("Extras", [("Extra cheese", 80), ("Fried egg", 60), ("Extra patty", 250), ("Jalapenos", 40)], True, food)
        mod_group("Spice level", [("Mild", 0), ("Medium", 0), ("Hot", 0)], False, food)
        mod_group("Drink size", [("Regular", 0), ("Large", 70)], False, [k for k in items if k.startswith("DRK")])
        for it in items.values():   # Dubai local pricing in AED
            BranchItem.objects.create(branch=dxb, item=it, price=max(5, round(float(it.price) / 70)))
        Branch.objects.filter(pk=dxb.pk).update(cost_factor=Decimal("0.01429"))
        BranchItem.objects.create(branch=lhe, item=items["DST-02"], is_available=False)

        # tables
        for b, n_main, n_terrace in ((khi, 6, 4), (lhe, 5, 3), (dxb, 5, 3)):
            for i in range(1, n_main + 1):
                Table.objects.create(branch=b, name=f"T{i}", area="Main Hall", capacity=random.choice([2, 4, 4, 6]))
            for i in range(1, n_terrace + 1):
                Table.objects.create(branch=b, name=f"R{i}", area="Terrace", capacity=4)

        # riders
        for b, names in ((khi, ["Kamran", "Zeeshan", "Naveed"]), (lhe, ["Usman", "Tariq"]), (dxb, ["Rashid", "Faisal"])):
            for n in names:
                Rider.objects.create(branch=b, name=n, phone="0300-" + str(random.randint(1000000, 9999999)),
                                     vehicle=f"Bike {random.randint(100, 999)}")

        # rider logins: every rider gets a user account (role Delivery Rider)
        for b in branches:
            for i, r in enumerate(Rider.objects.filter(branch=b).order_by("pk"), 1):
                u = User(username=f"{b.code.lower()}_rider{i}", role="rider", branch=b, first_name=r.name, phone=r.phone)
                u.set_password("demo1234")
                User.objects.bulk_create([u])           # bulk: the User.save() hook would create a second Rider
                Rider.objects.filter(pk=r.pk).update(user=User.objects.get(username=u.username))

        # supply chain
        sups = [Supplier.objects.create(name=n, contact_person=c, phone=p) for n, c, p in
                [("Fresh Farms Traders", "Mr. Rafiq", "0321-1112223"), ("Karachi Meat Co.", "Haji Anwar", "0333-4445556"),
                 ("Sunrise Wholesale", "Ms. Nida", "0345-7778889")]]
        ings = {}
        for n, u, c in INGREDIENTS:
            ings[n] = Ingredient.objects.create(name=n, unit=u, unit_cost=c, category=ING_CAT.get(n, "other"),
                                                 default_supplier=random.choice(sups))
        for sku, lines in RECIPES.items():
            for n, q in lines:
                Recipe.objects.create(item=items[sku], ingredient=ings[n], quantity=Decimal(q))

        # order history (before stock levels are set, so history does not drain demo stock)
        now = timezone.now()
        self.bulk_history(branches, items, waiters, cashiers, chans)
        for b in branches:
            menu = svc.branch_menu(b)
            tables = list(Table.objects.filter(branch=b))
            riders = list(Rider.objects.filter(branch=b))
            for day in range(6, -1, -1):
                for _ in range(random.randint(5, 9)):
                    otype = random.choices(["dine_in", "takeaway", "delivery"], [5, 2, 3])[0]
                    picks = random.sample(menu, random.randint(1, 4))
                    lines = [{"item": MenuItem.objects.get(pk=m["id"]), "name": m["name"], "qty": random.randint(1, 3),
                              "price": m["price"]} for m in picks]
                    src = chans["pos"]
                    if otype == "delivery":
                        src = random.choice([chans["foodpanda"], chans["website"], chans["whatsapp"]])
                    o = svc.create_order(
                        branch=b, order_type=otype, items=lines, source=src,
                        table=random.choice(tables) if otype == "dine_in" else None,
                        waiter=random.choice(waiters[b]) if otype == "dine_in" else None,
                        rider=random.choice(riders) if otype == "delivery" else None, user=cashiers[b],
                        customer_name=random.choice(["Ali", "Sana", "Hassan", "Mariam", "Zain"]),
                        phone="0300-555" + str(random.randint(1000, 9999)), address="House 12, Street 4" if otype == "delivery" else "",
                        external_ref=f"{src.code}-{random.randint(10000, 99999)}" if src.code == "foodpanda" else "")
                    if otype == "delivery":
                        o.status = Order.OUT
                        o.save(update_fields=["status"])
                    svc.set_status(o, Order.COMPLETED, cashiers[b], random.choice(["cash", "card", "online"]))
                    when = now - timedelta(days=day, hours=random.randint(0, 9), minutes=random.randint(0, 59))
                    Order.objects.filter(pk=o.pk).update(
                        created_at=when, started_at=when + timedelta(minutes=random.randint(1, 4)),
                        ready_at=when + timedelta(minutes=random.randint(9, 26)))

        Table.objects.filter(status__in=("cleaning", "bill")).update(status="free")   # history left tables in 'cleaning'
        managers = {b: User.objects.get(username=u) for b, u in ((khi, "khi_manager"), (lhe, "lhe_manager"), (dxb, "dxb_manager"))}
        self.past_ops(branches, cashiers, admin, now)

        # stock levels (set after history) — a few deliberately low to show alerts
        StockMovement.objects.all().delete()
        for b in branches:
            for n, ing in ings.items():
                reorder = Decimal(random.choice([5, 8, 10, 20]))
                qty = (reorder * Decimal(str(random.choice([0.6, 2, 3, 4, 6])))).quantize(Decimal("0.001"))
                if b is khi:
                    qty *= 3      # head office acts as central store
                Stock.objects.update_or_create(branch=b, ingredient=ing, defaults={"quantity": 0, "reorder_level": reorder})
                svc.adjust_stock(b, ing, qty, "opening", note="Demo opening stock", user=admin)

        # live orders so the Kitchen board has content: Karachi has 2 chefs, Lahore only 1 chef -> queue builds up
        first_lhe = None
        for b, n in ((khi, 5), (lhe, 6)):
            menu = svc.branch_menu(b)
            tables = list(Table.objects.filter(branch=b, status="free"))
            for _ in range(n):
                otype = random.choice(["dine_in", "dine_in", "takeaway"])
                picks = random.sample(menu, 2)
                lines = [{"item": MenuItem.objects.get(pk=m["id"]), "name": m["name"], "qty": random.randint(1, 2),
                          "price": m["price"]} for m in picks]
                tbl = tables.pop() if (otype == "dine_in" and tables) else None
                if otype == "dine_in" and not tbl:
                    otype = "takeaway"
                o = svc.create_order(branch=b, order_type=otype, items=lines, source=chans["pos"], table=tbl,
                                     waiter=waiters[b][0] if tbl else None, user=cashiers[b], customer_name="Walk-in")
                if b is lhe and first_lhe is None:
                    first_lhe = o
        if first_lhe:                       # one finished -> next queued order moves to the chef automatically
            svc.set_status(first_lhe, Order.READY)

        # a couple of reservations for tonight so the screen is not empty
        from datetime import time as _t
        from rms.models import Reservation
        for b, nm, hh, g in ((khi, "Mr. Ahmed", 20, 4), (khi, "Sana & family", 21, 6), (lhe, "Dr. Hassan", 20, 2)):
            tbl = Table.objects.filter(branch=b, status="free", capacity__gte=g).order_by("capacity").first()
            from zoneinfo import ZoneInfo
            zi = ZoneInfo(b.timezone)
            day = timezone.now().astimezone(zi).date()
            at = timezone.datetime.combine(day, _t(hh, 0), tzinfo=zi)
            Reservation.objects.create(branch=b, table=tbl, name=nm, phone="0300-1234567", guests=g, at=at, notes="Birthday" if g == 6 else "")
        self.supply_ops(khi, lhe, dxb, sups, ings, admin, managers)
        self.today_ops(branches, cashiers, waiters, chans, items, managers)
        self.stock_alerts(khi, lhe, dxb, ings, admin, managers)
        self.add_feedback(items, waiters)
        self.add_customers()
        for b in branches:
            aed = b.currency_code != "PKR"
            User.objects.filter(role="waiter", branch=b).update(monthly_salary=Decimal("1800") if aed else Decimal("35000"), commission_percent=Decimal("1.0"))
            for role, sal in (("cashier", 28000), ("kitchen", 40000), ("kds", 55000), ("storekeeper", 32000)):
                User.objects.filter(role=role, branch=b).update(monthly_salary=Decimal(sal) / (70 if aed else 1))
            Rider.objects.filter(branch=b).update(monthly_salary=Decimal("1500") if aed else Decimal("30000"),
                                                  per_delivery_fee=Decimal("5") if aed else Decimal("80"), commission_percent=Decimal("0"))
        # live deliveries for the riders: one waiting for pick-up, one on the road (per branch with riders)
        for b in (khi, lhe):
            menu = svc.branch_menu(b)
            rs = list(Rider.objects.filter(branch=b, user__isnull=False).order_by("pk")[:2])
            for r, target in zip(rs, (Order.READY, Order.OUT)):
                m = random.choice(menu)
                o = svc.create_order(branch=b, order_type="delivery", rider=r, user=cashiers[b], source=chans["website"],
                                     items=[{"item": MenuItem.objects.get(pk=m["id"]), "name": m["name"], "qty": 2, "price": m["price"]}],
                                     customer_name="Demo Customer", phone="0300-5551234", address="House 7, Street 9, near the park")
                if target == Order.OUT:
                    svc.set_status(o, Order.READY)
                svc.set_status(o, target)
        Setting.objects.filter(pk=1).update(block_if_out_of_stock=True, payment_gateway="sandbox", msg_customer_events=True,
                                            auto_late_alerts=True, auto_assign_rider=True, online_manual_accept=True)
        from rms.models import AutomationEvent, MessageLog, Notification
        MessageLog.objects.all().delete(); Notification.objects.all().delete(); AutomationEvent.objects.all().delete()
        # two online orders waiting for the cashier to Accept / Reject (Karachi)
        for otype, nm, ph, addr in (("delivery", "Farhan Ahmed", "0321-5550101", "House 22, Street 5, Gulshan-e-Iqbal"),
                                    ("takeaway", "Mehwish Ali", "0333-5550102", "")):
            m = random.choice([x for x in svc.branch_menu(khi) if x["max_qty"] is None or x["max_qty"] > 5])
            svc.create_order(branch=khi, order_type=otype, source=chans["website"], customer_name=nm, phone=ph, address=addr,
                             items=[{"item": MenuItem.objects.get(pk=m["id"]), "name": m["name"], "qty": 2, "price": m["price"]}])
        MessageLog.objects.all().delete()          # keep the bell alerts, drop the demo messages
        from rms.models import Coupon, Expense
        today = timezone.localdate()
        Coupon.objects.create(code="WELCOME10", description="10% off, max 200", kind="percent", value=10, max_discount=200)
        Coupon.objects.create(code="FLAT100", description="100 off orders above 1000", kind="fixed", value=100, min_order=1000)
        Coupon.objects.create(code="LHE20", description="Lahore branch: 20% off", kind="percent", value=20, branch=lhe,
                              valid_to=today + timedelta(days=30))
        for b, rent in ((khi, 180000), (lhe, 120000), (dxb, 9000)):
            Expense.objects.create(branch=b, category="rent", amount=rent, date=today.replace(day=1), note="Monthly rent")
            Expense.objects.create(branch=b, category="utilities", amount=rent // 6, date=today, note="Electricity & gas")
            Expense.objects.create(branch=b, category="marketing", amount=rent // 12, date=today, note="Social media ads")
        from rms.models import AuditLog
        AuditLog.objects.all().delete()
        self.stdout.write(self.style.SUCCESS(
            "Demo data ready.  Password for everyone: demo1234  (owner: admin / admin123)\n"
            "  Owner          admin\n  Master manager khi_manager (sees ALL branches)   Branch managers lhe_manager, dxb_manager\n"
            "  Cashier        khi_cashier, lhe_cashier, dxb_cashier\n  Waiter         khi_waiter1/2, lhe_waiter1/2, dxb_waiter1/2\n"
            "  Chef           khi_kitchen, khi_kitchen2, lhe_kitchen, dxb_kitchen\n  Head chef/KDS  khi_kds, lhe_kds, dxb_kds\n"
            "  Storekeeper    khi_store, lhe_store, dxb_store\n  Delivery rider khi_rider1/2/3, lhe_rider1/2, dxb_rider1/2"))
