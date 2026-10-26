from decimal import Decimal

from django.conf import settings
from django.db import models

from catalog.models import ProductVariant
from core.models import TimeStampedModel
from register.models import RegisterSession
from tenants.models import Store, Warehouse


class Sale(TimeStampedModel):
    STATUS_CHOICES = [
        ("completed", "Completed"), ("held", "Held / suspended"),
        ("returned", "Returned"), ("partially_returned", "Partially returned"),
        ("cancelled", "Cancelled"),
    ]

    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="sales")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="sales")
    register_session = models.ForeignKey(RegisterSession, on_delete=models.PROTECT, related_name="sales", null=True, blank=True)
    reference = models.CharField(max_length=30, unique=True)
    biller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sales_billed")
    customer = models.ForeignKey("customers.Customer", on_delete=models.SET_NULL, null=True, blank=True, related_name="sales")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="completed")

    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    shipping_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0, help_text="Delivery / shipping charge added to the bill.")
    rounding_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0, help_text="What the total was rounded up (+) or down (-) by, per Settings > POS behaviour.")
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    sale_note = models.TextField(blank=True)
    staff_note = models.TextField(blank=True)
    checkout_token = models.CharField(
        max_length=64, null=True, blank=True, unique=True,
        help_text="Random id the POS sends with every checkout attempt, so a double click / retry can never create two sales.")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="sale_status_created_idx"),
            models.Index(fields=["store", "created_at"], name="sale_store_created_idx"),
        ]

    def __str__(self):
        return self.reference

    @property
    def paid_amount(self):
        return sum((p.amount for p in self.payments.all() if p.status != "voided"), start=0)

    @property
    def returned_amount(self):
        """Value of the goods that came back (price - discount + tax). Zero for a sale nobody returned anything from."""
        if self.status not in ("partially_returned", "returned"):
            return Decimal("0")
        from .returns import refund_value
        total = Decimal("0")
        for item in self.items.all():
            back = sum((r.quantity for r in item.return_items.all()), Decimal("0"))
            if back:
                total += refund_value(item, back)
        return total

    @property
    def refunded_amount(self):
        if self.status not in ("partially_returned", "returned"):
            return Decimal("0")
        return sum((r.amount for r in self.refunds.all()), start=Decimal("0"))

    @property
    def net_total(self):
        """What the customer really owes for after returns."""
        return self.total_amount - self.returned_amount

    @property
    def net_paid(self):
        """What the shop really keeps of the payments after refunds."""
        return self.paid_amount - self.refunded_amount

    @property
    def due_amount(self):
        if self.status in ("partially_returned", "returned"):
            return max(Decimal("0"), self.net_total - self.net_paid)
        return self.total_amount - self.paid_amount

    @property
    def payment_status(self):
        paid, total = (self.net_paid, self.net_total) if self.status in ("partially_returned", "returned") else (self.paid_amount, self.total_amount)
        if paid <= 0:
            return "due" if total > 0 else "paid"
        if paid < total:
            return "partial"
        return "paid"


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="sale_items")
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    @property
    def line_total(self):
        return (self.quantity * self.unit_price) - self.discount_amount + self.tax_amount

    def __str__(self):
        return f"{self.variant} x {self.quantity}"


class PosCart(TimeStampedModel):
    """The cart of an open register session, kept on the server so a page refresh, an accidental reload or a short
    network drop never loses it. One cart per register session = per counter, so counters never see each other's carts.
    Holds items/customer/notes only - never any payment details. Deleted at checkout, on 'clear' and when the register closes."""
    register_session = models.OneToOneField(RegisterSession, on_delete=models.CASCADE, related_name="cart")
    data = models.JSONField(default=dict, blank=True)
    version = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Cart of session {self.register_session_id}"


class SaleReturn(TimeStampedModel):
    reference = models.CharField(max_length=30, unique=True)
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="returns")
    reason = models.CharField(max_length=255, blank=True)
    processed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference


class SaleReturnItem(models.Model):
    sale_return = models.ForeignKey(SaleReturn, on_delete=models.CASCADE, related_name="items")
    sale_item = models.ForeignKey(SaleItem, on_delete=models.PROTECT, related_name="return_items")
    quantity = models.DecimalField(max_digits=12, decimal_places=3)


class Delivery(TimeStampedModel):
    """Getting a sale to the customer: who it is for, where it goes, who carries it and whether it arrived.
    (status codes are kept from the first version; the labels are the ones the shop staff use.)"""
    PACKING, ON_THE_WAY, DELIVERED, CANCELLED = "pending", "out_for_delivery", "delivered", "cancelled"
    STATUS_CHOICES = [
        ("pending", "Packing"), ("out_for_delivery", "On the way"),
        ("delivered", "Delivered"), ("cancelled", "Cancelled"),
    ]
    CLOSED = ("delivered", "cancelled")

    reference = models.CharField(max_length=30, unique=True)
    sale = models.OneToOneField(Sale, on_delete=models.CASCADE, related_name="delivery")
    recipient_name = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=55, blank=True)
    delivery_person = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="deliveries")
    rider = models.CharField(max_length=100, blank=True, help_text="Rider, driver or courier company.")
    tracking_no = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True)
    scheduled_date = models.DateField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    received_by = models.CharField(max_length=100, blank=True, help_text="Who signed for the parcel.")
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Deliveries"

    def __str__(self):
        return self.reference

    @property
    def is_closed(self):
        return self.status in self.CLOSED


class SaleFeedback(TimeStampedModel):
    """What the customer said about their visit, collected at the POS after payment (optional, one per sale)."""
    sale = models.OneToOneField(Sale, on_delete=models.CASCADE, related_name="feedback")
    rating = models.PositiveSmallIntegerField(help_text="1 (poor) to 5 (excellent).")
    comment = models.TextField(blank=True, max_length=1000)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Sale feedback"
        constraints = [models.CheckConstraint(check=models.Q(rating__gte=1, rating__lte=5), name="salefeedback_rating_1_5")]

    def __str__(self):
        return f"{self.sale.reference}: {self.rating}/5"
