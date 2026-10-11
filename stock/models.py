"""
Stock ledger. The ledger is the single source of truth for WHAT happened to
stock; inventory.StockItem stays as the fast "current quantity" the POS reads
and is kept in step by post_stock_movement() (the only writer).
"""
from django.conf import settings
from django.db import models

from audit.models import ImmutableQuerySet
from core.models import CreatedByModel, TimeStampedModel


class StockPolicy(TimeStampedModel):
    company = models.OneToOneField("company.Company", on_delete=models.CASCADE, related_name="stock_policy")
    allow_negative = models.BooleanField(
        default=False, help_text="Allow ERP documents (issues, transfers, deliveries) to take stock below zero. "
                                 "The POS counter is not affected by this switch.")

    class Meta:
        verbose_name_plural = "Stock policies"

    def __str__(self):
        return f"Stock policy of {self.company}"


class Batch(TimeStampedModel):
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.CASCADE, related_name="batches")
    batch_no = models.CharField(max_length=60)
    manufacture_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    supplier_lot = models.CharField(max_length=60, blank=True)

    class Meta:
        verbose_name_plural = "Batches"
        constraints = [models.UniqueConstraint(fields=["variant", "batch_no"], name="uniq_batch_per_variant")]

    def __str__(self):
        return f"{self.variant.sku} / {self.batch_no}"


class BatchStock(TimeStampedModel):
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="stocks")
    warehouse = models.ForeignKey("tenants.Warehouse", on_delete=models.CASCADE, related_name="batch_stocks")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["batch", "warehouse"], name="uniq_batch_stock")]

    def __str__(self):
        return f"{self.batch} @ {self.warehouse}: {self.quantity}"


class ItemWarehouseCost(TimeStampedModel):
    """Weighted-average cost of one variant in one warehouse."""
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.CASCADE, related_name="warehouse_costs")
    warehouse = models.ForeignKey("tenants.Warehouse", on_delete=models.CASCADE, related_name="item_costs")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    avg_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["variant", "warehouse"], name="uniq_cost_per_variant_warehouse")]

    def __str__(self):
        return f"{self.variant.sku} @ {self.warehouse}: {self.quantity} x {self.avg_cost}"

    @property
    def value(self):
        return self.quantity * self.avg_cost


class _ImmutableLedgerQuerySet(ImmutableQuerySet):
    def update(self, *args, **kwargs):
        raise RuntimeError("Stock ledger entries are immutable. Post a reversing movement instead.")

    def delete(self):
        raise RuntimeError("Stock ledger entries are immutable.")


class StockLedgerEntry(models.Model):
    """One row per stock movement; never edited, never deleted."""
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="ledger_entries")
    warehouse = models.ForeignKey("tenants.Warehouse", on_delete=models.PROTECT, related_name="ledger_entries")
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, null=True, blank=True, related_name="ledger_entries")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, help_text="Signed: positive = in, negative = out.")
    balance_after = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    value_change = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    reference_type = models.CharField(max_length=30, db_index=True)
    reference_no = models.CharField(max_length=40, blank=True, db_index=True)
    remarks = models.CharField(max_length=255, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    posted_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = _ImmutableLedgerQuerySet.as_manager()

    class Meta:
        ordering = ["-posted_at", "-id"]
        verbose_name_plural = "Stock ledger"
        indexes = [models.Index(fields=["variant", "warehouse", "posted_at"], name="stockledger_vwp_idx")]

    def __str__(self):
        return f"{self.posted_at:%Y-%m-%d} {self.variant.sku} {self.quantity:+} {self.reference_type} {self.reference_no}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise RuntimeError("Stock ledger entries are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Stock ledger entries are immutable.")


# ------------------------------------------------------------------ transfers
class TransferOrder(TimeStampedModel, CreatedByModel):
    DRAFT, PENDING, APPROVED, IN_TRANSIT, RECEIVED, REJECTED, CANCELLED = (
        "draft", "pending_approval", "approved", "in_transit", "received", "rejected", "cancelled")
    STATUS_CHOICES = [(DRAFT, "Draft"), (PENDING, "Pending approval"), (APPROVED, "Approved"), (IN_TRANSIT, "In transit"),
                      (RECEIVED, "Received"), (REJECTED, "Rejected"), (CANCELLED, "Cancelled")]

    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="transfer_orders")
    reference = models.CharField(max_length=30, unique=True)
    from_warehouse = models.ForeignKey("tenants.Warehouse", on_delete=models.PROTECT, related_name="+")
    to_warehouse = models.ForeignKey("tenants.Warehouse", on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT, db_index=True)
    note = models.TextField(blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.CheckConstraint(check=~models.Q(from_warehouse=models.F("to_warehouse")), name="transfer_distinct_warehouses")]

    def __str__(self):
        return self.reference


class TransferOrderItem(models.Model):
    order = models.ForeignKey(TransferOrder, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0, help_text="Cost at dispatch (weighted average).")
    received_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)

    class Meta:
        constraints = [models.CheckConstraint(check=models.Q(quantity__gt=0), name="transfer_item_qty_positive")]

    def __str__(self):
        return f"{self.variant.sku} x {self.quantity}"


# ------------------------------------------------------------------ adjustments
class AdjustmentOrder(TimeStampedModel, CreatedByModel):
    DRAFT, PENDING, POSTED, REJECTED, CANCELLED = "draft", "pending_approval", "posted", "rejected", "cancelled"
    # 'approved' is transient: approval immediately posts the movement.
    STATUS_CHOICES = [(DRAFT, "Draft"), (PENDING, "Pending approval"), (POSTED, "Posted"), (REJECTED, "Rejected"), (CANCELLED, "Cancelled")]
    REASON_CHOICES = [("damage", "Damaged"), ("theft", "Theft / loss"), ("recount", "Physical count"), ("expired", "Expired"),
                      ("found", "Found / surplus"), ("scrap", "Scrap"), ("other", "Other")]

    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="adjustment_orders")
    reference = models.CharField(max_length=30, unique=True)
    warehouse = models.ForeignKey("tenants.Warehouse", on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=10, choices=REASON_CHOICES, default="recount")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT, db_index=True)
    note = models.TextField(blank=True)
    source_count = models.ForeignKey("StockCount", on_delete=models.SET_NULL, null=True, blank=True, related_name="adjustments")
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference


class AdjustmentOrderItem(models.Model):
    order = models.ForeignKey(AdjustmentOrder, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    quantity_change = models.DecimalField(max_digits=14, decimal_places=3, help_text="Positive adds stock, negative removes.")
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0, help_text="Only used for increases; decreases use the average cost.")

    class Meta:
        constraints = [models.CheckConstraint(check=~models.Q(quantity_change=0), name="adjustment_item_nonzero")]


# ------------------------------------------------------------------ physical count
class StockCount(TimeStampedModel, CreatedByModel):
    OPEN, SUBMITTED, POSTED, CANCELLED = "open", "submitted", "posted", "cancelled"
    STATUS_CHOICES = [(OPEN, "Counting"), (SUBMITTED, "Submitted for adjustment"), (POSTED, "Adjusted"), (CANCELLED, "Cancelled")]

    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="stock_counts")
    reference = models.CharField(max_length=30, unique=True)
    warehouse = models.ForeignKey("tenants.Warehouse", on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=OPEN)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference


class StockCountLine(models.Model):
    count = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name="lines")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    system_quantity = models.DecimalField(max_digits=14, decimal_places=3, help_text="Snapshot when the count was opened.")
    counted_quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["count", "variant"], name="uniq_count_line_variant")]

    @property
    def difference(self):
        return None if self.counted_quantity is None else self.counted_quantity - self.system_quantity
