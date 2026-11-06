from .countries import COUNTRIES, LANGUAGES
from .models import Branch

# (label, url_name, icon, roles allowed [None = everyone], needs_master_view)
NAV = [
    ("Dashboard", "dashboard", "grid", None, "main"),
    ("My Deliveries", "rider_home", "bike", ("rider",), "main"),
    ("POS", "pos", "bag", ("owner", "manager", "cashier", "waiter"), "main"),
    ("Orders", "orders", "list", ("owner", "manager", "cashier", "waiter"), "main"),
    ("Online Orders", "online_orders", "link", ("owner", "manager", "cashier"), "main"),
    ("Customers", "customers", "users", ("owner", "manager", "cashier"), "main"),
    ("Bills", "bills", "file", ("owner", "manager", "cashier"), "main"),
    ("Reservations", "reservations", "calendar", ("owner", "manager", "cashier", "waiter"), "main"),
    ("Kitchen", "kitchen", "flame", ("owner", "manager", "kitchen", "kds", "cashier"), "main"),
    ("Tables", "tables", "table", ("owner", "manager", "cashier", "waiter"), "main"),
    ("Menu", "menu_items", "book", ("owner", "manager"), "manage"),
    ("Branch Menu", "branch_menu", "store", ("owner", "manager"), "manage"),
    ("Waiters", "waiters", "user", ("owner", "manager"), "manage"),
    ("Riders", "riders", "bike", ("owner", "manager", "cashier"), "manage"),
    ("Suppliers", "suppliers", "truck", ("owner", "manager", "storekeeper"), "supply"),
    ("Ingredients", "ingredients", "package", ("owner", "manager", "storekeeper"), "supply"),
    ("Recipes", "recipes", "book", ("owner", "manager"), "supply"),
    ("Stock", "stock", "layers", ("owner", "manager", "storekeeper", "kitchen", "kds"), "supply"),
    ("Smart Ordering", "smart_order", "trend", ("owner", "manager", "storekeeper"), "supply"),
    ("Stock Requests", "stock_requests", "repeat", ("owner", "manager", "storekeeper"), "supply"),
    ("Purchase Orders", "purchase_orders", "file", ("owner", "manager", "storekeeper"), "supply"),
    ("Stock Transfers", "transfers", "repeat", ("owner", "manager", "storekeeper"), "supply"),
    ("Shifts", "shifts", "wallet", ("owner", "manager", "cashier"), "admin"),
    ("Day Close", "day_close", "clock", ("owner", "manager"), "admin"),
    ("Voids & Cancels", "voids", "alert", ("owner", "manager"), "admin"),
    ("Channels & Apps", "channels", "link", ("owner", "manager"), "admin"),
    ("Reports", "reports", "chart", ("owner", "manager"), "admin"),
    ("Profit & Loss", "pnl", "chart", ("owner", "manager"), "admin"),
    ("Expenses", "expenses", "file", ("owner", "manager"), "admin"),
    ("Promo Codes", "coupons", "star", ("owner", "manager"), "admin"),
    ("Modifiers", "modifiers", "list", ("owner", "manager"), "admin"),
    ("Staff Performance", "staff_perf", "star", ("owner", "manager"), "admin"),
    ("Customer Feedback", "feedback", "star", ("owner", "manager"), "admin"),
    ("Menu Analytics", "menu_analytics", "trend", ("owner", "manager"), "admin"),
    ("AI Centre", "ai_home", "flame", ("owner", "manager"), "admin"),
    ("AI Insights", "ai_insights", "flame", ("owner", "manager"), "admin"),
    ("My Stats", "my_stats", "star", ("waiter",), "main"),
    ("My Salary Slip", "my_slip", "wallet", ("cashier", "kitchen", "kds", "storekeeper", "rider"), "main"),
    ("Automations", "automation", "sliders", ("owner", "manager"), "admin"),
    ("Daily Reports", "daily_reports", "file", ("owner", "manager"), "admin"),
    ("Salary Slips", "slips", "wallet", ("owner", "manager"), "admin"),
    ("Branches", "branches", "building", ("owner", "manager"), "admin"),
    ("Staff", "staff", "users", ("owner", "manager"), "admin"),
    ("Backup & Import", "data_home", "layers", ("owner", "manager"), "admin"),
    ("Audit Log", "audit_log", "shield", ("owner", "manager"), "admin"),
    ("Roles & Access", "roles_matrix", "shield", ("owner", "manager"), "admin"),
    ("Settings", "settings", "sliders", ("owner", "manager"), "admin"),
]
ALIASES = {"customer_detail": "customers", "waiter_profile": "staff_perf", "rider_profile": "staff_perf", "ai_ask": "ai_insights", "feedback_ask": "feedback", "po_detail": "purchase_orders", "po_create": "purchase_orders", "recipe": "menu_items",
           "order_detail": "orders", "receipt": "orders", "kot": "orders", "ingredient_detail": "ingredients", "shift_report": "shifts", "movements": "stock", "categories": "menu_items",
           "channels_crud": "channels"}
GROUPS = [("main", "Operations"), ("manage", "Management"), ("supply", "Supply Chain"), ("admin", "Administration")]


def global_context(request):
    ctx = {"LANGS": LANGUAGES}
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not hasattr(request, "ctx_branch"):
        ctx.update({"lang": getattr(request, "lang", "en"),
                    "lang_dir": LANGUAGES.get(getattr(request, "lang", "en"), {}).get("dir", "ltr"),
                    "setting": getattr(request, "setting", None)})
        return ctx
    role = "owner" if user.is_superuser else user.role
    nav = {}
    current = request.resolver_match.url_name if getattr(request, "resolver_match", None) else ""
    for label, url, icon, roles, group in NAV:
        if roles is None or role in roles:
            # branch-managers can't open master-only pages
            if url in ("settings", "branches_master") and not user.can_access_all:
                continue
            active = current == url or current.startswith(url + "_") or ALIASES.get(current) == url
            nav.setdefault(group, []).append({"label": label, "url": url, "icon": icon, "active": active})
    nav_groups = [(title, nav[g]) for g, title in GROUPS if g in nav]
    ctx.update({
        "nav_groups": nav_groups,
        "branch": request.branch,
        "branch_all": request.branch_all,
        "ctx_branch": request.ctx_branch,
        "switch_branches": Branch.objects.filter(is_active=True) if user.can_access_all else [],
        "setting": request.setting,
        "lang": request.lang,
        "lang_dir": LANGUAGES.get(request.lang, {}).get("dir", "ltr"),
        "COUNTRIES": COUNTRIES,
    })
    return ctx
