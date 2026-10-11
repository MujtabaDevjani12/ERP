from django.contrib import admin

from .models import PurchaseOrder, PurchaseOrderItem, PurchasePayment, PurchaseReturn, PurchaseReturnItem, Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "contact_person", "phone", "store", "is_active")
    search_fields = ("name", "phone", "email")


class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 1


class PurchasePaymentInline(admin.TabularInline):
    model = PurchasePayment
    extra = 0
    fields = ("reference", "amount", "method", "paid_on")


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ("reference", "supplier", "warehouse", "status", "order_date", "total_amount", "due_amount")
    list_filter = ("status", "warehouse")
    inlines = [PurchaseOrderItemInline, PurchasePaymentInline]


class PurchaseReturnItemInline(admin.TabularInline):
    model = PurchaseReturnItem
    extra = 1


@admin.register(PurchaseReturn)
class PurchaseReturnAdmin(admin.ModelAdmin):
    list_display = ("reference", "purchase_order", "created_at")
    inlines = [PurchaseReturnItemInline]
