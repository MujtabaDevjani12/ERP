"""
Manufacturing: BOM (versioned) -> work order -> material issue -> job cards (operations) -> finished goods receipt -> close.
WIP is tracked per work order (materials issued + labour/overhead charged), so the WIP account always equals the
cost of the open work orders.
"""
from django.db import models

from core.models import CreatedByModel, TimeStampedModel


class Machine(TimeStampedModel):
    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="machines")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company", "code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="uniq_machine_code")]

    def __str__(self):
        return f"{self.code} {self.name}"


class Operation(TimeStampedModel):
    """A production step: cutting, stitching, dyeing, finishing, packing..."""
    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="operations")
    name = models.CharField(max_length=80)
    qc_required = models.BooleanField(default=False, help_text="An in-process quality check is needed before the job card closes.")

    class Meta:
        ordering = ["company", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_operation_name")]

    def __str__(self):
        return self.name


class BOM(TimeStampedModel, CreatedByModel):
    DRAFT, ACTIVE, ARCHIVED = "draft", "active", "archived"
    STATUS_CHOICES = [(DRAFT, "Draft"), (ACTIVE, "Active"), (ARCHIVED, "Archived")]

    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="boms")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="boms", help_text="The finished item this BOM makes.")
    version = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT, db_index=True)
    output_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=1, help_text="Quantity of finished item that the component list produces.")
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "BOM"
        verbose_name_plural = "BOMs"
        ordering = ["variant", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["variant", "version"], name="uniq_bom_version"),
            models.UniqueConstraint(fields=["variant"], condition=models.Q(status="active"), name="one_active_bom_per_item"),
            models.CheckConstraint(check=models.Q(output_quantity__gt=0), name="bom_output_positive"),
        ]

    def __str__(self):
        return f"BOM {self.variant.sku} v{self.version} ({self.status})"


class BOMItem(models.Model):
    bom = models.ForeignKey(BOM, on_delete=models.CASCADE, related_name="items")
    component = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=4, help_text="Per BOM output quantity.")
    scrap_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    class Meta:
        verbose_name = "BOM component"
        constraints = [
            models.UniqueConstraint(fields=["bom", "component"], name="uniq_bom_component"),
            models.CheckConstraint(check=models.Q(quantity__gt=0), name="bom_item_qty_positive"),
        ]


class BOMOperation(models.Model):
    bom = models.ForeignKey(BOM, on_delete=models.CASCADE, related_name="operations")
    operation = models.ForeignKey(Operation, on_delete=models.PROTECT, related_name="+")
    sequence = models.PositiveSmallIntegerField(default=10)
    machine = models.ForeignKey(Machine, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    minutes_per_unit = models.DecimalField(max_digits=8, decimal_places=2, default=0, help_text="Standard minutes for one finished unit.")
    labor_rate_per_hour = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    overhead_rate_per_hour = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        verbose_name = "BOM operation"
        ordering = ["bom", "sequence"]
        constraints = [models.UniqueConstraint(fields=["bom", "sequence"], name="uniq_bom_op_sequence")]


class WorkOrder(TimeStampedModel, CreatedByModel):
    DRAFT, PENDING, RELEASED, IN_PROGRESS, COMPLETED, CLOSED, REJECTED, CANCELLED = (
        "draft", "pending_approval", "released", "in_progress", "completed", "closed", "rejected", "cancelled")
    STATUS_CHOICES = [(DRAFT, "Draft"), (PENDING, "Pending approval"), (RELEASED, "Released"), (IN_PROGRESS, "In progress"),
                      (COMPLETED, "Completed"), (CLOSED, "Closed"), (REJECTED, "Rejected"), (CANCELLED, "Cancelled")]
    PRIORITY_CHOICES = [(1, "Urgent"), (2, "High"), (3, "Normal"), (4, "Low")]

    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="work_orders")
    reference = models.CharField(max_length=30, unique=True)
    bom = models.ForeignKey(BOM, on_delete=models.PROTECT, related_name="work_orders")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="work_orders")
    planned_quantity = models.DecimalField(max_digits=14, decimal_places=3)
    received_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0, help_text="Good finished goods received into stock.")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT, db_index=True)
    priority = models.PositiveSmallIntegerField(choices=PRIORITY_CHOICES, default=3)
    source_warehouse = models.ForeignKey("tenants.Warehouse", on_delete=models.PROTECT, related_name="+", help_text="Where raw materials are issued from.")
    fg_warehouse = models.ForeignKey("tenants.Warehouse", on_delete=models.PROTECT, related_name="+", help_text="Where finished goods are received.")
    planned_start = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    # cost accumulators (WIP)
    material_cost = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    labor_cost = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    overhead_cost = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    fg_value_received = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    variance_written_off = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["priority", "-created_at"]
        constraints = [models.CheckConstraint(check=models.Q(planned_quantity__gt=0), name="wo_planned_positive")]

    def __str__(self):
        return self.reference

    @property
    def total_cost(self):
        return self.material_cost + self.labor_cost + self.overhead_cost

    @property
    def wip_balance(self):
        return self.total_cost - self.fg_value_received - self.variance_written_off


class WorkOrderMaterial(models.Model):
    work_order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, related_name="materials")
    component = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    required_quantity = models.DecimalField(max_digits=14, decimal_places=4)
    issued_quantity = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    issued_value = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["work_order", "component"], name="uniq_wo_component")]

    @property
    def pending_quantity(self):
        return max(self.required_quantity - self.issued_quantity, 0)


class MaterialIssue(TimeStampedModel, CreatedByModel):
    ISSUE, RETURN = "issue", "return"
    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="material_issues")
    reference = models.CharField(max_length=30, unique=True)
    work_order = models.ForeignKey(WorkOrder, on_delete=models.PROTECT, related_name="issues")
    kind = models.CharField(max_length=6, choices=[(ISSUE, "Issue"), (RETURN, "Return to store")], default=ISSUE)
    total_value = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference


class MaterialIssueLine(models.Model):
    issue = models.ForeignKey(MaterialIssue, on_delete=models.CASCADE, related_name="lines")
    material = models.ForeignKey(WorkOrderMaterial, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=4)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4)


class JobCard(TimeStampedModel):
    PENDING, IN_PROGRESS, QC_PENDING, COMPLETED = "pending", "in_progress", "qc_pending", "completed"
    STATUS_CHOICES = [(PENDING, "Pending"), (IN_PROGRESS, "In progress"), (QC_PENDING, "Waiting for QC"), (COMPLETED, "Completed")]

    work_order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, related_name="job_cards")
    reference = models.CharField(max_length=30, unique=True)
    operation = models.ForeignKey(Operation, on_delete=models.PROTECT, related_name="+")
    sequence = models.PositiveSmallIntegerField()
    machine = models.ForeignKey(Machine, on_delete=models.SET_NULL, null=True, blank=True, related_name="job_cards")
    operator = models.ForeignKey("hr.Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="job_cards")
    planned_quantity = models.DecimalField(max_digits=14, decimal_places=3)
    completed_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    rejected_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    standard_minutes = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    actual_minutes = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    labor_rate_per_hour = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    overhead_rate_per_hour = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["work_order", "sequence"]
        constraints = [models.UniqueConstraint(fields=["work_order", "sequence"], name="uniq_jobcard_sequence")]

    def __str__(self):
        return self.reference


class ProductionReceipt(TimeStampedModel, CreatedByModel):
    POSTED, QC_PENDING = "posted", "qc_pending"
    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="production_receipts")
    reference = models.CharField(max_length=30, unique=True)
    work_order = models.ForeignKey(WorkOrder, on_delete=models.PROTECT, related_name="receipts")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    accepted_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    rejected_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    value = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    status = models.CharField(max_length=12, choices=[(POSTED, "Posted"), (QC_PENDING, "Waiting for final QC")], default=POSTED)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference
