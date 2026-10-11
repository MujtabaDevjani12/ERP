"""
ERP master data that EXTENDS the existing POS tables instead of changing
them: ItemProfile hangs off catalog.ProductVariant, VendorProfile off
suppliers.Supplier, CustomerProfile off customers.Customer (one-to-one).
The POS keeps reading its own tables exactly as before.
"""
from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class PaymentTerm(TimeStampedModel):
    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="payment_terms")
    name = models.CharField(max_length=60, help_text="e.g. Net 30, Cash on delivery")
    days = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company", "days", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_payment_term_name")]

    def __str__(self):
        return self.name


class TaxRate(TimeStampedModel):
    """Configurable tax. No tax rate is hard-coded anywhere in the ERP."""
    INPUT, OUTPUT, BOTH, EXEMPT = "input", "output", "both", "exempt"
    TYPE_CHOICES = [(INPUT, "Input tax (purchases)"), (OUTPUT, "Output tax (sales)"), (BOTH, "Input & output"), (EXEMPT, "Tax exempt")]

    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="tax_rates")
    name = models.CharField(max_length=60)
    rate = models.DecimalField(max_digits=6, decimal_places=2, default=0, help_text="Percent, e.g. 18 for 18%.")
    tax_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=BOTH)
    is_inclusive = models.BooleanField(default=False, help_text="Prices entered already include this tax.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_tax_rate_name")]

    def __str__(self):
        return f"{self.name} ({self.rate}%)"


class ItemProfile(TimeStampedModel):
    RAW, FINISHED, CONSUMABLE, PACKAGING, SPARE, TRADING = "raw_material", "finished_good", "consumable", "packaging", "spare_part", "trading"
    TYPE_CHOICES = [
        (RAW, "Raw material (yarn, fabric, chemicals, buttons, zippers, thread, labels)"),
        (FINISHED, "Finished good"), (CONSUMABLE, "Consumable"), (PACKAGING, "Packaging"),
        (SPARE, "Machine / spare part"), (TRADING, "Trading / retail item"),
    ]
    variant = models.OneToOneField("catalog.ProductVariant", on_delete=models.CASCADE, related_name="erp_profile")
    item_type = models.CharField(max_length=15, choices=TYPE_CHOICES, default=TRADING)
    purchase_uom = models.CharField(max_length=20, blank=True)
    stock_uom = models.CharField(max_length=20, blank=True)
    conversion_factor = models.DecimalField(max_digits=14, decimal_places=4, default=1,
                                            help_text="1 purchase UOM = this many stock UOM.")
    color = models.CharField(max_length=40, blank=True)
    size = models.CharField(max_length=40, blank=True)
    gsm = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    composition = models.CharField(max_length=120, blank=True)
    season = models.CharField(max_length=40, blank=True)
    batch_required = models.BooleanField(default=False)
    expiry_required = models.BooleanField(default=False)
    serial_required = models.BooleanField(default=False)
    qc_required_on_receipt = models.BooleanField(default=False, help_text="Goods receipt of this item waits for incoming QC.")
    reorder_level = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    minimum_stock = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    maximum_stock = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    standard_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    tax_rate = models.ForeignKey(TaxRate, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    def __str__(self):
        return f"ERP profile of {self.variant}"


class VendorProfile(TimeStampedModel):
    supplier = models.OneToOneField("suppliers.Supplier", on_delete=models.CASCADE, related_name="erp_profile")
    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="vendor_profiles")
    vendor_code = models.CharField(max_length=20)
    city = models.CharField(max_length=80, blank=True)
    tax_number = models.CharField(max_length=60, blank=True)
    payment_term = models.ForeignKey(PaymentTerm, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    credit_limit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    bank_details = models.TextField(blank=True)
    opening_balance = models.DecimalField(max_digits=16, decimal_places=2, default=0,
                                          help_text="Informational. Post real opening balances through an opening journal.")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "vendor_code"], name="uniq_vendor_code")]

    def __str__(self):
        return f"{self.vendor_code} {self.supplier}"


class CustomerProfile(TimeStampedModel):
    WHOLESALE, CORPORATE, DISTRIBUTOR, RETAIL, EXPORT = "wholesale", "corporate", "distributor", "retail", "export"
    TYPE_CHOICES = [(WHOLESALE, "Wholesale"), (CORPORATE, "Corporate"), (DISTRIBUTOR, "Distributor"), (RETAIL, "Retail"), (EXPORT, "Export")]

    customer = models.OneToOneField("customers.Customer", on_delete=models.CASCADE, related_name="erp_profile")
    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="customer_profiles")
    customer_code = models.CharField(max_length=20)
    customer_type = models.CharField(max_length=12, choices=TYPE_CHOICES, default=RETAIL)
    company_name = models.CharField(max_length=150, blank=True)
    city = models.CharField(max_length=80, blank=True)
    tax_number = models.CharField(max_length=60, blank=True)
    payment_term = models.ForeignKey(PaymentTerm, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    credit_limit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    opening_balance = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "customer_code"], name="uniq_customer_code")]

    def __str__(self):
        return f"{self.customer_code} {self.customer}"


class ImportJob(TimeStampedModel):
    """One Excel upload: Upload -> Validate -> Preview/Errors -> Confirm -> Import.
    Nothing is imported unless validation found zero errors."""
    UPLOADED, VALIDATED, FAILED, IMPORTED = "uploaded", "validated", "failed", "imported"
    STATUS_CHOICES = [(UPLOADED, "Uploaded"), (VALIDATED, "Validated - ready to import"), (FAILED, "Has errors"), (IMPORTED, "Imported")]

    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="import_jobs")
    import_type = models.CharField(max_length=30)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=UPLOADED)
    rows = models.JSONField(default=list, blank=True)
    errors = models.JSONField(default=list, blank=True)
    summary = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.import_type} ({self.status})"
