from django.contrib import admin

from .models import Delivery, PosCart, Sale, SaleFeedback, SaleItem, SaleReturn, SaleReturnItem


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ("reference", "store", "biller", "customer", "status", "total_amount", "payment_status", "created_at")
    list_filter = ("store", "status", "biller")
    search_fields = ("reference", "customer__name", "customer__phone")
    inlines = [SaleItemInline]


class SaleReturnItemInline(admin.TabularInline):
    model = SaleReturnItem
    extra = 0


@admin.register(SaleReturn)
class SaleReturnAdmin(admin.ModelAdmin):
    list_display = ("reference", "sale", "processed_by", "created_at")
    inlines = [SaleReturnItemInline]


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("reference", "sale", "recipient_name", "status", "rider", "scheduled_date")
    list_filter = ("status",)


@admin.register(PosCart)
class PosCartAdmin(admin.ModelAdmin):
    list_display = ("register_session", "version", "updated_at")
    raw_id_fields = ("register_session",)


@admin.register(SaleFeedback)
class SaleFeedbackAdmin(admin.ModelAdmin):
    list_display = ("sale", "rating", "created_at")
