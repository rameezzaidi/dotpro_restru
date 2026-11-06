import secrets
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

from .countries import COUNTRY_CHOICES, LANGUAGE_CHOICES

D0 = Decimal("0")


# ----------------------------------------------------------------- settings
class Setting(models.Model):
    """Single-row global settings."""
    business_name = models.CharField(max_length=120, default="Restopro")
    default_country = models.CharField(max_length=2, choices=COUNTRY_CHOICES, default="PK")
    default_language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES, default="en")
    language_selection_enabled = models.BooleanField(
        default=True, help_text="Allow every user to pick their own interface language")
    online_ordering_enabled = models.BooleanField(default=True, help_text="Public website ordering page")
    auto_print_kot = models.BooleanField(
        default=False, help_text="Open the kitchen ticket (KOT) print dialog automatically after an order / extra items are sent")
    require_shift = models.BooleanField(
        default=False, help_text="Cashiers must open a shift (opening cash) before they can take payments")
    block_if_out_of_stock = models.BooleanField(
        default=True, help_text="Refuse an order when a recipe ingredient is short in the branch stock "
                                "(OFF = order is accepted and stock goes negative)")
    loyalty_enabled = models.BooleanField(default=True, help_text="Customers earn points on completed orders")
    loyalty_points_per_100 = models.DecimalField(max_digits=6, decimal_places=2, default=1,
                                                 help_text="Points earned per 100 spent (in the branch currency)")
    loyalty_point_value = models.DecimalField(max_digits=8, decimal_places=2, default=1,
                                              help_text="Discount value of one point when redeemed")
    # ---- automations
    auto_late_alerts = models.BooleanField(default=True, help_text="Alert managers when an order cooks longer than its prep time or waits too long in the queue")
    late_queue_minutes = models.PositiveSmallIntegerField(default=10, help_text="Queue wait (minutes) before a 'waiting too long' alert")
    auto_low_rating = models.BooleanField(default=True, help_text="Alert managers the moment a customer leaves a low rating")
    low_rating_threshold = models.PositiveSmallIntegerField(default=2, help_text="Ratings at or below this many stars are 'low'")
    auto_table_alert = models.BooleanField(default=True, help_text="Alert staff when a table stays in 'Cleaning' too long")
    table_cleaning_minutes = models.PositiveSmallIntegerField(default=15)
    auto_shift_reminder = models.BooleanField(default=True, help_text="Remind the cashier / manager about a shift left open")
    shift_max_hours = models.PositiveSmallIntegerField(default=12)
    auto_reorder = models.BooleanField(default=False, help_text="Create draft purchase orders by itself (once a day) for ingredients at/below alert level")
    auto_assign_rider = models.BooleanField(default=True, help_text="Give a ready delivery order to the least-busy available rider")
    daily_report = models.BooleanField(default=True, help_text="Build the end-of-day summary for every branch and send it to managers")
    daily_report_hour = models.PositiveSmallIntegerField(default=23, help_text="Branch-local hour (0-23) after which the daily report is built")
    msg_provider = models.CharField(max_length=10, default="log", choices=[
        ("log", "Record only (nothing is sent)"), ("twilio", "Twilio (SMS + WhatsApp)"), ("webhook", "Custom webhook")],
        help_text="How customer / supplier / staff messages leave the system. Keys go in server environment variables")
    msg_customer_events = models.BooleanField(default=True, help_text="Message customers: order received, on the way, ready, thank-you + feedback link")
    msg_supplier_po = models.BooleanField(default=True, help_text="Send the purchase order to the supplier (email / WhatsApp) when it is approved")
    winback_enabled = models.BooleanField(default=False, help_text="Message customers who have not visited for a while")
    winback_days = models.PositiveSmallIntegerField(default=30)
    winback_text = models.CharField(max_length=240, default="Hi {name}, we miss you at {business}! Show this message for a treat on your next order. You have {points} loyalty points.")
    auto_salary_slips = models.BooleanField(default=True, help_text="On the 1st of the month prepare last month's salary slips and message staff")
    menu_sync = models.BooleanField(default=True, help_text="Push menu / price / availability changes to delivery apps that have a webhook URL")
    online_manual_accept = models.BooleanField(default=True, help_text="Website / WhatsApp orders wait for the cashier or manager to Accept or Reject before they reach the kitchen")
    online_accept_minutes = models.PositiveSmallIntegerField(default=5, help_text="Alert managers when an online order has waited this many minutes")
    online_auto_accept = models.BooleanField(default=False, help_text="Accept automatically (30 min) when nobody answered within the minutes above")
    ai_enabled = models.BooleanField(default=True, help_text="Use Claude (needs ANTHROPIC_API_KEY) for the AI features; without a key the built-in rule-based versions run")
    ai_upsell = models.BooleanField(default=True, help_text="Suggest add-ons ('frequently bought together') in POS and on the website")
    ai_anomaly = models.BooleanField(default=True, help_text="Watch for unusual voids, discounts, sales drops and stock shrinkage and alert the managers")
    ai_whatsapp = models.BooleanField(default=False, help_text="Let customers order by sending a WhatsApp message (Twilio webhook /ai/whatsapp/)")
    ai_wa_branch = models.ForeignKey("Branch", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                     help_text="Branch that receives WhatsApp orders (default: first online-ordering branch)")
    payment_gateway = models.CharField(max_length=10, default="none", choices=[
        ("none", "Off"), ("sandbox", "Sandbox (test / demo)"), ("stripe", "Stripe Checkout")],
        help_text="Online card payment links for delivery / takeaway orders. Stripe needs STRIPE_SECRET_KEY on the server")

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return self.business_name


class Branch(models.Model):
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=10, unique=True, help_text="Short code used in order numbers, e.g. KHI")
    address = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    country = models.CharField(max_length=2, choices=COUNTRY_CHOICES, default="PK")
    timezone = models.CharField(max_length=64, default="Asia/Karachi")
    currency_code = models.CharField(max_length=5, default="PKR")
    currency_symbol = models.CharField(max_length=6, default="Rs")
    language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES, default="en")
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=D0)
    service_charge_percent = models.DecimalField(max_digits=5, decimal_places=2, default=D0,
                                                 help_text="Applied on dine-in orders only")
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=D0)
    online_ordering = models.BooleanField(default=True)
    receipt_printer_host = models.CharField(max_length=80, blank=True, help_text="Thermal (ESC/POS) receipt printer IP, e.g. 192.168.1.50")
    receipt_printer_port = models.PositiveIntegerField(default=9100)
    kitchen_printer_host = models.CharField(max_length=80, blank=True, help_text="Kitchen ticket printer IP (leave empty to use the receipt printer)")
    kitchen_printer_port = models.PositiveIntegerField(default=9100)
    paper_chars = models.PositiveSmallIntegerField(default=42, help_text="Characters per line: 42 for 80mm paper, 32 for 58mm")
    cost_factor = models.DecimalField(
        max_digits=10, decimal_places=5, default=1,
        help_text="Ingredient costs are stored in head-office currency. Multiply by this to get this branch's currency "
                  "(1 = same currency, e.g. 0.01429 for PKR→AED)")
    kitchen_capacity = models.PositiveSmallIntegerField(
        default=3, help_text="Max orders one chef cooks at the same time; extra orders wait in the queue")
    is_master = models.BooleanField(default=False, help_text="Master branch managers can view/manage every branch")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-is_master", "name"]

    def save(self, *args, **kwargs):
        self.code = self.code.upper()
        super().save(*args, **kwargs)
        if self.is_master:
            Branch.objects.exclude(pk=self.pk).filter(is_master=True).update(is_master=False)

    def __str__(self):
        return f"{self.name} ({self.code})"


class User(AbstractUser):
    OWNER, MANAGER, CASHIER, WAITER, KITCHEN, KDS = "owner", "manager", "cashier", "waiter", "kitchen", "kds"
    STOREKEEPER, RIDER = "storekeeper", "rider"
    ROLES = [(OWNER, "Owner"), (MANAGER, "Manager"), (CASHIER, "Cashier"),
             (WAITER, "Waiter"), (KITCHEN, "Chef (Kitchen)"),
             (KDS, "Kitchen Display / Head Chef"), (STOREKEEPER, "Storekeeper"), (RIDER, "Delivery Rider")]
    role = models.CharField(max_length=12, choices=ROLES, default=CASHIER)
    monthly_salary = models.DecimalField(max_digits=12, decimal_places=2, default=D0, help_text="Fixed monthly salary (branch currency)")
    commission_percent = models.DecimalField(max_digits=5, decimal_places=2, default=D0, help_text="% of sales (without tax) on completed orders")
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.SET_NULL, related_name="staff")
    phone = models.CharField(max_length=30, blank=True)
    language = models.CharField(max_length=5, blank=True, choices=LANGUAGE_CHOICES)

    def save(self, *a, **kw):
        super().save(*a, **kw)
        if self.role == self.RIDER and self.branch_id:          # every rider login has its delivery profile
            r = Rider.objects.filter(user=self).first()
            if r:
                Rider.objects.filter(pk=r.pk).update(branch=self.branch, name=self.display_name, phone=self.phone or r.phone, is_active=self.is_active)
            else:
                Rider.objects.create(branch=self.branch, user=self, name=self.display_name, phone=self.phone)

    @property
    def can_access_all(self):
        """Owner / superuser, or a manager that belongs to the MASTER branch."""
        if self.is_superuser or self.role == self.OWNER:
            return True
        return bool(self.role == self.MANAGER and self.branch_id and self.branch.is_master)

    @property
    def is_manager_level(self):
        return self.is_superuser or self.role in (self.OWNER, self.MANAGER)

    @property
    def display_name(self):
        return self.get_full_name() or self.username


# ----------------------------------------------------------------- menu
class Category(models.Model):
    name = models.CharField(max_length=80)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class MediaFile(models.Model):
    """Uploaded photo stored in the database, so it survives on Vercel (read-only disk) and in backups."""
    name = models.CharField(max_length=120, blank=True)
    content_type = models.CharField(max_length=40, default="image/jpeg")
    data = models.BinaryField()
    created_at = models.DateTimeField(default=timezone.now)

    @property
    def url(self):
        return f"/media-db/{self.pk}/"

    @classmethod
    def from_upload(cls, f, max_px=900):
        """Resize to max_px and re-encode as JPEG (about 60-150 KB)."""
        import io

        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(f))
        if im.mode not in ("RGB", "L"):
            bg = Image.new("RGB", im.size, "white")
            bg.paste(im, mask=im.split()[-1] if im.mode in ("RGBA", "LA") else None)
            im = bg
        im = im.convert("RGB")
        im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82, optimize=True)
        return cls.objects.create(name=getattr(f, "name", "")[:120], content_type="image/jpeg", data=buf.getvalue())


class MenuItem(models.Model):
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="items")
    name = models.CharField(max_length=120)
    sku = models.CharField(max_length=40, unique=True, null=True, blank=True,
                           help_text="Used by aggregator apps / APIs to match items")
    description = models.CharField(max_length=255, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    image = models.ImageField(upload_to="menu/", blank=True, null=True, help_text="Old file upload (kept for existing photos)")
    photo = models.ForeignKey("MediaFile", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    prep_minutes = models.PositiveIntegerField(default=15)
    is_veg = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    modifier_groups = models.ManyToManyField("ModifierGroup", blank=True, related_name="items",
                                             help_text="Choices the customer makes: size, extras, spice level")

    class Meta:
        ordering = ["category__sort_order", "name"]

    def __str__(self):
        return self.name

    @property
    def image_url(self):
        if self.photo_id:
            return f"/media-db/{self.photo_id}/"
        try:
            return self.image.url if self.image else ""
        except ValueError:
            return ""

    @property
    def recipe_cost(self):
        """Ingredient cost of one portion."""
        return sum((r.quantity * r.ingredient.unit_cost for r in self.recipe.all()), D0)

    @property
    def recipe_label(self):
        n = self.recipe.count()
        return f"{n} ingredient{'s' if n != 1 else ''}" if n else "No recipe"


class BranchItem(models.Model):
    """Per-branch availability and optional price override."""
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="branch_items")
    item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name="branch_items")
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                help_text="Leave empty to use the base price")
    is_available = models.BooleanField(default=True)

    class Meta:
        unique_together = ("branch", "item")


# ----------------------------------------------------------------- floor
class Table(models.Model):
    FREE, OCCUPIED, RESERVED, BILL, CLEANING = "free", "occupied", "reserved", "bill", "cleaning"
    STATUS = [(FREE, "Free"), (OCCUPIED, "Occupied"), (RESERVED, "Reserved"), (BILL, "Bill requested"),
              (CLEANING, "Cleaning")]
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="tables")
    name = models.CharField(max_length=30)
    area = models.CharField(max_length=40, default="Main Hall")
    capacity = models.PositiveIntegerField(default=4)
    status = models.CharField(max_length=10, choices=STATUS, default=FREE)

    class Meta:
        ordering = ["branch", "area", "name"]
        unique_together = ("branch", "name")

    def __str__(self):
        return f"{self.name} ({self.area})"


# ----------------------------------------------------------------- channels & riders
class Channel(models.Model):
    """Order source: POS, own website, Foodpanda, WhatsApp, any custom app."""
    KINDS = [("pos", "POS / Counter"), ("website", "Own Website"), ("aggregator", "Delivery App"),
             ("phone", "Phone / WhatsApp"), ("custom", "Custom API")]
    name = models.CharField(max_length=60)
    code = models.SlugField(max_length=30, unique=True)
    kind = models.CharField(max_length=12, choices=KINDS, default="aggregator")
    api_key = models.CharField(max_length=64, unique=True, blank=True)
    commission_percent = models.DecimalField(max_digits=5, decimal_places=2, default=D0)
    webhook_url = models.URLField(blank=True, help_text="Menu changes are POSTed here automatically (signed with the API key, header X-Restopro-Signature)")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.api_key:
            self.api_key = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Rider(models.Model):
    AVAILABLE, BUSY, OFFLINE = "available", "busy", "offline"
    STATUS = [(AVAILABLE, "Available"), (BUSY, "On delivery"), (OFFLINE, "Offline")]
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="riders")
    user = models.OneToOneField("User", null=True, blank=True, on_delete=models.SET_NULL, related_name="rider_profile",
                                help_text="Login account of this rider (role Delivery Rider)")
    name = models.CharField(max_length=80)
    phone = models.CharField(max_length=30, blank=True)
    vehicle = models.CharField(max_length=60, blank=True, help_text="e.g. Bike LEB-1234")
    status = models.CharField(max_length=10, choices=STATUS, default=AVAILABLE)
    is_active = models.BooleanField(default=True)
    monthly_salary = models.DecimalField(max_digits=12, decimal_places=2, default=D0, help_text="Fixed monthly salary (branch currency)")
    commission_percent = models.DecimalField(max_digits=5, decimal_places=2, default=D0, help_text="% of sales (without tax) on delivered orders")
    per_delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=D0, help_text="Paid for each completed delivery")

    class Meta:
        ordering = ["branch", "name"]

    def __str__(self):
        return self.name


# ----------------------------------------------------------------- orders
class Order(models.Model):
    DINE_IN, TAKEAWAY, DELIVERY = "dine_in", "takeaway", "delivery"
    TYPES = [(DINE_IN, "Dine-in"), (TAKEAWAY, "Takeaway"), (DELIVERY, "Delivery")]
    NEW, PREPARING, READY, SERVED, OUT, COMPLETED, CANCELLED = (
        "new", "preparing", "ready", "served", "out_for_delivery", "completed", "cancelled")
    STATUS = [(NEW, "New"), (PREPARING, "Preparing"), (READY, "Ready"), (SERVED, "Served"),
              (OUT, "Out for delivery"), (COMPLETED, "Completed"), (CANCELLED, "Cancelled")]
    ACTIVE = (NEW, PREPARING, READY, SERVED, OUT)
    METHODS = [("cash", "Cash"), ("card", "Card"), ("online", "Online"), ("wallet", "Wallet")]
    PAY_METHODS = METHODS + [("mixed", "Mixed")]

    number = models.CharField(max_length=30, unique=True)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="orders")
    order_type = models.CharField(max_length=10, choices=TYPES)
    source = models.ForeignKey(Channel, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    external_ref = models.CharField(max_length=80, blank=True)
    table = models.ForeignKey(Table, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    waiter = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="served_orders")
    chef = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="cooked_orders")
    started_at = models.DateTimeField(null=True, blank=True, help_text="Kitchen timer start")
    ready_at = models.DateTimeField(null=True, blank=True)
    rider = models.ForeignKey(Rider, null=True, blank=True, on_delete=models.SET_NULL, related_name="deliveries")
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_orders")
    customer_name = models.CharField(max_length=100, blank=True)
    customer_phone = models.CharField(max_length=30, blank=True)
    customer_address = models.CharField(max_length=255, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default=NEW)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=D0)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=D0)
    coupon = models.ForeignKey("Coupon", null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    service_charge = models.DecimalField(max_digits=12, decimal_places=2, default=D0)
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=D0)
    delivery_fee = models.DecimalField(max_digits=12, decimal_places=2, default=D0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=D0)

    is_paid = models.BooleanField(default=False)
    payment_method = models.CharField(max_length=10, choices=PAY_METHODS, blank=True)
    tip = models.DecimalField(max_digits=12, decimal_places=2, default=D0)
    kot_round = models.PositiveIntegerField(default=1, help_text="Current kitchen ticket round (increases when items are added)")
    bill_requested_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=200, blank=True)
    acceptance = models.CharField(max_length=10, blank=True, default="", db_index=True,
                                  help_text="Online orders: pending -> accepted / rejected by the restaurant")
    accept_eta = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Promised preparation time in minutes")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="splits")
    paid_at = models.DateTimeField(null=True, blank=True)
    stock_deducted = models.BooleanField(default=False)
    bill_number = models.CharField(max_length=40, blank=True, db_index=True, help_text="Auto-generated when the order completes")
    billed_at = models.DateTimeField(null=True, blank=True)
    customer = models.ForeignKey("Customer", null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    feedback_token = models.CharField(max_length=24, blank=True, db_index=True, help_text="Secret link id for the customer feedback form")

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["bill_number"], condition=~models.Q(bill_number=""),
                                               name="uniq_bill_number")]

    def __str__(self):
        return self.number

    @property
    def is_active(self):
        return self.status in self.ACTIVE

    @property
    def next_status(self):
        flow = {self.NEW: self.PREPARING, self.PREPARING: self.READY, self.SERVED: self.COMPLETED}
        if self.status in flow:
            return flow[self.status]
        if self.status == self.READY:
            if self.order_type == self.DELIVERY:
                return self.OUT
            return self.SERVED if self.order_type == self.DINE_IN else self.COMPLETED
        if self.status == self.OUT:
            return self.COMPLETED
        return None

    @property
    def paid_total(self):
        return sum((p.amount for p in self.payments.all()), D0)

    def ensure_feedback_token(self):
        if not self.feedback_token:
            import secrets
            self.feedback_token = secrets.token_urlsafe(9)
            Order.objects.filter(pk=self.pk).update(feedback_token=self.feedback_token)
        return self.feedback_token

    @property
    def due(self):
        return max(self.total - self.paid_total, D0)

    @property
    def live_lines(self):
        return [l for l in self.lines.all() if not l.is_void]


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(MenuItem, null=True, blank=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=120)
    qty = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    notes = models.CharField(max_length=160, blank=True)
    round = models.PositiveIntegerField(default=1, help_text="Kitchen ticket round this line was sent in")
    deducted = models.BooleanField(default=False, help_text="Ingredients already removed from stock")
    is_void = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=200, blank=True)

    @property
    def line_total(self):
        return D0 if self.is_void else self.qty * self.price


# ----------------------------------------------------------------- supply chain
class Supplier(models.Model):
    name = models.CharField(max_length=120)
    contact_person = models.CharField(max_length=80, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Ingredient(models.Model):
    UNITS = [("kg", "kg"), ("g", "g"), ("l", "litre"), ("ml", "ml"), ("pcs", "pcs"), ("pack", "pack")]
    CATEGORIES = [("meat", "Meat & Poultry"), ("seafood", "Seafood"), ("dairy", "Dairy & Eggs"),
                  ("veg", "Vegetables & Fruit"), ("grocery", "Dry Grocery & Grains"), ("spice", "Spices & Sauces"),
                  ("bakery", "Bakery & Dough"), ("oil", "Oil & Fats"), ("beverage", "Beverages"),
                  ("packaging", "Packaging"), ("other", "Other")]
    name = models.CharField(max_length=100)
    category = models.CharField(max_length=10, choices=CATEGORIES, default="other")
    unit = models.CharField(max_length=5, choices=UNITS, default="kg")
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, default=D0)
    default_supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.SET_NULL)
    default_reorder_level = models.DecimalField(
        max_digits=14, decimal_places=3, default=D0,
        help_text="Alert level used when a branch has no level of its own (stock at or below this = LOW)")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.unit})"


class Stock(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="stocks")
    ingredient = models.ForeignKey(Ingredient, on_delete=models.CASCADE, related_name="stocks")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=D0)
    reorder_level = models.DecimalField(max_digits=14, decimal_places=3, default=D0)

    class Meta:
        unique_together = ("branch", "ingredient")
        ordering = ["ingredient__name"]

    @property
    def level(self):
        """Alert level: the branch's own reorder level, else the ingredient default."""
        return self.reorder_level if self.reorder_level else self.ingredient.default_reorder_level

    @property
    def is_low(self):
        return self.quantity <= self.level

    @property
    def is_out(self):
        return self.quantity <= 0


class Recipe(models.Model):
    item = models.ForeignKey(MenuItem, on_delete=models.CASCADE, related_name="recipe")
    ingredient = models.ForeignKey(Ingredient, on_delete=models.CASCADE)
    quantity = models.DecimalField(max_digits=12, decimal_places=4, help_text="Per 1 portion, in ingredient unit")

    class Meta:
        unique_together = ("item", "ingredient")


class PurchaseOrder(models.Model):
    DRAFT, ORDERED, PARTIAL, RECEIVED, CANCELLED = "draft", "ordered", "partial", "received", "cancelled"
    STATUS = [(DRAFT, "Draft"), (ORDERED, "Ordered"), (PARTIAL, "Partially received"), (RECEIVED, "Received"),
              (CANCELLED, "Cancelled")]
    number = models.CharField(max_length=30, unique=True)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="purchase_orders")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT)
    status = models.CharField(max_length=10, choices=STATUS, default=DRAFT)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)
    received_at = models.DateTimeField(null=True, blank=True)
    invoice_no = models.CharField(max_length=60, blank=True, help_text="Supplier invoice / challan number")

    class Meta:
        ordering = ["-created_at"]

    @property
    def total(self):
        return sum((l.qty * l.unit_cost for l in self.lines.all()), D0)


class PurchaseLine(models.Model):
    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    ingredient = models.ForeignKey(Ingredient, on_delete=models.PROTECT)
    qty = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, default=D0)
    received_qty = models.DecimalField(max_digits=14, decimal_places=3, default=D0)

    @property
    def line_total(self):
        return self.qty * self.unit_cost

    @property
    def pending_qty(self):
        return max(self.qty - self.received_qty, D0)


class StockTransfer(models.Model):
    from_branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="transfers_out")
    to_branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="transfers_in")
    ingredient = models.ForeignKey(Ingredient, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]


class StockMovement(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="movements")
    ingredient = models.ForeignKey(Ingredient, on_delete=models.CASCADE)
    change = models.DecimalField(max_digits=14, decimal_places=3)
    reason = models.CharField(max_length=30)  # purchase / sale / transfer_in / transfer_out / adjustment / wastage
    ref = models.CharField(max_length=60, blank=True)
    note = models.CharField(max_length=200, blank=True)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]



# ----------------------------------------------------------------- payments, shifts, day close, voids, reservations
class Shift(models.Model):
    """A cashier's till session: opening cash -> payments -> counted cash at close."""
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="shifts")
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="shifts")
    opened_at = models.DateTimeField(default=timezone.now)
    opening_cash = models.DecimalField(max_digits=12, decimal_places=2, default=D0)
    closed_at = models.DateTimeField(null=True, blank=True)
    expected_cash = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    counted_cash = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    difference = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-opened_at"]

    @property
    def is_open(self):
        return self.closed_at is None

    def __str__(self):
        return f"Shift {self.pk} · {self.user} · {self.opened_at:%d %b %H:%M}"


class Payment(models.Model):
    """One payment (or refund, negative amount) against an order. An order can have several (split / mixed tender)."""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    method = models.CharField(max_length=10, choices=Order.METHODS)
    amount = models.DecimalField(max_digits=12, decimal_places=2, help_text="Applied to the bill (negative = refund)")
    tendered = models.DecimalField(max_digits=12, decimal_places=2, default=D0, help_text="Cash handed over")
    change = models.DecimalField(max_digits=12, decimal_places=2, default=D0)
    tip = models.DecimalField(max_digits=12, decimal_places=2, default=D0)
    shift = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.SET_NULL, related_name="payments")
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    note = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]


class DayClose(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="day_closes")
    date = models.DateField()
    closed_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    closed_at = models.DateTimeField(default=timezone.now)
    summary = models.JSONField(default=dict)

    class Meta:
        unique_together = ("branch", "date")
        ordering = ["-date"]


class VoidLog(models.Model):
    """Audit trail of every cancelled order, voided item and refund, with the reason and who approved it."""
    ITEM, ORDER, REFUND = "item", "order", "refund"
    KINDS = [(ITEM, "Item void"), (ORDER, "Order cancel"), (REFUND, "Refund")]
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="voids")
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.SET_NULL, related_name="voids")
    order_number = models.CharField(max_length=30)
    kind = models.CharField(max_length=8, choices=KINDS)
    item_name = models.CharField(max_length=120, blank=True)
    qty = models.PositiveIntegerField(default=0)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=D0)
    reason = models.CharField(max_length=200)
    after_cooking = models.BooleanField(default=False, help_text="Food was already being cooked (wastage)")
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="voids_done")
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="voids_approved")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]


class Reservation(models.Model):
    BOOKED, SEATED, CANCELLED, NOSHOW = "booked", "seated", "cancelled", "no_show"
    STATUS = [(BOOKED, "Booked"), (SEATED, "Seated"), (CANCELLED, "Cancelled"), (NOSHOW, "No show")]
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="reservations")
    table = models.ForeignKey(Table, null=True, blank=True, on_delete=models.SET_NULL, related_name="reservations")
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=30, blank=True)
    guests = models.PositiveIntegerField(default=2)
    at = models.DateTimeField(help_text="Reservation date & time")
    status = models.CharField(max_length=10, choices=STATUS, default=BOOKED)
    notes = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["at"]


class Feedback(models.Model):
    """Customer feedback for one order: service rating (waiter / rider / counter) + rating per item."""
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="feedback")
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="feedbacks")
    service_rating = models.PositiveSmallIntegerField(default=0, help_text="1-5, 0 = not rated")
    comment = models.CharField(max_length=500, blank=True)
    source = models.CharField(max_length=10, default="link", help_text="link / staff")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def food_avg(self):
        r = [i.rating for i in self.items.all() if i.rating]
        return sum(r) / len(r) if r else 0


class ItemFeedback(models.Model):
    feedback = models.ForeignKey(Feedback, on_delete=models.CASCADE, related_name="items")
    item = models.ForeignKey(MenuItem, null=True, blank=True, on_delete=models.SET_NULL, related_name="feedbacks")
    name = models.CharField(max_length=120)
    rating = models.PositiveSmallIntegerField(default=0, help_text="1-5, 0 = not rated")
    comment = models.CharField(max_length=300, blank=True)


class Customer(models.Model):
    """Guest profile created automatically from the phone number typed on an order. Earns loyalty points."""
    name = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20, unique=True, help_text="Digits only")
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    notes = models.CharField(max_length=255, blank=True, help_text="Allergies, favourite table, etc.")
    points = models.IntegerField(default=0)
    lifetime_points = models.IntegerField(default=0)
    visits = models.PositiveIntegerField(default=0)
    total_spent = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    last_visit = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-last_visit", "-created_at"]

    def __str__(self):
        return self.name or self.phone

    @property
    def tier(self):
        v = self.visits
        return "Gold" if v >= 15 else "Silver" if v >= 6 else "Bronze"


class LoyaltyTxn(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="ledger")
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.SET_NULL, related_name="loyalty_txns")
    points = models.IntegerField(help_text="+ earned, - redeemed")
    reason = models.CharField(max_length=80)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]


class PaymentLink(models.Model):
    """Online payment page for one order (card / wallet through the configured gateway)."""
    PENDING, PAID, FAILED = "pending", "paid", "failed"
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="pay_links")
    token = models.CharField(max_length=24, unique=True)
    provider = models.CharField(max_length=10)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=8, default=PENDING)
    provider_ref = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class StockRequest(models.Model):
    """Chef / kitchen asks the manager for more of an ingredient; approving adds it to a draft purchase order."""
    PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"
    STATUS = [(PENDING, "Pending"), (APPROVED, "Approved"), (REJECTED, "Rejected")]
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="stock_requests")
    ingredient = models.ForeignKey(Ingredient, on_delete=models.CASCADE, related_name="requests")
    qty = models.DecimalField(max_digits=14, decimal_places=3)
    urgent = models.BooleanField(default=False)
    note = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default=PENDING)
    requested_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="stock_requests")
    decided_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=200, blank=True)
    po = models.ForeignKey(PurchaseOrder, null=True, blank=True, on_delete=models.SET_NULL, related_name="requests")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]


class Notification(models.Model):
    """In-app alert shown under the bell icon (stock requests, approvals...)."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=120)
    body = models.CharField(max_length=255, blank=True)
    url = models.CharField(max_length=200, blank=True)
    kind = models.CharField(max_length=20, default="info")      # info | urgent | ok | bad
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]


# ----------------------------------------------------------------- automation
class AutomationEvent(models.Model):
    """Dedupe + throttle markers for the automation engine (one row per thing already alerted / last-run time)."""
    key = models.CharField(max_length=120, unique=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now)


class MessageLog(models.Model):
    """Every message the system tried to send (customers, suppliers, staff, app webhooks)."""
    CHANNELS = [("whatsapp", "WhatsApp"), ("sms", "SMS"), ("email", "Email"), ("webhook", "Webhook")]
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    channel = models.CharField(max_length=10, choices=CHANNELS, default="sms")
    kind = models.CharField(max_length=30, blank=True)
    to = models.CharField(max_length=120)
    subject = models.CharField(max_length=160, blank=True)
    body = models.TextField()
    status = models.CharField(max_length=10, default="logged")       # sent | logged | failed
    error = models.CharField(max_length=255, blank=True)
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.SET_NULL)
    order = models.ForeignKey("Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="messages")

    class Meta:
        ordering = ["-created_at", "-id"]


class DailyReport(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="daily_reports")
    date = models.DateField()
    text = models.TextField()
    sales = models.DecimalField(max_digits=14, decimal_places=2, default=D0)
    orders = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-date", "branch"]
        unique_together = ("branch", "date")


class PriceRule(models.Model):
    """Happy-hour / slow-hour pricing: percent off (negative = surcharge) between two hours on chosen weekdays."""
    name = models.CharField(max_length=60)
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.CASCADE, help_text="Empty = all branches")
    start_hour = models.PositiveSmallIntegerField(default=15)
    end_hour = models.PositiveSmallIntegerField(default=18, help_text="Exclusive, 1-24")
    days = models.CharField(max_length=7, default="0123456", help_text="Weekdays, Mon=0 ... Sun=6")
    percent = models.DecimalField(max_digits=5, decimal_places=2, default=10)
    is_active = models.BooleanField(default=True)

    def applies(self, branch, local_dt):
        if not self.is_active or (self.branch_id and self.branch_id != branch.pk):
            return False
        if str(local_dt.weekday()) not in self.days:
            return False
        h = local_dt.hour
        return self.start_hour <= h < self.end_hour if self.start_hour < self.end_hour else (h >= self.start_hour or h < self.end_hour)


class AILog(models.Model):
    """One row per AI call: which feature, whether Claude or the built-in fallback answered, how long it took."""
    kind = models.CharField(max_length=30, db_index=True)
    used_llm = models.BooleanField(default=False)
    ok = models.BooleanField(default=True)
    ms = models.PositiveIntegerField(default=0)
    error = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]


class AISession(models.Model):
    """Pending WhatsApp order waiting for the customer's YES."""
    phone = models.CharField(max_length=30, unique=True)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="+")
    payload = models.TextField(default="{}")
    created_at = models.DateTimeField(default=timezone.now)


class AuditLog(models.Model):
    """Who changed what: master data edits, settings, staff, logins (and failed logins)."""
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_rows")
    username = models.CharField(max_length=150, blank=True)
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=14, db_index=True)       # created / changed / deleted / login / login_failed / logout
    model = models.CharField(max_length=40, blank=True)
    object_repr = models.CharField(max_length=160, blank=True)
    changes = models.TextField(blank=True)
    ip = models.CharField(max_length=45, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class Expense(models.Model):
    """Money going out that is not stock or salary: rent, bills, repairs, marketing..."""
    CATS = [("rent", "Rent"), ("utilities", "Utilities (power, gas, water)"), ("maintenance", "Repair & maintenance"),
            ("marketing", "Marketing"), ("packaging", "Packaging"), ("transport", "Transport & fuel"),
            ("licence", "Licences & fees"), ("other", "Other")]
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="expenses")
    category = models.CharField(max_length=12, choices=CATS, default="other")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField(default=timezone.localdate, db_index=True)
    note = models.CharField(max_length=200, blank=True)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.get_category_display()} {self.amount}"


class Coupon(models.Model):
    """Promo code: percent or fixed amount off the food subtotal."""
    PERCENT, FIXED = "percent", "fixed"
    KINDS = [(PERCENT, "Percent off"), (FIXED, "Fixed amount off")]
    code = models.CharField(max_length=20, unique=True)
    description = models.CharField(max_length=120, blank=True)
    kind = models.CharField(max_length=8, choices=KINDS, default=PERCENT)
    value = models.DecimalField(max_digits=10, decimal_places=2)
    min_order = models.DecimalField(max_digits=12, decimal_places=2, default=D0)
    max_discount = models.DecimalField(max_digits=12, decimal_places=2, default=D0, help_text="0 = no cap (percent coupons)")
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    usage_limit = models.PositiveIntegerField(default=0, help_text="0 = unlimited")
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.CASCADE, related_name="coupons",
                               help_text="Empty = every branch")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-id"]

    def save(self, *a, **k):
        self.code = (self.code or "").strip().upper()
        super().save(*a, **k)

    @property
    def used(self):
        return self.orders.exclude(status="cancelled").count()

    def __str__(self):
        return self.code


class ModifierGroup(models.Model):
    """A choice shown when an item is added: Size (pick one), Extras (pick many)..."""
    name = models.CharField(max_length=60)
    required = models.BooleanField(default=False, help_text="The customer must pick at least one option")
    multi = models.BooleanField(default=False, help_text="Allow more than one option (extras). Off = pick exactly one (size)")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def option_list(self):
        return ", ".join(o.name for o in self.options.all() if o.is_active)


class ModifierOption(models.Model):
    group = models.ForeignKey(ModifierGroup, on_delete=models.CASCADE, related_name="options")
    name = models.CharField(max_length=60)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=D0, help_text="Added to the item price")
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.group.name}: {self.name}"
