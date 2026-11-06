"""Restopro AI layer.

Every feature here works in two modes:
  * with ANTHROPIC_API_KEY (environment) -> Claude answers, using a compact snapshot of the restaurant's own numbers
  * without a key (or when the call fails) -> a deterministic rule-based answer, so nothing in the app ever breaks.
Each call is written to AILog (feature, who answered, time taken) and shown on the AI Centre page.
"""
import difflib
import json
import logging
import os
import re
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Avg, Count, Sum
from django.utils import timezone

from . import services as svc
from .models import AILog, AISession, Branch, BranchItem, Feedback, MenuItem, Order, OrderItem, Setting, StockMovement, VoidLog

log = logging.getLogger(__name__)
D0 = Decimal("0")
API_URL = "https://api.anthropic.com/v1/messages"


# --------------------------------------------------------------------------- core call
def api_key():
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def model_name():
    return os.environ.get("RESTOPRO_AI_MODEL", "claude-sonnet-5-5")


def available():
    try:
        on = Setting.get().ai_enabled
    except Exception:       # noqa: BLE001
        on = True
    return bool(api_key()) and on


def _call(system, user, max_tokens):
    """The only place that touches the network (tests replace this). Returns text or raises."""
    body = {"model": model_name(), "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": user}]}
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), method="POST", headers={
        "x-api-key": api_key(), "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.load(r)
    return "".join(b.get("text", "") for b in data.get("content", []))


def llm(kind, system, user, max_tokens=700):
    """Returns (text, error). text is None when Claude is off / failed -> the caller uses its fallback."""
    if not available():
        return None, "no key"
    t0 = time.time()
    try:
        text = _call(system, user, max_tokens)
        ok, err = bool(text.strip()), "" if text.strip() else "empty answer"
    except Exception as e:      # noqa: BLE001 - network / quota problems must never break a page
        text, ok, err = None, False, str(e)[:190]
    try:
        AILog.objects.create(kind=kind, used_llm=True, ok=ok, ms=int((time.time() - t0) * 1000), error=err)
    except Exception:           # noqa: BLE001
        pass
    return (text if ok else None), (err or None)


def note(kind, ms=0):
    """Record a rule-based (no Claude) answer."""
    try:
        AILog.objects.create(kind=kind, used_llm=False, ok=True, ms=ms)
    except Exception:           # noqa: BLE001
        pass


def parse_json(text):
    if not text:
        return None
    m = re.search(r"\{.*\}|\[.*\]", text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None


def usage(days=30):
    since = timezone.now() - timedelta(days=days)
    qs = AILog.objects.filter(created_at__gte=since)
    rows = list(qs.values("kind").annotate(n=Count("id"), llm=Sum("used_llm"), avg=Avg("ms")).order_by("-n"))
    tot = qs.count()
    return {"rows": rows, "total": tot, "llm": qs.filter(used_llm=True, ok=True).count(), "failed": qs.filter(ok=False).count(),
            "recent_errors": list(qs.filter(ok=False).values_list("error", flat=True)[:3])}


# --------------------------------------------------------------------------- snapshot of the business
def _local_today(branch):
    return timezone.now().astimezone(ZoneInfo(branch.timezone)).date()


def snapshot(branch):
    """Compact facts about one branch, used both as Claude's context and by the rule-based copilot."""
    from . import analytics as an
    today = _local_today(branch)
    s = svc.day_summary(branch, today)
    week = []
    for i in range(7):
        d = today - timedelta(days=i)
        ds = svc.day_summary(branch, d)
        week.append({"date": str(d), "sales": float(ds["sales"]), "orders": ds["orders"]})
    start, end = svc.day_bounds(today)
    top = list(OrderItem.objects.filter(order__branch=branch, order__created_at__gte=start, order__created_at__lt=end, is_void=False)
               .exclude(order__status=Order.CANCELLED).values("name").annotate(q=Sum("qty")).order_by("-q")[:5])
    low = [{"name": st.ingredient.name, "left": float(st.quantity), "unit": st.ingredient.unit} for st in svc.low_ingredients(branch)[:10]]
    now = timezone.now()
    late = Order.objects.filter(branch=branch, status__in=(Order.NEW, Order.PREPARING), created_at__lt=now - timedelta(minutes=25)).count()
    fb = Feedback.objects.filter(branch=branch, created_at__gte=now - timedelta(days=7), service_rating__gt=0).aggregate(a=Avg("service_rating"), n=Count("id"))
    try:
        fc = an.build_ai(branch)
        tomorrow = {"expected": float(fc["tomorrow"]["value"]), "low": float(fc["tomorrow"]["low"]), "high": float(fc["tomorrow"]["high"])} if fc.get("days_of_data") else None
        buy = [{"name": r["name"], "short": round(float(r["short"]), 1), "unit": r["unit"]} for r in fc.get("ing", []) if r["short"] > 0][:8]
    except Exception:           # noqa: BLE001
        tomorrow, buy = None, []
    return {
        "branch": branch.name, "currency": branch.currency_code, "date": str(today),
        "today": {"sales": float(s["sales"]), "orders": s["orders"], "discount": float(s["discount"]), "tips": float(s["tips"]),
                  "voids": s["void_count"], "void_amount": float(s["void_amount"]), "cancelled": s["cancelled"],
                  "open_orders": s["open_orders"], "by_type": {k: {"orders": v[0], "sales": float(v[1] or 0)} for k, v in s["by_type"].items()},
                  "top_items": [{"name": t["name"], "qty": t["q"]} for t in top]},
        "last_7_days": week, "late_orders": late, "low_stock": low, "to_buy": buy,
        "rating_7d": {"avg": round(float(fb["a"]), 2) if fb["a"] else None, "reviews": fb["n"]}, "tomorrow_forecast": tomorrow}


# --------------------------------------------------------------------------- copilot ("ask anything")
URDU_HINT = {"kitna", "kitne", "kitni", "aaj", "kal", "hai", "hain", "ka", "ki", "ke", "kya", "sab", "se", "zyada", "kam", "batao", "bata",
             "khatam", "bikri", "mehnga", "sasta", "dikhao", "konsa", "kaun", "kon", "mein", "mai", "wala", "hua", "hui", "karo", "chahiye", "hamara"}


def is_urdu(text):
    words = set(re.findall(r"[a-z]+", (text or "").lower()))
    return len(words & URDU_HINT) >= 1


def _money(b, v):
    return f"{b.currency_symbol}{float(v):,.0f}"


def _rule_answer(q, snap, branch, urdu):
    ql = q.lower()
    t, w = snap["today"], snap["last_7_days"]
    m = lambda v: _money(branch, v)       # noqa: E731

    def has(*keys):
        return any(k in ql for k in keys)

    if has("kal", "tomorrow", "forecast", "predict", "agle"):
        f = snap["tomorrow_forecast"]
        if not f:
            return "Forecast ke liye abhi kaafi sales data nahi hai." if urdu else "Not enough sales history for a forecast yet."
        return (f"Kal ki expected sale {m(f['expected'])} hai (range {m(f['low'])} - {m(f['high'])})." if urdu
                else f"Tomorrow's expected sales: {m(f['expected'])} (range {m(f['low'])} - {m(f['high'])}).")
    if has("stock", "khatam", "low", "ingredient", "kami", "buy", "kharid", "order karna"):
        if not snap["low_stock"] and not snap["to_buy"]:
            return "Abhi koi cheez kam ya khatam nahi hai." if urdu else "Nothing is low or out of stock right now."
        low = ", ".join(f"{x['name']} ({x['left']:g} {x['unit']})" for x in snap["low_stock"]) or "-"
        buy = ", ".join(f"{x['name']} {x['short']:g} {x['unit']}" for x in snap["to_buy"]) or "-"
        return (f"Kam stock: {low}. Forecast ke hisaab se kharidna hai: {buy}." if urdu else f"Low stock: {low}. To buy (forecast): {buy}.")
    if has("top", "best", "sab se zyada", "zyada bik", "popular", "bestseller", "mashhoor"):
        if not t["top_items"]:
            return "Aaj abhi tak koi item nahi bika." if urdu else "Nothing sold yet today."
        items = ", ".join(f"{x['name']} x{x['qty']}" for x in t["top_items"])
        return (f"Aaj sab se zyada bikne wale: {items}." if urdu else f"Top sellers today: {items}.")
    if has("late", "delay", "der", "queue", "kitchen"):
        return (f"Is waqt {snap['late_orders']} order 25 minute se zyada se kitchen mein hain; {t['open_orders']} order open hain." if urdu
                else f"{snap['late_orders']} order(s) have been in the kitchen over 25 minutes; {t['open_orders']} open in total.")
    if has("rating", "feedback", "review", "customer"):
        r = snap["rating_7d"]
        if not r["reviews"]:
            return "Pichle 7 din mein koi feedback nahi aaya." if urdu else "No feedback in the last 7 days."
        return (f"Pichle 7 din ki average rating {r['avg']}/5 hai ({r['reviews']} reviews)." if urdu
                else f"7-day average rating is {r['avg']}/5 from {r['reviews']} reviews.")
    if has("void", "cancel", "discount", "refund"):
        return (f"Aaj {t['voids']} void ({m(t['void_amount'])}), {t['cancelled']} cancel aur {m(t['discount'])} discount hua." if urdu
                else f"Today: {t['voids']} void(s) worth {m(t['void_amount'])}, {t['cancelled']} cancelled, discounts {m(t['discount'])}.")
    if has("week", "hafta", "haftay", "7 din", "pichle"):
        tot = sum(x["sales"] for x in w)
        best = max(w, key=lambda x: x["sales"])
        return (f"Pichle 7 din ki sale {m(tot)} hai. Sab se acha din {best['date']} ({m(best['sales'])})." if urdu
                else f"Last 7 days sales: {m(tot)}. Best day {best['date']} ({m(best['sales'])}).")
    return (f"Aaj ki sale {m(t['sales'])} hai, {t['orders']} orders. {t['open_orders']} order open hain." if urdu
            else f"Today: {m(t['sales'])} from {t['orders']} orders, {t['open_orders']} still open.")


def copilot(branch, question):
    """Returns {"text", "engine"}. Roman Urdu in -> Roman Urdu out."""
    q = (question or "").strip()[:500]
    if not q:
        return {"text": "", "engine": "none"}
    t0 = time.time()
    snap = snapshot(branch)
    urdu = is_urdu(q)
    system = ("You are the AI copilot of a restaurant POS called Restopro, talking to the owner or manager. Answer ONLY from the JSON "
              "data given; if it does not contain the answer, say so and suggest which page in the app to open. Be brief (max 6 lines), "
              "concrete, with numbers and currency. Reply in the same language the question is written in; if it is Roman Urdu "
              "(Urdu in English letters) answer in Roman Urdu. No markdown tables.")
    text, _ = llm("copilot", system, f"DATA:\n{json.dumps(snap, default=str)}\n\nQUESTION: {q}", 500)
    if text:
        return {"text": text.strip(), "engine": "claude"}
    ans = _rule_answer(q, snap, branch, urdu)
    note("copilot", int((time.time() - t0) * 1000))
    return {"text": ans, "engine": "rules"}


# --------------------------------------------------------------------------- order taking (text / WhatsApp -> cart)
NUM_WORDS = {"aik": 1, "ek": 1, "one": 1, "a": 1, "an": 1, "do": 2, "two": 2, "teen": 3, "tin": 3, "three": 3, "char": 4, "chaar": 4, "four": 4,
             "panch": 5, "paanch": 5, "five": 5, "chay": 6, "che": 6, "chhe": 6, "six": 6, "saat": 7, "seven": 7, "aath": 8, "eight": 8,
             "nau": 9, "nine": 9, "das": 10, "ten": 10, "dozen": 12, "darjan": 12, "adha": 0.5}
FILLER = {"please", "plz", "pls", "chahiye", "chahye", "chahiyen", "chaiye", "bhej", "bhejo", "dena", "dedo", "de", "do", "mujhe", "mjhe",
          "hamein", "humein", "hume", "mera", "meri", "order", "want", "need", "i", "we", "give", "me", "get", "send", "kar", "karo", "bhi",
          "ka", "ki", "ke", "wala", "wali", "ek", "aik", "of", "the", "some", "mein", "mai", "hmein", "add", "kijiye", "krdo", "kardo", "dijiye",
          "bana", "banao", "lao", "laao", "hai", "hain", "x", "pcs", "piece", "pieces", "plate", "plates", "glass", "bottle"}
SPLIT = re.compile(r"\s*(?:,|;|\n|\+|&|\band\b|\baur\b|\bor\b|\bplus\b|\bwith\b|\bsath\b|\bsaath\b|\bphir\b)\s*", re.I)


def _tok(s):
    return re.findall(r"[a-z0-9]+", s.lower())


def _match_item(words, menu):
    """Fuzzy match the words of one chunk against the menu. Returns (item, score)."""
    if not words:
        return None, 0
    best, best_score = None, 0
    for m in menu:
        name_t = _tok(m["name"])
        if not name_t:
            continue
        hit_q = hit_n = 0
        used = set()
        for w in words:
            r, bi = max(((1.0 if w == n else (0 if len(w) <= 2 else difflib.SequenceMatcher(None, w, n).ratio()), k)
                         for k, n in enumerate(name_t)), default=(0, 0))
            if r >= 0.8:
                hit_q += 1
                used.add(bi)
        hit_n = len(used)
        score = 0.6 * hit_q / len(words) + 0.4 * hit_n / len(name_t)
        if hit_q and score > best_score + 1e-9:
            best, best_score = m, score
        elif hit_q and abs(score - best_score) < 1e-9 and best and len(m["name"]) < len(best["name"]):
            best = m
    return (best, best_score) if best_score >= 0.55 else (None, best_score)


def rule_parse(text, menu):
    lines, unmatched = defaultdict(int), []
    for chunk in SPLIT.split(text or ""):
        toks = _tok(chunk)
        if not toks:
            continue
        qty, rest = None, []
        for i, w in enumerate(toks):
            if w.isdigit() and qty is None and len(w) <= 2:
                qty = int(w)
                continue
            m = re.fullmatch(r"(\d{1,2})x|x(\d{1,2})", w)
            if m and qty is None:
                qty = int(m.group(1) or m.group(2))
                continue
            if qty is None and w in NUM_WORDS and w not in ("adha", "a", "an"):
                if w == "do" and i != 0:
                    continue            # trailing "do" means "give", not 2
                qty = NUM_WORDS[w]
                continue
            if qty is None and w in ("a", "an") and i == 0:
                qty = 1
                continue
            if w in FILLER:
                continue
            rest.append(w)
        item, score = _match_item(rest, menu)
        if item:
            lines[item["id"]] += max(1, int(qty or 1))
        elif rest:
            unmatched.append(" ".join(rest))
    return lines, unmatched


def parse_order(text, menu):
    """Free text -> {"lines":[{id,name,qty,price}], "unmatched":[...], "engine"}. `menu` = services.branch_menu(branch)."""
    t0 = time.time()
    text = (text or "").strip()[:600]
    by_id = {m["id"]: m for m in menu}
    lines, unmatched, engine = None, [], "rules"
    if text and available():
        listing = "\n".join(f'{m["id"]}|{m["name"]}' for m in menu)
        out, _ = llm("order_parse",
                     "You turn a customer's food order (English, Urdu or Roman Urdu; may have typos) into JSON. Use ONLY ids from the menu. "
                     'Reply with JSON only: {"items":[{"id":<menu id>,"qty":<int>}],"unmatched":["words you could not match"]}. '
                     "'aik/ek'=1, 'do'=2 when it is a quantity, 'teen'=3, 'char'=4, 'panch'=5.", f"MENU (id|name):\n{listing}\n\nORDER: {text}", 400)
        j = parse_json(out)
        if isinstance(j, dict) and isinstance(j.get("items"), list):
            tmp = defaultdict(int)
            for r in j["items"]:
                try:
                    if int(r["id"]) in by_id:
                        tmp[int(r["id"])] += max(1, min(int(r.get("qty", 1)), 50))
                except (KeyError, ValueError, TypeError):
                    continue
            if tmp:
                lines, unmatched, engine = tmp, [str(x) for x in j.get("unmatched", [])][:5], "claude"
    if lines is None:
        lines, unmatched = rule_parse(text, menu)
        note("order_parse", int((time.time() - t0) * 1000))
    out_lines = [{"id": i, "name": by_id[i]["name"], "qty": q, "price": by_id[i]["price"]} for i, q in lines.items() if i in by_id]
    return {"lines": out_lines, "unmatched": unmatched, "engine": engine,
            "total": round(sum(l["qty"] * l["price"] for l in out_lines), 2)}


# --------------------------------------------------------------------------- smart upsell
def upsell(branch, item_ids, limit=3):
    """'Frequently bought together' from this branch's own order history; falls back to best sellers of other categories."""
    item_ids = {int(i) for i in item_ids if str(i).isdigit()}
    if not item_ids:
        return []
    menu = {m["id"]: m for m in svc.branch_menu(branch)}
    since = timezone.now() - timedelta(days=90)
    orders = list(OrderItem.objects.filter(item_id__in=item_ids, order__branch=branch, order__created_at__gte=since, is_void=False)
                  .exclude(order__status=Order.CANCELLED).values_list("order_id", flat=True).distinct()[:600])
    names = {m["id"]: m["name"] for m in menu.values()}
    out, seen = [], set(item_ids)
    if orders:
        co = Counter()
        for iid in OrderItem.objects.filter(order_id__in=orders, is_void=False).exclude(item_id__in=item_ids).exclude(item__isnull=True).values_list("item_id", flat=True):
            co[iid] += 1
        for iid, n in co.most_common(12):
            m = menu.get(iid)
            if m and (m["max_qty"] is None or m["max_qty"] > 0):
                out.append({"id": iid, "name": m["name"], "price": m["price"], "reason": f"often ordered together ({n}x)"})
                seen.add(iid)
            if len(out) >= limit:
                break
    if len(out) < limit:       # fill with best sellers from categories not yet in the cart
        have_cats = {menu[i]["category"] for i in item_ids if i in menu}
        pop = Counter()
        for iid in OrderItem.objects.filter(order__branch=branch, order__created_at__gte=since, is_void=False).exclude(item__isnull=True).values_list("item_id", flat=True)[:3000]:
            pop[iid] += 1
        for iid, n in pop.most_common(30):
            m = menu.get(iid)
            if m and iid not in seen and m["category"] not in have_cats and (m["max_qty"] is None or m["max_qty"] > 0):
                out.append({"id": iid, "name": m["name"], "price": m["price"], "reason": "popular add-on"})
                seen.add(iid)
            if len(out) >= limit:
                break
    return out[:limit]


# --------------------------------------------------------------------------- menu advisor
def menu_advice(branch, days=30):
    """Per item: raise / lower price, promote, or drop - with the numbers. Returns list of dicts (and an optional Claude summary)."""
    from . import analytics as an
    end = timezone.now()
    rows, _ = an.menu_rows(branch, end - timedelta(days=days), end)
    sold = [r["q"] for r in rows if r.get("q")]
    med = sorted(sold)[len(sold) // 2] if sold else 0
    out = []
    for r in rows:
        mp, qty = r.get("margin_pct"), r.get("q") or 0
        price = Decimal(str(r["price"]))
        step = Decimal("5") if branch.currency_code == "PKR" else Decimal("0.5")
        act = None
        if mp is not None and qty >= max(med, 3) and mp < 55:
            new = (price * Decimal("1.07") / step).to_integral_value() * step
            act = {"type": "raise", "label": "Raise price", "new_price": new, "why": f"sells well ({qty} sold) but margin is only {mp}% - a small rise adds profit"}
        elif mp is not None and qty and qty < max(med / 2, 3) and mp >= 65:
            act = {"type": "promote", "label": "Promote", "why": f"high margin ({mp}%) but only {qty} sold - feature it / add to combos"}
        elif qty == 0:
            act = {"type": "review", "label": "Review / remove", "why": "no sales in this period"}
        elif mp is not None and qty < max(med / 2, 3) and mp < 45:
            act = {"type": "drop", "label": "Consider removing", "why": f"low sales ({qty}) and low margin ({mp}%)"}
        if act:
            out.append({"id": r["id"], "name": r["name"], "price": price, "qty": qty, "margin_pct": mp, **act})
    order = {"raise": 0, "drop": 1, "promote": 2, "review": 3}
    out.sort(key=lambda x: (order[x["type"]], -x["qty"]))
    return out[:15]


def menu_summary(branch, advice):
    if not advice:
        return None
    payload = [{k: (float(v) if isinstance(v, Decimal) else v) for k, v in a.items() if k in ("name", "price", "qty", "margin_pct", "type", "why")} for a in advice]
    text, _ = llm("menu_advice", "You are a restaurant menu-engineering consultant. In 4 short bullet lines (plain text, '-' bullets) summarise the biggest "
                  "profit opportunities from this data. Reply in Roman Urdu mixed with English menu names.", json.dumps(payload), 350)
    return text


# --------------------------------------------------------------------------- feedback replies
def reply_drafts(branch, days=14, limit=8):
    """Draft a personal reply for each recent low-rated feedback that has a phone number."""
    since = timezone.now() - timedelta(days=days)
    out = []
    qs = Feedback.objects.filter(branch=branch, created_at__gte=since).select_related("order").prefetch_related("items").order_by("-created_at")
    for f in qs:
        low_items = [i for i in f.items.all() if 0 < i.rating <= 2]
        if not ((0 < f.service_rating <= 3) or low_items):
            continue
        o = f.order
        if not o.customer_phone:
            continue
        name = (o.customer_name or "").strip().split(" ")[0] or "there"
        probs = [f"{i.name}" for i in low_items]
        facts = {"name": name, "service_rating": f.service_rating, "comment": f.comment, "poor_items": probs, "restaurant": Setting.get().business_name}
        text, _ = llm("feedback_reply", "Write a short, warm, sincere WhatsApp reply (max 3 sentences) from a restaurant to an unhappy customer. "
                      "Apologise for the specific problems, do not make excuses, invite them back. Write in Roman Urdu. No emojis, no markdown.", json.dumps(facts), 220)
        engine = "claude"
        if not text:
            engine = "rules"
            bit = f" khaas tor par {', '.join(probs)} ke baare mein" if probs else ""
            cm = f" Aap ne likha: \"{f.comment[:80]}\"." if f.comment else ""
            text = (f"Assalam o Alaikum {name}, aap ke order ({o.number}) ka experience acha nahi raha{bit}, is par hum maazrat chahte hain.{cm} "
                    f"Hum is ko theek karenge. Agli baar aap ka khayal rakhne ka mauqa dijiye - {facts['restaurant']}.")
            note("feedback_reply")
        out.append({"id": f.pk, "order": o.number, "phone": o.customer_phone, "name": o.customer_name or "-", "rating": f.service_rating,
                    "comment": f.comment, "poor_items": probs, "draft": text.strip(), "engine": engine})
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- tomorrow prep plan
def prep_plan(branch):
    from . import analytics as an
    fc = an.build_ai(branch)
    if not fc.get("days_of_data"):
        return {"items": [], "text": None}
    tomorrow_wd = (_local_today(branch) + timedelta(days=1)).weekday()
    demand = fc.get("demand", [])
    items = [{"name": d["name"], "portions": max(1, round(d["daily"]))} for d in demand[:12]]
    buy = [{"name": r["name"], "short": round(float(r["short"]), 1), "unit": r["unit"]} for r in fc.get("ing", []) if r["short"] > 0][:8]
    payload = {"tomorrow": {"sales": float(fc["tomorrow"]["value"]), "weekday": tomorrow_wd}, "prep_portions": items, "buy": buy,
               "busy_hours": [h["label"] for h in fc.get("hours", []) if h.get("pct", 0) >= 70]}
    text, _ = llm("prep_plan", "You are an experienced restaurant kitchen manager. From this data write tomorrow's prep plan: 5-7 short '-' lines "
                  "covering what to prep first, what to buy today, and staffing for busy hours. Roman Urdu with English item names.", json.dumps(payload, default=str), 450)
    if not text:
        note("prep_plan")
    return {"items": items, "buy": buy, "text": text, "tomorrow": float(fc["tomorrow"]["value"]), "busy": payload["busy_hours"]}


# --------------------------------------------------------------------------- anomaly watch
def _once(key, note_=""):
    from .automation import _once as once
    return once(key, note_)


def job_anomalies():
    st = Setting.get()
    if not st.ai_anomaly:
        return 0
    n = 0
    for b in Branch.objects.filter(is_active=True):
        today = _local_today(b)
        tag = f"{b.pk}:{today}"
        mgrs = svc.managers_for_branch(b)
        s = svc.day_summary(b, today)
        start, end = svc.day_bounds(today)
        # 1. one person voiding a lot
        per_user = VoidLog.objects.filter(branch=b, created_at__gte=start, created_at__lt=end).exclude(kind=VoidLog.REFUND) \
            .values("user", "user__username").annotate(c=Count("id"), t=Sum("amount"))
        for r in per_user:
            if r["user"] and r["c"] >= 4 and _once(f"ai:void:{tag}:{r['user']}"):
                svc.notify(mgrs, "AI alert: many voids by one user", f"{r['user__username']} made {r['c']} voids/cancellations today ({_money(b, r['t'] or 0)}) - please review", "/voids/", "urgent")
                n += 1
        # 2. voids today vs usual
        past = [svc.day_summary(b, today - timedelta(days=i)) for i in range(1, 15)]
        avg_v = sum(float(p["void_amount"]) for p in past) / 14
        if float(s["void_amount"]) > max(3 * avg_v, 1) and s["void_count"] >= 3 and _once(f"ai:voidamt:{tag}"):
            svc.notify(mgrs, "AI alert: void amount is unusually high", f"{_money(b, s['void_amount'])} voided today vs ~{_money(b, avg_v)} on a normal day", "/voids/", "urgent")
            n += 1
        # 3. discounts share
        if float(s["sales"]) > 0 and float(s["discount"]) / (float(s["sales"]) + float(s["discount"])) > 0.15 and _once(f"ai:disc:{tag}"):
            svc.notify(mgrs, "AI alert: discounts are high today", f"{_money(b, s['discount'])} given on {_money(b, s['sales'])} sales (over 15%)", "/orders/", "urgent")
            n += 1
        # 4. sales drop vs same weekday
        now_h = timezone.now().astimezone(ZoneInfo(b.timezone)).hour
        same = [float(svc.day_summary(b, today - timedelta(days=7 * k))["sales"]) for k in range(1, 5)]
        same = [x for x in same if x > 0]
        if now_h >= 16 and len(same) >= 2:
            avg = sum(same) / len(same)
            if float(s["sales"]) < 0.5 * avg and _once(f"ai:drop:{tag}"):
                svc.notify(mgrs, "AI alert: sales are well below normal", f"{_money(b, s['sales'])} so far vs ~{_money(b, avg)} on a typical {today:%A}", "/analytics/ai/", "info")
                n += 1
        # 5. stock shrinkage (adjustments / wastage written off)
        def lost(d0, d1):
            tot = D0
            for mv in StockMovement.objects.filter(branch=b, created_at__gte=d0, created_at__lt=d1, change__lt=0, reason__in=("adjustment", "wastage")).select_related("ingredient"):
                tot += -mv.change * mv.ingredient.unit_cost * b.cost_factor
            return float(tot)
        today_loss = lost(start, end)
        base = lost(start - timedelta(days=14), start) / 14
        if today_loss > 0 and today_loss > max(3 * base, 0.02 * float(s["sales"]), 1) and _once(f"ai:shrink:{tag}"):
            svc.notify(mgrs, "AI alert: stock written off today", f"{_money(b, today_loss)} of stock adjusted / wasted vs ~{_money(b, base)} per day normally", "/stock/", "urgent")
            n += 1
    return n


# --------------------------------------------------------------------------- daily report headline
def report_headline(branch, text):
    out, _ = llm("report_headline", "You write the first line of a restaurant's end-of-day report for the owner. 2 short sentences in Roman Urdu: how the day went and the one thing to act on tomorrow. No markdown.", text, 160)
    if out:
        return out.strip()
    return None


# --------------------------------------------------------------------------- WhatsApp conversation
YES = {"yes", "y", "haan", "han", "ha", "ji", "ok", "okay", "confirm", "theek", "thik", "done", "yup", "jee"}
NO = {"no", "n", "nahi", "nahin", "cancel", "na"}


def wa_branch():
    st = Setting.get()
    return st.ai_wa_branch or Branch.objects.filter(is_active=True, online_ordering=True).first()


def wa_reply(phone, body):
    """One WhatsApp turn. Returns the text to send back. Two steps: text -> summary, YES -> order is created."""
    from .models import Channel
    b = wa_branch()
    if not b:
        return "Sorry, ordering is not available right now."
    word = re.sub(r"[^a-z]", "", (body or "").lower())
    ses = AISession.objects.filter(phone=phone).first()
    if ses and timezone.now() - ses.created_at > timedelta(minutes=30):
        ses.delete()
        ses = None
    if ses and word in YES:
        data = json.loads(ses.payload)
        menu = {m["id"]: m for m in svc.branch_menu(b)}
        items = MenuItem.objects.in_bulk([l["id"] for l in data["lines"]])
        lines = [{"item": items[l["id"]], "name": menu[l["id"]]["name"], "qty": l["qty"], "price": menu[l["id"]]["price"], "notes": ""}
                 for l in data["lines"] if l["id"] in menu and l["id"] in items]
        if not lines:
            ses.delete()
            return "Sorry, those items are no longer available. Please send your order again."
        ch, _ = Channel.objects.get_or_create(code="whatsapp", defaults={"name": "WhatsApp", "kind": "website"})
        try:
            o = svc.create_order(branch=b, order_type=Order.DELIVERY if data.get("address") else Order.TAKEAWAY, items=lines, source=ch,
                                 customer_name=data.get("name", ""), phone=phone, address=data.get("address", ""), notes="WhatsApp AI order")
        except svc.OrderError as e:
            return f"Sorry: {e}"
        ses.delete()
        return f"Shukriya! Order {o.number} receive ho gaya. Total {_money(b, o.total)}. Track: {__import__('rms.messaging', fromlist=['x']).site_url()}/track/{o.number}/"
    if ses and word in NO:
        ses.delete()
        return "Theek hai, order cancel kar diya. Naya order bhejne ke liye apni list likh dein."
    res = parse_order(body, svc.branch_menu(b))
    if not res["lines"]:
        return "Maaf kijiye, main menu ka koi item nahi samajh saka. Aise likhein: '2 zinger burger aur 1 coke'."
    AISession.objects.update_or_create(phone=phone, defaults={"branch": b, "payload": json.dumps({"lines": res["lines"]}), "created_at": timezone.now()})
    txt = "\n".join(f"{l['qty']} x {l['name']} = {_money(b, l['qty'] * l['price'])}" for l in res["lines"])
    extra = f"\n(Samajh nahi aya: {', '.join(res['unmatched'])})" if res["unmatched"] else ""
    return f"Aap ka order:\n{txt}\nTotal: {_money(b, res['total'])}{extra}\nConfirm karne ke liye YES likhein, cancel ke liye NO."
