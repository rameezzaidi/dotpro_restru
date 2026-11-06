import json
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from django.urls import reverse

from .models import (Branch, Channel, Ingredient, MenuItem, Order, PurchaseOrder, Rider, Setting, Stock, Supplier, VoidLog,
                     Table, User)


class RmsFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def clear_kitchen(self):
        """close demo live orders so each test starts with an empty kitchen"""
        Order.objects.filter(status__in=["new", "preparing", "ready"]).update(status="completed")
        Table.objects.update(status="free")

    def login(self, username, pw="demo1234"):
        self.client.logout()
        self.assertTrue(self.client.login(username=username, password=pw), username)

    # ------------------------------------------------------------ pages render
    def test_all_pages_render_for_owner(self):
        self.login("admin", "admin123")
        names = ["dashboard", "pos", "orders", "kitchen", "tables", "menu_items", "branch_menu", "waiters", "riders",
                 "suppliers", "ingredients", "stock", "movements", "purchase_orders", "po_create", "transfers",
                 "channels", "reports", "branches", "staff", "settings", "tables_manage", "categories",
                 "bills", "shifts", "day_close", "voids", "reservations", "menu_items_add", "branches_add", "staff_add", "waiters_add", "riders_add", "suppliers_add",
                 "ingredients_add", "tables_manage_add", "channels_crud_add", "categories_add"]
        for mode in ("all", Branch.objects.get(code="KHI").pk, Branch.objects.get(code="DXB").pk):
            self.client.get(reverse("switch_branch", args=[mode]))
            for n in names:
                r = self.client.get(reverse(n), follow=True)
                self.assertEqual(r.status_code, 200, f"{n} in mode {mode}")
                self.assertNotIn(b"TemplateSyntaxError", r.content)
        o = Order.objects.first()
        self.client.get(reverse("switch_branch", args=["all"]))
        for n in ("order_detail", "receipt"):
            self.assertEqual(self.client.get(reverse(n, args=[o.pk])).status_code, 200)
        item = MenuItem.objects.first()
        self.assertEqual(self.client.get(reverse("recipe", args=[item.pk])).status_code, 200)

    def test_public_pages(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)
        self.assertEqual(self.client.get(reverse("site_branches")).status_code, 200)
        self.assertEqual(self.client.get(reverse("site_menu", args=["khi"])).status_code, 200)
        o = Order.objects.first()
        self.assertEqual(self.client.get(reverse("track", args=[o.number])).status_code, 200)

    # ------------------------------------------------------------ branch scoping
    def test_branch_isolation_and_master_access(self):
        khi = Branch.objects.get(code="KHI")
        lhe = Branch.objects.get(code="LHE")
        khi_order = Order.objects.filter(branch=khi).first()
        lhe_order = Order.objects.filter(branch=lhe).first()
        self.login("lhe_manager")
        self.assertEqual(self.client.get(reverse("order_detail", args=[lhe_order.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("order_detail", args=[khi_order.pk])).status_code, 404)
        self.client.get(reverse("switch_branch", args=[khi.pk]))          # branch manager cannot switch
        self.assertEqual(self.client.get(reverse("order_detail", args=[khi_order.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("branches"), follow=True).redirect_chain[-1][0], reverse("dashboard"))
        # master-branch manager sees everything
        self.login("khi_manager")
        self.client.get(reverse("switch_branch", args=["all"]))
        self.assertEqual(self.client.get(reverse("order_detail", args=[lhe_order.pk])).status_code, 200)
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, "Lahore")
        self.assertContains(r, "Dubai")
        self.client.get(reverse("switch_branch", args=[lhe.pk]))
        self.assertEqual(self.client.get(reverse("order_detail", args=[lhe_order.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("order_detail", args=[khi_order.pk])).status_code, 404)

    def test_roles(self):
        self.login("khi_waiter1")
        for n in ("staff", "reports", "stock", "channels", "settings"):
            r = self.client.get(reverse(n), follow=True)
            self.assertEqual(r.redirect_chain[-1][0], reverse("dashboard"), n)
        self.assertEqual(self.client.get(reverse("tables")).status_code, 200)
        self.login("khi_kitchen")
        self.assertEqual(self.client.get(reverse("kitchen")).status_code, 200)
        # kitchen staff have no POS: they are bounced to dashboard -> straight to their kitchen board
        self.assertEqual(self.client.get(reverse("pos"), follow=True).redirect_chain[-1][0], reverse("kitchen"))

    # ------------------------------------------------------------ order flows
    def _post_order(self, **extra):
        khi = Branch.objects.get(code="KHI")
        items = list(MenuItem.objects.filter(sku__in=["BRG-01", "DRK-03"]))
        body = {"order_type": "dine_in", "items": [{"id": i.pk, "qty": 2} for i in items], "discount": 0, **extra}
        return self.client.post(reverse("pos_submit"), json.dumps(body), content_type="application/json")

    def test_dine_in_flow_stock_and_table(self):
        self.clear_kitchen()
        self.login("khi_cashier")
        khi = Branch.objects.get(code="KHI")
        table = Table.objects.filter(branch=khi, status="free").exclude(
            orders__status__in=Order.ACTIVE).first()
        chicken = Ingredient.objects.get(name="Chicken")
        before = Stock.objects.get(branch=khi, ingredient=chicken).quantity
        r = self._post_order(table_id=table.pk)
        self.assertTrue(r.json()["ok"], r.content)
        o = Order.objects.get(pk=r.json()["id"])
        table.refresh_from_db()
        self.assertEqual(table.status, "occupied")
        # 2 x 650 + 2 x 120 = 1540 ; service 5% = 77 ; tax 15% on 1617 = 242.55
        self.assertEqual((o.subtotal, o.service_charge, o.tax, o.total),
                         (Decimal("1540.00"), Decimal("77.00"), Decimal("242.55"), Decimal("1859.55")))
        # auto-sent to a chef: preparing + timer started + stock deducted immediately
        self.assertEqual(o.status, "preparing")
        self.assertIsNotNone(o.chef)
        self.assertIsNotNone(o.started_at)
        self.assertEqual(Stock.objects.get(branch=khi, ingredient=chicken).quantity, before - Decimal("0.36"))
        act = lambda a, **kw: self.client.post(reverse("order_action", args=[o.pk]), {"action": a, **kw}, follow=True)
        act("advance")                               # preparing -> ready
        o.refresh_from_db()
        self.assertEqual(o.status, "ready")
        self.assertIsNotNone(o.ready_at)
        act("advance")                               # ready -> served (dine-in)
        o.refresh_from_db()
        self.assertEqual(o.status, "served")
        act("advance", method="card")                # served -> completed and paid
        o.refresh_from_db()
        self.assertEqual((o.status, o.is_paid, o.payment_method), ("completed", True, "card"))
        table.refresh_from_db()
        self.assertEqual(table.status, "cleaning")   # needs cleaning before the next guests
        self.client.post(reverse("table_status", args=[table.pk]), {"status": "free"})
        table.refresh_from_db()
        self.assertEqual(table.status, "free")
        self.assertEqual(Stock.objects.get(branch=khi, ingredient=chicken).quantity, before - Decimal("0.36"))

    def test_dine_in_requires_table_and_delivery_requires_address(self):
        self.login("khi_cashier")
        self.assertFalse(self._post_order().json()["ok"])
        self.assertFalse(self._post_order(order_type="delivery").json()["ok"])

    def test_delivery_flow_with_rider(self):
        from .models import Setting
        Setting.objects.filter(pk=1).update(auto_assign_rider=False)      # this test covers the manual path; auto path: AutomationTests
        self.clear_kitchen()
        self.login("khi_cashier")
        khi = Branch.objects.get(code="KHI")
        rider = Rider.objects.filter(branch=khi, status="available").first()
        r = self._post_order(order_type="delivery", phone="0300111", address="DHA", customer_name="X")
        o = Order.objects.get(pk=r.json()["id"])
        self.assertEqual((o.delivery_fee, o.service_charge, o.status), (khi.delivery_fee, 0, "preparing"))
        act = lambda a, **kw: self.client.post(reverse("order_action", args=[o.pk]), {"action": a, **kw}, follow=True)
        act("advance")                                # -> ready
        act("advance")                                # ready -> out requires a rider
        o.refresh_from_db(); self.assertEqual(o.status, "ready")
        act("assign_rider", rider=rider.pk)
        act("advance")
        o.refresh_from_db(); rider.refresh_from_db()
        self.assertEqual((o.status, rider.status), ("out_for_delivery", "busy"))
        act("advance", method="cash")
        o.refresh_from_db(); rider.refresh_from_db()
        self.assertEqual((o.status, o.is_paid, rider.status), ("completed", True, "available"))

    def test_kitchen_role_limits(self):
        self.clear_kitchen()
        self.login("khi_cashier")
        khi = Branch.objects.get(code="KHI")
        table = Table.objects.filter(branch=khi, status="free").first()
        oid = self._post_order(table_id=table.pk).json()["id"]
        order = Order.objects.get(pk=oid)
        mine, other = (("khi_kitchen", "khi_kitchen2") if order.chef.username == "khi_kitchen" else ("khi_kitchen2", "khi_kitchen"))
        self.login(other)                              # another chef cannot touch this order
        self.client.post(reverse("order_action", args=[oid]), {"action": "advance"}, follow=True)
        self.assertEqual(Order.objects.get(pk=oid).status, "preparing")
        self.login(mine)
        a = lambda: self.client.post(reverse("order_action", args=[oid]), {"action": "advance"}, follow=True)
        a()
        self.assertEqual(Order.objects.get(pk=oid).status, "ready")
        a()                                            # chef may not complete / serve
        self.assertEqual(Order.objects.get(pk=oid).status, "ready")

    # ------------------------------------------------------------ kitchen queue
    def _quick_order(self, branch, n=1):
        items = [{"item": MenuItem.objects.get(sku="DRK-03"), "name": "Soft Drink", "qty": 1, "price": 120}]
        from . import services as svc
        return [svc.create_order(branch=branch, order_type="takeaway", items=items, customer_name="q") for _ in range(n)]

    def test_one_chef_max_three_then_queue_then_auto_push(self):
        from . import services as svc
        self.clear_kitchen()
        lhe = Branch.objects.get(code="LHE")                       # exactly one chef
        orders = self._quick_order(lhe, 5)
        for o in orders:
            o.refresh_from_db()
        self.assertEqual([o.status for o in orders], ["preparing"] * 3 + ["new"] * 2)
        self.assertTrue(all(o.started_at for o in orders[:3]))
        self.assertIsNone(orders[3].started_at)
        self.assertEqual(svc.queue_position(orders[3]), 1)
        self.assertEqual(svc.queue_position(orders[4]), 2)
        svc.set_status(orders[0], "ready")                        # a slot frees -> FIFO order #4 starts by itself
        for o in orders:
            o.refresh_from_db()
        self.assertEqual([o.status for o in orders], ["ready", "preparing", "preparing", "preparing", "new"])
        self.assertIsNotNone(orders[3].chef)
        self.assertIsNotNone(orders[3].started_at)
        svc.set_status(orders[1], "cancelled")                    # cancelling also frees the slot
        orders[4].refresh_from_db()
        self.assertEqual(orders[4].status, "preparing")

    def test_two_chefs_balance_load(self):
        self.clear_kitchen()
        khi = Branch.objects.get(code="KHI")
        orders = self._quick_order(khi, 7)
        for o in orders:
            o.refresh_from_db()
        cooking = [o for o in orders if o.status == "preparing"]
        self.assertEqual(len(cooking), 6)                          # 2 chefs x 3
        self.assertEqual(sorted({o.chef.username for o in cooking}), ["khi_kitchen", "khi_kitchen2"])
        self.assertEqual(orders[-1].status, "new")                 # 7th waits

    def test_branch_without_chef_starts_immediately(self):
        self.clear_kitchen()
        dxb = Branch.objects.get(code="DXB")
        from .models import User
        User.objects.filter(branch=dxb, role="kitchen").delete()          # a branch with no chef accounts
        orders = self._quick_order(dxb, 5)
        for o in orders:
            o.refresh_from_db()
        self.assertTrue(all(o.status == "preparing" for o in orders))

    def test_kitchen_board_for_chef(self):
        self.clear_kitchen()
        lhe = Branch.objects.get(code="LHE")
        self._quick_order(lhe, 4)
        self.login("lhe_kitchen")
        r = self.client.get(reverse("kitchen"))
        self.assertContains(r, "3 / 3")
        self.assertContains(r, "data-since")
        self.assertContains(r, "#1 in queue")
        self.assertContains(r, "00:00:00")

    # ------------------------------------------------------------ integrations
    def test_api_orders(self):
        ch = Channel.objects.get(code="foodpanda")
        h = {"HTTP_X_API_KEY": ch.api_key}
        payload = {"branch": "LHE", "external_id": "FP-1", "order_type": "delivery",
                   "customer": {"name": "Ali", "phone": "0300", "address": "Gulberg"}, "paid": True,
                   "payment_method": "online",
                   "items": [{"sku": "BRG-01", "qty": 2}, {"name": "Special combo", "qty": 1, "price": 950}]}
        post = lambda p, **hh: self.client.post("/api/v1/orders/", json.dumps(p), content_type="application/json", **hh)
        self.assertEqual(post(payload).status_code, 401)
        r = post(payload, **h)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(r.json()["paid"])
        r2 = post(payload, **h)
        self.assertEqual((r2.status_code, r2.json().get("duplicate")), (200, True))
        self.assertEqual(Order.objects.filter(external_ref="FP-1").count(), 1)
        bad = dict(payload, external_id="FP-2", items=[{"sku": "NOPE", "qty": 1}])
        self.assertEqual(post(bad, **h).status_code, 422)
        st = self.client.get(f"/api/v1/orders/{r.json()['order_number']}/", **h)
        self.assertEqual(st.status_code, 200)
        m = self.client.get("/api/v1/menu/?branch=DXB", **h).json()
        self.assertEqual(m["currency"], "AED")
        self.assertTrue(all(i["price"] < 100 for i in m["items"]))   # Dubai price overrides

    def test_website_order(self):
        body = {"items": [{"id": MenuItem.objects.get(sku="BRG-01").pk, "qty": 1}], "order_type": "delivery",
                "name": "Guest", "phone": "0300", "address": "Somewhere"}
        r = self.client.post("/order/khi/submit/", json.dumps(body), content_type="application/json")
        self.assertTrue(r.json()["ok"], r.content)
        self.assertEqual(Order.objects.get(number=r.json()["number"]).source.code, "website")
        body["phone"] = ""
        self.assertFalse(self.client.post("/order/khi/submit/", json.dumps(body), content_type="application/json").json()["ok"])
        Setting.objects.filter(pk=1).update(online_ordering_enabled=False)
        self.assertEqual(self.client.get("/order/khi/").status_code, 404)

    # ------------------------------------------------------------ supply chain
    def test_purchase_receive_and_transfer(self):
        self.login("khi_manager")
        khi, lhe = Branch.objects.get(code="KHI"), Branch.objects.get(code="LHE")
        self.client.get(reverse("switch_branch", args=[khi.pk]))
        ing = Ingredient.objects.get(name="Cooking oil")
        sup = Supplier.objects.first()
        r = self.client.post(reverse("po_create"), {"branch": khi.pk, "supplier": sup.pk, "ingredient": [ing.pk], "qty": ["10"], "cost": ["500"]})
        po = PurchaseOrder.objects.latest("id")
        self.assertEqual(po.status, "ordered")
        q0 = Stock.objects.get(branch=khi, ingredient=ing).quantity
        self.client.post(reverse("po_action", args=[po.pk]), {"action": "receive"})
        self.client.post(reverse("po_action", args=[po.pk]), {"action": "receive"})   # double receive ignored
        self.assertEqual(Stock.objects.get(branch=khi, ingredient=ing).quantity, q0 + 10)
        l0 = Stock.objects.get(branch=lhe, ingredient=ing).quantity
        self.client.post(reverse("transfers"), {"from_branch": khi.pk, "to_branch": lhe.pk, "ingredient": ing.pk, "quantity": "4"})
        self.assertEqual(Stock.objects.get(branch=khi, ingredient=ing).quantity, q0 + 6)
        self.assertEqual(Stock.objects.get(branch=lhe, ingredient=ing).quantity, l0 + 4)
        # over-transfer is refused
        self.client.post(reverse("transfers"), {"from_branch": khi.pk, "to_branch": lhe.pk, "ingredient": ing.pk, "quantity": "99999"})
        self.assertEqual(Stock.objects.get(branch=khi, ingredient=ing).quantity, q0 + 6)

    # ------------------------------------------------------------ i18n / timezone / currency
    def test_language_timezone_currency(self):
        self.login("admin", "admin123")
        dxb = Branch.objects.get(code="DXB")
        self.client.get(reverse("switch_branch", args=[dxb.pk]))
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, 'dir="rtl"')                       # Dubai branch default language = Arabic
        self.assertContains(r, "لوحة التحكم")
        self.client.get(reverse("set_language", args=["en"]))
        self.assertContains(self.client.get(reverse("dashboard")), 'dir="ltr"')
        Setting.objects.filter(pk=1).update(language_selection_enabled=False)   # optional language selection OFF
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, 'dir="rtl"')                       # falls back to branch language
        self.assertNotContains(r, "/lang/ur/")
        r = self.client.get(reverse("orders"))
        self.assertContains(r, "AED")                             # currency per branch


class BillTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def test_bill_auto_generated_on_complete(self):
        from . import services as svc
        o = Order.objects.filter(status=Order.NEW, order_type=Order.TAKEAWAY).first() or \
            Order.objects.exclude(status__in=(Order.COMPLETED, Order.CANCELLED)).exclude(order_type=Order.DELIVERY).first()
        self.assertEqual(o.bill_number, "")
        svc.set_status(o, Order.READY)
        svc.set_status(o, Order.COMPLETED, None, "cash")
        o.refresh_from_db()
        self.assertTrue(o.bill_number.startswith(f"INV-{o.branch.code}-"))
        self.assertIsNotNone(o.billed_at)
        again = o.bill_number
        svc.generate_bill(o)
        self.assertEqual(o.bill_number, again)          # idempotent

    def test_numbers_unique_and_history_backfilled(self):
        nums = list(Order.objects.filter(status=Order.COMPLETED).values_list("bill_number", flat=True))
        self.assertTrue(all(nums))
        self.assertEqual(len(nums), len(set(nums)))

    def test_bills_page_and_redirect(self):
        self.client.login(username="admin", password="admin123")
        self.assertEqual(self.client.get("/bills/?from=2000-01-01").status_code, 200)
        o = Order.objects.exclude(status__in=(Order.COMPLETED, Order.CANCELLED)).exclude(order_type=Order.DELIVERY).exclude(acceptance="pending").first()
        o.status = Order.READY; o.save()
        r = self.client.post(f"/orders/{o.pk}/action/", {"action": "advance", "method": "cash"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/receipt/", r["Location"])
        o.refresh_from_db()
        self.assertContains(self.client.get(f"/orders/{o.pk}/receipt/"), o.bill_number)


class StockOnOrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def _setup(self):
        from . import services as svc
        from .models import Recipe
        b = Branch.objects.get(code="LHE")
        item = Recipe.objects.filter(item__branch_overrides__branch=b).first() if False else Recipe.objects.first().item
        recs = list(Recipe.objects.filter(item=item))
        for r in recs:
            Stock.objects.filter(branch=b, ingredient=r.ingredient).update(quantity=r.quantity * 10)
        return svc, b, item, recs

    def _line(self, item, qty):
        return [{"item": item, "name": item.name, "qty": qty, "price": 100}]

    def test_stock_removed_when_order_created_and_returned_on_cancel(self):
        svc, b, item, recs = self._setup()
        o = svc.create_order(branch=b, order_type=Order.TAKEAWAY, items=self._line(item, 2))
        for r in recs:
            self.assertEqual(Stock.objects.get(branch=b, ingredient=r.ingredient).quantity, r.quantity * 8)
        self.assertTrue(o.stock_deducted)
        o.refresh_from_db()
        if o.status == Order.NEW:                       # not started cooking -> cancel returns the stock
            svc.set_status(o, Order.CANCELLED)
            for r in recs:
                self.assertEqual(Stock.objects.get(branch=b, ingredient=r.ingredient).quantity, r.quantity * 10)

    def test_no_double_deduction_when_cooking_starts(self):
        svc, b, item, recs = self._setup()
        o = svc.create_order(branch=b, order_type=Order.TAKEAWAY, items=self._line(item, 1))
        svc.set_status(o, Order.READY)
        for r in recs:
            self.assertEqual(Stock.objects.get(branch=b, ingredient=r.ingredient).quantity, r.quantity * 9)

    def test_blocked_when_ingredient_short(self):
        svc, b, item, recs = self._setup()
        with self.assertRaises(svc.OrderError) as cm:
            svc.create_order(branch=b, order_type=Order.TAKEAWAY, items=self._line(item, 11))
        self.assertIn("Not enough stock", str(cm.exception))
        for r in recs:                                   # nothing was deducted
            self.assertEqual(Stock.objects.get(branch=b, ingredient=r.ingredient).quantity, r.quantity * 10)
        Setting.objects.filter(pk=1).update(block_if_out_of_stock=False)
        svc.create_order(branch=b, order_type=Order.TAKEAWAY, items=self._line(item, 11))   # allowed, goes negative



class RestaurantFlowTests(TestCase):
    """Standard restaurant flow: KOT rounds, served, bill request, void/cancel, split & multi payment, shifts, day close."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def setUp(self):
        from . import services as svc
        self.svc = svc
        Order.objects.filter(status__in=["new", "preparing", "ready", "served", "out_for_delivery"]).update(status="completed")
        Table.objects.update(status="free")
        from .models import Shift
        Shift.objects.filter(closed_at__isnull=True).update(closed_at=timezone.now())   # demo data leaves shifts open
        self.khi = Branch.objects.get(code="KHI")
        self.items = {m.sku: m for m in MenuItem.objects.filter(sku__in=["BRG-01", "DRK-03", "PIZ-01"])}
        self.cashier = User.objects.get(username="khi_cashier")
        self.manager = User.objects.get(username="khi_manager")
        self.waiter = User.objects.get(username="khi_waiter1")
        Setting.objects.filter(pk=1).update(block_if_out_of_stock=False)

    def line(self, sku, qty=1):
        m = self.items[sku]
        return {"item": m, "name": m.name, "qty": qty, "price": 100}

    def make(self, *lines, otype="dine_in", **kw):
        table = Table.objects.filter(branch=self.khi, status="free").first() if otype == "dine_in" else None
        return self.svc.create_order(branch=self.khi, order_type=otype, items=list(lines) or [self.line("BRG-01", 2)],
                                     table=table, user=self.cashier, **kw)

    def login(self, u):
        self.client.logout()
        self.assertTrue(self.client.login(username=u, password="demo1234"))

    # ---------------------------------------------------------------- dine-in cycle
    def test_add_items_new_kot_round_and_stock(self):
        o = self.make(self.line("BRG-01", 1))
        self.svc.set_status(o, "ready")
        patty = Ingredient.objects.filter(recipe__item=self.items["BRG-01"]).first()
        before = Stock.objects.get(branch=self.khi, ingredient=patty).quantity
        o = self.svc.add_items(o, [self.line("BRG-01", 2)], self.cashier)
        self.assertEqual(o.kot_round, 2)
        self.assertEqual(o.lines.count(), 2)
        self.assertEqual(o.lines.filter(round=2).first().qty, 2)
        self.assertIn(o.status, ("new", "preparing"))        # back to the kitchen
        after = Stock.objects.get(branch=self.khi, ingredient=patty).quantity
        self.assertLess(after, before)                         # only the new portions were deducted
        self.assertEqual(o.subtotal, Decimal("300.00"))
        self.assertEqual(self.client.get(reverse("kot", args=[o.pk])).status_code in (200, 302), True)

    def test_bill_request_served_and_table_cleaning(self):
        o = self.make()
        t = o.table
        self.svc.set_status(o, "ready")
        self.assertEqual(o.next_status, "served")
        self.svc.request_bill(o)
        t.refresh_from_db()
        self.assertEqual(t.status, "bill")
        self.svc.set_status(o, "served")
        self.svc.set_status(o, "completed", self.cashier, "cash")
        t.refresh_from_db()
        self.assertEqual(t.status, "cleaning")
        self.assertTrue(o.bill_number)

    def test_waiter_cannot_take_payment_or_complete(self):
        o = self.make()
        self.svc.set_status(o, "ready")
        self.login("khi_waiter1")
        r = self.client.post(reverse("order_action", args=[o.pk]), {"action": "pay", "method": "cash"}, follow=True)
        o.refresh_from_db()
        self.assertFalse(o.is_paid)
        self.client.post(reverse("order_action", args=[o.pk]), {"action": "advance"})          # ready -> served is allowed
        o.refresh_from_db()
        self.assertEqual(o.status, "served")
        self.client.post(reverse("order_action", args=[o.pk]), {"action": "advance", "method": "cash"})   # complete is not
        o.refresh_from_db()
        self.assertEqual(o.status, "served")

    def test_move_table(self):
        o = self.make()
        old = o.table
        new = Table.objects.filter(branch=self.khi, status="free").exclude(pk=old.pk).first()
        self.svc.move_table(o, new)
        old.refresh_from_db(); new.refresh_from_db()
        self.assertEqual((old.status, new.status), ("cleaning", "occupied"))

    # ---------------------------------------------------------------- void / cancel
    def test_void_before_cooking_restores_stock_and_logs(self):
        Order.objects.update()                                    # keep chefs busy so the order waits
        for c in User.objects.filter(role="kitchen", branch=self.khi):
            for _ in range(3):
                self.make(otype="takeaway")
        o = self.make(self.line("BRG-01", 2), self.line("DRK-03", 1))
        self.assertEqual(o.status, "new")
        ing = Ingredient.objects.filter(recipe__item=self.items["BRG-01"]).first()
        mid = Stock.objects.get(branch=self.khi, ingredient=ing).quantity
        line = o.lines.get(item=self.items["BRG-01"])
        with self.assertRaises(self.svc.OrderError):
            self.svc.void_line(o, line, "", self.cashier)         # reason required
        with self.assertRaises(self.svc.OrderError):
            self.svc.void_line(o, line, "Wrong item", self.waiter)   # waiters cannot void
        self.svc.void_line(o, line, "Wrong item punched", self.cashier)
        self.assertGreater(Stock.objects.get(branch=self.khi, ingredient=ing).quantity, mid)
        o.refresh_from_db()
        self.assertEqual(o.subtotal, Decimal("100.00"))
        log = VoidLog.objects.get(order=o)
        self.assertEqual((log.kind, log.reason, log.after_cooking), ("item", "Wrong item punched", False))

    def test_void_after_cooking_needs_manager(self):
        o = self.make(self.line("BRG-01", 1), self.line("DRK-03", 1))
        self.assertEqual(o.status, "preparing")
        line = o.lines.get(item=self.items["BRG-01"])
        with self.assertRaises(self.svc.OrderError):
            self.svc.void_line(o, line, "Customer complaint", self.cashier)
        self.svc.void_line(o, line, "Customer complaint", self.cashier, approver=self.manager)
        v = VoidLog.objects.get(order=o)
        self.assertTrue(v.after_cooking)
        self.assertEqual(v.approved_by, self.manager)
        # manager approval via credentials in the web form
        o2 = self.make(self.line("BRG-01", 1), self.line("DRK-03", 1))
        l2 = o2.lines.first()
        self.login("khi_cashier")
        self.client.post(reverse("order_action", args=[o2.pk]), {"action": "void_line", "line": l2.pk, "reason": "Duplicate order"})
        l2.refresh_from_db()
        self.assertFalse(l2.is_void)                                 # no approval -> refused
        self.client.post(reverse("order_action", args=[o2.pk]), {"action": "void_line", "line": l2.pk, "reason": "Duplicate order",
                                                                 "approver_user": "khi_manager", "approver_pass": "demo1234"})
        l2.refresh_from_db()
        self.assertTrue(l2.is_void)

    def test_cancel_needs_reason_refunds_payments(self):
        o = self.make()
        self.svc.add_payment(o, "cash", Decimal("100"), user=self.cashier)
        with self.assertRaises(self.svc.OrderError):
            self.svc.cancel_order(o, " ", self.manager)
        with self.assertRaises(self.svc.OrderError):
            self.svc.cancel_order(o, "Customer left", self.cashier)         # cooking + paid -> manager approval
        self.svc.cancel_order(o, "Customer left", self.cashier, approver=self.manager)
        o.refresh_from_db()
        self.assertEqual((o.status, o.cancel_reason, o.paid_total), ("cancelled", "Customer left", Decimal("0")))
        self.assertEqual(VoidLog.objects.filter(order=o, kind="refund").count(), 1)

    # ---------------------------------------------------------------- payments / split
    def test_multi_payment_tip_change_and_overpay(self):
        o = self.make(self.line("BRG-01", 2), otype="takeaway")
        total = o.total
        p1 = self.svc.add_payment(o, "cash", Decimal("100"), tendered=Decimal("150"), tip=Decimal("10"), user=self.cashier)
        self.assertEqual(p1.change, Decimal("50"))
        o.refresh_from_db()
        self.assertFalse(o.is_paid)
        with self.assertRaises(self.svc.OrderError):
            self.svc.add_payment(o, "card", total, user=self.cashier)          # more than the balance
        with self.assertRaises(self.svc.OrderError):
            self.svc.add_payment(o, "cash", Decimal("10"), tendered=Decimal("5"))   # not enough cash
        self.svc.add_payment(o, "card", user=self.cashier)                     # rest on card
        o.refresh_from_db()
        self.assertTrue(o.is_paid)
        self.assertEqual((o.payment_method, o.tip), ("mixed", Decimal("10.00")))
        self.assertEqual(o.paid_total, total)

    def test_split_bill_by_items(self):
        o = self.make(self.line("BRG-01", 2), self.line("DRK-03", 1))
        a = o.lines.get(item=self.items["BRG-01"])
        b = o.lines.get(item=self.items["DRK-03"])
        child = self.svc.split_order(o, {a.pk: 1, b.pk: 1}, self.cashier)
        o.refresh_from_db(); child.refresh_from_db()
        self.assertEqual(child.number, f"{o.number}-S1")
        self.assertEqual(child.parent, o)
        self.assertEqual(o.lines.count(), 1)
        self.assertEqual(child.lines.count(), 2)
        self.assertEqual(o.subtotal + child.subtotal, Decimal("300.00"))
        with self.assertRaises(self.svc.OrderError):
            self.svc.split_order(o, {o.lines.first().pk: 1}, self.cashier)      # must leave an item behind
        self.svc.add_payment(child, "cash", user=self.cashier)
        with self.assertRaises(self.svc.OrderError):
            self.svc.split_order(child, {child.lines.first().pk: 1}, self.cashier)   # already paid

    # ---------------------------------------------------------------- shifts / day close
    def test_shift_cash_reconciliation(self):
        sh = self.svc.open_shift(self.khi, self.cashier, Decimal("1000"))
        with self.assertRaises(self.svc.OrderError):
            self.svc.open_shift(self.khi, self.cashier, 0)
        o = self.make(self.line("BRG-01", 2), otype="takeaway")
        self.svc.add_payment(o, "cash", Decimal("100"), tip=Decimal("5"), user=self.cashier)
        self.svc.add_payment(o, "card", user=self.cashier)
        sh = self.svc.close_shift(sh, Decimal("1100"))
        self.assertEqual(sh.expected_cash, Decimal("1105.00"))
        self.assertEqual(sh.difference, Decimal("-5.00"))
        self.assertIsNotNone(sh.closed_at)
        self.login("khi_cashier")
        self.assertEqual(self.client.get(reverse("shift_report", args=[sh.pk])).status_code, 200)

    def test_require_shift_blocks_cashier_payment(self):
        Setting.objects.filter(pk=1).update(require_shift=True)
        o = self.make(otype="takeaway")
        with self.assertRaises(self.svc.OrderError):
            self.svc.add_payment(o, "cash", user=self.cashier)
        self.svc.open_shift(self.khi, self.cashier, 0)
        self.svc.add_payment(o, "cash", user=self.cashier)

    def test_day_close_blocks_and_reopens(self):
        from .models import DayClose
        o = self.make(otype="takeaway")
        with self.assertRaises(self.svc.OrderError):
            self.svc.close_day(self.khi, __import__("django.utils.timezone", fromlist=["x"]).localdate(), self.manager)   # open order
        self.svc.set_status(o, "ready"); self.svc.set_status(o, "completed", self.cashier, "cash")
        Order.objects.filter(branch=self.khi, status__in=Order.ACTIVE).update(status="completed")
        from django.utils import timezone
        self.svc.close_day(self.khi, timezone.localdate(), self.manager)
        with self.assertRaises(self.svc.OrderError):
            self.make(otype="takeaway")
        DayClose.objects.all().delete()
        self.make(otype="takeaway")

    # ---------------------------------------------------------------- supply chain
    def test_low_stock_creates_draft_pos_and_partial_receiving(self):
        from .models import PurchaseOrder
        PurchaseOrder.objects.filter(branch=self.khi).delete()
        ing = Ingredient.objects.filter(default_supplier__isnull=False).first()
        Stock.objects.filter(branch=self.khi, ingredient=ing).update(quantity=Decimal("0.5"), reorder_level=Decimal("10"))
        made, _ = self.svc.create_reorder_pos(self.khi, self.manager)
        po = [p for p in made if p.lines.filter(ingredient=ing).exists()][0]
        self.assertEqual(po.status, "draft")
        again, _ = self.svc.create_reorder_pos(self.khi, self.manager)          # no duplicates while one is open
        self.assertFalse([p for p in again if p.lines.filter(ingredient=ing).exists()])
        po.status = "ordered"; po.save()
        line = po.lines.get(ingredient=ing)
        half = (line.qty / 2).quantize(Decimal("0.001"))
        self.svc.receive_purchase_order(po, self.manager, {line.pk: half}, "INV-77")
        po.refresh_from_db()
        self.assertEqual((po.status, po.invoice_no), ("partial", "INV-77"))
        with self.assertRaises(self.svc.OrderError):
            self.svc.receive_purchase_order(po, self.manager, {line.pk: line.qty})   # more than pending
        self.svc.receive_purchase_order(po, self.manager)
        po.refresh_from_db()
        self.assertEqual(po.status, "received")
        self.assertEqual(Stock.objects.get(branch=self.khi, ingredient=ing).quantity, Decimal("0.5") + line.qty)

    def test_sold_out_items_flagged_and_blocked(self):
        Setting.objects.filter(pk=1).update(block_if_out_of_stock=True)
        item = self.items["BRG-01"]
        for r in item.recipe.all():
            Stock.objects.filter(branch=self.khi, ingredient=r.ingredient).update(quantity=0)
        m = {x["id"]: x for x in self.svc.branch_menu(self.khi)}
        self.assertEqual(m[item.pk]["max_qty"], 0)
        with self.assertRaises(self.svc.OrderError):
            self.make(self.line("BRG-01", 1))

    # ---------------------------------------------------------------- reservations & pages
    def test_reservation_seat_flow_and_pages(self):
        self.login("khi_cashier")
        o = self.make()
        for n, a in (("order_detail", [o.pk]), ("kot", [o.pk]), ("receipt", [o.pk]), ("tables", []), ("reservations", []),
                     ("shifts", [])):
            self.assertEqual(self.client.get(reverse(n, args=a)).status_code, 200, n)
        self.assertEqual(self.client.get(reverse("pos") + f"?add={o.pk}").status_code, 200)
        r = self.client.post(reverse("reservations"), {"name": "Test Guest", "guests": 3, "at": "2030-01-01T19:30"})
        self.assertEqual(r.status_code, 302)
        from .models import Reservation
        res = Reservation.objects.get(name="Test Guest")
        self.client.post(reverse("reservation_action", args=[res.pk]), {"action": "cancel"})
        res.refresh_from_db()
        self.assertEqual(res.status, "cancelled")
        # add-items through the web endpoint
        item = self.items["DRK-03"]
        r = self.client.post(reverse("order_add_items", args=[o.pk]), json.dumps({"items": [{"id": item.pk, "qty": 1}]}),
                             content_type="application/json")
        self.assertTrue(r.json()["ok"], r.content)


class KitchenDisplayAccountTests(TestCase):
    """Separate kitchen account (Kitchen Display / Head Chef): sees the whole kitchen, never gets orders assigned."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def test_kds_sees_all_chefs_marks_ready_but_cannot_complete(self):
        from . import services as svc
        khi = Branch.objects.get(code="KHI")
        kds = User.objects.get(username="khi_kds")
        self.assertEqual(kds.role, "kds")
        chef_ids = set(User.objects.filter(branch=khi, role="kitchen").values_list("pk", flat=True))
        self.assertNotIn(kds.pk, chef_ids)                       # not a cooking chef -> never receives orders
        self.assertTrue(self.client.login(username="khi_kds", password="demo1234"))
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)       # lands on the kitchen board
        r = self.client.get(reverse("kitchen"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Chef Imran")
        self.assertContains(r, "Chef Waseem")                    # every chef column, not just one
        o = Order.objects.filter(branch=khi, status="preparing").first()
        self.assertIsNotNone(o.chef)
        self.client.post(reverse("order_action", args=[o.pk]), {"action": "advance"})
        o.refresh_from_db()
        self.assertEqual(o.status, "ready")                      # can mark another chef's order ready
        self.client.post(reverse("order_action", args=[o.pk]), {"action": "advance"})
        o.refresh_from_db()
        self.assertEqual(o.status, "ready")                      # but cannot serve / complete
        self.assertEqual(self.client.get(reverse("pos"), follow=True).redirect_chain[-1][0], reverse("kitchen"))
        self.assertNotContains(self.client.get(reverse("kitchen")), "Start now (override)")


class IngredientModuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def setUp(self):
        from . import services as svc
        self.svc = svc
        self.khi = Branch.objects.get(code="KHI")
        self.burger = MenuItem.objects.get(sku="BRG-01")
        self.chicken = Ingredient.objects.get(name="Chicken")
        self.assertTrue(self.client.login(username="admin", password="admin123"))
        Order.objects.filter(status__in=["new", "preparing", "ready"]).update(status="completed")

    def pick(self, branch):
        self.client.get(reverse("switch_branch", args=[branch.pk]))

    def test_pages_render_in_branch_and_all_mode(self):
        for mode in (self.khi.pk, "all"):
            self.client.get(reverse("switch_branch", args=[mode]))
            for url in (reverse("ingredients"), reverse("ingredients") + "?show=low", reverse("ingredients") + "?cat=meat",
                        reverse("ingredient_detail", args=[self.chicken.pk]), reverse("recipes"),
                        reverse("recipes") + "?show=missing", reverse("recipe", args=[self.burger.pk]),
                        reverse("recipe", args=[self.burger.pk]) + f"?copy={MenuItem.objects.get(sku='BRG-02').pk}"):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 200, url)
        self.assertContains(self.client.get(reverse("ingredients")), "Chicken")

    def test_category_and_default_alert_level(self):
        self.assertEqual(self.chicken.category, "meat")
        self.chicken.default_reorder_level = Decimal("10")
        self.chicken.save()
        st = Stock.objects.get(branch=self.khi, ingredient=self.chicken)
        st.reorder_level, st.quantity = Decimal("0"), Decimal("7")
        st.save()
        st.refresh_from_db()
        self.assertEqual(st.level, Decimal("10"))
        self.assertTrue(st.is_low)

    def test_recipe_editor_saves_and_costs(self):
        self.pick(self.khi)
        buns = Ingredient.objects.get(name="Burger buns")
        r = self.client.post(reverse("recipe", args=[self.burger.pk]),
                             {"ingredient": [self.chicken.pk, buns.pk], "qty": ["0.2", "1"]})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.burger.recipe.count(), 2)
        self.assertEqual(self.burger.recipe_cost, Decimal("0.2") * self.chicken.unit_cost + buns.unit_cost)
        r = self.client.post(reverse("ingredient_detail", args=[Ingredient.objects.get(name="Lemon").pk]),
                             {"item": self.burger.pk, "qty": "0.01"})
        self.assertEqual(self.burger.recipe.count(), 3)

    def test_dish_alert_when_ingredient_low(self):
        Setting.objects.filter(pk=1).update(block_if_out_of_stock=True)
        rec = self.burger.recipe.select_related("ingredient").first()
        st = Stock.objects.get(branch=self.khi, ingredient=rec.ingredient)
        st.reorder_level, st.quantity = Decimal("10"), Decimal("9")
        st.save()
        menu = {m["id"]: m for m in self.svc.branch_menu(self.khi)}
        low = menu[self.burger.pk]["low"]
        self.assertIn(rec.ingredient.name, [x["name"] for x in low])
        self.pick(self.khi)
        self.assertContains(self.client.get(reverse("pos")), "ingredient")          # banner on POS
        # ordering the dish pushes the ingredient further down -> warning after the order
        o = self.svc.create_order(branch=self.khi, order_type="takeaway", user=None,
                                  items=[{"item": self.burger, "name": "x", "qty": 1, "price": 100}])
        alerts = self.svc.order_stock_alerts(o)
        self.assertTrue(any(rec.ingredient.name in a for a in alerts), alerts)
        # out of stock -> blocked with the ingredient named
        Stock.objects.filter(branch=self.khi, ingredient=rec.ingredient).update(quantity=0)
        with self.assertRaises(self.svc.OrderError) as cm:
            self.svc.create_order(branch=self.khi, order_type="takeaway",
                                  items=[{"item": self.burger, "name": "x", "qty": 1, "price": 100}])
        self.assertIn(rec.ingredient.name, str(cm.exception))

    def test_pos_submit_flashes_stock_alert(self):
        self.pick(self.khi)
        Setting.objects.filter(pk=1).update(block_if_out_of_stock=False)
        for r in self.burger.recipe.all():
            Stock.objects.filter(branch=self.khi, ingredient=r.ingredient).update(quantity=Decimal("0.5"), reorder_level=Decimal("5"))
        body = {"order_type": "takeaway", "items": [{"id": self.burger.pk, "qty": 1}], "discount": 0}
        r = self.client.post(reverse("pos_submit"), json.dumps(body), content_type="application/json")
        self.assertTrue(r.json()["ok"], r.content)
        page = self.client.get(r.json()["url"])
        self.assertContains(page, "Stock alert")


class AnalyticsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def test_forecast_model(self):
        from datetime import date, timedelta
        from . import analytics as an
        d0 = date(2026, 1, 1)
        dates = [d0 + timedelta(days=i) for i in range(42)]
        vals = [100.0 * (1.5 if (d0 + timedelta(days=i)).weekday() == 4 else 1) for i in range(42)]
        fc, conf = an.forecast_series(dates, vals, 7)
        fri = next(f for f in fc if f["date"].weekday() == 4)
        other = next(f for f in fc if f["date"].weekday() == 1)
        self.assertGreater(fri["value"], other["value"] * 1.3)
        self.assertEqual(len(fc), 7)
        self.assertGreater(an.backtest(dates, vals), 95)

    def test_pages_and_numbers(self):
        c = self.client
        c.login(username="khi_manager", password="demo1234")
        w = User.objects.get(username="khi_waiter1")
        r = Rider.objects.filter(branch__code="KHI").first()
        for url in ["/performance/", f"/performance/waiter/{w.pk}/", f"/performance/rider/{r.pk}/", "/analytics/menu/",
                    "/analytics/ai/", "/waiters/", "/riders/"]:
            self.assertEqual(c.get(url).status_code, 200, url)
        from . import analytics as an
        from django.utils import timezone
        from datetime import timedelta
        start = timezone.now() - timedelta(days=60)
        data = an.staff_profile_data("waiter", w, (timezone.localdate() - timedelta(days=60)), timezone.localdate(), start, timezone.now() + timedelta(days=1))
        done = Order.objects.filter(waiter=w, status="completed", created_at__gte=start)
        self.assertEqual(data["s"]["n"], done.count())
        self.assertEqual(data["s"]["tips"], sum(o.tip for o in done))
        self.assertGreater(data["s"]["tips"], 0)
        self.assertGreater(data["s"]["profit"], 0)

    def test_waiter_sees_only_own_profile(self):
        c = self.client
        c.login(username="khi_waiter1", password="demo1234")
        me = User.objects.get(username="khi_waiter1")
        other = User.objects.filter(role="waiter").exclude(pk=me.pk).first()
        self.assertEqual(c.get(f"/performance/waiter/{me.pk}/").status_code, 200)
        self.assertEqual(c.get(f"/performance/waiter/{other.pk}/").status_code, 404)
        self.assertEqual(c.get("/analytics/ai/").status_code, 302)

    def test_ai_ask_without_key(self):
        import os
        os.environ.pop("ANTHROPIC_API_KEY", None)
        self.client.login(username="khi_manager", password="demo1234")
        j = self.client.post("/analytics/ai/ask/", {"lang": "ur"}).json()
        self.assertIsNone(j["text"])
        self.assertIn("ANTHROPIC_API_KEY", j["error"])

    def test_dubai_cost_uses_branch_currency(self):
        from . import analytics as an
        from .models import Branch
        dxb = Branch.objects.get(code="DXB")
        khi = Branch.objects.get(code="KHI")
        from django.utils import timezone
        s = timezone.now() - __import__("datetime").timedelta(days=30)
        e = timezone.now()
        k = {r["sku"]: r for r in an.menu_rows(khi, s, e)[0]}["BRG-01"]
        d = {r["sku"]: r for r in an.menu_rows(dxb, s, e)[0]}["BRG-01"]
        self.assertAlmostEqual(float(d["cost"]) / float(k["cost"]), 0.01429, places=4)


class FeedbackTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def test_sentiment_and_themes(self):
        from . import feedback as fb
        s, th = fb.classify("Burger was cold and salty, waiter was rude", 1)
        self.assertEqual(s, -1)
        names = {t for t, p in th if p == "neg"}
        self.assertTrue({"temperature", "taste", "service"} <= names)
        s, th = fb.classify("Bohat mazedar khana, zabardast!", 5)
        self.assertEqual(s, 1)
        self.assertIn(("taste", "pos"), th)
        self.assertIn(("temperature", "neg"), fb.classify("drink was not cold")[1])
        self.assertIn(("speed", "neg"), fb.classify("Bohat dair lagi")[1])

    def test_public_form_saves_once(self):
        o = Order.objects.filter(status="completed", feedback__isnull=True).exclude(waiter=None).first()
        tok = o.ensure_feedback_token()
        url = f"/rate/{tok}/"
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        line = o.lines.filter(is_void=False).first()
        key = line.item_id
        r = self.client.post(url, {f"r_{key}": "2", f"c_{key}": "was cold", "service": "5", "comment": "nice waiter"})
        self.assertContains(r, "Thank you")
        from .models import Feedback
        fbk = Feedback.objects.get(order=o)
        self.assertEqual((fbk.service_rating, fbk.items.first().rating, fbk.items.first().comment), (5, 2, "was cold"))
        self.client.post(url, {"service": "1"})               # second submit is ignored
        self.assertEqual(Feedback.objects.filter(order=o).count(), 1)
        self.assertEqual(Feedback.objects.get(order=o).service_rating, 5)
        self.assertEqual(self.client.get("/rate/does-not-exist/").status_code, 404)

    def test_form_not_open_before_completion(self):
        o = Order.objects.exclude(status__in=["completed", "cancelled"]).first()
        r = self.client.get(f"/rate/{o.ensure_feedback_token()}/")
        self.assertContains(r, "opens once your order is completed")

    def test_waiter_tables_and_feedback(self):
        from . import analytics as an
        from django.utils import timezone
        from datetime import timedelta
        w = User.objects.get(username="khi_waiter1")
        end = timezone.now() + timedelta(days=1)
        start = end - timedelta(days=90)
        d = an.staff_profile_data("waiter", w, start.date(), end.date(), start, end)
        done = Order.objects.filter(waiter=w, status="completed", table__isnull=False, created_at__gte=start)
        self.assertEqual(sum(t["visits"] for t in d["tables"]), done.count())
        self.assertEqual(sum(t["earned"] for t in d["tables"]), sum(o.total - o.tax for o in done))
        self.assertGreater(d["fb"]["rated"], 0)
        self.client.login(username="khi_manager", password="demo1234")
        r = self.client.get(f"/performance/waiter/{w.pk}/")
        self.assertContains(r, "Earnings by table")

    def test_feedback_page_and_receipt_qr(self):
        self.client.login(username="khi_manager", password="demo1234")
        r = self.client.get("/feedback/")
        self.assertContains(r, "Rating per item")
        self.assertContains(r, "Chicken Tikka Wrap")            # seeded as the weak dish
        o = Order.objects.filter(status="completed", branch__code="KHI").first()
        r = self.client.get(f"/orders/{o.pk}/receipt/")
        self.assertContains(r, "<svg")
        self.assertContains(r, "/rate/")
        j = self.client.post("/feedback/ask/", {"lang": "ur"}).json()
        self.assertTrue(j["text"] is None or isinstance(j["text"], str))


class CrmPayPrintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def setUp(self):
        from . import services as svc
        self.svc = svc
        Order.objects.filter(status__in=["new", "preparing", "ready", "served", "out_for_delivery"]).update(status="completed")
        Table.objects.update(status="free")
        from .models import Shift
        Shift.objects.filter(closed_at__isnull=True).update(closed_at=timezone.now())
        self.khi = Branch.objects.get(code="KHI")
        self.cashier = User.objects.get(username="khi_cashier")
        self.item = MenuItem.objects.get(sku="BRG-01")

    def mk(self, phone="0300-9990001", qty=2, otype="takeaway"):
        line = {"item": self.item, "name": self.item.name, "qty": qty, "price": self.item.price}
        return self.svc.create_order(branch=self.khi, order_type=otype, items=[line], user=self.cashier,
                                     customer_name="Test Cust", phone=phone)

    # ---- income
    def test_waiter_income_breakdown(self):
        from . import analytics as an
        w = User.objects.get(username="khi_waiter1")
        w.monthly_salary, w.commission_percent = Decimal("30000"), Decimal("2")
        w.save()
        r = an.pay_breakdown(w, "waiter", 10, Decimal("50000"), 5, Decimal("1200"))
        self.assertEqual(r["salary_part"], Decimal("10000"))
        self.assertEqual(r["commission"], Decimal("1000"))
        self.assertEqual(r["total_income"], Decimal("12200"))
        self.client.login(username="khi_manager", password="demo1234")
        self.assertContains(self.client.get(f"/performance/waiter/{w.pk}/"), "TOTAL INCOME")

    # ---- customers + loyalty
    def test_customer_created_and_points_earned(self):
        from .models import Customer
        o = self.mk()
        c = Customer.objects.get(phone="3009990001")
        self.assertEqual(o.customer_id, c.pk)
        self.assertEqual(c.visits, 0)
        self.svc.set_status(o, "completed", self.cashier, "cash")
        c.refresh_from_db()
        self.assertEqual((c.visits, c.points), (1, int(o.total / 100)))
        o2 = self.mk(phone="+92 300 9990001")                       # same person, different formatting
        self.assertEqual(o2.customer_id, c.pk)
        self.assertEqual(Customer.objects.filter(phone="3009990001").count(), 1)

    def test_redeem_and_cancel_restores(self):
        from .models import Customer
        c = Customer.objects.create(name="R", phone="3001112223", points=500)
        o = self.mk(phone="03001112223", qty=3)
        total_before = o.total
        pts, value = self.svc.redeem_points(o, 200, self.cashier)
        o.refresh_from_db(); c.refresh_from_db()
        self.assertEqual((pts, value, c.points), (200, Decimal("200.00"), 300))
        self.assertLess(o.total, total_before)
        with self.assertRaises(self.svc.OrderError):
            self.svc.redeem_points(o, 9999, self.cashier)           # more than he has
        self.svc.cancel_order(o, "Customer left", User.objects.get(username="khi_manager"))
        c.refresh_from_db()
        self.assertEqual(c.points, 500)

    def test_redeem_blocked_after_payment(self):
        from .models import Customer
        Customer.objects.create(name="R", phone="3004445556", points=100)
        o = self.mk(phone="03004445556")
        self.svc.add_payment(o, "cash", Decimal("10"), user=self.cashier)
        with self.assertRaises(self.svc.OrderError):
            self.svc.redeem_points(o, 10, self.cashier)

    def test_customer_pages(self):
        from .models import Customer
        self.client.login(username="khi_manager", password="demo1234")
        c = Customer.objects.order_by("-visits").first()
        self.assertEqual(self.client.get("/customers/").status_code, 200)
        self.assertContains(self.client.get(f"/customers/{c.pk}/"), "Favourite items")
        r = self.client.post(f"/customers/{c.pk}/action/", {"action": "adjust", "points": "25", "reason": "Birthday"})
        c2 = Customer.objects.get(pk=c.pk)
        self.assertEqual(c2.points, c.points + 25)

    # ---- thermal printer
    def test_escpos_bytes(self):
        from . import escpos
        o = self.mk()
        self.svc.set_status(o, "completed", self.cashier, "cash")
        o.refresh_from_db()
        data = escpos.build_receipt(o, list(o.lines.all()), list(o.payments.all()), 42, "http://x/rate/abc/")
        self.assertTrue(data.startswith(b"\x1b@"))
        self.assertIn(o.bill_number.encode(), data)
        self.assertIn(b"\x1d(k", data)                       # QR commands
        self.assertTrue(data.rstrip(b"\n").endswith(b"\x1dV\x01") or b"\x1dV" in data[-8:])
        kot = escpos.build_kot(o, list(o.lines.all()), 1, 32)
        self.assertIn(b"KITCHEN ORDER", kot)
        self.assertEqual(escpos.ascii_text("Café زبردست"), "Cafe ??????")  # no crash on Urdu

    def test_print_to_network_printer(self):
        import socket
        import threading
        srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
        port = srv.getsockname()[1]
        got = []

        def serve():
            conn, _ = srv.accept()
            buf = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            got.append(buf)
        t = threading.Thread(target=serve); t.start()
        Branch.objects.filter(pk=self.khi.pk).update(receipt_printer_host="127.0.0.1", receipt_printer_port=port)
        o = self.mk()
        self.client.login(username="khi_cashier", password="demo1234")
        r = self.client.post(f"/orders/{o.pk}/print/kot/", follow=True)
        t.join(5); srv.close()
        self.assertContains(r, "Sent to the thermal printer")
        self.assertIn(b"KITCHEN ORDER", got[0])
        d = self.client.get(f"/orders/{o.pk}/escpos/receipt/")
        self.assertEqual(d["Content-Type"], "application/octet-stream")
        Branch.objects.filter(pk=self.khi.pk).update(receipt_printer_host="")
        r = self.client.post(f"/orders/{o.pk}/print/kot/", follow=True)
        self.assertContains(r, "No printer IP")

    # ---- payment gateway
    def test_sandbox_payment_flow(self):
        o = self.mk(qty=1, otype="takeaway")
        self.client.login(username="khi_cashier", password="demo1234")
        self.client.post(f"/orders/{o.pk}/pay-link/")
        from .models import PaymentLink
        link = PaymentLink.objects.get(order=o)
        self.assertEqual((link.provider, link.amount), ("sandbox", o.due))
        anon = self.client.__class__()
        self.assertContains(anon.get(f"/pay/{link.token}/"), "Pay now")
        anon.post(f"/pay/{link.token}/start/")
        anon.post(f"/pay/{link.token}/sandbox/", {"outcome": "success"})
        o.refresh_from_db(); link.refresh_from_db()
        self.assertEqual(link.status, "paid")
        self.assertTrue(o.is_paid)
        self.assertEqual(o.payments.get().method, "online")
        anon.post(f"/pay/{link.token}/sandbox/", {"outcome": "success"})    # replay does nothing
        self.assertEqual(o.payments.count(), 1)

    def test_failed_payment_leaves_order_unpaid(self):
        o = self.mk(qty=1)
        from . import gateway
        link = gateway.create_link(o)
        self.client.post(f"/pay/{link.token}/sandbox/", {"outcome": "fail"})
        o.refresh_from_db(); link.refresh_from_db()
        self.assertEqual((link.status, o.is_paid), ("failed", False))

    def test_gateway_off(self):
        from . import gateway
        Setting.objects.filter(pk=1).update(payment_gateway="none")
        with self.assertRaises(gateway.GatewayError):
            gateway.create_link(self.mk())

    def test_stripe_signature_and_webhook(self):
        import hashlib, hmac, json, os, time
        from . import gateway
        from .models import PaymentLink
        Setting.objects.filter(pk=1).update(payment_gateway="stripe")
        o = self.mk(qty=1)
        link = gateway.create_link(o)
        body = json.dumps({"type": "checkout.session.completed", "data": {"object": {
            "client_reference_id": link.token, "payment_status": "paid", "payment_intent": "pi_123", "id": "cs_1"}}}).encode()
        secret = "whsec_test"
        t = int(time.time())
        sig = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
        self.assertTrue(gateway.verify_stripe_signature(body, f"t={t},v1={sig}", secret))
        self.assertFalse(gateway.verify_stripe_signature(body + b"x", f"t={t},v1={sig}", secret))
        self.assertFalse(gateway.verify_stripe_signature(body, f"t={t - 9999},v1={sig}", secret))
        os.environ["STRIPE_WEBHOOK_SECRET"] = secret
        try:
            bad = self.client.post("/webhooks/stripe/", body, content_type="application/json", HTTP_STRIPE_SIGNATURE="t=1,v1=00")
            self.assertEqual(bad.status_code, 400)
            ok = self.client.post("/webhooks/stripe/", body, content_type="application/json", HTTP_STRIPE_SIGNATURE=f"t={t},v1={sig}")
            self.assertEqual(ok.status_code, 200)
        finally:
            os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
        o.refresh_from_db(); link.refresh_from_db()
        self.assertTrue(o.is_paid)
        self.assertEqual(link.status, "paid")

    def test_order_page_shows_new_cards(self):
        o = self.mk()
        self.client.login(username="khi_manager", password="demo1234")
        r = self.client.get(f"/orders/{o.pk}/")
        for text in ("Thermal printer", "Online payment", "Customer"):
            self.assertContains(r, text)
        self.assertEqual(self.client.get(f"/track/{o.number}/").status_code, 200)
        self.assertContains(self.client.get("/settings/"), "loyalty")


class ReportTablesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def test_report_js_and_context_in_base(self):
        self.client.login(username="khi_manager", password="demo1234")
        r = self.client.get("/orders/")
        self.assertContains(r, "rms/report.js")
        self.assertContains(r, "data-business=")
        self.assertContains(r, 'data-server="1"')
        self.assertContains(r, "data-sum")

    def test_table_pages_render(self):
        self.client.login(username="khi_manager", password="demo1234")
        for u in ("/performance/", "/bills/", "/customers/", "/ingredients/", "/reports/",
                  "/voids/", "/stock/", "/feedback/", "/analytics/menu/", "/shifts/"):
            self.assertEqual(self.client.get(u).status_code, 200, u)


class PremiumSiteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def test_public_pages_use_premium_theme(self):
        self.assertContains(self.client.get("/order/"), "rms/site.css")
        r = self.client.get("/order/KHI/")
        self.assertContains(r, "sx-cart"); self.assertContains(r, 'id="q"')
        o = Order.objects.filter(branch__code="KHI").first()
        t = self.client.get(f"/track/{o.number}/")
        self.assertContains(t, "sx-steps"); self.assertContains(t, o.number)


class KitchenStockTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def test_chef_panel_request_and_approval(self):
        from .models import StockRequest, PurchaseOrder, Ingredient, Supplier
        ing = Ingredient.objects.filter(default_supplier__isnull=False).first()
        self.client.login(username="khi_kitchen", password="demo1234")
        r = self.client.get("/kitchen/")
        self.assertContains(r, "done today"); self.assertContains(r, "Need more stock?")
        self.client.post("/stock/request/", {"ingredient": ing.pk, "qty": "5", "urgent": "on"})
        req = StockRequest.objects.get()
        self.assertEqual((req.status, req.urgent), ("pending", True))
        self.client.post("/stock/request/", {"ingredient": ing.pk, "qty": "8"})          # duplicate -> updated, not doubled
        self.assertEqual(StockRequest.objects.count(), 1)
        self.assertEqual(self.client.get("/stock/requests/").status_code, 302)           # chef cannot open manager page
        self.client.logout(); self.client.login(username="khi_manager", password="demo1234")
        self.assertContains(self.client.get("/stock/requests/"), ing.name)
        self.client.post(f"/stock/requests/{req.pk}/action/", {"action": "approve", "qty": "8"})
        req.refresh_from_db()
        self.assertEqual(req.status, "approved")
        self.assertEqual(req.po.status, "draft")
        self.assertEqual(req.po.lines.get(ingredient=ing).qty, 8)
        self.client.login(username="khi_kitchen", password="demo1234")
        self.assertContains(self.client.get("/kitchen/"), req.po.number)


class BellNotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def test_request_notifies_managers_and_decision_notifies_chef(self):
        from .models import Ingredient, Notification, StockRequest, User
        ing = Ingredient.objects.filter(default_supplier__isnull=False).first()
        chef = self.client
        chef.login(username="khi_kitchen", password="demo1234")
        chef.post("/stock/request/", {"ingredient": ing.pk, "qty": "3", "urgent": "on"})
        mgr = User.objects.get(username="khi_manager")
        n = Notification.objects.filter(user=mgr).first()
        self.assertIn("URGENT", n.title); self.assertEqual(n.kind, "urgent")
        self.assertTrue(Notification.objects.filter(user__username="admin").exists())
        self.assertFalse(Notification.objects.filter(user__username="lhe_manager").exists())   # other branch not alerted
        self.assertFalse(Notification.objects.filter(user__username="khi_kitchen").exists())
        chef.logout(); chef.login(username="khi_manager", password="demo1234")
        feed = chef.get("/notifications/feed/").json()
        self.assertEqual([i["title"] for i in feed["items"] if "stock request" in i["title"].lower()].__len__(), 1)   # engine alerts may also be present
        req = StockRequest.objects.get()
        chef.post(f"/stock/requests/{req.pk}/action/", {"action": "reject", "note": "enough in central store"})
        chef.post("/notifications/read/", {})
        self.assertEqual(chef.get("/notifications/feed/").json()["unread"], 0)
        chef.logout(); chef.login(username="khi_kitchen", password="demo1234")
        f = chef.get("/notifications/feed/").json()
        self.assertEqual(f["unread"], 1); self.assertIn("rejected", f["items"][0]["title"])
        self.assertContains(chef.get("/kitchen/"), "enough in central store")
        self.assertContains(chef.get("/kitchen/"), 'id="bell"')


class AutomationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def setUp(self):
        from .models import Setting
        from . import services as svc
        self.svc = svc
        self.khi = Branch.objects.get(code="KHI")
        Order.objects.filter(status__in=Order.ACTIVE).update(status="cancelled")      # quiet kitchen

    def mk(self, otype="takeaway", phone="0300 1234567", **kw):
        from .models import MenuItem
        m = MenuItem.objects.filter(name__icontains="Burger").first()
        extra = {"address": "Clifton"} if otype == "delivery" else {}
        return self.svc.create_order(branch=self.khi, order_type=otype, phone=phone, customer_name="Ali Khan",
                                     items=[{"item": m, "name": m.name, "qty": 1, "price": m.price}], **extra, **kw)

    def test_phone_normalising(self):
        from .messaging import e164
        self.assertEqual(e164("0300 1234567", "PK"), "+923001234567")
        self.assertEqual(e164("+92 300 1234567"), "+923001234567")
        self.assertEqual(e164("3001234567", "PK"), "+923001234567")
        self.assertEqual(e164("12", "PK"), "")

    def test_customer_messages_through_order_life(self):
        from .models import MessageLog
        o = self.mk("delivery")
        self.assertEqual(MessageLog.objects.filter(order=o, kind="order_received").count(), 1)
        self.svc.set_status(o, Order.READY)
        o.refresh_from_db()
        self.assertIsNotNone(o.rider_id)                                              # rider auto-assigned
        self.svc.set_status(o, Order.OUT)
        self.svc.set_status(o, Order.COMPLETED, method="cash")
        kinds = set(MessageLog.objects.filter(order=o).values_list("kind", flat=True))
        self.assertTrue({"order_received", "order_out", "order_completed"} <= kinds)
        self.assertTrue(all(m.to.startswith("+92") for m in MessageLog.objects.filter(order=o)))
        n = MessageLog.objects.filter(order=o).count()
        from . import automation as au
        au.on_status(o, Order.COMPLETED)                                              # de-duplicated
        self.assertEqual(MessageLog.objects.filter(order=o).count(), n)

    def test_no_message_without_phone_or_when_off(self):
        from .models import MessageLog, Setting
        self.mk(phone="")
        self.assertEqual(MessageLog.objects.count(), 0)
        Setting.objects.filter(pk=1).update(msg_customer_events=False)
        self.mk()
        self.assertEqual(MessageLog.objects.count(), 0)

    def test_twilio_provider_sends(self):
        import os
        from unittest import mock
        from .models import MessageLog, Setting
        Setting.objects.filter(pk=1).update(msg_provider="twilio")
        env = {"TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "t", "TWILIO_WHATSAPP_FROM": "whatsapp:+14155238886"}
        with mock.patch.dict(os.environ, env), mock.patch("rms.messaging._post", return_value=201) as p:
            self.mk()
        self.assertTrue(p.called)
        self.assertEqual(p.call_args[0][1]["To"], "whatsapp:+923001234567")
        self.assertEqual(MessageLog.objects.first().status, "sent")
        with mock.patch.dict(os.environ, env), mock.patch("rms.messaging._post", side_effect=OSError("down")):
            self.mk(phone="0301 7654321")
        self.assertEqual(MessageLog.objects.first().status, "failed")                 # a failure never breaks the order

    def test_late_queue_table_shift_alerts_dedupe(self):
        from datetime import timedelta
        from django.utils import timezone
        from .models import AutomationEvent, Notification, Shift, Table, User
        from . import automation as au
        o = self.mk("takeaway")
        Order.objects.filter(pk=o.pk).update(status="preparing", started_at=timezone.now() - timedelta(minutes=60))
        t = Table.objects.filter(branch=self.khi).first()
        Table.objects.filter(pk=t.pk).update(status="cleaning")
        AutomationEvent.objects.update_or_create(key=f"clean:{t.pk}", defaults={"created_at": timezone.now() - timedelta(minutes=30)})
        cashier = User.objects.get(username="khi_cashier")
        Shift.objects.filter(closed_at__isnull=True, branch=self.khi).delete()
        Shift.objects.create(branch=self.khi, user=cashier, opened_at=timezone.now() - timedelta(hours=20))
        before = Notification.objects.count()
        self.assertGreaterEqual(au.job_alerts(), 3)
        self.assertGreater(Notification.objects.count(), before)
        self.assertEqual(au.job_alerts(), 0)                                          # same things are not alerted twice
        titles = " ".join(Notification.objects.values_list("title", flat=True))
        self.assertIn("running late", titles); self.assertIn("still needs cleaning", titles); self.assertIn("still open", titles)

    def test_low_rating_alert(self):
        from .models import Feedback, Notification
        o = self.mk(); self.client.get(f"/track/{o.number}/")
        self.svc.set_status(o, Order.COMPLETED, method="cash")
        tok = Order.objects.get(pk=o.pk).feedback_token
        before = Notification.objects.filter(title__startswith="Low rating").count()
        self.client.post(f"/rate/{tok}/", {"service": "1", "comment": "cold food"})
        self.assertEqual(Notification.objects.filter(title__startswith="Low rating").count() - before > 0, True)

    def test_auto_reorder_and_supplier_po_message(self):
        from .models import MessageLog, PurchaseOrder, Setting, Stock, Supplier
        Setting.objects.filter(pk=1).update(auto_reorder=True)
        st = Stock.objects.filter(branch=self.khi, ingredient__default_supplier__isnull=False).first()
        Stock.objects.filter(pk=st.pk).update(quantity=0, reorder_level=10)
        PurchaseOrder.objects.filter(branch=self.khi, status__in=("draft", "ordered")).delete()
        from . import automation as au
        self.assertGreaterEqual(au.job_reorder(), 1)
        po = PurchaseOrder.objects.filter(branch=self.khi, status="draft").first()
        Supplier.objects.filter(pk=po.supplier_id).update(phone="0321 1112223", email="s@example.com")
        self.client.login(username="khi_manager", password="demo1234")
        self.client.post(f"/purchase-orders/{po.pk}/action/", {"action": "send"})
        got = MessageLog.objects.filter(kind="purchase_order")
        self.assertEqual({m.channel for m in got}, {"email", "whatsapp"})
        self.assertIn(po.number, got.first().body)

    def test_daily_report_and_hour_gate(self):
        from .models import DailyReport, Setting
        from . import automation as au
        Setting.objects.filter(pk=1).update(daily_report_hour=0)
        self.assertGreaterEqual(au.job_daily_reports(), 1)
        self.assertTrue(DailyReport.objects.filter(branch=self.khi).exists())
        self.assertEqual(au.job_daily_reports(), 0)                                   # once per branch per day
        self.client.login(username="khi_manager", password="demo1234")
        self.assertContains(self.client.get("/automation/reports/"), "Sales:")

    def test_winback(self):
        from datetime import timedelta
        from django.utils import timezone
        from .models import Customer, MessageLog, Setting
        from . import automation as au
        Setting.objects.filter(pk=1).update(winback_enabled=True, winback_days=30)
        Customer.objects.all().update(last_visit=timezone.now() - timedelta(days=5))
        c = Customer.objects.create(phone="3004445556", name="Sara Z", visits=3, last_visit=timezone.now() - timedelta(days=45))
        self.assertEqual(au.job_winback(), 1)
        self.assertIn("Sara", MessageLog.objects.get(kind="winback").body)
        self.assertEqual(au.job_winback(), 0)                                         # not nagged again within 60 days

    def test_happy_hour_changes_price_everywhere(self):
        from .models import PriceRule
        m = next(x for x in self.svc.branch_menu(self.khi))
        base = m["price"]
        PriceRule.objects.create(name="All day test", branch=self.khi, start_hour=0, end_hour=24, percent=20)
        m2 = next(x for x in self.svc.branch_menu(self.khi) if x["id"] == m["id"])
        self.assertAlmostEqual(m2["price"], round(base * 0.8, 2), places=2)
        self.assertEqual(m2["promo"], 20.0)
        o = self.svc.create_order(branch=self.khi, order_type="takeaway", items=[{"item": None, "name": "x", "qty": 1, "price": m2["price"]}])
        self.assertEqual(float(o.subtotal), m2["price"])
        PriceRule.objects.all().update(is_active=False)
        self.assertEqual(next(x for x in self.svc.branch_menu(self.khi) if x["id"] == m["id"])["price"], base)

    def test_menu_sync_pushes_only_on_change(self):
        from unittest import mock
        from .models import Channel
        from . import automation as au
        Channel.objects.filter(code="foodpanda").update(webhook_url="https://example.com/hook")
        if not Channel.objects.filter(webhook_url__gt="").exists():
            Channel.objects.create(name="Test app", code="testapp", kind="aggregator", webhook_url="https://example.com/hook")
        with mock.patch("rms.messaging.sync_webhook", return_value=(True, "")) as p:
            first = au.job_menu_sync()
            second = au.job_menu_sync()
        self.assertGreaterEqual(first, 1); self.assertEqual(second, 0)
        sent = p.call_args_list[0][0][1]
        self.assertIn("items", sent); self.assertIn("available", sent["items"][0])

    def test_smart_order_slips_pages_and_cron(self):
        import os
        from unittest import mock
        self.client.login(username="khi_manager", password="demo1234")
        for u in ("/automation/", "/automation/smart-order/", "/automation/slips/", "/automation/reports/"):
            self.assertEqual(self.client.get(u).status_code, 200, u)
        from .models import Rider
        r = Rider.objects.filter(branch=self.khi).first()
        self.assertContains(self.client.get(f"/automation/slips/rider/{r.pk}/"), "Total payable")
        self.assertEqual(self.client.get("/cron/tick/").status_code, 403)
        with mock.patch.dict(os.environ, {"CRON_SECRET": "abc"}):
            self.assertEqual(self.client.get("/cron/tick/?key=wrong").status_code, 403)
            self.assertEqual(self.client.get("/cron/tick/?key=abc").status_code, 200)
            self.assertEqual(self.client.get("/cron/tick/", HTTP_AUTHORIZATION="Bearer abc").status_code, 200)
        self.client.logout(); self.client.login(username="khi_waiter1", password="demo1234")
        self.assertEqual(self.client.get("/automation/").status_code, 302)           # waiters cannot open it

    def test_smart_order_creates_draft_po(self):
        from .models import Ingredient, PurchaseOrder, Recipe, Stock
        self.client.login(username="khi_manager", password="demo1234")
        ing = Ingredient.objects.filter(default_supplier__isnull=False).first()
        PurchaseOrder.objects.filter(branch=self.khi).delete()
        r = self.client.post("/automation/smart-order/", {"pick": [ing.pk], f"qty_{ing.pk}": "12"})
        po = PurchaseOrder.objects.get(branch=self.khi, status="draft")
        self.assertEqual(po.lines.get(ingredient=ing).qty, 12)


class AITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def setUp(self):
        from django.contrib.auth import get_user_model
        from . import services as svc
        self.svc = svc
        self.khi = Branch.objects.get(code="KHI")
        self.mgr = get_user_model().objects.get(username="owner") if get_user_model().objects.filter(username="owner").exists() else \
            get_user_model().objects.filter(role="owner").first()
        self.client.force_login(self.mgr)

    def test_rule_parser_english_and_urdu(self):
        from . import ai
        menu = self.svc.branch_menu(self.khi)
        r = ai.parse_order("do zinger burgar, teen fries please", menu)
        got = {l["name"]: l["qty"] for l in r["lines"]}
        self.assertEqual(got.get("Zinger Burger"), 2)
        self.assertEqual(got.get("Loaded Fries"), 3)
        r = ai.parse_order("2 zinger burger aur 1 xyzzy", menu)
        self.assertEqual(r["lines"][0]["qty"], 2)
        self.assertIn("xyzzy", r["unmatched"][0])
        self.assertEqual(ai.parse_order("", menu)["lines"], [])

    def test_llm_order_parse_is_validated(self):
        from unittest import mock
        from . import ai
        menu = self.svc.branch_menu(self.khi)
        ok_id = menu[0]["id"]
        fake = json.dumps({"items": [{"id": ok_id, "qty": 2}, {"id": 999999, "qty": 5}], "unmatched": []})
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}), mock.patch.object(ai, "_call", return_value=fake):
            r = ai.parse_order("anything", menu)
        self.assertEqual(r["engine"], "claude")
        self.assertEqual([(l["id"], l["qty"]) for l in r["lines"]], [(ok_id, 2)])       # unknown id dropped
        from .models import AILog
        self.assertTrue(AILog.objects.filter(kind="order_parse", used_llm=True, ok=True).exists())

    def test_llm_failure_falls_back(self):
        from unittest import mock
        from . import ai
        menu = self.svc.branch_menu(self.khi)
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}), mock.patch.object(ai, "_call", side_effect=RuntimeError("boom")):
            r = ai.parse_order("2 zinger burger", menu)
        self.assertEqual(r["engine"], "rules")
        self.assertEqual(r["lines"][0]["qty"], 2)
        from .models import AILog
        self.assertTrue(AILog.objects.filter(ok=False, error__contains="boom").exists())

    def test_order_endpoints(self):
        self.client.logout()
        self.assertTrue(self.client.login(username="khi_cashier", password="demo1234"))
        r = self.client.post("/ai/order/", {"text": "teen zinger burger"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["lines"][0]["qty"], 3)
        b = Branch.objects.filter(online_ordering=True).first()
        self.client.logout()
        r = self.client.post(f"/order/{b.code}/ai/", {"text": "2 zinger burger"})
        self.assertTrue(r.json()["ok"])

    def test_copilot_rules_roman_urdu(self):
        from . import ai
        r = ai.copilot(self.khi, "aaj ki sale kitni hai?")
        self.assertEqual(r["engine"], "rules")
        self.assertIn("sale", r["text"].lower())
        r = ai.copilot(self.khi, "how is stock?")
        self.assertTrue(r["text"])
        self.client.force_login(self.mgr)
        j = self.client.post("/ai/copilot/", {"q": "kal ki forecast?"}).json()
        self.assertTrue(j["text"])

    def test_copilot_uses_claude_with_snapshot(self):
        from unittest import mock
        from . import ai
        seen = {}

        def fake(system, user, mt):
            seen["user"] = user
            return "Aaj achi sale hai."
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}), mock.patch.object(ai, "_call", side_effect=fake):
            r = ai.copilot(self.khi, "aaj kya hua?")
        self.assertEqual(r["engine"], "claude")
        self.assertIn("last_7_days", seen["user"])

    def test_upsell(self):
        from . import ai
        burger = MenuItem.objects.filter(name__icontains="Zinger").first()
        out = ai.upsell(self.khi, [burger.pk])
        self.assertTrue(0 < len(out) <= 3)
        self.assertNotIn(burger.pk, [o["id"] for o in out])
        self.assertEqual(ai.upsell(self.khi, []), [])
        r = self.client.get(f"/ai/upsell/?ids={burger.pk}")        # logged-in owner but no branch -> empty is fine
        self.assertEqual(r.status_code, 200)

    def test_menu_advisor_apply_price(self):
        from .models import BranchItem
        item = MenuItem.objects.first()
        r = self.client.post("/ai/apply-price/?b=%d" % self.khi.pk, {"item": item.pk, "price": "999"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(BranchItem.objects.get(branch=self.khi, item=item).price, Decimal("999"))
        self.assertEqual(self.client.post("/ai/apply-price/?b=%d" % self.khi.pk, {"item": item.pk, "price": "abc"}).status_code, 302)
        self.assertEqual(self.client.get("/ai/").status_code, 200)
        self.assertEqual(self.client.get("/ai/?prep=1&replies=1&summary=1").status_code, 200)

    def test_reply_draft_and_send(self):
        from . import ai
        from .models import Feedback, MessageLog
        o = Order.objects.filter(branch=self.khi, status="completed").first() or Order.objects.filter(branch=self.khi).first()
        o.customer_phone, o.customer_name = "03001234567", "Ali Raza"
        o.save()
        Feedback.objects.filter(order=o).delete()
        fb = Feedback.objects.create(order=o, branch=self.khi, service_rating=1, comment="cold food")
        d = ai.reply_drafts(self.khi)
        mine = [x for x in d if x["id"] == fb.pk]
        self.assertTrue(mine and "Ali" in mine[0]["draft"])
        r = self.client.post("/ai/send-reply/?b=%d" % self.khi.pk, {"fb": fb.pk, "text": "Sorry Ali"})
        self.assertTrue(r.json()["ok"])
        self.assertTrue(MessageLog.objects.filter(kind="feedback_reply").exists())

    def test_whatsapp_two_step(self):
        from .models import AISession, Setting
        b = Branch.objects.filter(online_ordering=True).first()
        st = Setting.get()
        r = self.client.post("/ai/whatsapp/", {"From": "whatsapp:+923001112222", "Body": "2 zinger burger"})
        self.assertNotIn("Order", r.content.decode().split("Confirm")[0])           # disabled -> empty reply
        st.ai_whatsapp = True
        st.save()
        before = Order.objects.count()
        r = self.client.post("/ai/whatsapp/", {"From": "whatsapp:+923001112222", "Body": "2 zinger burger aur 1 loaded fries"})
        self.assertIn("YES", r.content.decode())
        self.assertEqual(Order.objects.count(), before)
        self.assertTrue(AISession.objects.filter(phone="+923001112222").exists())
        r = self.client.post("/ai/whatsapp/", {"From": "whatsapp:+923001112222", "Body": "Yes"})
        self.assertEqual(Order.objects.count(), before + 1)
        self.assertFalse(AISession.objects.exists())
        o = Order.objects.order_by("-pk").first()
        self.assertEqual(o.lines.count(), 2)
        with __import__("unittest").mock.patch.dict("os.environ", {"WHATSAPP_WEBHOOK_KEY": "s3"}):
            self.assertEqual(self.client.post("/ai/whatsapp/", {"From": "x", "Body": "y"}).status_code, 403)
            self.assertEqual(self.client.post("/ai/whatsapp/?key=s3", {"From": "x", "Body": "y"}).status_code, 200)

    def test_anomaly_watch(self):
        from . import ai, services as svc
        from .models import AutomationEvent, Notification, User
        Notification.objects.all().delete()
        waiter = User.objects.filter(branch=self.khi, role="waiter").first()
        o = Order.objects.filter(branch=self.khi).first()
        for i in range(5):
            VoidLog.objects.create(branch=self.khi, order=o, order_number=o.number, kind="item", item_name="x", qty=1, amount=Decimal("500"),
                                   reason="test", user=waiter)
        n = ai.job_anomalies()
        self.assertGreaterEqual(n, 1)
        self.assertTrue(Notification.objects.filter(title__startswith="AI alert").exists())
        self.assertEqual(ai.job_anomalies(), 0)                        # de-duplicated
        s = Setting.get()
        s.ai_anomaly = False
        s.save()
        AutomationEvent.objects.filter(key__startswith="ai:").delete()
        self.assertEqual(ai.job_anomalies(), 0)

    def test_usage_and_daily_report_headline(self):
        from unittest import mock
        from . import ai, automation as au
        from .models import DailyReport
        DailyReport.objects.all().delete()
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}), mock.patch.object(ai, "_call", return_value="Din acha raha."):
            rep, _ = au.build_daily_report(self.khi)
        self.assertTrue(rep.text.startswith("AI summary: Din acha raha."))
        u = ai.usage()
        self.assertGreaterEqual(u["llm"], 1)


class RoleAccessTests(TestCase):
    """Every role x every menu page: allowed pages open, everything else is refused, nobody gets a 500."""
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    USERS = ["admin", "khi_manager", "lhe_manager", "dxb_manager", "khi_cashier", "khi_waiter1", "khi_kitchen", "khi_kds",
             "khi_store", "khi_rider1", "lhe_store", "dxb_rider1", "dxb_kitchen", "dxb_cashier"]

    def test_matrix(self):
        from .context import NAV
        from .models import User
        for uname in self.USERS:
            c = self.client_class()
            self.assertTrue(c.login(username=uname, password="admin123" if uname == "admin" else "demo1234"), uname)
            u = User.objects.get(username=uname)
            role = "owner" if u.is_superuser else u.role
            for label, url, icon, roles, group in NAV:
                r = c.get(reverse(url))
                self.assertLess(r.status_code, 500, f"{uname} {url}")
                allowed = roles is None or role in roles
                if url in ("settings", "branches", "coupons", "modifiers") and not u.can_access_all:
                    allowed = False
                if allowed:
                    self.assertIn(r.status_code, (200, 302), f"{uname} should open {url}")
                    if r.status_code == 302 and url not in ("dashboard", "my_slip", "rider_home"):
                        # redirects are only fine for pages that hop to the right branch / landing
                        self.assertNotIn("/login", r["Location"], f"{uname} {url}")
                elif role != "owner":                  # the owner may open everything (an empty rider page, say)
                    self.assertEqual(r.status_code, 302, f"{uname} must NOT open {url} (got {r.status_code})")

    def test_landing_pages(self):
        for uname, dest in (("khi_kitchen", "/kitchen/"), ("khi_kds", "/kitchen/"), ("khi_store", "/stock/"), ("khi_rider1", "/deliveries/")):
            self.client.login(username=uname, password="demo1234")
            self.assertEqual(self.client.get("/dashboard/")["Location"], dest, uname)
            self.client.logout()

    def test_waiter_and_cashier_dashboards_hide_business_data(self):
        self.client.login(username="khi_waiter1", password="demo1234")
        html = self.client.get("/dashboard/").content.decode()
        self.assertNotIn("Revenue today", html)
        self.assertNotIn("Top items today", html)
        self.assertIn("My orders today", html)
        self.client.logout(); self.client.login(username="khi_cashier", password="demo1234")
        html = self.client.get("/dashboard/").content.decode()
        self.assertNotIn("Top items today", html)
        self.client.logout(); self.client.login(username="khi_manager", password="demo1234")
        self.assertIn("Top items today", self.client.get("/dashboard/").content.decode())

    def test_branch_isolation(self):
        from .models import Order
        self.client.login(username="lhe_store", password="demo1234")
        r = self.client.get("/stock/")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("Karachi", r.content.decode().split("<main")[1][:600])
        o = Order.objects.filter(branch__code="KHI").first()
        self.client.logout(); self.client.login(username="lhe_manager", password="demo1234")
        self.assertEqual(self.client.get(f"/orders/{o.pk}/").status_code, 404)

    def test_roles_page_and_password_change(self):
        self.client.login(username="khi_manager", password="demo1234")
        r = self.client.get("/roles/")
        self.assertContains(r, "Storekeeper"); self.assertContains(r, "Delivery Rider")
        self.client.logout(); self.client.login(username="khi_cashier", password="demo1234")
        self.assertEqual(self.client.get("/roles/").status_code, 302)
        r = self.client.post("/password/", {"old_password": "demo1234", "new_password1": "Str0ng!pass#77", "new_password2": "Str0ng!pass#77"})
        self.assertEqual(r.status_code, 302)
        self.client.logout()
        self.assertTrue(self.client.login(username="khi_cashier", password="Str0ng!pass#77"))

    def test_rider_user_gets_profile_and_staff_form_roles(self):
        from .models import Branch, Rider, User
        khi = Branch.objects.get(code="KHI")
        u = User(username="newrider", role="rider", branch=khi, first_name="Waqar", phone="0300111")
        u.set_password("x12345678")
        u.save()
        r = Rider.objects.get(user=u)
        self.assertEqual((r.branch, r.name), (khi, "Waqar"))
        u.first_name = "Waqar Ali"
        u.save()
        self.assertEqual(Rider.objects.filter(user=u).count(), 1)
        self.assertEqual(Rider.objects.get(user=u).name, "Waqar Ali")
        self.client.login(username="khi_manager", password="demo1234")
        self.assertContains(self.client.get("/staff/add/"), "Storekeeper")

    def test_every_role_has_a_user_with_data(self):
        from .models import User
        have = set(User.objects.values_list("role", flat=True))
        self.assertTrue({"owner", "manager", "cashier", "waiter", "kitchen", "kds", "storekeeper", "rider"} <= have)
        for b in ("KHI", "LHE", "DXB"):
            for role in ("manager", "cashier", "waiter", "kds", "storekeeper", "rider"):
                self.assertTrue(User.objects.filter(branch__code=b, role=role).exists(), f"{b} {role}")


class RoleWorkflowTests(TestCase):
    """The real work of each role, one after the other, on the same orders."""
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def login(self, u):
        self.client.logout()
        self.assertTrue(self.client.login(username=u, password="demo1234"), u)

    def setUp(self):
        Order.objects.filter(status__in=["new", "preparing", "ready", "out_for_delivery", "served"]).update(status="completed")
        from .models import Table
        Table.objects.update(status="free")
        self.khi = Branch.objects.get(code="KHI")

    def pos(self, **extra):
        from . import services as svc
        m = [x for x in svc.branch_menu(self.khi) if x["max_qty"] is None or x["max_qty"] > 5][0]
        body = {"order_type": "dine_in", "items": [{"id": m["id"], "qty": 2}], "pay_now": False, "method": "cash"}
        body.update(extra)
        r = self.client.post("/pos/submit/", json.dumps(body), content_type="application/json")
        self.assertTrue(r.json()["ok"], r.content)
        return Order.objects.get(pk=r.json()["id"])

    def test_dine_in_waiter_chef_cashier_manager(self):
        from .models import Table
        t = Table.objects.filter(branch=self.khi, status="free").first()
        self.login("khi_waiter1")
        o = self.pos(table_id=t.pk)
        self.assertEqual(o.waiter.username, "khi_waiter1")
        # waiter cannot cook or take payment
        r = self.client.post(f"/orders/{o.pk}/action/", {"action": "advance"})
        o.refresh_from_db()
        self.assertIn(o.status, ("preparing", "new"))
        self.assertNotEqual(o.status, "completed")
        # kitchen: the assigned chef (or head chef) marks it ready
        o.refresh_from_db()
        chef = o.chef.username if o.chef else "khi_kds"
        self.login(chef)
        self.assertContains(self.client.get("/kitchen/"), o.number)
        self.client.post(f"/orders/{o.pk}/action/", {"action": "advance"})
        o.refresh_from_db()
        self.assertEqual(o.status, "ready")
        # waiter serves, asks for bill
        self.login("khi_waiter1")
        self.client.post(f"/orders/{o.pk}/action/", {"action": "advance"})
        o.refresh_from_db()
        self.assertEqual(o.status, "served")
        self.client.post(f"/orders/{o.pk}/action/", {"action": "request_bill"})
        # cashier opens shift and collects
        self.login("khi_cashier")
        self.client.post("/shifts/", {"action": "open", "opening_cash": "1000"})
        self.client.post(f"/orders/{o.pk}/action/", {"action": "advance", "method": "cash"})
        o.refresh_from_db()
        self.assertEqual(o.status, "completed")
        self.assertTrue(o.bill_number)
        # manager sees it in the orders list and the dashboard numbers
        self.login("khi_manager")
        self.assertContains(self.client.get("/orders/?q=" + o.number), o.number)

    def test_delivery_cashier_chef_rider(self):
        from .models import Rider
        self.login("khi_cashier")
        o = self.pos(order_type="delivery", customer_name="Imran", phone="03001239999", address="Flat 5, Clifton", table_id="")
        self.assertEqual(o.order_type, "delivery")
        chef = o.chef.username if o.chef else "khi_kds"
        self.login(chef)
        self.client.post(f"/orders/{o.pk}/action/", {"action": "advance"})
        o.refresh_from_db()
        self.assertEqual(o.status, "ready")
        self.assertIsNotNone(o.rider_id)                    # auto-assigned at READY
        rider_user = o.rider.user
        self.assertIsNotNone(rider_user)
        from .models import Notification
        self.assertTrue(Notification.objects.filter(user=rider_user, title__startswith="New delivery").exists())
        self.login(rider_user.username)
        page = self.client.get("/deliveries/")
        self.assertContains(page, o.number); self.assertContains(page, "Flat 5, Clifton")
        # a different rider cannot touch it
        other = Rider.objects.filter(branch=self.khi, user__isnull=False).exclude(pk=o.rider_id).first()
        self.login(other.user.username)
        self.client.post(f"/deliveries/{o.pk}/action/", {"action": "pickup"})
        o.refresh_from_db()
        self.assertEqual(o.status, "ready")
        self.login(rider_user.username)
        self.client.post(f"/deliveries/{o.pk}/action/", {"action": "pickup"})
        o.refresh_from_db()
        self.assertEqual(o.status, "out_for_delivery")
        self.client.post(f"/deliveries/{o.pk}/action/", {"action": "deliver"})
        o.refresh_from_db()
        self.assertEqual(o.status, "completed")
        self.assertEqual(o.due, 0)
        self.assertContains(self.client.get("/deliveries/"), "Delivered today")
        self.assertEqual(self.client.get("/orders/").status_code, 302)       # a rider has no order list

    def test_chef_request_storekeeper_approves_and_receives(self):
        from .models import Ingredient, Notification, PurchaseOrder, StockRequest
        ing = Ingredient.objects.filter(default_supplier__isnull=False).first()
        self.login("khi_kitchen")
        self.client.post("/stock/request/", {"ingredient": ing.pk, "qty": "6", "urgent": "on"})
        req = StockRequest.objects.get()
        self.assertTrue(Notification.objects.filter(user__username="khi_store", title__contains="stock request").exists())
        self.login("khi_store")
        self.assertContains(self.client.get("/stock/requests/"), ing.name)
        self.client.post(f"/stock/requests/{req.pk}/action/", {"action": "approve", "qty": "6"})
        req.refresh_from_db()
        po = req.po
        self.assertEqual(po.status, "draft")
        self.assertEqual(self.client.get(f"/purchase-orders/{po.pk}/").status_code, 200)
        # storekeeper never sees sales / reports
        for url in ("/reports/", "/analytics/ai/", "/customers/", "/bills/", "/pos/"):
            self.assertEqual(self.client.get(url).status_code, 302, url)

    def test_cashier_cannot_see_void_audit_or_reports(self):
        self.login("khi_cashier")
        for url in ("/voids/", "/reports/", "/staff/", "/settings/", "/automation/", "/ai/"):
            self.assertEqual(self.client.get(url).status_code, 302, url)

    def test_salary_slip_for_staff_roles(self):
        for u in ("khi_cashier", "khi_store", "khi_rider1", "khi_waiter1", "khi_kitchen"):
            self.login(u)
            r = self.client.get("/my-salary/", follow=True)
            self.assertEqual(r.status_code, 200, u)
            self.assertContains(r, "alary")
        self.login("khi_cashier")
        from .models import User
        other = User.objects.get(username="khi_store")
        self.assertEqual(self.client.get(f"/automation/slips/staff/{other.pk}/").status_code, 302)


class OnlineAcceptTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def setUp(self):
        from .models import Notification, Order as O
        O.objects.filter(status__in=["new", "preparing", "ready"]).update(status="completed")
        O.objects.filter(acceptance="pending").update(acceptance="")
        Notification.objects.all().delete()
        self.khi = Branch.objects.get(code="KHI")

    def website_order(self):
        from . import services as svc
        m = [x for x in svc.branch_menu(self.khi) if x["max_qty"] is None or x["max_qty"] > 5][0]
        r = self.client_class().post(f"/order/KHI/submit/", json.dumps({"items": [{"id": m["id"], "qty": 2}], "order_type": "delivery",
                                      "name": "Sana Khan", "phone": "03001112233", "address": "Block 5 Clifton"}), content_type="application/json")
        self.assertTrue(r.json()["ok"], r.content)
        return Order.objects.get(number=r.json()["number"])

    def login(self, u):
        self.client.logout()
        self.assertTrue(self.client.login(username=u, password="demo1234"), u)

    def total_stock(self):
        from django.db.models import Sum
        return Stock.objects.filter(branch=self.khi).aggregate(s=Sum("quantity"))["s"]

    def test_order_waits_alerts_staff_and_hides_from_kitchen(self):
        from .models import MessageLog, Notification
        o = self.website_order()
        self.assertEqual((o.acceptance, o.status), ("pending", "new"))
        self.assertIsNone(o.chef_id)
        self.assertTrue(Notification.objects.filter(user__username="khi_cashier", title__startswith="NEW online").exists())
        self.assertTrue(Notification.objects.filter(user__username="khi_manager", title__startswith="NEW online").exists())
        self.assertFalse(Notification.objects.filter(user__username="lhe_cashier").exists())
        self.assertIn("confirm", MessageLog.objects.get(order=o, kind="order_received").body)
        self.login("khi_kds")
        self.assertNotContains(self.client.get("/kitchen/"), o.number)
        self.login("khi_cashier")
        r = self.client.post(f"/orders/{o.pk}/action/", {"action": "advance"}, follow=True)
        o.refresh_from_db()
        self.assertEqual(o.status, "new")                                  # cannot be pushed to the kitchen by hand
        self.assertContains(self.client.get("/orders/online/"), o.number)
        self.assertContains(self.client_class().get(f"/track/{o.number}/"), "Waiting for the restaurant")

    def test_accept_sends_to_kitchen_with_eta(self):
        from .models import MessageLog
        o = self.website_order()
        self.login("khi_cashier")
        self.client.post(f"/orders/online/{o.pk}/action/", {"action": "accept", "eta": "20"})
        o.refresh_from_db()
        self.assertEqual((o.acceptance, o.accept_eta), ("accepted", 20))
        self.assertIn(o.status, ("preparing", "new"))
        self.assertTrue(o.chef_id or o.status == "preparing")
        self.assertIn("20 min", MessageLog.objects.get(order=o, kind="order_accepted").body)
        self.assertContains(self.client_class().get(f"/track/{o.number}/"), "about 20 minutes")
        self.assertEqual(self.client.post(f"/orders/online/{o.pk}/action/", {"action": "accept"}).status_code, 302)   # second accept is refused politely

    def test_reject_needs_reason_cancels_and_restores_stock(self):
        from .models import MessageLog
        before = self.total_stock()
        o = self.website_order()
        self.assertLess(self.total_stock(), before)
        self.login("khi_manager")
        self.client.post(f"/orders/online/{o.pk}/action/", {"action": "reject", "reason": "other", "other": ""})
        o.refresh_from_db()
        self.assertEqual(o.acceptance, "pending")                          # reason is mandatory
        self.client.post(f"/orders/online/{o.pk}/action/", {"action": "reject", "reason": "Item sold out"})
        o.refresh_from_db()
        self.assertEqual((o.status, o.acceptance, o.cancel_reason), ("cancelled", "rejected", "Item sold out"))
        self.assertEqual(self.total_stock(), before)
        self.assertIn("sold out", MessageLog.objects.get(order=o, kind="order_rejected").body)
        self.assertContains(self.client_class().get(f"/track/{o.number}/"), "Item sold out")

    def test_only_cashier_and_manager_and_setting_off(self):
        o = self.website_order()
        for u in ("khi_waiter1", "khi_kitchen", "khi_store", "khi_rider1"):
            self.login(u)
            self.assertEqual(self.client.get("/orders/online/").status_code, 302, u)
            self.client.post(f"/orders/online/{o.pk}/action/", {"action": "accept"})
            o.refresh_from_db()
            self.assertEqual(o.acceptance, "pending", u)
        self.login("lhe_manager")                                          # another branch cannot touch it
        self.assertEqual(self.client.post(f"/orders/online/{o.pk}/action/", {"action": "accept"}).status_code, 404)
        from .models import Setting
        Setting.objects.update(online_manual_accept=False)
        o2 = self.website_order()
        self.assertEqual(o2.acceptance, "")
        self.assertIn(o2.status, ("preparing", "new"))

    def test_pos_orders_are_never_pending(self):
        from . import services as svc
        m = svc.branch_menu(self.khi)[0]
        o = svc.create_order(branch=self.khi, order_type="takeaway", items=[{"item": MenuItem.objects.get(pk=m["id"]), "name": m["name"], "qty": 1, "price": m["price"]}])
        self.assertEqual(o.acceptance, "")

    def test_waiting_alert_and_auto_accept(self):
        from datetime import timedelta
        from . import automation as au
        from .models import Notification, Setting
        o = self.website_order()
        Order.objects.filter(pk=o.pk).update(created_at=timezone.now() - timedelta(minutes=9))
        Notification.objects.all().delete()
        self.assertEqual(au.job_online_waiting(), 1)
        self.assertTrue(Notification.objects.filter(user__username="khi_cashier", title__contains="still waiting").exists())
        self.assertEqual(au.job_online_waiting(), 0)                       # only once
        Setting.objects.update(online_auto_accept=True)
        self.assertEqual(au.job_online_waiting(), 1)
        o.refresh_from_db()
        self.assertEqual((o.acceptance, o.accept_eta), ("accepted", 30))


class SecurityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def test_audit_records_changes_with_actor(self):
        from . import audit
        from .models import AuditLog, MenuItem, User
        mgr = User.objects.get(username="khi_manager")
        audit.set_actor(mgr, "1.2.3.4")
        item = MenuItem.objects.first()
        old = item.price
        item.price = old + 25
        item.save()
        row = AuditLog.objects.filter(model="MenuItem", action="changed").first()
        self.assertEqual(row.username, "khi_manager")
        self.assertIn(f"{old} -> {old + 25}", row.changes)
        self.assertEqual(row.ip, "1.2.3.4")
        u = User.objects.get(username="khi_cashier")
        u.set_password("newpass-12345"); u.save()
        pw = AuditLog.objects.filter(model="User", action="changed").first()
        self.assertIn("password changed", pw.changes)
        self.assertNotIn("newpass", pw.changes)
        item.delete()
        self.assertTrue(AuditLog.objects.filter(action="deleted", model="MenuItem").exists())
        audit.set_actor(None, "")

    def test_audit_page_permissions_and_filters(self):
        from .models import AuditLog
        AuditLog.objects.create(username="x", action="changed", model="MenuItem", object_repr="Burger", changes="price: 1 -> 2")
        self.client.login(username="khi_cashier", password="demo1234")
        self.assertEqual(self.client.get("/audit/").status_code, 302)
        self.client.logout(); self.client.login(username="khi_manager", password="demo1234")
        r = self.client.get("/audit/?model=MenuItem&q=Burger")
        self.assertContains(r, "price: 1 -&gt; 2")

    def test_login_lockout_after_five_failures(self):
        from .models import AuditLog
        for _ in range(5):
            self.client.post("/login/", {"username": "khi_cashier", "password": "wrong"})
        self.assertEqual(AuditLog.objects.filter(action="login_failed", username="khi_cashier").count(), 5)
        r = self.client.post("/login/", {"username": "khi_cashier", "password": "demo1234"})
        self.assertEqual(r.status_code, 429)
        self.assertContains(r, "Too many wrong passwords", status_code=429)
        self.assertNotIn("_auth_user_id", self.client.session)
        # another user is not affected
        self.assertEqual(self.client.post("/login/", {"username": "khi_waiter1", "password": "demo1234"}).status_code, 302)

    def test_good_login_is_logged_and_resets_failures(self):
        from .models import AuditLog
        for _ in range(3):
            self.client.post("/login/", {"username": "khi_store", "password": "bad"})
        self.assertEqual(self.client.post("/login/", {"username": "khi_store", "password": "demo1234"}).status_code, 302)
        self.assertTrue(AuditLog.objects.filter(action="login", username="khi_store").exists())
        self.client.logout()
        for _ in range(4):
            self.client.post("/login/", {"username": "khi_store", "password": "bad"})
        self.assertEqual(self.client.post("/login/", {"username": "khi_store", "password": "demo1234"}).status_code, 302)   # earlier fails don't pile up

    def test_pwa_endpoints(self):
        r = self.client.get("/manifest.webmanifest")
        self.assertEqual(r.json()["display"], "standalone")
        self.assertIn("respondWith", self.client.get("/sw.js").content.decode())
        self.assertContains(self.client.get("/offline/"), "offline")
        self.client.login(username="khi_manager", password="demo1234")
        self.assertContains(self.client.get("/dashboard/"), "manifest.webmanifest")


class DataToolsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def png(self, size=(1600, 1200), mode="RGBA"):
        import io
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        buf = io.BytesIO()
        Image.new(mode, size, (200, 80, 40, 255) if mode == "RGBA" else (200, 80, 40)).save(buf, "PNG")
        return SimpleUploadedFile("dish.png", buf.getvalue(), content_type="image/png")

    def test_photo_upload_stored_in_db_resized_and_served(self):
        import io
        from PIL import Image
        from .models import MediaFile
        self.client.login(username="khi_manager", password="demo1234")
        cat = Category_first()
        r = self.client.post("/menu/add/", {"category": cat.pk, "name": "Photo Burger", "price": "500", "prep_minutes": "10", "is_active": "on",
                                            "photo_file": self.png()})
        self.assertEqual(r.status_code, 302, getattr(r, "content", b"")[:300])
        item = MenuItem.objects.get(name="Photo Burger")
        self.assertIsNotNone(item.photo_id)
        self.assertEqual(item.image_url, f"/media-db/{item.photo_id}/")
        im = Image.open(io.BytesIO(bytes(MediaFile.objects.get(pk=item.photo_id).data)))
        self.assertLessEqual(max(im.size), 900)
        r = self.client.get(item.image_url)
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "image/jpeg"))
        self.client.logout()
        self.assertEqual(self.client.get(item.image_url).status_code, 200)               # public: the website shows it
        self.assertEqual(self.client.get("/media-db/99999/").status_code, 404)
        from . import services as svc
        self.assertEqual([m["img"] for m in svc.branch_menu(Branch.objects.get(code="KHI")) if m["name"] == "Photo Burger"], [item.image_url])
        self.client.login(username="khi_manager", password="demo1234")                  # remove
        self.client.post(f"/menu/{item.pk}/edit/", {"category": cat.pk, "name": "Photo Burger", "price": "500", "prep_minutes": "10", "is_active": "on", "remove_photo": "on"})
        item.refresh_from_db()
        self.assertIsNone(item.photo_id)

    def test_exports(self):
        self.client.login(username="khi_manager", password="demo1234")
        for n in ("orders", "order_items", "customers", "menu", "ingredients", "stock", "suppliers", "staff", "audit"):
            r = self.client.get(f"/data/export/{n}/")
            self.assertEqual(r.status_code, 200, n)
            self.assertIn("text/csv", r["Content-Type"])
            self.assertGreater(len(r.content.decode("utf-8-sig").splitlines()), 1, n)
        self.assertIn("Zinger Burger", self.client.get("/data/export/menu/").content.decode("utf-8-sig"))
        self.client.logout(); self.client.login(username="khi_cashier", password="demo1234")
        self.assertEqual(self.client.get("/data/export/orders/").status_code, 302)

    def test_import_menu_and_ingredients(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import Ingredient
        self.client.login(username="khi_manager", password="demo1234")
        csv_text = ("category,name,sku,price,description,prep_minutes,veg\n"
                    "Burgers & Wraps,Zinger Burger,,700,Updated,12,no\n"        # existing: price update
                    "Specials,Chapli Kabab,CHP-1,450,Peshawari,15,no\n"        # new category + item
                    "Specials,Bad Price,BP-1,abc,,5,no\n"
                    "Specials,,X-1,100,,5,no\n")
        r = self.client.post("/data/import/menu/", {"file": SimpleUploadedFile("m.csv", csv_text.encode())}, follow=True)
        self.assertContains(r, "1 added, 1 updated")
        self.assertContains(r, "2 row(s) skipped")
        self.assertContains(r, "not a number")
        self.assertEqual(MenuItem.objects.get(name="Zinger Burger").price, Decimal("700"))
        self.assertTrue(MenuItem.objects.filter(sku="CHP-1", category__name="Specials").exists())
        existing = Ingredient.objects.first().name
        ing = (f"name,unit,unit_cost,category,reorder_level,supplier\nSaffron,g,90,spice,50,Royal Traders\n{existing},kg,800,meat,10,\nBadOne,xx,1,meat,1,\n")
        r = self.client.post("/data/import/ingredients/", {"file": SimpleUploadedFile("i.csv", ing.encode())}, follow=True)
        self.assertContains(r, "1 added")
        self.assertEqual(Ingredient.objects.get(name="Saffron").default_supplier.name, "Royal Traders")
        self.assertContains(r, "unit must be one of")
        self.assertEqual(self.client.get("/data/sample/menu/").status_code, 200)

    def test_backup_is_valid_json_and_master_only(self):
        import json as j
        self.client.login(username="khi_manager", password="demo1234")
        r = self.client.get("/data/backup/")
        self.assertEqual(r.status_code, 200)
        data = j.loads(r.content)
        models_ = {d["model"] for d in data}
        self.assertTrue({"rms.menuitem", "rms.order", "rms.user", "rms.mediafile"} <= models_ or {"rms.menuitem", "rms.order", "rms.user"} <= models_)
        self.client.logout(); self.client.login(username="lhe_manager", password="demo1234")
        self.assertEqual(self.client.get("/data/backup/").status_code, 302)               # branch manager: no full backup
        self.assertEqual(self.client.get("/data/").status_code, 200)


def Category_first():
    from .models import Category
    return Category.objects.first()


class FinanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def body(self, **extra):
        items = list(MenuItem.objects.filter(sku__in=["BRG-01", "DRK-03"]))
        return {"order_type": "takeaway", "items": [{"id": i.pk, "qty": 2} for i in items], "discount": 0, **extra}

    def post(self, **extra):
        return self.client.post(reverse("pos_submit"), json.dumps(self.body(**extra)), content_type="application/json")

    def test_percent_coupon_in_pos_and_capped(self):
        from .models import Coupon
        Coupon.objects.create(code="save10", kind="percent", value=10, max_discount=50)
        self.client.login(username="khi_cashier", password="demo1234")
        r = self.post(coupon="SAVE10")
        self.assertTrue(r.json()["ok"], r.content)
        o = Order.objects.get(pk=r.json()["id"])
        self.assertEqual(o.coupon.code, "SAVE10")
        self.assertEqual(o.discount, Decimal("50.00"))          # 10% was bigger than the 50 cap
        self.assertEqual(o.total, o.subtotal - 50 + o.tax + o.service_charge + o.delivery_fee)

    def test_fixed_coupon_and_rules(self):
        from datetime import timedelta
        from .models import Coupon
        today = timezone.localdate()
        Coupon.objects.create(code="FLAT", kind="fixed", value=100, min_order=100000)
        Coupon.objects.create(code="OLD", kind="fixed", value=10, valid_to=today - timedelta(days=1))
        Coupon.objects.create(code="ONCE", kind="fixed", value=10, usage_limit=1)
        Coupon.objects.create(code="DXBONLY", kind="fixed", value=10, branch=Branch.objects.get(code="DXB"))
        Coupon.objects.create(code="OFF", kind="fixed", value=10, is_active=False)
        self.client.login(username="khi_cashier", password="demo1234")
        for code in ("FLAT", "OLD", "DXBONLY", "OFF", "NOPE"):
            r = self.post(coupon=code)
            self.assertEqual(r.status_code, 400, code)
        self.assertEqual(self.post(coupon="ONCE").status_code, 200)
        self.assertEqual(self.post(coupon="ONCE").status_code, 400)       # usage limit reached
        self.assertEqual(Order.objects.filter(coupon__code="FLAT").count(), 0)

    def test_coupon_check_endpoint_and_website(self):
        from .models import Coupon
        Coupon.objects.create(code="WEB", kind="percent", value=20)
        self.client.login(username="khi_cashier", password="demo1234")
        r = self.client.post(reverse("coupon_check"), json.dumps({"code": "web", "subtotal": 1000}), content_type="application/json").json()
        self.assertEqual((r["ok"], r["amount"]), (True, 200.0))
        self.assertFalse(self.client.post(reverse("coupon_check"), json.dumps({"code": "x", "subtotal": 1}), content_type="application/json").json()["ok"])
        self.client.logout()
        item = MenuItem.objects.get(sku="BRG-01")
        body = {"items": [{"id": item.pk, "qty": 1}], "order_type": "delivery", "name": "G", "phone": "0300", "address": "A", "coupon": "WEB"}
        chk = self.client.post("/order/khi/coupon/", json.dumps({"items": body["items"], "code": "WEB"}), content_type="application/json").json()
        self.assertTrue(chk["ok"])
        r = self.client.post("/order/khi/submit/", json.dumps(body), content_type="application/json").json()
        self.assertTrue(r["ok"], r)
        o = Order.objects.get(number=r["number"])
        self.assertEqual(o.coupon.code, "WEB")
        self.assertGreater(o.discount, 0)
        bad = dict(body, coupon="NOPE")
        self.assertFalse(self.client.post("/order/khi/submit/", json.dumps(bad), content_type="application/json").json()["ok"])

    def test_expense_crud_scoped_and_pnl(self):
        from .models import Expense
        Expense.objects.create(branch=Branch.objects.get(code="KHI"), category="rent", amount=999, date=timezone.localdate())
        self.client.login(username="lhe_manager", password="demo1234")
        before = self.client.get(reverse("pnl")).context["p"]["expenses"]
        r = self.client.post("/expenses/add/", {"category": "rent", "amount": "50000", "date": str(timezone.localdate()), "note": "Month rent"})
        self.assertEqual(r.status_code, 302, getattr(r, "content", b"")[:300])
        e = Expense.objects.get(note="Month rent")
        self.assertEqual((e.branch.code, e.user.username), ("LHE", "lhe_manager"))
        p = self.client.get(reverse("pnl")).context["p"]
        self.assertEqual(p["expenses"], before + Decimal("50000"))               # the KHI expense is not visible to LHE
        self.assertEqual(p["net"], p["gross"] - p["expenses"] - p["salaries"])
        self.assertContains(self.client.get(reverse("pnl")), "Net profit")
        self.assertEqual(self.client.get("/expenses/").status_code, 200)

    def test_roles_for_finance_pages(self):
        for u, ok in (("khi_cashier", False), ("khi_waiter", False), ("khi_kitchen", False), ("admin", True)):
            self.client.login(username=u, password="admin123" if u == "admin" else "demo1234")
            self.assertEqual(self.client.get(reverse("pnl"), follow=False).status_code == 200, ok, u)
            self.assertEqual(self.client.get("/coupons/", follow=False).status_code == 200, ok, u)
            self.client.logout()
        self.client.login(username="lhe_manager", password="demo1234")
        self.assertEqual(self.client.get("/coupons/").status_code, 302)       # coupons are master-only


class ModifierTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def setUp(self):
        from .models import ModifierGroup, ModifierOption
        self.item = MenuItem.objects.get(sku="BRG-01")
        self.size = ModifierGroup.objects.create(name="Size", required=True, multi=False)
        self.small = ModifierOption.objects.create(group=self.size, name="Regular", price=0)
        self.large = ModifierOption.objects.create(group=self.size, name="Large", price=150)
        self.extra = ModifierGroup.objects.create(name="Extras", required=False, multi=True)
        self.cheese = ModifierOption.objects.create(group=self.extra, name="Extra cheese", price=80)
        self.egg = ModifierOption.objects.create(group=self.extra, name="Egg", price=40)
        self.item.modifier_groups.set([self.size, self.extra])

    def pos(self, mods, qty=1, **extra):
        body = {"order_type": "takeaway", "items": [{"id": self.item.pk, "qty": qty, "mods": mods}], "discount": 0, **extra}
        return self.client.post(reverse("pos_submit"), json.dumps(body), content_type="application/json")

    def test_pos_price_notes_and_kot(self):
        self.client.login(username="khi_cashier", password="demo1234")
        r = self.pos([self.large.pk, self.cheese.pk, self.egg.pk], qty=2)
        self.assertEqual(r.status_code, 200, r.content)
        o = Order.objects.get(pk=r.json()["id"])
        line = o.lines.get()
        self.assertEqual(line.price, self.item.price + 150 + 80 + 40)
        self.assertEqual(line.notes, "+ Large, Extra cheese, Egg")
        self.assertEqual(o.subtotal, line.price * 2)
        self.assertContains(self.client.get(f"/orders/{o.pk}/kot/"), "Extra cheese")

    def test_rules_required_single_foreign(self):
        from .models import ModifierGroup, ModifierOption
        self.client.login(username="khi_cashier", password="demo1234")
        self.assertEqual(self.pos([]).status_code, 400)                               # Size is required
        self.assertEqual(self.pos([self.small.pk, self.large.pk]).status_code, 400)   # one size only
        other = ModifierOption.objects.create(group=ModifierGroup.objects.create(name="Other"), name="X", price=1)
        self.assertEqual(self.pos([self.small.pk, other.pk]).status_code, 400)        # option of another item
        self.large.is_active = False
        self.large.save()
        self.assertEqual(self.pos([self.large.pk]).status_code, 400)
        self.assertEqual(self.pos([self.small.pk]).status_code, 200)

    def test_menu_json_and_website(self):
        from . import services as svc
        row = next(m for m in svc.branch_menu(Branch.objects.get(code="KHI")) if m["id"] == self.item.pk)
        self.assertEqual([g["name"] for g in row["mods"]], ["Size", "Extras"])
        body = {"items": [{"id": self.item.pk, "qty": 1, "mods": [self.large.pk]}], "order_type": "takeaway", "name": "G", "phone": "0300"}
        r = self.client.post("/order/khi/submit/", json.dumps(body), content_type="application/json").json()
        self.assertTrue(r["ok"], r)
        self.assertEqual(Order.objects.get(number=r["number"]).lines.get().price, self.item.price + 150)
        no = dict(body, items=[{"id": self.item.pk, "qty": 1}])
        self.assertFalse(self.client.post("/order/khi/submit/", json.dumps(no), content_type="application/json").json()["ok"])
        page = self.client.get("/order/khi/").content.decode()
        self.assertIn("Extra cheese", page)

    def test_group_admin_form_syncs_options(self):
        from .models import ModifierGroup
        self.client.login(username="admin", password="admin123")
        r = self.client.post("/modifiers/add/", {"name": "Spice", "required": "on", "options_text": "Mild | 0\nHot | 20\n\nExtra hot|40"})
        self.assertEqual(r.status_code, 302, getattr(r, "content", b"")[:200])
        g = ModifierGroup.objects.get(name="Spice")
        self.assertEqual([(o.name, o.price) for o in g.options.all()], [("Mild", 0), ("Hot", 20), ("Extra hot", 40)])
        r = self.client.post(f"/modifiers/{g.pk}/edit/", {"name": "Spice", "options_text": "Mild | 0\nHot | 25"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(sorted(g.options.filter(is_active=True).values_list("name", "price")), [("Hot", 25), ("Mild", 0)])
        self.assertFalse(g.options.get(name="Extra hot").is_active)
        bad = self.client.post("/modifiers/add/", {"name": "Bad", "options_text": "Small | abc"})
        self.assertEqual(bad.status_code, 200)
        # attach to a menu item through the menu form
        cat = self.item.category
        r = self.client.post(f"/menu/{self.item.pk}/edit/", {"category": cat.pk, "name": self.item.name, "sku": self.item.sku, "price": self.item.price,
                                                             "prep_minutes": 10, "is_active": "on", "modifier_groups": [g.pk]})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(list(self.item.modifier_groups.values_list("name", flat=True)), ["Spice"])

    def test_add_items_to_open_order_with_mods(self):
        self.client.login(username="khi_cashier", password="demo1234")
        o = Order.objects.get(pk=self.pos([self.small.pk]).json()["id"])
        body = {"items": [{"id": self.item.pk, "qty": 1, "mods": [self.large.pk, self.egg.pk]}]}
        r = self.client.post(reverse("order_add_items", args=[o.pk]), json.dumps(body), content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(o.lines.count(), 2)
        self.assertEqual(o.lines.order_by("-id").first().notes, "+ Large, Egg")
