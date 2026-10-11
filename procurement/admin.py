from django.contrib import admin, messages

from numbering.services import next_reference
from . import services
from .models import (GoodsReceipt, GoodsReceiptLine, PurchaseInvoice, PurchaseInvoiceLine, PurchaseOrderExt, PurchaseRequisition,
                     PurchaseRequisitionItem, RFQ, RFQItem, RFQVendor, VendorPayment, VendorPaymentAllocation, VendorQuotation,
                     VendorQuotationItem, VendorReturn, VendorReturnLine)


def _each(modeladmin, request, queryset, func, label):
    for obj in queryset:
        try:
            func(obj, request.user)
            modeladmin.message_user(request, f"{obj}: {label}.", messages.SUCCESS)
        except Exception as exc:
            modeladmin.message_user(request, f"{obj}: {exc}", messages.ERROR)


class _NumberedAdmin(admin.ModelAdmin):
    numbering_type = ""
    readonly_fields = ("reference", "created_by")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.reference = obj.reference or next_reference(obj.company, self.numbering_type)
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


# --- requisition
class PRItemInline(admin.TabularInline):
    model = PurchaseRequisitionItem
    extra = 1
    raw_id_fields = ("variant",)


@admin.register(PurchaseRequisition)
class PurchaseRequisitionAdmin(_NumberedAdmin):
    numbering_type = "purchase_requisition"
    list_display = ("reference", "warehouse", "department", "required_date", "status")
    list_filter = ("status", "company")
    inlines = [PRItemInline]
    readonly_fields = ("reference", "created_by", "status")
    actions = ["submit"]

    @admin.action(description="Submit for approval")
    def submit(self, request, queryset):
        _each(self, request, queryset, services.submit_requisition, "submitted")


# --- RFQ & quotations
class RFQItemInline(admin.TabularInline):
    model = RFQItem
    extra = 1
    raw_id_fields = ("variant",)


class RFQVendorInline(admin.TabularInline):
    model = RFQVendor
    extra = 1


@admin.register(RFQ)
class RFQAdmin(_NumberedAdmin):
    numbering_type = "rfq"
    list_display = ("reference", "warehouse", "status", "issue_date", "due_date")
    list_filter = ("status", "company")
    inlines = [RFQItemInline, RFQVendorInline]
    readonly_fields = ("reference", "created_by", "status")
    actions = ["send"]

    @admin.action(description="Mark as sent to vendors")
    def send(self, request, queryset):
        _each(self, request, queryset, services.send_rfq, "sent")


class QuotationItemInline(admin.TabularInline):
    model = VendorQuotationItem
    extra = 0


@admin.register(VendorQuotation)
class VendorQuotationAdmin(_NumberedAdmin):
    numbering_type = "vendor_quotation"
    list_display = ("reference", "rfq", "supplier", "quotation_date", "delivery_days", "status")
    list_filter = ("status", "company")
    inlines = [QuotationItemInline]
    readonly_fields = ("reference", "created_by", "status")
    actions = ["award"]

    @admin.action(description="Select this quotation and create the purchase order")
    def award(self, request, queryset):
        _each(self, request, queryset, lambda q, u: services.select_quotation(q, u), "awarded (draft PO created)")


# --- PO
@admin.register(PurchaseOrderExt)
class PurchaseOrderExtAdmin(admin.ModelAdmin):
    """Edit order lines in Suppliers > Purchase orders (legacy screen); approval, receiving and cancelling happen here."""
    list_display = ("purchase_order", "company", "status", "expected_date")
    list_filter = ("status", "company")
    search_fields = ("purchase_order__reference", "purchase_order__supplier__name")
    readonly_fields = ("status",)
    actions = ["submit", "receive_all_pending", "cancel"]

    @admin.action(description="Submit for approval")
    def submit(self, request, queryset):
        _each(self, request, queryset, lambda e, u: services.submit_purchase_order(e.purchase_order, u), "submitted")

    @admin.action(description="Receive ALL pending quantities (creates a goods receipt)")
    def receive_all_pending(self, request, queryset):
        def run(ext, user):
            lines = [{"po_item": r["item"], "quantity": r["pending"]} for r in services.po_receipt_status(ext.purchase_order) if r["pending"] > 0]
            return services.receive_goods(ext.purchase_order, lines, user=user)
        _each(self, request, queryset, run, "goods received")

    @admin.action(description="Cancel order")
    def cancel(self, request, queryset):
        _each(self, request, queryset, lambda e, u: services.cancel_purchase_order(e.purchase_order, u), "cancelled")


# --- GRN
class GRNLineInline(admin.TabularInline):
    model = GoodsReceiptLine
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]


@admin.register(GoodsReceipt)
class GoodsReceiptAdmin(admin.ModelAdmin):
    list_display = ("reference", "purchase_order", "supplier", "warehouse", "received_date", "status")
    list_filter = ("status", "company")
    search_fields = ("reference", "challan_no", "purchase_order__reference")
    inlines = [GRNLineInline]
    actions = ["reverse"]

    def has_add_permission(self, request):
        return False        # created from the PO (Receive action) so stock/GL are always posted together

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Reverse this receipt (not yet invoiced)")
    def reverse(self, request, queryset):
        _each(self, request, queryset, lambda g, u: services.reverse_goods_receipt(g, u), "reversed")


# --- invoice
class InvoiceLineInline(admin.TabularInline):
    model = PurchaseInvoiceLine
    extra = 1
    raw_id_fields = ("grn_line", "variant", "account")
    readonly_fields = ("line_total",)


@admin.register(PurchaseInvoice)
class PurchaseInvoiceAdmin(_NumberedAdmin):
    numbering_type = "purchase_invoice"
    list_display = ("reference", "supplier", "vendor_invoice_no", "invoice_date", "due_date", "total", "paid_amount", "status")
    list_filter = ("status", "company")
    search_fields = ("reference", "vendor_invoice_no", "supplier__name")
    inlines = [InvoiceLineInline]
    readonly_fields = ("reference", "created_by", "status", "subtotal", "tax_amount", "total", "paid_amount")
    actions = ["submit", "cancel"]

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        try:
            services.recompute_invoice(form.instance)
        except Exception as exc:
            self.message_user(request, f"Check the invoice: {exc}", messages.ERROR)

    @admin.action(description="Submit for approval / posting")
    def submit(self, request, queryset):
        _each(self, request, queryset, services.submit_purchase_invoice, "submitted")

    @admin.action(description="Cancel invoice")
    def cancel(self, request, queryset):
        _each(self, request, queryset, services.cancel_purchase_invoice, "cancelled")


# --- payment
class AllocationInline(admin.TabularInline):
    model = VendorPaymentAllocation
    extra = 1
    raw_id_fields = ("invoice",)


@admin.register(VendorPayment)
class VendorPaymentAdmin(_NumberedAdmin):
    numbering_type = "vendor_payment"
    list_display = ("reference", "supplier", "payment_date", "amount", "method", "status")
    list_filter = ("status", "method", "company")
    inlines = [AllocationInline]
    readonly_fields = ("reference", "created_by", "status")
    actions = ["submit", "reverse"]

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        try:
            services.recompute_payment(form.instance)
        except Exception as exc:
            self.message_user(request, f"Check the payment: {exc}", messages.ERROR)

    @admin.action(description="Submit for approval / posting")
    def submit(self, request, queryset):
        _each(self, request, queryset, services.submit_vendor_payment, "submitted")

    @admin.action(description="Reverse payment")
    def reverse(self, request, queryset):
        _each(self, request, queryset, services.reverse_vendor_payment, "reversed")


class ReturnLineInline(admin.TabularInline):
    model = VendorReturnLine
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(VendorReturn)
class VendorReturnAdmin(admin.ModelAdmin):
    """Created through services.return_to_vendor (stock + accounting together); view only here."""
    list_display = ("reference", "supplier", "warehouse", "return_date", "total")
    inlines = [ReturnLineInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
