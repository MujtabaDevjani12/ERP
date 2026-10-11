from django.db import models

from catalog.models import ProductVariant
from core.models import CreatedByModel, TimeStampedModel
from tenants.models import Store, Warehouse


class Supplier(TimeStampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="suppliers")
    name = models.CharField(max_length=150)
    contact_person = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    company = models.CharField("Company / trade name", max_length=150, blank=True)
    tax_no = models.CharField("NTN / tax number", max_length=40, blank=True)
    city = models.CharField(max_length=80, blank=True)
    state = models.CharField("Province / state", max_length=80, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=80, blank=True)
    payment_term_days = models.PositiveSmallIntegerField("Payment terms (days)", default=0,
                                                         help_text="Days allowed to pay after receiving goods. 0 = pay on delivery.")
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PurchaseOrder(TimeStampedModel, CreatedByModel):
    STATUS_CHOICES = [
        ("draft", "Draft"), ("ordered", "Ordered"), ("received", "Received"), ("cancelled", "Cancelled"),
    ]

    reference = models.CharField(max_length=30, unique=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_orders")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="purchase_orders")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="draft")
    order_date = models.DateField()
    expected_date = models.DateField("Expected delivery", null=True, blank=True)
    supplier_ref = models.CharField("Supplier invoice / ref", max_length=60, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-order_date", "-created_at"]

    def __str__(self):
        return self.reference

    @property
    def total_amount(self):
        return sum((item.line_total for item in self.items.all()), start=0)

    @property
    def paid_amount(self):
        return sum((p.amount for p in self.payments.all()), start=0)

    @property
    def returned_amount(self):
        """Value of goods sent back to the supplier (what we no longer owe for them)."""
        return sum((i.quantity * i.cost_price for r in self.returns.all() for i in r.items.all()), start=0)

    @property
    def due_amount(self):
        """What is still owed to the supplier: the order, less goods sent back, less what was paid (negative = the supplier owes us)."""
        return self.total_amount - self.returned_amount - self.paid_amount


class PurchaseOrderItem(models.Model):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="purchase_items")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2)

    @property
    def line_total(self):
        return self.quantity * self.cost_price

    def __str__(self):
        return f"{self.variant} x {self.quantity}"


class PurchasePayment(TimeStampedModel, CreatedByModel):
    METHOD_CHOICES = [
        ("cash", "Cash"), ("card", "Card"), ("bank_transfer", "Bank transfer"), ("cheque", "Cheque"),
    ]

    reference = models.CharField(max_length=30, unique=True)
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=15, choices=METHOD_CHOICES, default="cash")
    paid_on = models.DateField()
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-paid_on"]

    def __str__(self):
        return self.reference


class PurchaseReturn(TimeStampedModel, CreatedByModel):
    reference = models.CharField(max_length=30, unique=True)
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="returns")
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference


class PurchaseReturnItem(models.Model):
    purchase_return = models.ForeignKey(PurchaseReturn, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="purchase_return_items")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2)
