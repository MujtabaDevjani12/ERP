"""
Procurement: PR -> RFQ -> vendor quotations -> PO -> GRN (-> QC) -> purchase invoice -> vendor payment.

The existing POS purchase order (suppliers.PurchaseOrder) is REUSED as the PO
header/lines; PurchaseOrderExt adds the ERP fields (approval status, tax,
terms) next to it without altering the legacy table.
"""
from django.db import models

from core.models import CreatedByModel, TimeStampedModel


# ------------------------------------------------------------------ requisition
class PurchaseRequisition(TimeStampedModel, CreatedByModel):
    DRAFT, PENDING, APPROVED, REJECTED, CONVERTED, CANCELLED = (
        "draft", "pending_approval", "approved", "rejected", "converted", "cancelled")
    STATUS_CHOICES = [(DRAFT, "Draft"), (PENDING, "Pending approval"), (APPROVED, "Approved"), (REJECTED, "Rejected"),
                      (CONVERTED, "Converted"), (CANCELLED, "Cancelled")]

    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="requisitions")
    reference = models.CharField(max_length=30, unique=True)
    warehouse = models.ForeignKey("tenants.Warehouse", on_delete=models.PROTECT, related_name="+")
    department = models.CharField(max_length=80, blank=True)
    required_date = models.DateField(null=True, blank=True)
    purpose = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference


class PurchaseRequisitionItem(models.Model):
    requisition = models.ForeignKey(PurchaseRequisition, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    estimated_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(check=models.Q(quantity__gt=0), name="pr_item_qty_positive")]


# ------------------------------------------------------------------ RFQ + quotations
class RFQ(TimeStampedModel, CreatedByModel):
    DRAFT, SENT, AWARDED, CLOSED, CANCELLED = "draft", "sent", "awarded", "closed", "cancelled"
    STATUS_CHOICES = [(DRAFT, "Draft"), (SENT, "Sent to vendors"), (AWARDED, "Awarded"), (CLOSED, "Closed"), (CANCELLED, "Cancelled")]

    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="rfqs")
    reference = models.CharField(max_length=30, unique=True)
    requisition = models.ForeignKey(PurchaseRequisition, on_delete=models.SET_NULL, null=True, blank=True, related_name="rfqs")
    warehouse = models.ForeignKey("tenants.Warehouse", on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    issue_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        verbose_name = "RFQ"
        verbose_name_plural = "RFQs"
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference


class RFQItem(models.Model):
    rfq = models.ForeignKey(RFQ, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)

    class Meta:
        verbose_name = "RFQ item"


class RFQVendor(models.Model):
    rfq = models.ForeignKey(RFQ, on_delete=models.CASCADE, related_name="vendors")
    supplier = models.ForeignKey("suppliers.Supplier", on_delete=models.PROTECT, related_name="+")

    class Meta:
        verbose_name = "RFQ vendor"
        constraints = [models.UniqueConstraint(fields=["rfq", "supplier"], name="uniq_rfq_vendor")]


class VendorQuotation(TimeStampedModel, CreatedByModel):
    RECEIVED, SELECTED, REJECTED = "received", "selected", "rejected"
    STATUS_CHOICES = [(RECEIVED, "Received"), (SELECTED, "Selected"), (REJECTED, "Not selected")]

    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="vendor_quotations")
    reference = models.CharField(max_length=30, unique=True)
    rfq = models.ForeignKey(RFQ, on_delete=models.CASCADE, related_name="quotations")
    supplier = models.ForeignKey("suppliers.Supplier", on_delete=models.PROTECT, related_name="+")
    vendor_reference = models.CharField(max_length=60, blank=True)
    quotation_date = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    delivery_days = models.PositiveSmallIntegerField(default=0)
    payment_term = models.ForeignKey("masters.PaymentTerm", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=RECEIVED)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["rfq", "supplier"], name="one_quotation_per_vendor_per_rfq")]

    def __str__(self):
        return self.reference


class VendorQuotationItem(models.Model):
    quotation = models.ForeignKey(VendorQuotation, on_delete=models.CASCADE, related_name="items")
    rfq_item = models.ForeignKey(RFQItem, on_delete=models.CASCADE, related_name="quotation_items")
    unit_price = models.DecimalField(max_digits=14, decimal_places=4)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["quotation", "rfq_item"], name="uniq_quotation_item")]


# ------------------------------------------------------------------ purchase order extension
class PurchaseOrderExt(TimeStampedModel):
    DRAFT, PENDING, APPROVED, REJECTED, CANCELLED, CLOSED = (
        "draft", "pending_approval", "approved", "rejected", "cancelled", "closed")
    STATUS_CHOICES = [(DRAFT, "Draft"), (PENDING, "Pending approval"), (APPROVED, "Approved"), (REJECTED, "Rejected"),
                      (CANCELLED, "Cancelled"), (CLOSED, "Closed")]

    purchase_order = models.OneToOneField("suppliers.PurchaseOrder", on_delete=models.CASCADE, related_name="erp")
    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="purchase_orders_ext")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT, db_index=True)
    requisition = models.ForeignKey(PurchaseRequisition, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    quotation = models.ForeignKey(VendorQuotation, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    expected_date = models.DateField(null=True, blank=True)
    payment_term = models.ForeignKey("masters.PaymentTerm", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    tax_rate = models.ForeignKey("masters.TaxRate", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    terms = models.TextField(blank=True)

    class Meta:
        verbose_name = "PO details (ERP)"
        verbose_name_plural = "PO details (ERP)"

    def __str__(self):
        return f"{self.purchase_order.reference} ({self.status})"


# ------------------------------------------------------------------ goods receipt
class GoodsReceipt(TimeStampedModel, CreatedByModel):
    QC_PENDING, POSTED, REVERSED = "qc_pending", "posted", "reversed"
    STATUS_CHOICES = [(QC_PENDING, "Waiting for QC"), (POSTED, "Posted"), (REVERSED, "Reversed")]

    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="goods_receipts")
    reference = models.CharField(max_length=30, unique=True)
    purchase_order = models.ForeignKey("suppliers.PurchaseOrder", on_delete=models.PROTECT, related_name="goods_receipts")
    supplier = models.ForeignKey("suppliers.Supplier", on_delete=models.PROTECT, related_name="+")
    warehouse = models.ForeignKey("tenants.Warehouse", on_delete=models.PROTECT, related_name="+")
    received_date = models.DateField()
    challan_no = models.CharField(max_length=60, blank=True, help_text="Vendor delivery challan / gate pass number.")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=POSTED, db_index=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-received_date", "-created_at"]

    def __str__(self):
        return self.reference


class GoodsReceiptLine(models.Model):
    NOT_REQUIRED, PENDING, DONE = "not_required", "pending", "done"
    QC_CHOICES = [(NOT_REQUIRED, "No QC needed"), (PENDING, "QC pending"), (DONE, "QC done")]

    grn = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="lines")
    po_item = models.ForeignKey("suppliers.PurchaseOrderItem", on_delete=models.PROTECT, related_name="grn_lines")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey("stock.Batch", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    quantity_received = models.DecimalField(max_digits=14, decimal_places=3)
    quantity_accepted = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_rejected = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4)
    qc_status = models.CharField(max_length=12, choices=QC_CHOICES, default=NOT_REQUIRED)
    invoiced_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)

    class Meta:
        constraints = [models.CheckConstraint(check=models.Q(quantity_received__gt=0), name="grn_line_qty_positive")]

    @property
    def uninvoiced_quantity(self):
        return self.quantity_accepted - self.invoiced_quantity


# ------------------------------------------------------------------ purchase invoice
class PurchaseInvoice(TimeStampedModel, CreatedByModel):
    DRAFT, PENDING, POSTED, REJECTED, CANCELLED = "draft", "pending_approval", "posted", "rejected", "cancelled"
    STATUS_CHOICES = [(DRAFT, "Draft"), (PENDING, "Pending approval"), (POSTED, "Posted"), (REJECTED, "Rejected"), (CANCELLED, "Cancelled")]

    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="purchase_invoices")
    reference = models.CharField(max_length=30, unique=True)
    supplier = models.ForeignKey("suppliers.Supplier", on_delete=models.PROTECT, related_name="erp_invoices")
    vendor_invoice_no = models.CharField(max_length=60)
    invoice_date = models.DateField()
    due_date = models.DateField()
    purchase_order = models.ForeignKey("suppliers.PurchaseOrder", on_delete=models.SET_NULL, null=True, blank=True, related_name="erp_invoices")
    tax_rate = models.ForeignKey("masters.TaxRate", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT, db_index=True)
    subtotal = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-invoice_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "supplier", "vendor_invoice_no"],
                                    condition=~models.Q(status="cancelled"), name="uniq_vendor_invoice_no"),
            models.CheckConstraint(check=models.Q(paid_amount__gte=0), name="purchase_invoice_paid_non_negative"),
        ]

    def __str__(self):
        return self.reference

    @property
    def outstanding(self):
        return self.total - self.paid_amount


class PurchaseInvoiceLine(models.Model):
    invoice = models.ForeignKey(PurchaseInvoice, on_delete=models.CASCADE, related_name="lines")
    grn_line = models.ForeignKey(GoodsReceiptLine, on_delete=models.PROTECT, null=True, blank=True, related_name="invoice_lines")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    description = models.CharField(max_length=255, blank=True)
    account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, null=True, blank=True, related_name="+",
                                help_text="Expense account, for lines that are not stock items (freight, services...).")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_price = models.DecimalField(max_digits=14, decimal_places=4)
    line_total = models.DecimalField(max_digits=16, decimal_places=2)


# ------------------------------------------------------------------ vendor payment
class VendorPayment(TimeStampedModel, CreatedByModel):
    DRAFT, PENDING, POSTED, REJECTED, CANCELLED = "draft", "pending_approval", "posted", "rejected", "cancelled"
    STATUS_CHOICES = [(DRAFT, "Draft"), (PENDING, "Pending approval"), (POSTED, "Posted"), (REJECTED, "Rejected"), (CANCELLED, "Cancelled")]
    METHOD_CHOICES = [("cash", "Cash"), ("bank", "Bank transfer"), ("cheque", "Cheque"), ("online", "Online")]

    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="vendor_payments")
    reference = models.CharField(max_length=30, unique=True)
    supplier = models.ForeignKey("suppliers.Supplier", on_delete=models.PROTECT, related_name="erp_payments")
    payment_date = models.DateField()
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    method = models.CharField(max_length=10, choices=METHOD_CHOICES, default="bank")
    bank_account = models.ForeignKey("accounting.BankAccount", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    cheque_no = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT, db_index=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-payment_date", "-created_at"]
        constraints = [models.CheckConstraint(check=models.Q(amount__gt=0), name="vendor_payment_amount_positive")]

    def __str__(self):
        return self.reference


class VendorPaymentAllocation(models.Model):
    payment = models.ForeignKey(VendorPayment, on_delete=models.CASCADE, related_name="allocations")
    invoice = models.ForeignKey(PurchaseInvoice, on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(max_digits=16, decimal_places=2)

    class Meta:
        constraints = [models.CheckConstraint(check=models.Q(amount__gt=0), name="vendor_allocation_positive")]


# ------------------------------------------------------------------ return to vendor
class VendorReturn(TimeStampedModel, CreatedByModel):
    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="vendor_returns")
    reference = models.CharField(max_length=30, unique=True)
    supplier = models.ForeignKey("suppliers.Supplier", on_delete=models.PROTECT, related_name="+")
    warehouse = models.ForeignKey("tenants.Warehouse", on_delete=models.PROTECT, related_name="+")
    grn = models.ForeignKey(GoodsReceipt, on_delete=models.SET_NULL, null=True, blank=True, related_name="returns")
    return_date = models.DateField()
    tax_rate = models.ForeignKey("masters.TaxRate", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    reason = models.CharField(max_length=255, blank=True)
    total = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference


class VendorReturnLine(models.Model):
    vendor_return = models.ForeignKey(VendorReturn, on_delete=models.CASCADE, related_name="lines")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, help_text="Credit price agreed with the vendor.")
