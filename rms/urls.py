from django.contrib.auth import views as auth_views
from django.db.models import Count
from django.urls import path

from .crud import build_crud
from .forms import (ModifierGroupForm, CouponForm, ExpenseForm, BranchForm, CategoryForm, ChannelForm, IngredientForm, MenuItemForm, RiderForm, StaffForm,
                    SupplierForm, TableForm, WaiterForm)
from .models import Branch, Category, Channel, Coupon, Expense, ModifierGroup, Ingredient, MenuItem, Rider, Supplier, Table, User
from .views import admin_pages, core, flow, ingredients as ing_views, insights, customers, pay, pos, public, supply, kitchen_stock, automation, ai_views, roles_views, online, security, data_tools, finance

# ----- generic master-data screens ------------------------------------------------------------------
cruds = {
    "branches": build_crud(
        prefix="branches", model=Branch, form_class=BranchForm, title="Branches", master_only=True, icon="building",
        subtitle="Create branches, one of them can be the Master branch that sees every branch.",
        columns=[("Name", "name", None), ("Code", "code", None), ("Country", "country", None),
                 ("Time zone", "timezone", None), ("Currency", "currency_code", None), ("Master", "is_master", "bool"),
                 ("Active", "is_active", "bool")], search=("name", "code")),
    "categories": build_crud(
        prefix="categories", model=Category, form_class=CategoryForm, title="Menu Categories", master_only=True,
        columns=[("Name", "name", None), ("Order", "sort_order", None), ("Active", "is_active", "bool")]),
    "menu_items": build_crud(
        prefix="menu_items", model=MenuItem, form_class=MenuItemForm, title="Menu Items", master_only=True, icon="book",
        subtitle="Global menu. Branch managers can switch items off / change price per branch from Branch Menu.",
        base_qs=lambda r: MenuItem.objects.select_related("category"),
        columns=[("Name", "name", None), ("Category", "category.name", None), ("SKU", "sku", None),
                 ("Price", "price", "money"), ("Recipe", "recipe_label", None), ("Veg", "is_veg", "bool"),
                 ("Active", "is_active", "bool")],
        row_actions=[("Recipe", "recipe", "package")], search=("name", "sku", "category__name")),
    "tables_manage": build_crud(
        prefix="tables_manage", model=Table, form_class=TableForm, title="Manage Tables", scoped=True, icon="table",
        roles=("manager",), base_qs=lambda r: Table.objects.select_related("branch"),
        columns=[("Branch", "branch.name", "branch"), ("Table", "name", None), ("Area", "area", None),
                 ("Seats", "capacity", None), ("Status", "get_status_display", "badge")], search=("name", "area")),
    "waiters": build_crud(
        prefix="waiters", model=User, form_class=WaiterForm, title="Waiters", scoped=True, icon="user",
        roles=("manager",), defaults={"role": "waiter"}, row_actions=[("Profile", "waiter_profile", "star")],
        base_qs=lambda r: User.objects.filter(role="waiter").select_related("branch").annotate(orders_n=Count("served_orders")),
        subtitle="Waiters log in with their own account, take dine-in orders and are tracked per order.",
        columns=[("Branch", "branch.name", "branch"), ("Name", "display_name", None), ("Username", "username", None),
                 ("Phone", "phone", None), ("Orders served", "orders_n", None), ("Active", "is_active", "bool")],
        search=("username", "first_name", "last_name")),
    "riders": build_crud(
        prefix="riders", model=Rider, form_class=RiderForm, title="Riders", scoped=True, icon="bike",
        roles=("manager", "cashier"), row_actions=[("Profile", "rider_profile", "star")],
        base_qs=lambda r: Rider.objects.select_related("branch").annotate(deliveries_n=Count("deliveries")),
        subtitle="Delivery riders per branch. Assign a rider from the order screen.",
        columns=[("Branch", "branch.name", "branch"), ("Name", "name", None), ("Phone", "phone", None),
                 ("Vehicle", "vehicle", None), ("Status", "get_status_display", "badge"),
                 ("Deliveries", "deliveries_n", None), ("Active", "is_active", "bool")], search=("name", "phone")),
    "expenses": build_crud(
        prefix="expenses", model=Expense, form_class=ExpenseForm, title="Expenses", scoped=True, icon="file",
        roles=("manager",), base_qs=lambda r: Expense.objects.select_related("branch"),
        subtitle="Rent, bills, repairs, marketing. These feed the Profit & Loss report.",
        columns=[("Branch", "branch.name", "branch"), ("Date", "date", None), ("Category", "get_category_display", None),
                 ("Amount", "amount", "money"), ("Note", "note", None)], search=("note",),
        after_save=lambda o, r, new: (setattr(o, "user", r.user), o.save(update_fields=["user"])) if new else None),
    "coupons": build_crud(
        prefix="coupons", model=Coupon, form_class=CouponForm, title="Promo Codes", master_only=True, icon="star",
        subtitle="Customers type the code in the POS or on the website to get a discount.",
        base_qs=lambda r: Coupon.objects.select_related("branch"),
        columns=[("Code", "code", None), ("Type", "get_kind_display", None), ("Value", "value", "num"),
                 ("Min order", "min_order", "num"), ("Used", "used", None), ("Limit", "usage_limit", None),
                 ("Valid to", "valid_to", None), ("Active", "is_active", "bool")], search=("code", "description")),
    "modifiers": build_crud(
        prefix="modifiers", model=ModifierGroup, form_class=ModifierGroupForm, title="Modifiers (Size / Extras)", master_only=True, icon="list",
        subtitle="Choices shown when an item is added, e.g. Size, Extra cheese, Spice level. Attach them to a menu item in Menu Items.",
        base_qs=lambda r: ModifierGroup.objects.prefetch_related("options"),
        columns=[("Name", "name", None), ("Required", "required", "bool"), ("Several allowed", "multi", "bool"),
                 ("Options", "option_list", None)], search=("name",)),
    "suppliers": build_crud(
        prefix="suppliers", model=Supplier, form_class=SupplierForm, title="Suppliers", icon="truck",
        roles=("manager", "storekeeper"),
        columns=[("Name", "name", None), ("Contact", "contact_person", None), ("Phone", "phone", None),
                 ("Email", "email", None), ("Active", "is_active", "bool")], search=("name", "phone")),
    "ingredients": build_crud(
        prefix="ingredients", model=Ingredient, form_class=IngredientForm, title="Ingredients", icon="package",
        roles=("manager", "storekeeper"), base_qs=lambda r: Ingredient.objects.select_related("default_supplier"),
        columns=[("Name", "name", None), ("Category", "get_category_display", None), ("Unit", "unit", None),
                 ("Unit cost", "unit_cost", "num"), ("Supplier", "default_supplier.name", None)], search=("name",)),
    "channels_crud": build_crud(
        prefix="channels_crud", model=Channel, form_class=ChannelForm, title="Channels", master_only=True,
        columns=[("Name", "name", None)]),
}
cruds["channels_crud"]["list"] = admin_pages.channels_list   # custom page shows API keys + docs


def _staff_prepare(form, request, obj):
    """Branch managers can only create non-owner staff for their own branch."""
    if not request.user.can_access_all:
        form.fields["role"].choices = [c for c in User.ROLES if c[0] != "owner"]


cruds["staff"] = build_crud(
    prefix="staff", model=User, form_class=StaffForm, title="Staff & Users", scoped=True, icon="users",
    roles=("manager",), base_qs=lambda r: User.objects.select_related("branch"), prepare=_staff_prepare,
    subtitle="Owner / Manager / Cashier / Waiter / Kitchen accounts. Master-branch managers can see all branches.",
    columns=[("Branch", "branch.name", "branch"), ("Username", "username", None), ("Name", "display_name", None),
             ("Role", "get_role_display", "badge"), ("Active", "is_active", "bool")],
    search=("username", "first_name", "last_name"))


def crud_paths(prefix, c, url=None):
    url = url or prefix.replace("_", "-")
    return [
        path(f"{url}/", c["list"], name=prefix),
        path(f"{url}/add/", c["add"], name=f"{prefix}_add"),
        path(f"{url}/<int:pk>/edit/", c["edit"], name=f"{prefix}_edit"),
        path(f"{url}/<int:pk>/delete/", c["delete"], name=f"{prefix}_delete"),
    ]


urlpatterns = [
    path("", core.home, name="home"),
    path("login/", security.SafeLoginView.as_view(), name="login"),
    path("data/", data_tools.data_home, name="data_home"),
    path("data/export/<str:name>/", data_tools.export_csv, name="export_csv"),
    path("data/backup/", data_tools.backup, name="backup"),
    path("data/sample/<str:name>/", data_tools.sample, name="import_sample"),
    path("data/import/<str:name>/", data_tools.import_csv, name="import_csv"),
    path("audit/", security.audit_log, name="audit_log"),
    path("media-db/<int:pk>/", security.media_db, name="media_db"),
    path("manifest.webmanifest", security.manifest, name="manifest"),
    path("sw.js", security.service_worker, name="sw"),
    path("offline/", security.offline, name="offline"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("dashboard/", core.dashboard, name="dashboard"),
    path("branch/switch/<str:branch_id>/", core.switch_branch, name="switch_branch"),
    path("lang/<str:code>/", core.set_language, name="set_language"),
    path("settings/", core.settings_view, name="settings"),

    # POS & orders
    path("pos/", pos.pos, name="pos"),
    path("pos/submit/", pos.pos_submit, name="pos_submit"),
    path("orders/", pos.orders, name="orders"),
    path("orders/<int:pk>/", pos.order_detail, name="order_detail"),
    path("bills/", pos.bills, name="bills"),
    path("orders/<int:pk>/kot/", pos.kot, name="kot"),
    path("orders/<int:pk>/print/<str:kind>/", pos.order_print, name="order_print"),
    path("orders/<int:pk>/escpos/<str:kind>/", pos.order_escpos, name="order_escpos"),
    path("orders/<int:pk>/add-items/", pos.order_add_items, name="order_add_items"),
    path("performance/", insights.staff_performance, name="staff_perf"),
    path("performance/waiter/<int:pk>/", insights.waiter_profile, name="waiter_profile"),
    path("performance/rider/<int:pk>/", insights.rider_profile, name="rider_profile"),
    path("feedback/", insights.feedback_page, name="feedback"),
    path("feedback/ask/", insights.feedback_ask, name="feedback_ask"),
    path("rate/<str:token>/", insights.feedback_form, name="feedback_form"),
    path("customers/", customers.customers, name="customers"),
    path("customers/<int:pk>/", customers.customer_detail, name="customer_detail"),
    path("customers/<int:pk>/action/", customers.customer_action, name="customer_action"),
    path("pay/<str:token>/", pay.pay_page, name="pay_page"),
    path("pay/<str:token>/start/", pay.pay_start, name="pay_start"),
    path("pay/<str:token>/sandbox/", pay.pay_sandbox, name="pay_sandbox"),
    path("webhooks/stripe/", pay.stripe_webhook, name="stripe_webhook"),
    path("track/<str:number>/pay/", pay.track_pay, name="track_pay"),
    path("orders/<int:pk>/pay-link/", pay.order_pay_link, name="order_pay_link"),
    path("my-stats/", insights.my_stats, name="my_stats"),
    path("analytics/menu/", insights.menu_analytics, name="menu_analytics"),
    path("analytics/ai/", insights.ai_insights, name="ai_insights"),
    path("analytics/ai/ask/", insights.ai_ask, name="ai_ask"),
    path("shifts/", flow.shifts, name="shifts"),
    path("shifts/action/", flow.shift_action, name="shift_action"),
    path("shifts/<int:pk>/report/", flow.shift_report, name="shift_report"),
    path("day-close/", flow.day_close, name="day_close"),
    path("voids/", flow.voids, name="voids"),
    path("reservations/", flow.reservations, name="reservations"),
    path("reservations/<int:pk>/action/", flow.reservation_action, name="reservation_action"),
    path("orders/<int:pk>/receipt/", pos.receipt, name="receipt"),
    path("orders/<int:pk>/action/", pos.order_action, name="order_action"),
    path("kitchen/", pos.kitchen, name="kitchen"),
    path("tables/", pos.tables, name="tables"),
    path("tables/<int:pk>/open/", pos.table_open, name="table_open"),
    path("tables/<int:pk>/status/", pos.table_status, name="table_status"),

    # supply chain
    path("stock/", supply.stock, name="stock"),
    path("stock/<int:ingredient_id>/adjust/", supply.stock_adjust, name="stock_adjust"),
    path("automation/", automation.automation_home, name="automation"),
    path("automation/action/", automation.automation_action, name="automation_action"),
    path("automation/reports/", automation.daily_reports, name="daily_reports"),
    path("automation/smart-order/", automation.smart_order, name="smart_order"),
    path("automation/slips/", automation.slips, name="slips"),
    path("automation/slips/<str:kind>/<int:pk>/", automation.slip, name="slip"),
    path("password/", auth_views.PasswordChangeView.as_view(template_name="rms/password.html", success_url="/password/done/"), name="password_change"),
    path("password/done/", auth_views.PasswordChangeDoneView.as_view(template_name="rms/password_done.html"), name="password_change_done"),
    path("orders/online/", online.online_orders, name="online_orders"),
    path("orders/online/count/", online.online_count, name="online_count"),
    path("orders/online/<int:pk>/action/", online.online_action, name="online_action"),
    path("deliveries/", roles_views.rider_home, name="rider_home"),
    path("deliveries/<int:pk>/action/", roles_views.rider_action, name="rider_action"),
    path("my-salary/", roles_views.my_slip, name="my_slip"),
    path("roles/", roles_views.roles_matrix, name="roles_matrix"),
    path("ai/", ai_views.ai_home, name="ai_home"),
    path("ai/settings/", ai_views.ai_settings, name="ai_settings"),
    path("ai/copilot/", ai_views.ai_copilot, name="ai_copilot"),
    path("ai/order/", ai_views.ai_order_staff, name="ai_order"),
    path("ai/upsell/", ai_views.ai_upsell_staff, name="ai_upsell"),
    path("ai/apply-price/", ai_views.ai_apply_price, name="ai_apply_price"),
    path("ai/send-reply/", ai_views.ai_send_reply, name="ai_send_reply"),
    path("ai/whatsapp/", ai_views.wa_webhook, name="ai_whatsapp"),
    path("order/<str:code>/ai/", ai_views.ai_order_site, name="site_ai_order"),
    path("order/<str:code>/upsell/", ai_views.ai_upsell_site, name="site_upsell"),
    path("cron/tick/", automation.cron_tick, name="cron_tick"),
    path("notifications/feed/", kitchen_stock.notif_feed, name="notif_feed"),
    path("notifications/read/", kitchen_stock.notif_read, name="notif_read"),
    path("stock/request/", kitchen_stock.request_stock, name="request_stock"),
    path("stock/requests/", kitchen_stock.stock_requests, name="stock_requests"),
    path("stock/requests/<int:pk>/action/", kitchen_stock.stock_request_action, name="stock_request_action"),
    path("stock/po-from-low/", supply.po_from_low, name="po_from_low"),
    path("stock/<int:ingredient_id>/reorder/", supply.stock_reorder, name="stock_reorder"),
    path("stock/movements/", supply.stock_movements, name="movements"),
    path("purchase-orders/", supply.purchase_orders, name="purchase_orders"),
    path("purchase-orders/new/", supply.po_create, name="po_create"),
    path("purchase-orders/<int:pk>/", supply.po_detail, name="po_detail"),
    path("purchase-orders/<int:pk>/action/", supply.po_action, name="po_action"),
    path("transfers/", supply.transfers, name="transfers"),
    path("menu-items/<int:pk>/recipe/", supply.recipe, name="recipe"),
    path("ingredients/", ing_views.ingredient_list, name="ingredients"),
    path("recipes/", ing_views.recipes_overview, name="recipes"),
    path("ingredients/<int:pk>/", ing_views.ingredient_detail, name="ingredient_detail"),
    path("branch-menu/", supply.branch_menu, name="branch_menu"),

    # integrations & reports
    path("channels/", admin_pages.channels_list, name="channels"),
    path("channels/<int:pk>/regenerate/", admin_pages.channel_regen, name="channel_regen"),
    path("channels/<int:pk>/remove/", admin_pages.channel_delete_safe, name="channel_remove"),
    path("reports/", admin_pages.reports, name="reports"),
    path("profit-loss/", finance.profit_loss, name="pnl"),
    path("coupon-check/", finance.coupon_check, name="coupon_check"),

    # public API (delivery apps / websites)
    path("api/v1/menu/", public.api_menu, name="api_menu"),
    path("api/v1/orders/", public.api_orders, name="api_orders"),
    path("api/v1/orders/<str:number>/", public.api_order_status, name="api_order_status"),

    # public website ordering
    path("order/", public.site_branches, name="site_branches"),
    path("order/<str:code>/", public.site_menu, name="site_menu"),
    path("order/<str:code>/submit/", public.site_submit, name="site_submit"),
    path("order/<str:code>/coupon/", public.site_coupon, name="site_coupon"),
    path("track/<str:number>/", public.track, name="track"),
]

for _prefix, _c in cruds.items():
    urlpatterns += crud_paths(_prefix, _c, {"menu_items": "menu", "tables_manage": "tables/manage",
                                            "channels_crud": "channels/manage"}.get(_prefix))
