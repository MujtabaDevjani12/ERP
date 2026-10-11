from django.db import models

from catalog.models import ProductVariant
from core.models import CreatedByModel, TimeStampedModel
from tenants.models import Warehouse


class Rack(TimeStampedModel):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name="racks")
    name = models.CharField(max_length=60)

    class Meta:
        ordering = ["warehouse", "name"]
        constraints = [models.UniqueConstraint(fields=["warehouse", "name"], name="uniq_rack_name_per_warehouse")]

    def __str__(self):
        return f"{self.name} ({self.warehouse.name})"


class StockItem(TimeStampedModel):
    """The live, current quantity of one variant in one warehouse. Sales
    decrement it, purchases/adjustments/transfers move it — always through
    the model methods below, never by editing `quantity` directly, so the
    number on screen and the audit trail (StockAdjustment/PurchaseOrder/
    Sale) never drift apart."""
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="stock_items")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name="stock_items")
    rack = models.ForeignKey(Rack, on_delete=models.SET_NULL, null=True, blank=True, related_name="stock_items")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)

    class Meta:
        ordering = ["warehouse", "variant"]
        constraints = [models.UniqueConstraint(fields=["variant", "warehouse"], name="uniq_stock_per_variant_warehouse")]

    def __str__(self):
        return f"{self.variant} @ {self.warehouse.name}: {self.quantity}"

    def adjust(self, delta, *, reference_type="POS_LEGACY", reference_no="", user=None, remarks="", unit_cost=None):
        """delta can be positive (stock in) or negative (stock out).

        With the ERP `stock` app installed the movement goes through the audited stock ledger
        (so history and average cost stay correct) and this row's quantity is updated by it.
        Without it, this behaves exactly like before. The POS may sell below zero, as it always could
        (the over-selling rule is checked by the POS views, not here)."""
        from django.apps import apps
        from django.db import transaction
        if delta and apps.is_installed("stock"):
            from stock.services import post_stock_movement
            with transaction.atomic():
                post_stock_movement(
                    self.variant, self.warehouse, delta, reference_type=reference_type, reference_no=reference_no,
                    unit_cost=unit_cost, user=user, remarks=remarks, allow_negative=True)
            self.refresh_from_db(fields=["quantity"])
            return
        self.quantity = models.F("quantity") + delta
        self.save(update_fields=["quantity", "updated_at"])
        self.refresh_from_db(fields=["quantity"])


class StockAdjustment(TimeStampedModel, CreatedByModel):
    REASON_CHOICES = [
        ("damage", "Damaged"), ("theft", "Theft/loss"), ("recount", "Physical recount"),
        ("expired", "Expired"), ("other", "Other"),
    ]
    reference = models.CharField(max_length=30, unique=True)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="adjustments")
    reason = models.CharField(max_length=10, choices=REASON_CHOICES, default="recount")
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference


class StockAdjustmentItem(models.Model):
    adjustment = models.ForeignKey(StockAdjustment, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="adjustment_items")
    quantity_before = models.DecimalField(max_digits=14, decimal_places=3)
    quantity_change = models.DecimalField(max_digits=14, decimal_places=3, help_text="Positive to add stock, negative to remove.")

    @property
    def quantity_after(self):
        return self.quantity_before + self.quantity_change

    def __str__(self):
        return f"{self.variant} {self.quantity_change:+}"


class StockTransfer(TimeStampedModel, CreatedByModel):
    STATUS_CHOICES = [("draft", "Pending"), ("in_transit", "In transit"), ("received", "Completed"), ("cancelled", "Cancelled")]

    reference = models.CharField(max_length=30, unique=True)
    from_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="transfers_out")
    to_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="transfers_in")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="draft", db_index=True)
    note = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference


class StockTransferItem(models.Model):
    transfer = models.ForeignKey(StockTransfer, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="transfer_items")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0, help_text="Cost per unit when it left the source warehouse.")
