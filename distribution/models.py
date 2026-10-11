"""
Sales & distribution: quotation -> sales order -> delivery challan (stock out + cost of sales) -> invoice (revenue + AR)
-> customer receipt, plus sales returns / credit notes. Customers are the existing customers.Customer (POS) records.
"""
from django.db import models

from core.models import CreatedByModel, TimeStampedModel

SALES_TYPES = [("wholesale", "Wholesale"), ("retail", "Retail"), ("corporate", "Corporate"), ("export", "Export")]


class SalesQuotation(TimeStampedModel, CreatedByModel):
    DRAFT, SENT, ACCEPTED, REJECTED, EXPIRED, CONVERTED = "draft", "sent", "accepted", "rejected", "expired", "converted"
    STATUS_CHOICES = [(DRAFT, "Draft"), (SENT, "Sent"), (ACCEPTED, "Accepted"), (REJECTED, "Rejected"), (EXPIRED, "Expired"), (CONVERTED, "Converted to order")]

    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="sales_quotations")
    reference = models.CharField(max_length=30, unique=True)
    customer = models.ForeignKey("customers.Customer", on_delete=models.PROTECT, related_name="+")
    warehouse = models.ForeignKey("tenants.Warehouse", on_delete=models.PROTECT, related_name="+")
    quotation_date = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    sales_type = models.CharField(max_length=10, choices=SALES_TYPES, default="wholesale")
    tax_rate = models.ForeignKey("masters.TaxRate", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    terms = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference


class SalesQuotationItem(models.Model):
    quotation = models.ForeignKey(SalesQuotation, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)


class SalesOrder(TimeStampedModel, CreatedByModel):
    DRAFT, PENDING, CONFIRMED, PARTIAL, DELIVERED, REJECTED, CANCELLED, CLOSED = (
        "draft", "pending_approval", "confirmed", "partially_delivered", "delivered", "rejected", "cancelled", "closed")
    STATUS_CHOICES = [(DRAFT, "Draft"), (PENDING, "Pending approval"), (CONFIRMED, "Confirmed"), (PARTIAL, "Partially delivered"),
                      (DELIVERED, "Delivered"), (REJECTED, "Rejected"), (CANCELLED, "Cancelled"), (CLOSED, "Closed")]

    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="sales_orders")
    reference = models.CharField(max_length=30, unique=True)
    customer = models.ForeignKey("customers.Customer", on_delete=models.PROTECT, related_name="erp_orders")
    warehouse = models.ForeignKey("tenants.Warehouse", on_delete=models.PROTECT, related_name="+")
    quotation = models.ForeignKey(SalesQuotation, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    order_date = models.DateField()
    delivery_date = models.DateField(null=True, blank=True)
    sales_type = models.CharField(max_length=10, choices=SALES_TYPES, default="wholesale")
    tax_rate = models.ForeignKey("masters.TaxRate", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT, db_index=True)
    subtotal = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    credit_override = models.BooleanField(default=False, help_text="A manager allowed this order above the customer's credit limit.")
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-order_date", "-created_at"]

    def __str__(self):
        return self.reference


class SalesOrderItem(models.Model):
    order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    delivered_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    invoiced_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)

    class Meta:
        constraints = [models.CheckConstraint(check=models.Q(quantity__gt=0), name="so_item_qty_positive")]

    @property
    def net_unit_price(self):
        return self.unit_price * (100 - self.discount_percent) / 100


class DeliveryChallan(TimeStampedModel, CreatedByModel):
    DISPATCHED, CANCELLED = "dispatched", "cancelled"
    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="delivery_challans")
    reference = models.CharField(max_length=30, unique=True)
    order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name="challans")
    customer = models.ForeignKey("customers.Customer", on_delete=models.PROTECT, related_name="+")
    warehouse = models.ForeignKey("tenants.Warehouse", on_delete=models.PROTECT, related_name="+")
    delivery_date = models.DateField()
    vehicle_no = models.CharField(max_length=30, blank=True)
    driver = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=10, choices=[(DISPATCHED, "Dispatched"), (CANCELLED, "Cancelled")], default=DISPATCHED)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-delivery_date", "-created_at"]

    def __str__(self):
        return self.reference


class DeliveryChallanLine(models.Model):
    challan = models.ForeignKey(DeliveryChallan, on_delete=models.CASCADE, related_name="lines")
    order_item = models.ForeignKey(SalesOrderItem, on_delete=models.PROTECT, related_name="challan_lines")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    batch = models.ForeignKey("stock.Batch", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0, help_text="Average cost at dispatch (cost of sales).")
    invoiced_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)

    class Meta:
        constraints = [models.CheckConstraint(check=models.Q(quantity__gt=0), name="dc_line_qty_positive")]


class SalesInvoice(TimeStampedModel, CreatedByModel):
    POSTED, CANCELLED = "posted", "cancelled"
    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="sales_invoices")
    reference = models.CharField(max_length=30, unique=True)
    customer = models.ForeignKey("customers.Customer", on_delete=models.PROTECT, related_name="erp_invoices")
    order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, null=True, blank=True, related_name="invoices")
    sales_type = models.CharField(max_length=10, choices=SALES_TYPES, default="wholesale")
    invoice_date = models.DateField()
    due_date = models.DateField()
    tax_rate = models.ForeignKey("masters.TaxRate", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=10, choices=[(POSTED, "Posted"), (CANCELLED, "Cancelled")], default=POSTED, db_index=True)
    subtotal = models.DecimalField(max_digits=16, decimal_places=2, default=0, help_text="After line discounts, before tax.")
    discount_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0, help_text="Informational: discount already inside the subtotal.")
    tax_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    credited_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-invoice_date", "-created_at"]
        constraints = [models.CheckConstraint(check=models.Q(paid_amount__gte=0, credited_amount__gte=0), name="sales_invoice_amounts_non_negative")]

    def __str__(self):
        return self.reference

    @property
    def outstanding(self):
        return self.total - self.paid_amount - self.credited_amount


class SalesInvoiceLine(models.Model):
    invoice = models.ForeignKey(SalesInvoice, on_delete=models.CASCADE, related_name="lines")
    challan_line = models.ForeignKey(DeliveryChallanLine, on_delete=models.PROTECT, null=True, blank=True, related_name="invoice_lines")
    order_item = models.ForeignKey(SalesOrderItem, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, help_text="After the line discount.")
    list_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    line_total = models.DecimalField(max_digits=16, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    returned_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    returned_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)


class CustomerReceipt(TimeStampedModel, CreatedByModel):
    POSTED, CANCELLED = "posted", "cancelled"
    METHOD_CHOICES = [("cash", "Cash"), ("bank", "Bank transfer"), ("cheque", "Cheque"), ("card", "Card"), ("online", "Online")]
    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="customer_receipts")
    reference = models.CharField(max_length=30, unique=True)
    customer = models.ForeignKey("customers.Customer", on_delete=models.PROTECT, related_name="erp_receipts")
    receipt_date = models.DateField()
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    method = models.CharField(max_length=10, choices=METHOD_CHOICES, default="cash")
    bank_account = models.ForeignKey("accounting.BankAccount", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    cheque_no = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=10, choices=[(POSTED, "Posted"), (CANCELLED, "Cancelled")], default=POSTED)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-receipt_date", "-created_at"]
        constraints = [models.CheckConstraint(check=models.Q(amount__gt=0), name="customer_receipt_amount_positive")]

    def __str__(self):
        return self.reference


class CustomerReceiptAllocation(models.Model):
    receipt = models.ForeignKey(CustomerReceipt, on_delete=models.CASCADE, related_name="allocations")
    invoice = models.ForeignKey(SalesInvoice, on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(max_digits=16, decimal_places=2)


class ERPSalesReturn(TimeStampedModel, CreatedByModel):
    """Return against an ERP invoice -> credit note. (The POS has its own sales.SaleReturn.)"""
    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="erp_sales_returns")
    reference = models.CharField(max_length=30, unique=True)
    invoice = models.ForeignKey(SalesInvoice, on_delete=models.PROTECT, related_name="returns")
    return_date = models.DateField()
    reason = models.CharField(max_length=255)
    restock_warehouse = models.ForeignKey("tenants.Warehouse", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    net_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    credit_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    refunded_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference


class ERPSalesReturnLine(models.Model):
    sales_return = models.ForeignKey(ERPSalesReturn, on_delete=models.CASCADE, related_name="lines")
    invoice_line = models.ForeignKey(SalesInvoiceLine, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    restocked = models.BooleanField(default=True, help_text="Good condition: goes back into stock. Untick for damaged goods.")
