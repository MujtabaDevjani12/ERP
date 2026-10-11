from django.contrib import admin

from .models import Payment, PaymentMethod, Refund


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "store", "requires_reference", "is_active", "sort_order")
    list_filter = ("store", "is_active")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("reference", "sale", "amount", "method", "status", "register_session", "received_by", "paid_at")
    list_filter = ("method", "status")
    raw_id_fields = ("sale", "register_session")


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ("reference", "order", "amount", "method", "processed_by", "created_at")
