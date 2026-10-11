from django.contrib import admin, messages

from numbering.services import next_reference
from . import services
from .models import (CustomerReceipt, CustomerReceiptAllocation, DeliveryChallan, DeliveryChallanLine, ERPSalesReturn,
                     ERPSalesReturnLine, SalesInvoice, SalesInvoiceLine, SalesOrder, SalesOrderItem, SalesQuotation,
                     SalesQuotationItem)


def _each(modeladmin, request, queryset, func, label):
    for obj in queryset:
        try:
            func(obj, request.user)
            modeladmin.message_user(request, f"{obj}: {label}.", messages.SUCCESS)
        except Exception as exc:
            modeladmin.message_user(request, f"{obj}: {exc}", messages.ERROR)


class QuotationItemInline(admin.TabularInline):
    model = SalesQuotationItem
    extra = 1
    raw_id_fields = ("variant",)


@admin.register(SalesQuotation)
class SalesQuotationAdmin(admin.ModelAdmin):
    list_display = ("reference", "customer", "quotation_date", "valid_until", "status")
    list_filter = ("status", "company")
    search_fields = ("reference", "customer__name")
    raw_id_fields = ("customer",)
    inlines = [QuotationItemInline]
    readonly_fields = ("reference", "status", "created_by")
    actions = ["send", "accept", "reject", "convert"]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.reference = next_reference(obj.company, "quotation")
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Mark as sent")
    def send(self, request, queryset):
        _each(self, request, queryset, services.send_quotation, "sent")

    @admin.action(description="Customer accepted")
    def accept(self, request, queryset):
        _each(self, request, queryset, services.accept_quotation, "accepted")

    @admin.action(description="Customer rejected")
    def reject(self, request, queryset):
        _each(self, request, queryset, services.reject_quotation, "rejected")

    @admin.action(description="Convert to sales order")
    def convert(self, request, queryset):
        _each(self, request, queryset, lambda q, u: services.convert_quotation(q, u), "converted to order")


class OrderItemInline(admin.TabularInline):
    model = SalesOrderItem
    extra = 1
    raw_id_fields = ("variant",)
    readonly_fields = ("delivered_quantity", "invoiced_quantity")


@admin.register(SalesOrder)
class SalesOrderAdmin(admin.ModelAdmin):
    list_display = ("reference", "customer", "order_date", "sales_type", "total", "status")
    list_filter = ("status", "sales_type", "company")
    search_fields = ("reference", "customer__name")
    raw_id_fields = ("customer",)
    inlines = [OrderItemInline]
    readonly_fields = ("reference", "status", "subtotal", "discount_amount", "tax_amount", "total", "credit_override", "created_by")
    actions = ["submit", "submit_with_credit_override", "deliver_and_invoice", "cancel"]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.reference = next_reference(obj.company, "sales_order")
            obj.created_by = request.user
        elif obj.status not in ("draft", "rejected"):
            self.message_user(request, "Only a draft order can be edited.", messages.ERROR)
            return
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        if form.instance.status not in ("draft", "rejected"):
            self.message_user(request, "Items of a submitted order cannot be changed.", messages.ERROR)
            return
        super().save_formset(request, form, formset, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        try:
            services.recompute_order(form.instance)
        except Exception as exc:
            self.message_user(request, f"Check the order: {exc}", messages.ERROR)

    @admin.action(description="Submit / confirm")
    def submit(self, request, queryset):
        _each(self, request, queryset, lambda o, u: services.submit_sales_order(o, u), "submitted")

    @admin.action(description="Submit with CREDIT-LIMIT override (managers only)")
    def submit_with_credit_override(self, request, queryset):
        _each(self, request, queryset, lambda o, u: services.submit_sales_order(o, u, credit_override=True), "submitted with override")

    @admin.action(description="Deliver everything pending and invoice it")
    def deliver_and_invoice(self, request, queryset):
        _each(self, request, queryset, lambda o, u: services.deliver_and_invoice(o, user=u), "delivered and invoiced")

    @admin.action(description="Cancel order")
    def cancel(self, request, queryset):
        _each(self, request, queryset, services.cancel_sales_order, "cancelled")


class ChallanLineInline(admin.TabularInline):
    model = DeliveryChallanLine
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]


@admin.register(DeliveryChallan)
class DeliveryChallanAdmin(admin.ModelAdmin):
    list_display = ("reference", "order", "customer", "delivery_date", "vehicle_no", "status")
    list_filter = ("status", "company")
    search_fields = ("reference", "order__reference")
    inlines = [ChallanLineInline]
    actions = ["reverse"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Reverse delivery (not invoiced)")
    def reverse(self, request, queryset):
        _each(self, request, queryset, lambda d, u: services.reverse_delivery(d, u), "reversed")


class InvoiceLineInline(admin.TabularInline):
    model = SalesInvoiceLine
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]


@admin.register(SalesInvoice)
class SalesInvoiceAdmin(admin.ModelAdmin):
    list_display = ("reference", "customer", "invoice_date", "due_date", "total", "paid_amount", "credited_amount", "status")
    list_filter = ("status", "sales_type", "company")
    search_fields = ("reference", "customer__name")
    inlines = [InvoiceLineInline]
    actions = ["cancel", "return_everything"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Cancel invoice (no receipts/returns)")
    def cancel(self, request, queryset):
        _each(self, request, queryset, lambda i, u: services.cancel_invoice(i, u), "cancelled")

    @admin.action(description="Return everything not yet returned (restock to the order's warehouse) -> credit note")
    def return_everything(self, request, queryset):
        def run(inv, user):
            items = [{"invoice_line": l, "quantity": l.quantity - l.returned_quantity, "restock": True}
                     for l in inv.lines.all() if l.quantity > l.returned_quantity]
            return services.create_sales_return(inv, items, "Full return (admin action)", user=user)
        _each(self, request, queryset, run, "credit note created")


@admin.register(CustomerReceipt)
class CustomerReceiptAdmin(admin.ModelAdmin):
    """Enter customer, amount, method: the money is allocated to the oldest open invoices automatically."""
    list_display = ("reference", "customer", "receipt_date", "amount", "method", "status")
    list_filter = ("status", "method", "company")
    search_fields = ("reference", "customer__name")
    raw_id_fields = ("customer",)
    readonly_fields = ("reference", "status", "created_by")
    actions = ["reverse"]

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if change:
            self.message_user(request, "Posted receipts cannot be edited; reverse and re-enter.", messages.ERROR)
            return
        try:
            rec = services.create_customer_receipt(
                obj.company, obj.customer, obj.amount, user=request.user, method=obj.method, bank_account=obj.bank_account,
                receipt_date=obj.receipt_date, allocations=services.auto_allocate(obj.company, obj.customer, obj.amount),
                cheque_no=obj.cheque_no, note=obj.note)
            obj.pk, obj.reference = rec.pk, rec.reference
        except Exception as exc:
            self.message_user(request, f"Not saved: {exc}", messages.ERROR)

    @admin.action(description="Reverse receipt")
    def reverse(self, request, queryset):
        _each(self, request, queryset, lambda r, u: services.reverse_customer_receipt(r, u), "reversed")


@admin.register(ERPSalesReturn)
class ERPSalesReturnAdmin(admin.ModelAdmin):
    list_display = ("reference", "invoice", "return_date", "net_amount", "credit_amount", "refunded_amount")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
