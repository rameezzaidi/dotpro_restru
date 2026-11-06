"""Customer feedback analysis: sentiment + themes (English / Roman Urdu keywords), per item, waiter, rider and table.

Runs automatically whenever a page is opened — no manual step. The optional Claude advisor can write a narrative on top.
"""
import re
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Avg, Count
from django.utils import timezone

from .models import Feedback, ItemFeedback, Order

# theme -> (negative keywords, positive keywords). Matching is on lower-case text, whole words or phrases.
THEMES = {
    "taste": (["bland", "tasteless", "bad taste", "bekar", "be-swad", "beswad", "besvad", "kharab", "burnt", "jala", "sour", "not tasty",
               "too spicy", "bohat tez", "pheeka", "fika", "dry", "sukha", "raw", "kacha", "undercooked", "oily", "tel zyada", "too salty",
               "salty", "namak zyada", "namkeen"],
              ["tasty", "delicious", "yummy", "mazedar", "mazay", "lajawab", "zabardast", "behtareen", "amazing", "perfect", "great taste",
               "loved", "love it", "best", "excellent", "superb", "swad", "crispy", "juicy"]),
    "temperature": (["cold", "thanda", "thandi", "lukewarm", "not hot", "not warm", "garam nahi", "not cold", "thanda nahi"],
                    ["piping hot", "hot and fresh", "garam garam", "ice cold", "perfectly cold"]),
    "freshness": (["stale", "baasi", "basi", "not fresh", "old", "purana", "smell", "smelly", "bu aa", "stink"], ["fresh", "taza", "tazah"]),
    "portion": (["small portion", "small", "chota", "chhota", "less quantity", "quantity kam", "kam quantity", "tiny", "portion kam", "very less"],
                ["big portion", "generous", "filling", "bhar", "quantity achi", "value for money"]),
    "speed": (["late", "slow", "delay", "delayed", "dair", "der se", "waited", "wait", "took long", "took too long", "bohat time"],
              ["quick", "fast", "jaldi", "on time", "prompt"]),
    "service": (["rude", "ignored", "careless", "bad service", "bad behaviour", "bad behavior", "bakhlaq", "bad manners", "not attentive", "unprofessional"],
                ["polite", "friendly", "helpful", "courteous", "attentive", "good service", "great service", "akhlaaq", "professional", "smiling"]),
    "hygiene": (["dirty", "gandgi", "gandi", "hair in", "fly", "makhi", "cockroach", "unhygienic", "not clean", "messy"], ["clean", "saaf", "hygienic", "neat"]),
    "price": (["expensive", "mehnga", "mehngi", "costly", "overpriced", "too much price", "pricey"], ["affordable", "reasonable", "cheap", "sasta", "value"]),
}
LABEL = {"taste": "Taste", "temperature": "Temperature", "freshness": "Freshness", "portion": "Portion size", "speed": "Speed",
         "service": "Service / behaviour", "hygiene": "Hygiene", "price": "Price"}

_NEG_PHRASES = ("not hot", "not warm", "not cold", "not fresh", "not tasty", "not clean", "not attentive", "garam nahi", "thanda nahi")


def _has(text, kw):
    return re.search(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", text) is not None


def classify(text, rating=0):
    """Return (sentiment, [(theme, 'neg'|'pos')]). Sentiment: -1 / 0 / +1, rating decides when present."""
    t = (text or "").lower()
    found = []
    masked = t
    for th, (neg, pos) in THEMES.items():
        hit_neg = False
        for kw in sorted(neg, key=len, reverse=True):
            if _has(masked, kw):
                found.append((th, "neg"))
                hit_neg = True
                masked = re.sub(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", " ", masked)
                break
        if not hit_neg:
            for kw in pos:
                if _has(masked, kw):
                    found.append((th, "pos"))
                    break
    neg_n = sum(1 for _, p in found if p == "neg")
    pos_n = len(found) - neg_n
    if rating:
        sent = 1 if rating >= 4 else -1 if rating <= 2 else 0
        if sent == 0 and neg_n != pos_n:
            sent = 1 if pos_n > neg_n else -1
    else:
        sent = 1 if pos_n > neg_n else -1 if neg_n > pos_n else 0
    return sent, found


def _aware(d):
    return timezone.make_aware(timezone.datetime.combine(d, timezone.datetime.min.time()), timezone.get_current_timezone())


def _stars(v):
    return round(float(v or 0), 2)


def waiter_feedback(waiter, start, end):
    """Rating block for the waiter / rider profile."""
    field = "order__waiter" if getattr(waiter, "role", "") == "waiter" else "order__rider"
    fbs = Feedback.objects.filter(**{field: waiter}, created_at__gte=start, created_at__lt=end).select_related("order", "order__table")
    rated = [f for f in fbs if f.service_rating]
    themes = Counter()
    for f in fbs:
        for th, pol in classify(f.comment, f.service_rating)[1]:
            if th in ("service", "speed", "hygiene", "temperature") or True:
                themes[(th, pol)] += 1
    avg = sum(f.service_rating for f in rated) / len(rated) if rated else 0
    return {"n": fbs.count(), "avg": _stars(avg), "rated": len(rated),
            "dist": [sum(1 for f in rated if f.service_rating == k) for k in (5, 4, 3, 2, 1)],
            "dist_rows": [{"star": k, "n": sum(1 for f in rated if f.service_rating == k), "pct": round(sum(1 for f in rated if f.service_rating == k) / max(1, len(rated)) * 100)} for k in (5, 4, 3, 2, 1)],
            "pos": [(LABEL[t], n) for (t, p), n in themes.most_common() if p == "pos"][:5],
            "neg": [(LABEL[t], n) for (t, p), n in themes.most_common() if p == "neg"][:5],
            "recent": [f for f in fbs.order_by("-created_at") if f.comment][:6]}


def analyze(branches_qs, start, end, prev_start=None):
    """branches_qs: Feedback queryset already scoped. Returns everything the Feedback page shows."""
    fbs = list(branches_qs.filter(created_at__gte=start, created_at__lt=end)
               .select_related("order", "order__waiter", "order__rider", "order__table", "branch").prefetch_related("items"))
    done_n = Order.objects.filter(status=Order.COMPLETED, created_at__gte=start, created_at__lt=end,
                                  branch__in={f.branch_id for f in fbs} or []).count() if fbs else 0
    rated = [f for f in fbs if f.service_rating]
    food = [i for f in fbs for i in f.items.all() if i.rating]
    all_ratings = [f.service_rating for f in rated] + [i.rating for i in food]
    csat = round(sum(1 for r in all_ratings if r >= 4) / len(all_ratings) * 100) if all_ratings else None
    dist = [sum(1 for r in all_ratings if r == k) for k in (5, 4, 3, 2, 1)]

    items = defaultdict(lambda: {"ratings": [], "neg": Counter(), "pos": Counter(), "comments": [], "name": ""})
    themes = defaultdict(lambda: {"neg": 0, "pos": 0, "samples": []})
    sent = Counter()
    for f in fbs:
        s_overall, found = classify(f.comment, f.service_rating)
        sent[s_overall] += 1
        for th, pol in found:
            themes[th][pol] += 1
            if len(themes[th]["samples"]) < 3 and f.comment and pol == "neg":
                themes[th]["samples"].append(f.comment)
        for i in f.items.all():
            d = items[i.item_id or i.name]
            d["name"] = i.name
            if i.rating:
                d["ratings"].append(i.rating)
            s_i, fi = classify(i.comment, i.rating)
            for th, pol in fi:
                d["neg" if pol == "neg" else "pos"][th] += 1
                themes[th][pol] += 1
                if pol == "neg" and len(themes[th]["samples"]) < 3 and i.comment:
                    themes[th]["samples"].append(f"{i.name}: {i.comment}")
            if i.comment:
                d["comments"].append((i.rating, i.comment, f.created_at))
            sent[s_i] += 1

    item_rows = []
    for key, d in items.items():
        if not d["ratings"]:
            continue
        avg = sum(d["ratings"]) / len(d["ratings"])
        item_rows.append({"name": d["name"], "avg": _stars(avg), "n": len(d["ratings"]),
                          "neg": [(LABEL[t], n) for t, n in d["neg"].most_common(3)],
                          "pos": [(LABEL[t], n) for t, n in d["pos"].most_common(2)],
                          "comments": sorted(d["comments"], key=lambda c: c[2], reverse=True)[:3],
                          "low": sum(1 for r in d["ratings"] if r <= 2)})
    item_rows.sort(key=lambda r: (r["avg"], -r["n"]))

    by_w = defaultdict(list)
    by_r = defaultdict(list)
    for f in rated:
        if f.order.waiter_id:
            by_w[f.order.waiter].append(f.service_rating)
        if f.order.rider_id:
            by_r[f.order.rider].append(f.service_rating)
    mk = lambda dct: sorted([{"p": p, "avg": _stars(sum(v) / len(v)), "n": len(v)} for p, v in dct.items()], key=lambda r: -r["avg"])
    waiters, riders = mk(by_w), mk(by_r)

    theme_rows = sorted([{"key": k, "label": LABEL[k], **v} for k, v in themes.items()], key=lambda r: -(r["neg"] + r["pos"]))
    insights = _insights(fbs, rated, food, item_rows, waiters, riders, theme_rows, csat, done_n)
    if prev_start is not None:
        prev = list(branches_qs.filter(created_at__gte=prev_start, created_at__lt=start).values_list("service_rating", flat=True))
        prev = [p for p in prev if p]
        if prev and rated:
            a, b = sum(f.service_rating for f in rated) / len(rated), sum(prev) / len(prev)
            if abs(a - b) >= 0.15:
                insights.insert(0, {"level": "good" if a > b else "bad", "title": "Service rating trend",
                                    "text": f"Average service rating is {a:.2f} vs {b:.2f} in the previous period ({'up' if a > b else 'down'} {abs(a - b):.2f})."})
    return {"n": len(fbs), "rated": len(rated), "avg_service": _stars(sum(f.service_rating for f in rated) / len(rated)) if rated else 0,
            "avg_food": _stars(sum(i.rating for i in food) / len(food)) if food else 0, "csat": csat, "dist": dist,
            "dist_rows": [{"star": 5 - i, "n": n, "pct": round(n / max(dist + [1]) * 100)} for i, n in enumerate(dist)], "response_rate": round(len(fbs) / done_n * 100) if done_n else None,
            "items": item_rows, "waiters": waiters, "riders": riders, "themes": theme_rows, "insights": insights,
            "recent": sorted([f for f in fbs if f.comment], key=lambda f: f.created_at, reverse=True)[:12],
            "low": sorted([f for f in fbs if f.service_rating and f.service_rating <= 2 or (f.food_avg and f.food_avg <= 2)],
                          key=lambda f: f.created_at, reverse=True)[:8],
            "sentiment": {"pos": sent[1], "neu": sent[0], "neg": sent[-1]}}


def _insights(fbs, rated, food, item_rows, waiters, riders, theme_rows, csat, done_n):
    out = []
    add = lambda lvl, title, text: out.append({"level": lvl, "title": title, "text": text})
    if not fbs:
        return out
    if csat is not None:
        add("good" if csat >= 80 else "warn" if csat >= 65 else "bad", "Customer satisfaction",
            f"{csat}% of all ratings are 4★ or 5★ across {len(fbs)} feedback forms.")
    worst = [r for r in item_rows if r["n"] >= 3 and r["avg"] < 3.5]
    for r in worst[:3]:
        why = (" Main complaints: " + ", ".join(f"{t} ({n})" for t, n in r["neg"]) + ".") if r["neg"] else ""
        add("bad", f"{r['name']} is rated {r['avg']}★", f"{r['n']} ratings, {r['low']} of them 1–2★.{why} Check the recipe / kitchen process for this dish.")
    best = [r for r in reversed(item_rows) if r["n"] >= 3 and r["avg"] >= 4.3][:3]
    if best:
        add("good", "Customer favourites", ", ".join(f"{r['name']} ({r['avg']}★)" for r in best) + " — feature them in promotions.")
    for t in theme_rows:
        if t["neg"] >= 3 and t["neg"] > t["pos"]:
            add("warn", f"Recurring complaint: {t['label'].lower()}", f"{t['neg']} mentions" + (f", e.g. “{t['samples'][0]}”" if t["samples"] else "") + ".")
    if len(waiters) >= 2:
        w = [x for x in waiters if x["n"] >= 3]
        if w:
            add("good", "Best rated waiter", f"{w[0]['p'].display_name} — {w[0]['avg']}★ from {w[0]['n']} customers.")
            if w[-1]["avg"] < 3.8 and w[-1] is not w[0]:
                add("warn", "Waiter needs coaching", f"{w[-1]['p'].display_name} is rated {w[-1]['avg']}★ from {w[-1]['n']} customers — review the comments on their profile.")
    r = [x for x in riders if x["n"] >= 3]
    if r and r[-1]["avg"] < 3.8:
        add("warn", "Delivery rating is low", f"Rider {r[-1]['p'].name}: {r[-1]['avg']}★ ({r[-1]['n']} ratings). Late / cold food is the usual reason.")
    if done_n and len(fbs) / done_n < 0.2:
        add("info", "Few customers rate", f"Only {len(fbs) / done_n * 100:.0f}% of completed orders got feedback. Keep the QR code on every bill and ask waiters to mention it.")
    return out


def llm_payload(a, branch_name):
    return {"branch": branch_name, "responses": a["n"], "avg_service": a["avg_service"], "avg_food": a["avg_food"], "csat_pct": a["csat"],
            "worst_items": [{"item": r["name"], "avg": r["avg"], "n": r["n"], "complaints": r["neg"]} for r in a["items"][:6]],
            "best_items": [{"item": r["name"], "avg": r["avg"], "n": r["n"]} for r in a["items"][-4:]],
            "themes": [{"theme": t["label"], "negative": t["neg"], "positive": t["pos"], "samples": t["samples"]} for t in a["themes"][:8]],
            "waiters": [{"name": w["p"].display_name, "avg": w["avg"], "n": w["n"]} for w in a["waiters"]],
            "latest_comments": [f.comment for f in a["recent"][:10]]}
