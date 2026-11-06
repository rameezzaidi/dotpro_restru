"""Shifts, day close, void log, reservations, supply helpers (standard restaurant flow)."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .. import services as svc
from ..models import DayClose, Order, Payment, Reservation, Shift, Table, VoidLog
from ..permissions import role_required, scope


def _dec(v, default=None):
    try:
        return Decimal(str(v).strip())
    except (InvalidOperation, ValueError, AttributeError):
        return default


def _need_branch(request):
    if not request.branch:
        messages.warning(request, "Select a specific branch from the top bar first.")
        return redirect("dashboard")
    return None


# ------------------------------------------------------------------ shifts
@role_required("manager", "cashier")
def shifts(request):
    qs = scope(Shift.objects.select_related("branch", "user"), request)
    if request.user.role == "cashier" and not request.user.is_superuser:
        qs = qs.filter(user=request.user)
    mine = svc.current_shift(request.branch, request.user) if request.branch else None
    rows = []
    for sh in qs[:60]:
        sh.totals = svc.shift_totals(sh)
        rows.append(sh)
    return render(request, "rms/shifts.html", {"shifts": rows, "mine": mine,
                                               "mine_totals": svc.shift_totals(mine) if mine else None})


@role_required("manager", "cashier")
@require_POST
def shift_action(request):
    bad = _need_branch(request)
    if bad:
        return bad
    try:
        if request.POST.get("action") == "open":
            svc.open_shift(request.branch, request.user, _dec(request.POST.get("opening_cash"), Decimal(0)))
            messages.success(request, "Shift opened. Payments you take are now tracked in this shift.")
        elif request.POST.get("action") == "close":
            sh = get_object_or_404(scope(Shift.objects, request), pk=request.POST.get("shift") or 0)
            if sh.user_id != request.user.pk and not request.user.can_access_all and request.user.role != "manager":
                raise svc.OrderError("This is another cashier's shift")
            svc.close_shift(sh, _dec(request.POST.get("counted_cash")), request.POST.get("note", ""))
            messages.success(request, "Shift closed.")
            return redirect("shift_report", pk=sh.pk)
    except svc.OrderError as e:
        messages.error(request, str(e))
    return redirect("shifts")


@role_required("manager", "cashier")
def shift_report(request, pk):
    sh = get_object_or_404(scope(Shift.objects.select_related("branch", "user"), request), pk=pk)
    t = svc.shift_totals(sh)
    pays = sh.payments.select_related("order").order_by("created_at")
    return render(request, "rms/shift_report.html", {"sh": sh, "t": t, "pays": pays})


# ------------------------------------------------------------------ day close
@role_required("manager")
def day_close(request):
    bad = _need_branch(request)
    if bad:
        return bad
    b = request.branch
    today = timezone.localdate()
    if request.method == "POST":
        try:
            if request.POST.get("action") == "close":
                svc.close_day(b, today, request.user)
                messages.success(request, "Business day closed. New orders are blocked until it is reopened.")
            elif request.POST.get("action") == "reopen":
                DayClose.objects.filter(pk=request.POST.get("id") or 0, branch=b).delete()
                messages.success(request, "Day reopened.")
        except svc.OrderError as e:
            messages.error(request, str(e))
        return redirect("day_close")
    closed = DayClose.objects.filter(branch=b, date=today).first()
    return render(request, "rms/day_close.html", {
        "s": svc.day_summary(b, today), "today": today, "closed": closed,
        "history": DayClose.objects.filter(branch=b).select_related("closed_by")[:30],
        "open_orders": Order.objects.filter(branch=b, status__in=Order.ACTIVE).order_by("created_at")[:20],
        "open_shifts": Shift.objects.filter(branch=b, closed_at__isnull=True).select_related("user")})


# ------------------------------------------------------------------ void / cancel log
@role_required("manager")
def voids(request):
    qs = scope(VoidLog.objects.select_related("branch", "user", "approved_by"), request)
    today = timezone.localdate()
    f_from, f_to = request.GET.get("from") or str(today - timedelta(days=6)), request.GET.get("to") or str(today)
    try:
        d1 = timezone.datetime.fromisoformat(f_from).date()
        d2 = timezone.datetime.fromisoformat(f_to).date()
    except ValueError:
        d1, d2 = today - timedelta(days=6), today
    s, _ = svc.day_bounds(d1)
    _, e = svc.day_bounds(d2)
    qs = qs.filter(created_at__gte=s, created_at__lt=e)
    kind = request.GET.get("kind", "")
    if kind:
        qs = qs.filter(kind=kind)
    agg = qs.exclude(kind=VoidLog.REFUND).aggregate(n=Count("id"), t=Sum("amount"))
    wasted = qs.filter(after_cooking=True).exclude(kind=VoidLog.REFUND).aggregate(t=Sum("amount"))["t"] or 0
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    return render(request, "rms/voids.html", {"page": page, "agg": agg, "wasted": wasted, "kinds": VoidLog.KINDS,
                                              "f": {"from": str(d1), "to": str(d2), "kind": kind}})


# ------------------------------------------------------------------ reservations
@role_required("manager", "cashier", "waiter")
def reservations(request):
    qs = scope(Reservation.objects.select_related("table", "branch"), request)
    today = timezone.localdate()
    try:
        day = timezone.datetime.fromisoformat(request.GET.get("date") or str(today)).date()
    except ValueError:
        day = today
    s, e = svc.day_bounds(day)
    b = request.branch
    if request.method == "POST":
        bad = _need_branch(request)
        if bad:
            return bad
        try:
            at = timezone.make_aware(timezone.datetime.fromisoformat(request.POST.get("at")), timezone.get_current_timezone())
            name = request.POST.get("name", "").strip()
            if not name:
                raise ValueError
            table = Table.objects.filter(pk=request.POST.get("table") or 0, branch=b).first()
            r = Reservation.objects.create(branch=b, table=table, name=name[:100], phone=request.POST.get("phone", "")[:30],
                                           guests=max(1, int(request.POST.get("guests") or 2)), at=at,
                                           notes=request.POST.get("notes", "")[:200], created_by=request.user)
            if table and r.at.date() == today and table.status == Table.FREE:
                table.status = Table.RESERVED
                table.save(update_fields=["status"])
            messages.success(request, f"Reservation for {r.name} saved.")
        except (ValueError, TypeError):
            messages.error(request, "Enter a name and a valid date / time.")
        return redirect(f"/reservations/?date={day}")
    return render(request, "rms/reservations.html", {
        "items": qs.filter(at__gte=s, at__lt=e), "day": day, "tables": Table.objects.filter(branch=b) if b else [],
        "prev": day - timedelta(days=1), "next": day + timedelta(days=1),
        "default_at": f"{day}T19:00"})


@role_required("manager", "cashier", "waiter")
@require_POST
def reservation_action(request, pk):
    r = get_object_or_404(scope(Reservation.objects.select_related("table"), request), pk=pk)
    act = request.POST.get("action")
    if r.status != Reservation.BOOKED:
        messages.error(request, "Reservation is already closed.")
        return redirect("reservations")
    if act == "seat":
        r.status = Reservation.SEATED
        r.save(update_fields=["status"])
        if r.table:
            return redirect(f"/tables/{r.table_id}/open/")
        messages.success(request, "Guest seated — pick a table in POS.")
        return redirect("tables")
    if act in ("cancel", "no_show"):
        r.status = Reservation.CANCELLED if act == "cancel" else Reservation.NOSHOW
        r.save(update_fields=["status"])
        if r.table and r.table.status == Table.RESERVED and not Reservation.objects.filter(
                table=r.table, status=Reservation.BOOKED).exclude(pk=r.pk).exists():
            Table.objects.filter(pk=r.table_id).update(status=Table.FREE)
    return redirect(f"/reservations/?date={timezone.localtime(r.at).date()}")
