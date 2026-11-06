import csv
import io
import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.apps import apps
from django.contrib import messages
from django.core import serializers
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..models import (AuditLog, Category, Customer, Ingredient, MenuItem, Order, OrderItem, Stock, Supplier, User)
from ..permissions import master_required, role_required, scope

SAMPLE = {
    "menu": "category,name,sku,price,description,prep_minutes,veg\nBurgers & Wraps,Zinger Burger,ZNG-01,650,Crispy chicken burger,12,no\nDrinks,Fresh Lime Soda,DRK-02,180,,5,yes\n",
    "ingredients": "name,unit,unit_cost,category,reorder_level,supplier\nChicken breast,kg,720,meat,10,Karachi Meat Co.\nCooking oil,l,380,oil,20,Sunrise Wholesale\n",
}
UNITS = {u for u, _ in Ingredient.UNITS}
ING_CATS = {c for c, _ in Ingredient.CATEGORIES}


def _csv(name, header, rows):
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{name}-{timezone.localdate()}.csv"'
    resp.write("﻿")               # Excel opens UTF-8 (Urdu names) correctly
    w = csv.writer(resp)
    w.writerow(header)
    w.writerows(rows)
    return resp


@role_required("manager")
def data_home(request):
    return render(request, "rms/data_tools.html", {"can_backup": request.user.can_access_all, "result": request.session.pop("import_result", None),
                                                   "imports": [("menu", "menu items"), ("ingredients", "ingredients")], "today": timezone.localdate(), "month_ago": timezone.localdate() - timedelta(days=30)})


@role_required("manager")
def export_csv(request, name):
    if name == "orders":
        try:
            d1 = timezone.datetime.fromisoformat(request.GET.get("from") or "").date()
            d2 = timezone.datetime.fromisoformat(request.GET.get("to") or "").date()
        except ValueError:
            d2 = timezone.localdate()
            d1 = d2 - timedelta(days=30)
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(timezone.datetime.combine(d1, timezone.datetime.min.time()), tz)
        end = timezone.make_aware(timezone.datetime.combine(d2 + timedelta(days=1), timezone.datetime.min.time()), tz)
        qs = scope(Order.objects.select_related("branch", "source", "waiter", "rider", "table"), request).filter(created_at__gte=start, created_at__lt=end)
        rows = [[o.number, timezone.localtime(o.created_at).strftime("%Y-%m-%d %H:%M"), o.branch.code, o.get_order_type_display(), o.source.name if o.source else "",
                 o.status, o.table.name if o.table else "", o.waiter.display_name if o.waiter else "", o.rider.name if o.rider else "",
                 o.customer_name, o.customer_phone, o.subtotal, o.discount, o.tax, o.service_charge, o.delivery_fee, o.tip, o.total, o.bill_number]
                for o in qs[:20000]]
        return _csv("orders", ["Number", "Date", "Branch", "Type", "Source", "Status", "Table", "Waiter", "Rider", "Customer", "Phone", "Subtotal",
                               "Discount", "Tax", "Service charge", "Delivery fee", "Tip", "Total", "Bill"], rows)
    if name == "order_items":
        qs = scope(OrderItem.objects.filter(is_void=False).select_related("order", "order__branch"), request, "order__branch")[:50000]
        return _csv("order-items", ["Order", "Date", "Branch", "Item", "Qty", "Price", "Line total"],
                    [[l.order.number, timezone.localtime(l.order.created_at).strftime("%Y-%m-%d %H:%M"), l.order.branch.code, l.name, l.qty, l.price, l.qty * l.price] for l in qs])
    if name == "customers":
        return _csv("customers", ["Name", "Phone", "Email", "Address", "Visits", "Total spent", "Points", "Last visit"],
                    [[c.name, c.phone, c.email, c.address, c.visits, c.total_spent, c.points, c.last_visit.strftime("%Y-%m-%d") if c.last_visit else ""] for c in Customer.objects.all()[:20000]])
    if name == "menu":
        return _csv("menu", ["category", "name", "sku", "price", "description", "prep_minutes", "veg"],
                    [[m.category.name, m.name, m.sku or "", m.price, m.description, m.prep_minutes, "yes" if m.is_veg else "no"] for m in MenuItem.objects.select_related("category")])
    if name == "ingredients":
        return _csv("ingredients", ["name", "unit", "unit_cost", "category", "reorder_level", "supplier"],
                    [[i.name, i.unit, i.unit_cost, i.category, i.default_reorder_level, i.default_supplier.name if i.default_supplier else ""] for i in Ingredient.objects.select_related("default_supplier")])
    if name == "stock":
        qs = scope(Stock.objects.select_related("branch", "ingredient"), request)
        return _csv("stock", ["Branch", "Ingredient", "Unit", "Quantity", "Reorder level", "Unit cost"],
                    [[s.branch.code, s.ingredient.name, s.ingredient.unit, s.quantity, s.level, s.ingredient.unit_cost] for s in qs])
    if name == "suppliers":
        return _csv("suppliers", ["Name", "Contact", "Phone", "Email", "Address", "Active"],
                    [[s.name, s.contact_person, s.phone, s.email, s.address, s.is_active] for s in Supplier.objects.all()])
    if name == "staff":
        qs = scope(User.objects.select_related("branch"), request) if not request.user.can_access_all else User.objects.select_related("branch")
        return _csv("staff", ["Username", "Name", "Role", "Branch", "Phone", "Active", "Salary"],
                    [[u.username, u.display_name, u.get_role_display(), u.branch.code if u.branch else "", u.phone, u.is_active, u.monthly_salary] for u in qs])
    if name == "audit" and request.user.can_access_all:
        return _csv("audit", ["When", "User", "Action", "What", "Object", "Details", "IP"],
                    [[a.created_at.strftime("%Y-%m-%d %H:%M:%S"), a.username, a.action, a.model, a.object_repr, a.changes, a.ip] for a in AuditLog.objects.all()[:20000]])
    messages.error(request, "Unknown export.")
    return redirect("data_home")


@master_required
def backup(request):
    """Whole database (all app tables) as one JSON file. Restore with: python manage.py loaddata backup.json"""
    models_ = [m for m in apps.get_app_config("rms").get_models() if m.__name__ not in ("AuditLog",)]
    data = serializers.serialize("json", [o for m in models_ for o in m._default_manager.all().iterator()], indent=None)
    resp = HttpResponse(data, content_type="application/json")
    resp["Content-Disposition"] = f'attachment; filename="restopro-backup-{timezone.localtime():%Y%m%d-%H%M}.json"'
    from .. import audit
    audit.write("backup", model="Backup", repr_="full database backup downloaded")
    return resp


def sample(request, name):
    if name not in SAMPLE:
        return redirect("data_home")
    resp = HttpResponse(SAMPLE[name], content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{name}-template.csv"'
    return resp


def _rows(upload):
    text = upload.read().decode("utf-8-sig", errors="replace")
    rd = csv.DictReader(io.StringIO(text))
    rd.fieldnames = [(h or "").strip().lower() for h in (rd.fieldnames or [])]
    return list(rd)


def _dec(v, default="0"):
    try:
        return Decimal(str(v).replace(",", "").strip() or default)
    except InvalidOperation:
        raise ValueError(f"'{v}' is not a number")


@role_required("manager")
@require_POST
def import_csv(request, name):
    up = request.FILES.get("file")
    if name not in SAMPLE or not up:
        messages.error(request, "Choose a CSV file.")
        return redirect("data_home")
    created = updated = 0
    errors = []
    try:
        rows = _rows(up)
    except Exception:                  # noqa: BLE001
        messages.error(request, "Could not read that file. Save it as CSV (UTF-8).")
        return redirect("data_home")
    for n, r in enumerate(rows[:3000], start=2):
        try:
            nm = (r.get("name") or "").strip()
            if not nm:
                raise ValueError("name is empty")
            if name == "menu":
                cat = (r.get("category") or "").strip()
                if not cat:
                    raise ValueError("category is empty")
                category, _ = Category.objects.get_or_create(name=cat)
                sku = (r.get("sku") or "").strip() or None
                price = _dec(r.get("price"))
                if price <= 0:
                    raise ValueError("price must be above 0")
                item = (MenuItem.objects.filter(sku=sku).first() if sku else None) or MenuItem.objects.filter(name__iexact=nm, category=category).first()
                vals = dict(category=category, name=nm, sku=sku, price=price, description=(r.get("description") or "")[:255],
                            prep_minutes=int(_dec(r.get("prep_minutes"), "15")), is_veg=(r.get("veg") or "").strip().lower() in ("yes", "y", "1", "true", "veg"))
                if item:
                    for k, v in vals.items():
                        setattr(item, k, v)
                    item.save()
                    updated += 1
                else:
                    MenuItem.objects.create(**vals)
                    created += 1
            else:
                unit = (r.get("unit") or "kg").strip().lower()
                if unit not in UNITS:
                    raise ValueError(f"unit must be one of {', '.join(sorted(UNITS))}")
                cat = (r.get("category") or "other").strip().lower()
                cat = cat if cat in ING_CATS else "other"
                sup = None
                if (r.get("supplier") or "").strip():
                    sup, _ = Supplier.objects.get_or_create(name=r["supplier"].strip())
                vals = dict(unit=unit, unit_cost=_dec(r.get("unit_cost")), category=cat, default_reorder_level=_dec(r.get("reorder_level")), default_supplier=sup)
                ing = Ingredient.objects.filter(name__iexact=nm).first()
                if ing:
                    for k, v in vals.items():
                        setattr(ing, k, v)
                    ing.save()
                    updated += 1
                else:
                    Ingredient.objects.create(name=nm, **vals)
                    created += 1
        except (ValueError, TypeError) as e:
            errors.append(f"Row {n}: {e}")
    request.session["import_result"] = {"what": name, "created": created, "updated": updated, "errors": errors[:30], "n_errors": len(errors)}
    from .. import audit
    audit.write("imported", model=name.title(), repr_=f"{created} new, {updated} updated, {len(errors)} errors")
    return redirect("data_home")
