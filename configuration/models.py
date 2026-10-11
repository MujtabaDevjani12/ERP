"""
Two deliberately separate settings surfaces, matching how you described
them: **POS Configuration** (how the billing screen behaves — one row per
store/branch, since a Gold Diamond counter and a Mart counter want
different defaults) and **System Configuration** (site-wide — one row for
the whole deployment). NumberingPrefix drives every document reference
number (SALE-000001, PO-000001, ...) from one shared place.
"""
from django.db import models, transaction

from core.models import TimeStampedModel
from tenants.models import Store, Warehouse


class POSConfiguration(TimeStampedModel):
    ITEM_ORDER_CHOICES = [("name", "Name"), ("created_at", "Newest first"), ("custom", "Custom sort order")]
    AFTER_SALE_CHOICES = [
        ("receipt", "Show receipt"),
        ("new_sale", "Start a new sale"),
        ("sale_list", "Go to sale list"),
    ]
    ROUNDING_CHOICES = [
        ("none", "No rounding"),
        ("nearest_1", "Nearest 1"),
        ("nearest_5", "Nearest 5"),
        ("nearest_10", "Nearest 10"),
        ("up", "Always round up"),
        ("down", "Always round down"),
    ]
    PRINTING_CHOICES = [
        ("browser", "Browser printing"),
        ("local_server", "Local print server (PHP/Node print bridge)"),
    ]

    store = models.OneToOneField(Store, on_delete=models.CASCADE, related_name="pos_configuration")

    # Display
    products_per_page = models.PositiveIntegerField(default=30)
    pos_access_pin = models.CharField(max_length=6, blank=True, help_text="Optional PIN required to open the POS screen.")
    default_category = models.ForeignKey("catalog.Category", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    default_biller = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", limit_choices_to={"is_biller": True})
    default_customer = models.ForeignKey("customers.Customer", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    default_warehouse = models.ForeignKey(Warehouse, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    show_clock = models.BooleanField(default=True, verbose_name="Display time")
    require_customer = models.BooleanField(default=True, help_text="The cashier must pick a customer before the first item goes into the cart.")
    on_screen_keyboard = models.BooleanField(default=False)
    product_button_color = models.CharField(max_length=7, default="#2563eb", help_text="Hex color for product tiles.")
    show_tooltips = models.BooleanField(default=True)
    rounding = models.CharField(max_length=12, choices=ROUNDING_CHOICES, default="none")
    item_order = models.CharField(max_length=12, choices=ITEM_ORDER_CHOICES, default="name")
    after_sale_page = models.CharField(max_length=12, choices=AFTER_SALE_CHOICES, default="receipt")
    show_customer_details_on_receipt = models.BooleanField(default=True)
    auto_print_receipt = models.BooleanField(default=True, help_text="Open the print dialog by itself as soon as a sale is completed.")

    # Printing
    printing_mode = models.CharField(max_length=15, choices=PRINTING_CHOICES, default="browser")
    receipt_custom_field1_title = models.CharField(max_length=60, blank=True)
    receipt_custom_field1_value = models.CharField(max_length=120, blank=True)
    receipt_custom_field2_title = models.CharField(max_length=60, blank=True)
    receipt_custom_field2_value = models.CharField(max_length=120, blank=True)

    class Meta:
        verbose_name = "POS configuration"
        verbose_name_plural = "POS configuration"

    def __str__(self):
        return f"POS settings — {self.store.name}"


class SystemConfiguration(TimeStampedModel):
    """Singleton (always pk=1) — one set of site-wide defaults for the
    whole deployment, shared by every store/branch."""
    DATE_FORMAT_CHOICES = [("d-m-Y", "DD-MM-YYYY"), ("m-d-Y", "MM-DD-YYYY"), ("Y-m-d", "YYYY-MM-DD")]
    BARCODE_CHOICES = [("code128", "Code 128"), ("code39", "Code 39"), ("qr", "QR Code")]
    FONT_CHOICES = [(name, name) for name in ["Inter", "Poppins", "Roboto", "Nunito", "Manrope", "Lato", "Work Sans"]]

    site_name = models.CharField(max_length=150, default="My POS")

    # Branding — drives the whole app's look: base.html generates a full
    # Tailwind color scale from primary_color (see core.utils.generate_color_shades)
    # and loads the chosen Google Font, so this one section re-skins every
    # page without editing individual templates.
    site_logo = models.ImageField(upload_to="branding/", blank=True, null=True, help_text="Shown in the sidebar header and the login page instead of the default icon.")
    primary_color = models.CharField(max_length=7, default="#4f46e5", help_text="Main brand color (hex) — buttons, links, active nav state, everywhere.")
    secondary_color = models.CharField(max_length=7, default="#111827", help_text="Sidebar / dark-surface color.")
    font_family = models.CharField(max_length=30, choices=FONT_CHOICES, default="Inter")
    FONT_SIZE_CHOICES = [("small", "Small"), ("normal", "Normal"), ("large", "Large")]
    font_size = models.CharField(max_length=8, choices=FONT_SIZE_CHOICES, default="normal", help_text="Makes everything slightly smaller or larger.")
    text_color = models.CharField(max_length=7, blank=True, default="", help_text="Main text colour as #rrggbb. Leave empty for the default.")

    tagline = models.CharField(max_length=150, blank=True, help_text="Short line shown under the business name (login page, receipts).")
    website = models.CharField(max_length=150, blank=True)
    tax_number = models.CharField("Tax / NTN number", max_length=40, blank=True, help_text="Printed on invoices and receipts.")

    default_language = models.CharField(max_length=10, default="en")
    default_currency_code = models.CharField(max_length=6, default="PKR")
    default_currency_symbol = models.CharField(max_length=6, default="Rs")
    default_email = models.EmailField(blank=True)
    default_warehouse = models.ForeignKey(Warehouse, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    maintenance_mode = models.BooleanField(default=False)
    theme = models.CharField(max_length=30, default="default")
    rtl_support = models.BooleanField(default=False)
    login_captcha = models.BooleanField(default=False)
    edit_lock_days = models.PositiveIntegerField(default=0, help_text="Days after which a sale/purchase can no longer be edited. 0 = never lock.")
    rows_per_page = models.PositiveIntegerField(default=25)
    date_format = models.CharField(max_length=10, choices=DATE_FORMAT_CHOICES, default="d-m-Y")
    default_timezone = models.CharField(max_length=50, default="Asia/Karachi")

    # Products
    enable_racks = models.BooleanField(default=False)
    enable_product_variants = models.BooleanField(default=True)
    enable_product_expiry = models.BooleanField(default=False)
    remove_expired_from_stock = models.BooleanField(default=False)
    product_image_width = models.PositiveIntegerField(default=1020)
    product_image_height = models.PositiveIntegerField(default=1020)
    thumbnail_width = models.PositiveIntegerField(default=60)
    thumbnail_height = models.PositiveIntegerField(default=60)
    barcode_separator = models.CharField(max_length=3, default="-")
    barcode_renderer = models.CharField(max_length=10, choices=BARCODE_CHOICES, default="code128")
    update_cost_with_purchase = models.BooleanField(default=True, help_text="Update a product's cost price automatically from purchase entries.")

    # Sales
    allow_over_selling = models.BooleanField(default=False, help_text="Allow a sale to exceed available stock.")
    order_tax_inclusive = models.BooleanField(default=False)
    allow_product_level_discount = models.BooleanField(default=True)
    auto_detect_barcode_scan = models.BooleanField(default=True, help_text="Treat fast, Enter-terminated input in the POS search box as a scanner, not typing.")
    barcode_scan_min_chars = models.PositiveIntegerField(default=4)

    # Money & number format
    decimals = models.PositiveIntegerField(default=2)
    quantity_decimals = models.PositiveIntegerField(default=0)
    decimal_separator = models.CharField(max_length=1, default=".")
    thousands_separator = models.CharField(max_length=1, default=",")
    display_currency_symbol = models.BooleanField(default=True)
    CURRENCY_POSITION_CHOICES = [("before", "Before the amount (Rs 1,250.00)"), ("after", "After the amount (1,250.00 Rs)")]
    currency_position = models.CharField(max_length=6, choices=CURRENCY_POSITION_CHOICES, default="before")

    # Outgoing email — who invoices and password-reset mails come from. The SMTP host/user/password themselves stay in .env
    # (secrets don't belong in the database); these only control the sender identity.
    email_from_name = models.CharField(max_length=100, blank=True, help_text="Shown as the sender name. Leave empty to use the business name.")
    email_from_address = models.EmailField(blank=True, help_text="Leave empty to use the server's DEFAULT_FROM_EMAIL. Must be an address your mail provider lets you send from.")
    email_reply_to = models.EmailField(blank=True, help_text="Where customer replies go.")

    # WhatsApp (sending uses the Meta Cloud API credentials in .env)
    whatsapp_country_code = models.CharField(max_length=5, default="92", help_text="Added in front of local phone numbers that start with 0 (92 for Pakistan).")
    whatsapp_message = models.TextField(
        blank=True, default="Hi {customer_name}, thank you for shopping with {store_name}. Your invoice {reference} for {total} is ready.",
        help_text="Placeholders: {customer_name}, {store_name}, {reference}, {total}.",
    )

    # Award points (loyalty)
    award_points_per_amount = models.DecimalField(max_digits=12, decimal_places=2, default=100, help_text="Spend this much to earn 'award points value' points.")
    award_points_value = models.PositiveIntegerField(default=1)

    class Meta:
        verbose_name = "System configuration"
        verbose_name_plural = "System configuration"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # singleton — never actually delete it

    @classmethod
    def get(cls) -> "SystemConfiguration":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "System configuration"


class NumberingPrefix(TimeStampedModel):
    """One row per document type. call `.next_reference()` to get e.g.
    'SALE-000001' and atomically bump the counter — used by sales, returns,
    payments, purchases, deliveries, transfers, expenses, adjustments."""
    SALE = "sale"
    SALE_RETURN = "sale_return"
    PAYMENT = "payment"
    PURCHASE_PAYMENT = "purchase_payment"
    DELIVERY = "delivery"
    PURCHASE = "purchase"
    PURCHASE_RETURN = "purchase_return"
    TRANSFER = "transfer"
    EXPENSE = "expense"
    QUANTITY_ADJUSTMENT = "quantity_adjustment"

    DOCUMENT_TYPES = [
        (SALE, "Sales reference"),
        (SALE_RETURN, "Return sale reference"),
        (PAYMENT, "Payment reference"),
        (PURCHASE_PAYMENT, "Purchase payment reference"),
        (DELIVERY, "Delivery reference"),
        (PURCHASE, "Purchase reference"),
        (PURCHASE_RETURN, "Return purchase reference"),
        (TRANSFER, "Stock transfer reference"),
        (EXPENSE, "Expense reference"),
        (QUANTITY_ADJUSTMENT, "Quantity adjustment reference"),
    ]
    DEFAULT_PREFIXES = {
        SALE: "SALE", SALE_RETURN: "SR", PAYMENT: "IPAY", PURCHASE_PAYMENT: "POP",
        DELIVERY: "DO", PURCHASE: "PO", PURCHASE_RETURN: "PR", TRANSFER: "TR",
        EXPENSE: "EXP", QUANTITY_ADJUSTMENT: "QA",
    }

    document_type = models.CharField(max_length=25, choices=DOCUMENT_TYPES, unique=True)
    prefix = models.CharField(max_length=15)
    next_number = models.PositiveIntegerField(default=1)
    padding = models.PositiveIntegerField(default=6)

    class Meta:
        ordering = ["document_type"]

    def __str__(self):
        return f"{self.get_document_type_display()} ({self.prefix}-{'0' * self.padding})"

    @classmethod
    def next_reference(cls, document_type: str) -> str:
        with transaction.atomic():
            row, _ = cls.objects.select_for_update().get_or_create(
                document_type=document_type,
                defaults={"prefix": cls.DEFAULT_PREFIXES.get(document_type, document_type.upper()[:6])},
            )
            number = row.next_number
            row.next_number = number + 1
            row.save(update_fields=["next_number", "updated_at"])
        return f"{row.prefix}-{str(number).zfill(row.padding)}"


class Currency(TimeStampedModel):
    """An extra currency a store can show prices in (barcode labels today). The store's own currency (Store.currency_code /
    currency_symbol) is the base and is implicit. `rate` says how much base currency one unit of this currency is worth:
    base PKR, USD rate 280 means 1 USD = 280 PKR, so a PKR 1,400 product is USD 5.00."""
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="currencies")
    code = models.CharField(max_length=6, help_text="International code, e.g. USD, AED, GBP.")
    name = models.CharField(max_length=60, blank=True, help_text="Optional, e.g. US Dollar.")
    symbol = models.CharField(max_length=8, help_text="Printed before the amount, e.g. $, AED, £.")
    rate = models.DecimalField(max_digits=18, decimal_places=6, help_text="How many units of your own (base) currency equal ONE unit of this currency. Example: 280 if 1 USD = 280 PKR.")
    decimals = models.PositiveSmallIntegerField(default=2, help_text="Decimal places to print (0 for whole numbers).")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "Currencies"
        constraints = [models.UniqueConstraint(fields=["store", "code"], name="uniq_currency_code_per_store")]

    def __str__(self):
        return f"{self.code} ({self.symbol}) · 1 {self.code} = {self.rate.normalize():f} {self.store.currency_code}"

    def convert(self, base_amount):
        """Base-currency amount -> this currency."""
        from decimal import Decimal
        return (Decimal(base_amount) / self.rate).quantize(Decimal(1).scaleb(-self.decimals))
