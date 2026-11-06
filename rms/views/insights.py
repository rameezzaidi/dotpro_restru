"""Staff performance profiles, menu cost / top sellers, AI insights & forecast."""
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .. import analytics as an
from ..models import Rider, User
from ..permissions import role_required, scope


def _scoper(request):
    return lambda qs: scope(qs, request)


@role_required("manager")
def staff_performance(request):
    d1, d2, start, end = an.period(request)
    waiters = an.staff_board("waiter", _scoper(request), start, end)
    riders = an.staff_board("rider", _scoper(request), start, end)
    return render(request, "rms/staff_perf.html", {"f": {"from": str(d1), "to": str(d2)}, "waiters": waiters, "riders": riders,
                                                    "tab": request.GET.get("tab", "waiters")})


def _profile(request, kind, pk):
    obj = get_object_or_404(User, pk=pk, role="waiter") if kind == "waiter" else get_object_or_404(Rider, pk=pk)
    u = request.user
    if u.is_superuser or u.role in ("owner", "manager"):
        if not request.branch_all and obj.branch_id != request.branch.pk:
            raise Http404()
    elif kind == "waiter" and u.role == "waiter" and u.pk == obj.pk:
        pass
    else:
        raise Http404()
    d1, d2, start, end = an.period(request)
    data = an.staff_profile_data(kind, obj, d1, d2, start, end)
    return render(request, "rms/staff_profile.html", {"kind": kind, "p": obj, "f": {"from": str(d1), "to": str(d2)}, **data,
                                                       "pbranch": obj.branch})


@login_required
def waiter_profile(request, pk):
    return _profile(request, "waiter", pk)


@role_required("manager", "cashier")
def rider_profile(request, pk):
    return _profile(request, "rider", pk)


@login_required
def my_stats(request):
    if request.user.role == "waiter":
        return redirect("waiter_profile", pk=request.user.pk)
    return redirect("staff_perf")


@role_required("manager")
def menu_analytics(request):
    branch, picker = an.single_branch(request)
    d1, d2, start, end = an.period(request)
    try:
        target = max(5, min(80, int(request.GET.get("target", 30))))
    except ValueError:
        target = 30
    rows, avg_margin = an.menu_rows(branch, start, end, target)
    from ..models import MenuItem
    previews = {}
    for it in MenuItem.objects.filter(pk__in=[r["id"] for r in rows]).prefetch_related("recipe__ingredient"):
        lines, total = an.recipe_preview(it, branch)
        previews[it.pk] = {"lines": [{"name": l["name"], "qty": f"{l['qty']:f} {l['unit']}", "unit_cost": f"{l['unit_cost']:.2f}",
                                      "cost": f"{l['cost']:.2f}", "pct": l["pct"]} for l in lines], "total": f"{total:.2f}"}
    from ..templatetags.rms_tags import money
    pv = {}
    for r in rows:
        r["preview"] = previews.get(r["id"])
        if r["preview"] and r["preview"]["lines"]:
            pv[str(r["id"])] = {"name": r["name"], "price": money(r["price"], branch), "total": r["preview"]["total"],
                                "cost_pct": r["cost_pct"] or "",
                                "lines": [[l["name"], l["qty"], l["unit_cost"], l["cost"], l["pct"]] for l in r["preview"]["lines"]]}
    by_qty = sorted([r for r in rows if r["q"]], key=lambda r: -r["q"])[:10]
    by_profit = sorted([r for r in rows if r["profit"] is not None and r["q"]], key=lambda r: -r["profit"])[:10]
    mq = max([r["q"] for r in by_qty] + [1])
    mp = max([float(r["profit"]) for r in by_profit] + [1])
    for r in by_qty:
        r["qpct"] = round(r["q"] / mq * 100)
    for r in by_profit:
        r["ppct"] = round(float(r["profit"]) / mp * 100)
    tot_rev = sum(r["rev"] for r in rows)
    tot_profit = sum((r["profit"] or 0) for r in rows)
    costed = [r for r in rows if r["cost_pct"] is not None]
    return render(request, "rms/menu_analytics.html", {
        "abranch": branch, "picker": picker, "f": {"from": str(d1), "to": str(d2), "target": target}, "rows": rows,
        "pv": pv, "by_qty": by_qty, "by_profit": by_profit, "tot_rev": tot_rev, "tot_profit": tot_profit,
        "avg_cost_pct": round(sum(r["cost_pct"] for r in costed) / len(costed), 1) if costed else None,
        "nocost": len(rows) - len(costed), "classes": an.CLASS_INFO})


@role_required("manager")
def ai_insights(request):
    branch, picker = an.single_branch(request)
    ai = an.build_ai(branch)
    return render(request, "rms/ai_insights.html", {"abranch": branch, "picker": picker, "ai": ai})


@role_required("manager")
@require_POST
def ai_ask(request):
    branch, _ = an.single_branch(request)
    ai = an.build_ai(branch)
    lang = {"en": "English", "ur": "Roman Urdu (Urdu written in English letters)"}.get(request.POST.get("lang"), "English")
    text, err = an.ask_claude(an.llm_summary_payload(branch, ai), lang)
    return JsonResponse({"text": text, "error": err})


# ------------------------------------------------------------------ customer feedback
from datetime import timedelta  # noqa: E402

from django.utils import timezone  # noqa: E402

from .. import feedback as fbk  # noqa: E402
from ..models import Feedback, ItemFeedback, Order  # noqa: E402


def feedback_form(request, token):
    """Public page the customer opens from the QR code on the bill (no login)."""
    o = get_object_or_404(Order.objects.select_related("branch", "waiter", "rider"), feedback_token=token)
    lines = {}
    for l in o.lines.filter(is_void=False):
        key = l.item_id or l.name
        lines.setdefault(key, {"key": str(key), "item_id": l.item_id, "name": l.name, "qty": 0})["qty"] += l.qty
    done = hasattr(o, "feedback")
    if o.status != Order.COMPLETED:
        return render(request, "rms/feedback_form.html", {"o": o, "state": "early"})
    if done:
        return render(request, "rms/feedback_form.html", {"o": o, "state": "thanks"})
    if request.method == "POST":
        def rate(v):
            try:
                return max(0, min(5, int(v)))
            except (TypeError, ValueError):
                return 0
        service = rate(request.POST.get("service"))
        rows = []
        for it in lines.values():
            r = rate(request.POST.get(f"r_{it['key']}"))
            c = (request.POST.get(f"c_{it['key']}") or "").strip()[:300]
            if r or c:
                rows.append((it, r, c))
        comment = (request.POST.get("comment") or "").strip()[:500]
        if not (service or comment or rows):
            return render(request, "rms/feedback_form.html", {"o": o, "state": "form", "items": list(lines.values()),
                                                               "error": "Please rate at least one thing."})
        fb = Feedback.objects.create(order=o, branch=o.branch, service_rating=service, comment=comment)
        ItemFeedback.objects.bulk_create([ItemFeedback(feedback=fb, item_id=it["item_id"], name=it["name"], rating=r, comment=c)
                                          for it, r, c in rows])
        try:
            from ..automation import on_feedback
            on_feedback(fb)
        except Exception:                      # noqa: BLE001 - an alert must never break the customer's thank-you page
            pass
        return render(request, "rms/feedback_form.html", {"o": o, "state": "thanks", "just": True})
    return render(request, "rms/feedback_form.html", {"o": o, "state": "form", "items": list(lines.values())})


@role_required("manager")
def feedback_page(request):
    d1, d2, start, end = an.period(request, default_days=30)
    qs = scope(Feedback.objects.all(), request)
    span = end - start
    a = fbk.analyze(qs, start, end, prev_start=start - span)
    return render(request, "rms/feedback.html", {"f": {"from": str(d1), "to": str(d2)}, "a": a})


@role_required("manager")
@require_POST
def feedback_ask(request):
    d1, d2, start, end = an.period(request, default_days=30)
    a = fbk.analyze(scope(Feedback.objects.all(), request), start, end)
    lang = {"en": "English", "ur": "Roman Urdu (Urdu written in English letters)"}.get(request.POST.get("lang"), "English")
    text, err = an.ask_claude(fbk.llm_payload(a, request.branch.name if request.branch else "All branches"), lang,
                              task="Analyse this customer feedback summary: main problems per dish and per staff member, what customers love, "
                                   "and 6 prioritised actions.")
    return JsonResponse({"text": text, "error": err})
