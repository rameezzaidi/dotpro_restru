"""Automation engine. `tick()` is cheap, idempotent and safe to call often: it runs from the bell poll of any logged-in
user (at most once a minute), from the `run_automations` command and from the /cron/tick/ URL (Vercel Cron / Task Scheduler).
Every alert is de-duplicated through AutomationEvent keys, so running it twice never alerts twice."""
import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Avg, Count, Sum
from django.utils import timezone

from . import ai, messaging
from . import services as svc
from .models import (AutomationEvent, Branch, Channel, Customer, DailyReport, Feedback, Order, OrderItem, Rider, Setting,
                     Shift, Table, User)

log = logging.getLogger("restopro.automation")


# ------------------------------------------------------------------ helpers
def _once(key, note=""):
    """True only the first time this key is seen."""
    return AutomationEvent.objects.get_or_create(key=key[:120], defaults={"note": note[:255]})[1]


def _due(key, seconds):
    """Atomically claim a periodic job: True if its last run is older than `seconds` (or never ran)."""
    now = timezone.now()
    ev, created = AutomationEvent.objects.get_or_create(key=key[:120])
    if created:
        return True
    return AutomationEvent.objects.filter(pk=ev.pk, created_at__lte=now - timedelta(seconds=seconds)).update(created_at=now) == 1


def local_now(branch):
    return timezone.localtime(timezone.now(), ZoneInfo(branch.timezone or "UTC"))


def _branch_users(branch, roles):
    return list(User.objects.filter(branch=branch, role__in=roles, is_active=True))


def _money(branch, v):
    return f"{branch.currency_symbol} {Decimal(v or 0):,.2f}"


def prep_secs(o):
    mins = [l.item.prep_minutes for l in o.lines.all() if l.item_id and l.item]
    return (max(mins) if mins else 15) * 60


# ------------------------------------------------------------------ pricing (happy hour)
def active_promo(branch):
    """Biggest percent-off among PriceRules that are active in this branch right now (0 = none)."""
    from .models import PriceRule
    rules = list(PriceRule.objects.filter(is_active=True))
    if not rules:
        return Decimal(0)
    now = local_now(branch)
    pct = [r.percent for r in rules if r.applies(branch, now)]
    return max(pct) if pct else Decimal(0)


# ------------------------------------------------------------------ order hooks (called from services)
def _track_link(order):
    base = messaging.site_url()
    return f"{base}/track/{order.number}/" if base else ""


def _customer_msg(order, event, text):
    try:
        st = Setting.get()
        if not (st.msg_customer_events and order.customer_phone):
            return
        if order.order_type == Order.DINE_IN and event not in ("completed",):
            return
        if not _once(f"msg:{order.pk}:{event}"):
            return
        messaging.send_phone(order.customer_phone, text, country=order.branch.country, kind=f"order_{event}",
                             branch=order.branch, order=order)
    except Exception:                                  # noqa: BLE001 - messaging must never break an order
        log.exception("customer message failed")


def on_order_created(order):
    name = (order.customer_name or "").split(" ")[0] or "there"
    link = _track_link(order)
    kind = "delivery" if order.order_type == Order.DELIVERY else "pickup"
    pending = order.acceptance == "pending"
    _customer_msg(order, "received",
                  f"Hi {name}, thank you for ordering from {order.branch.name}! Order {order.number} is received "
                  f"({_money(order.branch, order.total)}, {kind})." + (" We will confirm it in a moment." if pending else "")
                  + (f" Track it: {link}" if link else ""))
    if order.source_id and order.source.code in svc.ONLINE_CODES:       # bell for the people who handle online orders
        try:
            who = _branch_users(order.branch, ("cashier",)) + svc.managers_for_branch(order.branch)
            svc.notify(who, ("NEW online order " if pending else "Online order ") + order.number,
                       f"{order.customer_name or 'Customer'} - {order.get_order_type_display()} - {_money(order.branch, order.total)}"
                       + (" - Accept or Reject" if pending else ""), "/orders/online/", "urgent" if pending else "info")
        except Exception:                      # noqa: BLE001
            log.exception("online order alert failed")


def on_online_decision(order, accepted):
    name = (order.customer_name or "").split(" ")[0] or "there"
    link = _track_link(order)
    if accepted:
        _customer_msg(order, "accepted", f"Hi {name}, your order {order.number} is confirmed! Ready in about {order.accept_eta} min."
                      + (f" Track it: {link}" if link else ""))
    else:
        _customer_msg(order, "rejected", f"Hi {name}, sorry - {order.branch.name} could not take order {order.number}: {order.cancel_reason}. "
                      "Any online payment will be refunded.")


def job_online_waiting():
    """Escalate (or auto-accept) online orders nobody has answered."""
    st = Setting.get()
    n = 0
    limit = timedelta(minutes=max(1, st.online_accept_minutes))
    for o in Order.objects.filter(acceptance="pending", status=Order.NEW).select_related("branch", "source"):
        if timezone.now() - o.created_at < limit:
            continue
        if st.online_auto_accept:
            try:
                svc.accept_online(o, None, 30)
                svc.notify(_branch_users(o.branch, ("cashier",)) + svc.managers_for_branch(o.branch), f"Order {o.number} auto-accepted",
                           "Nobody answered in time - promised 30 min to the customer", f"/orders/{o.pk}/", "info")
                n += 1
            except svc.OrderError:
                pass
        elif _once(f"onlinewait:{o.pk}"):
            svc.notify(_branch_users(o.branch, ("cashier",)) + svc.managers_for_branch(o.branch), f"Online order {o.number} is still waiting",
                       f"Waiting {int((timezone.now() - o.created_at).total_seconds() // 60)} min - Accept or Reject", "/orders/online/", "urgent")
            n += 1
    return n


def on_status(order, status):
    try:
        name = (order.customer_name or "").split(" ")[0] or "there"
        b = order.branch
        if status == Order.READY and order.order_type == Order.DELIVERY and Setting.get().auto_assign_rider:
            assign_rider(order)
        if status == Order.READY and order.order_type == Order.TAKEAWAY:
            _customer_msg(order, "ready", f"Hi {name}, your order {order.number} is ready for pickup at {b.name}.")
        elif status == Order.OUT:
            r = order.rider
            _customer_msg(order, "out", f"Hi {name}, your order {order.number} is on the way"
                          + (f" with {r.name} ({r.phone})" if r and r.phone else (f" with {r.name}" if r else "")) + ".")
        elif status == Order.COMPLETED:
            from django.urls import reverse
            base = messaging.site_url()
            fb = f" Tell us how we did: {base}{reverse('feedback_form', args=[order.ensure_feedback_token()])}" if base else ""
            _customer_msg(order, "completed", f"Thank you {name}! We hope you enjoyed your meal at {b.name}.{fb}")
        elif status == Order.CANCELLED:
            _customer_msg(order, "cancelled", f"Hi {name}, your order {order.number} has been cancelled. Please call {b.phone or b.name} if this is unexpected.")
    except Exception:                                  # noqa: BLE001
        log.exception("order status hook failed")


def assign_rider(order):
    """Least-busy available rider of the branch; None (and a manager alert, once) when nobody is free."""
    if order.rider_id or order.order_type != Order.DELIVERY:
        return None
    best, best_load = None, None
    for r in Rider.objects.filter(branch=order.branch, is_active=True, status=Rider.AVAILABLE):
        load = Order.objects.filter(rider=r, status__in=Order.ACTIVE).count()
        if best is None or load < best_load:
            best, best_load = r, load
    if best:
        order.rider = best
        order.save(update_fields=["rider"])
        svc.notify_rider(order)
        return best
    if _once(f"norider:{order.pk}"):
        svc.notify(svc.managers_for_branch(order.branch), f"No rider free for {order.number}",
                   "Delivery is ready but every rider is busy or offline - assign one manually.", f"/orders/{order.pk}/", "urgent")
    return None


def on_feedback(fb):
    st = Setting.get()
    if not st.auto_low_rating:
        return
    low = fb.service_rating and fb.service_rating <= st.low_rating_threshold
    bad_items = [i for i in fb.items.all() if i.rating and i.rating <= st.low_rating_threshold]
    if not (low or bad_items):
        return
    what = (f"service {fb.service_rating}★" if low else "") + ("; " if low and bad_items else "") + \
           ", ".join(f"{i.name} {i.rating}★" for i in bad_items[:3])
    svc.notify(svc.managers_for_branch(fb.branch), f"Low rating on {fb.order.number}",
               f"{what}" + (f" - \"{fb.comment[:90]}\"" if fb.comment else ""), "/feedback/", "bad")


def on_po_sent(po):
    """Approved purchase order -> supplier (email and/or WhatsApp)."""
    try:
        st = Setting.get()
        sup = po.supplier
        if not st.msg_supplier_po or not _once(f"po:{po.pk}"):
            return
        lines = "\n".join(f"- {l.ingredient.name}: {l.qty.normalize():f} {l.ingredient.unit}" for l in po.lines.select_related("ingredient"))
        body = (f"Purchase order {po.number}\nFrom: {st.business_name} - {po.branch.name}\nTo: {sup.name}\n\n{lines}\n\n"
                f"Please confirm the delivery date. Thank you.")
        if sup.email:
            messaging.send(sup.email, body, channel="email", kind="purchase_order", subject=f"Purchase order {po.number}", branch=po.branch)
        if sup.phone:
            messaging.send_phone(sup.phone, body, country=po.branch.country, kind="purchase_order", branch=po.branch)
        if not (sup.email or sup.phone):
            svc.notify(svc.supply_users(po.branch), f"{po.number}: supplier has no phone/email",
                       f"{sup.name} could not be notified automatically.", f"/purchase-orders/{po.pk}/", "info")
    except Exception:                                  # noqa: BLE001
        log.exception("PO message failed")


# ------------------------------------------------------------------ periodic jobs
def job_alerts():
    st = Setting.get()
    now = timezone.now()
    n = 0
    if st.auto_late_alerts:
        for o in Order.objects.filter(status=Order.PREPARING, started_at__isnull=False).select_related("branch", "chef").prefetch_related("lines__item"):
            tgt, el = prep_secs(o), (now - o.started_at).total_seconds()
            if el > tgt and _once(f"late:{o.pk}"):
                who = _branch_users(o.branch, ("kds",)) + svc.managers_for_branch(o.branch)
                svc.notify(who, f"Order {o.number} is running late",
                           f"Cooking for {int(el // 60)} min (target {tgt // 60})" + (f" - chef {o.chef.display_name}" if o.chef else ""),
                           "/kitchen/", "urgent")
                n += 1
        wait = st.late_queue_minutes * 60
        for o in Order.objects.filter(status=Order.NEW, chef__isnull=True).exclude(acceptance="pending").select_related("branch"):
            if (now - o.created_at).total_seconds() > wait and _once(f"lateq:{o.pk}"):
                svc.notify(_branch_users(o.branch, ("kds",)) + svc.managers_for_branch(o.branch), f"Order {o.number} waiting in queue",
                           f"Waiting {int((now - o.created_at).total_seconds() // 60)} min - every chef is full", "/kitchen/", "urgent")
                n += 1
    if st.auto_table_alert:
        cleaning = list(Table.objects.filter(status=Table.CLEANING).select_related("branch"))
        keys = [f"clean:{t.pk}" for t in cleaning]
        AutomationEvent.objects.filter(key__startswith="clean:").exclude(key__in=keys).delete()
        for t in cleaning:
            ev, _ = AutomationEvent.objects.get_or_create(key=f"clean:{t.pk}")
            if ev.note != "alerted" and now - ev.created_at > timedelta(minutes=st.table_cleaning_minutes):
                ev.note = "alerted"
                ev.save(update_fields=["note"])
                svc.notify(_branch_users(t.branch, ("waiter", "cashier")) + svc.managers_for_branch(t.branch),
                           f"Table {t.name} still needs cleaning", f"In 'Cleaning' for over {st.table_cleaning_minutes} min", "/tables/", "info")
                n += 1
    if st.auto_shift_reminder:
        for s in Shift.objects.filter(closed_at__isnull=True, opened_at__lt=now - timedelta(hours=st.shift_max_hours)).select_related("user", "branch"):
            if _once(f"shift:{s.pk}"):
                svc.notify([s.user] + svc.managers_for_branch(s.branch), f"Shift of {s.user.display_name} is still open",
                           f"Open since {timezone.localtime(s.opened_at, ZoneInfo(s.branch.timezone)):%d %b %H:%M} - close it and count the cash", "/shifts/", "urgent")
                n += 1
    return n


def job_riders():
    if not Setting.get().auto_assign_rider:
        return 0
    n = 0
    for o in Order.objects.filter(order_type=Order.DELIVERY, status=Order.READY, rider__isnull=True).select_related("branch"):
        if assign_rider(o):
            n += 1
    return n


def job_reorder():
    if not Setting.get().auto_reorder:
        return 0
    n = 0
    for b in Branch.objects.filter(is_active=True):
        if _once(f"reorder:{b.pk}:{local_now(b).date()}"):
            made, skipped = svc.create_reorder_pos(b, None)
            if made:
                svc.notify(svc.supply_users(b), "Auto reorder: draft purchase orders ready",
                           ", ".join(p.number for p in made) + " - review and approve", "/purchase-orders/", "info")
                n += len(made)
            if skipped:
                svc.notify(svc.supply_users(b), "Low stock without a supplier", ", ".join(skipped[:6]), "/ingredients/", "urgent")
    return n


def build_daily_report(branch, day=None):
    day = day or local_now(branch).date()
    with timezone.override(ZoneInfo(branch.timezone)):
        s = svc.day_summary(branch, day)
        start, end = svc.day_bounds(day)
        top = list(OrderItem.objects.filter(order__branch=branch, order__status=Order.COMPLETED, order__created_at__gte=start,
                                            order__created_at__lt=end, is_void=False).values("name").annotate(q=Sum("qty")).order_by("-q")[:3])
        done = Order.objects.filter(branch=branch, ready_at__gte=start, ready_at__lt=end, started_at__isnull=False)
        cook = [(r - st).total_seconds() / 60 for st, r in done.values_list("started_at", "ready_at")[:500]]
        fb = Feedback.objects.filter(branch=branch, created_at__gte=start, created_at__lt=end, service_rating__gt=0).aggregate(a=Avg("service_rating"), n=Count("id"))
    low = len(svc.low_ingredients(branch))
    lines = [f"{branch.name} - {day:%A %d %b %Y}",
             f"Sales: {_money(branch, s['sales'])} from {s['orders']} orders",
             "By type: " + (", ".join(f"{k.replace('_', ' ')} {v[0]}" for k, v in s["by_type"].items()) or "-"),
             "Payments: " + (", ".join(f"{k} {_money(branch, v)}" for k, v in s["by_method"].items()) or "-"),
             f"Tips: {_money(branch, s['tips'])}  Discounts: {_money(branch, s['discount'])}",
             f"Voids: {s['void_count']} ({_money(branch, s['void_amount'])})  Cancelled: {s['cancelled']}",
             "Top items: " + (", ".join(f"{t['name']} x{t['q']}" for t in top) or "-"),
             f"Avg cooking time: {round(sum(cook) / len(cook), 1)} min" if cook else "Avg cooking time: -",
             f"Customer rating: {fb['a']:.1f}/5 ({fb['n']} reviews)" if fb["n"] else "Customer rating: no reviews today",
             f"Low / out of stock: {low} ingredient(s)",
             f"Still open: {s['open_orders']} orders, {s['open_shifts']} shifts"]
    body = "\n".join(lines)
    if not DailyReport.objects.filter(branch=branch, date=day).exists():
        head = ai.report_headline(branch, body)
        if head:
            body = "AI summary: " + head + "\n\n" + body
    rep, created = DailyReport.objects.get_or_create(branch=branch, date=day, defaults={
        "text": body, "sales": s["sales"], "orders": s["orders"]})
    return rep, created


def job_daily_reports():
    st = Setting.get()
    if not st.daily_report:
        return 0
    n = 0
    for b in Branch.objects.filter(is_active=True):
        now = local_now(b)
        if now.hour < st.daily_report_hour or DailyReport.objects.filter(branch=b, date=now.date()).exists():
            continue
        rep, created = build_daily_report(b, now.date())
        if not created:
            continue
        n += 1
        mgrs = svc.managers_for_branch(b)
        svc.notify(mgrs, f"Daily report - {b.name}", f"{rep.orders} orders, {_money(b, rep.sales)}", "/automation/reports/", "info")
        short = rep.text if len(rep.text) < 900 else rep.text[:900]
        for m in mgrs:
            if m.email:
                messaging.send(m.email, rep.text, channel="email", kind="daily_report", subject=f"Daily report {b.name} {rep.date}", branch=b)
            if m.phone:
                messaging.send_phone(m.phone, short, country=b.country, kind="daily_report", branch=b)
    return n


def job_winback():
    st = Setting.get()
    if not st.winback_enabled:
        return 0
    now = timezone.now()
    n = 0
    qs = Customer.objects.filter(last_visit__lt=now - timedelta(days=st.winback_days), last_visit__gt=now - timedelta(days=240),
                                 visits__gte=1).order_by("last_visit")[:40]
    for c in qs:
        if _due(f"winback:{c.pk}", 60 * 86400):
            txt = st.winback_text.format_map({"name": (c.name or "there").split(" ")[0], "business": st.business_name,
                                              "points": c.points, "days": st.winback_days})
            messaging.send_phone(c.phone, txt, country=st.default_country, kind="winback")
            n += 1
    return n


def slip_data(kind, obj, year, month):
    """Everything on one monthly salary slip (waiter = User, rider = Rider)."""
    from . import analytics as an
    d1 = date(year, month, 1)
    d2 = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))
    if kind == "staff":             # cashier / chef / storekeeper: fixed salary only
        sal = obj.monthly_salary
        return {"p": obj, "label": obj.display_name, "kind": kind, "d1": d1, "d2": d2, "salary": sal, "commission": Decimal(0), "delivery_pay": Decimal(0),
                "tips": Decimal(0), "total": sal, "orders": 0, "revenue": Decimal(0), "branch": obj.branch}
    with timezone.override(ZoneInfo(obj.branch.timezone)):
        s = an.staff_profile_data(kind, obj, d1, d2, an._aware(d1), an._aware(d2 + timedelta(days=1)))["s"]
    sal = obj.monthly_salary
    total = sal + s["commission"] + s["delivery_pay"] + s["tips"]
    return {"p": obj, "label": getattr(obj, "display_name", None) or obj.name, "kind": kind, "d1": d1, "d2": d2, "salary": sal, "commission": s["commission"], "delivery_pay": s["delivery_pay"],
            "tips": s["tips"], "total": total, "orders": s["n"], "revenue": s["revenue"], "branch": obj.branch}


STAFF_SALARY_ROLES = ("cashier", "kitchen", "kds", "storekeeper")


def job_salary_slips():
    st = Setting.get()
    if not st.auto_salary_slips:
        return 0
    today = timezone.localdate()
    prev = date(today.year - (today.month == 1), (today.month - 2) % 12 + 1, 1)
    n = 0
    for b in Branch.objects.filter(is_active=True):
        if not _once(f"slips:{b.pk}:{prev:%Y-%m}"):
            continue
        staff = [("waiter", u) for u in User.objects.filter(branch=b, role="waiter", is_active=True)] + \
                [("rider", r) for r in Rider.objects.filter(branch=b, is_active=True)] + \
                [("staff", u) for u in User.objects.filter(branch=b, role__in=STAFF_SALARY_ROLES, is_active=True, monthly_salary__gt=0)]
        total = Decimal(0)
        for kind, o in staff:
            d = slip_data(kind, o, prev.year, prev.month)
            total += d["total"]
            if o.phone and (o.monthly_salary or d["total"]):
                messaging.send_phone(o.phone, f"{st.business_name} - salary slip {prev:%B %Y}\nSalary {_money(b, d['salary'])}, commission {_money(b, d['commission'])}, "
                                     f"delivery pay {_money(b, d['delivery_pay'])}, tips {_money(b, d['tips'])}\nTotal: {_money(b, d['total'])}",
                                     country=b.country, kind="salary_slip", branch=b)
        svc.notify(svc.managers_for_branch(b), f"Salary slips ready - {prev:%B %Y}", f"{len(staff)} staff, payroll {_money(b, total)}",
                   f"/automation/slips/?month={prev:%Y-%m}", "info")
        n += 1
    return n


def menu_payload(branch):
    return {"branch": branch.code, "currency": branch.currency_code,
            "items": [{"sku": m["sku"], "name": m["name"], "price": m["price"], "available": m["max_qty"] != 0} for m in svc.branch_menu(branch)]}


def job_menu_sync(force=False):
    """Push the menu to every channel that has a webhook URL, only when something changed."""
    from .models import MessageLog
    if not Setting.get().menu_sync:
        return 0
    n = 0
    for ch in Channel.objects.filter(is_active=True).exclude(webhook_url=""):
        for b in Branch.objects.filter(is_active=True, online_ordering=True):
            payload = menu_payload(b)
            h = hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            ev, _ = AutomationEvent.objects.get_or_create(key=f"sync:{ch.pk}:{b.pk}")
            if ev.note == h and not force:
                continue
            ok, err = messaging.sync_webhook(ch.webhook_url, payload, ch.api_key)
            MessageLog.objects.create(channel="webhook", kind="menu_sync", to=ch.webhook_url[:120], branch=b,
                                      body=f"{ch.name}: {len(payload['items'])} items for {b.code}", status="sent" if ok else "failed", error=err)
            if ok:
                ev.note = h
                ev.save(update_fields=["note"])
                n += 1
    return n


# ------------------------------------------------------------------ the engine
def tick(force=False):
    """Run everything that is due. Returns a dict of what happened (used by the cron URL / command / Run-now button)."""
    if not force and not _due("tick", 45):
        return {}
    out = {}
    for name, fn, every in (("alerts", job_alerts, 0), ("riders", job_riders, 0), ("daily_reports", job_daily_reports, 120),
                            ("reorder", job_reorder, 600), ("winback", job_winback, 6 * 3600), ("slips", job_salary_slips, 3600),
                            ("menu_sync", job_menu_sync, 300), ("ai_anomalies", ai.job_anomalies, 300), ("online_waiting", job_online_waiting, 0)):
        try:
            if force or every == 0 or _due(f"job:{name}", every):
                out[name] = fn()
        except Exception:                              # noqa: BLE001 - one broken job must not stop the rest
            log.exception("automation job %s failed", name)
            out[name] = "error"
    if _due("housekeeping", 86400):
        from .models import MessageLog, Notification
        Notification.objects.filter(created_at__lt=timezone.now() - timedelta(days=30)).delete()
        MessageLog.objects.filter(created_at__lt=timezone.now() - timedelta(days=90)).delete()
        AutomationEvent.objects.filter(created_at__lt=timezone.now() - timedelta(days=120)).exclude(
            key__in=("tick", "last_run", "housekeeping")).exclude(key__startswith="clean:").delete()
    AutomationEvent.objects.update_or_create(key="last_run", defaults={"note": json.dumps(out)[:250], "created_at": timezone.now()})
    return out
