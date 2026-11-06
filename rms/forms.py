from decimal import Decimal
from django import forms

from .countries import COUNTRIES, timezone_choices
from .models import (Branch, Category, Channel, Ingredient, MenuItem, Rider, Setting, Supplier, Table, User,
                     Stock, BranchItem, Expense, Coupon, ModifierGroup, ModifierOption)


class Styled:
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for f in self.fields.values():
            w = f.widget
            if isinstance(w, forms.CheckboxInput):
                w.attrs["class"] = "check"
            else:
                w.attrs["class"] = (w.attrs.get("class", "") + " input").strip()


class SettingForm(Styled, forms.ModelForm):
    class Meta:
        model = Setting
        fields = ["business_name", "default_country", "default_language", "language_selection_enabled",
                  "online_ordering_enabled", "block_if_out_of_stock", "require_shift", "auto_print_kot",
                  "loyalty_enabled", "loyalty_points_per_100", "loyalty_point_value", "payment_gateway"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["default_country"].widget.attrs["data-country-select"] = "1"
        self.fields["default_country"].help_text = "Default country; also suggests the default language"


class BranchForm(Styled, forms.ModelForm):
    timezone = forms.ChoiceField(choices=[])

    class Meta:
        model = Branch
        fields = ["name", "code", "address", "phone", "country", "timezone", "currency_code", "currency_symbol",
                  "language", "tax_percent", "service_charge_percent", "delivery_fee", "cost_factor", "receipt_printer_host", "receipt_printer_port", "kitchen_printer_host",
                  "kitchen_printer_port", "paper_chars", "kitchen_capacity", "online_ordering",
                  "is_master", "is_active"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["timezone"].choices = timezone_choices()
        self.fields["country"].widget.attrs["data-country-select"] = "1"
        self.fields["country"].help_text = "Picking a country fills time zone, currency and language automatically"


class CategoryForm(Styled, forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "sort_order", "is_active"]


class MenuItemForm(Styled, forms.ModelForm):
    photo_file = forms.ImageField(required=False, label="Photo", help_text="JPG / PNG / WebP up to 5 MB. Stored in the database, resized automatically.")
    remove_photo = forms.BooleanField(required=False, label="Remove current photo")

    class Meta:
        model = MenuItem
        fields = ["category", "name", "sku", "description", "price", "prep_minutes", "is_veg", "is_active", "modifier_groups"]

    field_order = ["category", "name", "sku", "photo_file", "remove_photo", "description", "price", "prep_minutes", "is_veg", "modifier_groups", "is_active"]

    def clean_photo_file(self):
        f = self.cleaned_data.get("photo_file")
        if f and f.size > 5 * 1024 * 1024:
            raise forms.ValidationError("Photo is larger than 5 MB")
        return f

    def save(self, commit=True):
        from .models import MediaFile
        obj = super().save(commit=False)
        f = self.cleaned_data.get("photo_file")
        if f:
            obj.photo = MediaFile.from_upload(f)
            obj.image = None
        elif self.cleaned_data.get("remove_photo"):
            obj.photo, obj.image = None, None
        if commit:
            obj.save()
        return obj


class ModifierGroupForm(Styled, forms.ModelForm):
    options_text = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 7}), label="Options",
        help_text="One option per line as  Name | extra price   e.g.  Large | 150   or  Extra cheese | 80   (price can be empty or 0)")

    class Meta:
        model = ModifierGroup
        fields = ["name", "required", "multi"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        if self.instance.pk:
            self.fields["options_text"].initial = "\n".join(
                f"{o.name} | {o.price:g}" for o in self.instance.options.filter(is_active=True))

    def clean_options_text(self):
        rows = []
        for line in self.cleaned_data["options_text"].splitlines():
            line = line.strip()
            if not line:
                continue
            name, _, price = line.partition("|")
            try:
                val = Decimal(price.strip() or "0")
            except Exception:
                raise forms.ValidationError(f"Bad price in: {line}")
            if val < 0 or not name.strip():
                raise forms.ValidationError(f"Bad option: {line}")
            rows.append((name.strip()[:60], val))
        if not rows:
            raise forms.ValidationError("Add at least one option")
        return rows

    def save(self, commit=True):
        obj = super().save(commit=commit)
        rows = self.cleaned_data["options_text"]

        def sync():
            keep = []
            old = {o.name: o for o in obj.options.all()}
            for i, (name, price) in enumerate(rows):
                o = old.get(name) or ModifierOption(group=obj, name=name)
                o.price, o.sort_order, o.is_active = price, i, True
                o.save()
                keep.append(o.pk)
            obj.options.exclude(pk__in=keep).update(is_active=False)     # old orders keep their text; the option just disappears
        if commit:
            sync()
        else:
            orig = self.save_m2m

            def both():
                orig()
                sync()
            self.save_m2m = both
        return obj


class BranchItemForm(Styled, forms.ModelForm):
    class Meta:
        model = BranchItem
        fields = ["branch", "item", "price", "is_available"]


class TableForm(Styled, forms.ModelForm):
    class Meta:
        model = Table
        fields = ["branch", "name", "area", "capacity", "status"]


class RiderForm(Styled, forms.ModelForm):
    class Meta:
        model = Rider
        fields = ["branch", "name", "phone", "vehicle", "status", "monthly_salary", "commission_percent", "per_delivery_fee", "is_active"]


class ExpenseForm(Styled, forms.ModelForm):
    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))

    class Meta:
        model = Expense
        fields = ["branch", "category", "amount", "date", "note"]


class CouponForm(Styled, forms.ModelForm):
    valid_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    valid_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))

    class Meta:
        model = Coupon
        fields = ["code", "description", "kind", "value", "min_order", "max_discount", "valid_from", "valid_to",
                  "usage_limit", "branch", "is_active"]

    def clean_value(self):
        v = self.cleaned_data["value"]
        if v <= 0:
            raise forms.ValidationError("Must be more than 0")
        if self.data.get("kind") == "percent" and v > 100:
            raise forms.ValidationError("A percent coupon cannot be more than 100")
        return v


class SupplierForm(Styled, forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "contact_person", "phone", "email", "address", "is_active"]


class IngredientForm(Styled, forms.ModelForm):
    class Meta:
        model = Ingredient
        fields = ["name", "category", "unit", "unit_cost", "default_supplier", "default_reorder_level"]


class ChannelForm(Styled, forms.ModelForm):
    class Meta:
        model = Channel
        fields = ["name", "code", "kind", "commission_percent", "webhook_url", "is_active"]


class StockForm(Styled, forms.ModelForm):
    class Meta:
        model = Stock
        fields = ["branch", "ingredient", "quantity", "reorder_level"]


class StaffForm(Styled, forms.ModelForm):
    password = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False),
                               help_text="Leave blank to keep the current password")

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "role", "branch", "phone", "language", "is_active"]

    def clean(self):
        data = super().clean()
        if not self.instance.pk and not data.get("password"):
            self.add_error("password", "Password is required for new users")
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        pw = self.cleaned_data.get("password")
        if pw:
            user.set_password(pw)
        if commit:
            user.save()
        return user


class WaiterForm(StaffForm):
    class Meta(StaffForm.Meta):
        fields = ["username", "first_name", "last_name", "branch", "phone", "language", "monthly_salary", "commission_percent", "is_active"]


AUTOMATION_FIELDS = [
    ("Alerts (bell)", ["auto_late_alerts", "late_queue_minutes", "auto_low_rating", "low_rating_threshold", "auto_table_alert",
                       "table_cleaning_minutes", "auto_shift_reminder", "shift_max_hours"]),
    ("Online orders", ["online_manual_accept", "online_accept_minutes", "online_auto_accept"]),
    ("Kitchen & delivery", ["auto_reorder", "auto_assign_rider"]),
    ("Reports & payroll", ["daily_report", "daily_report_hour", "auto_salary_slips"]),
    ("Messages", ["msg_provider", "msg_customer_events", "msg_supplier_po", "winback_enabled", "winback_days", "winback_text"]),
    ("Delivery apps", ["menu_sync"]),
]


class AutomationForm(Styled, forms.ModelForm):
    class Meta:
        model = Setting
        fields = [f for _, fs in AUTOMATION_FIELDS for f in fs]
