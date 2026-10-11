from django.contrib import admin

from .models import Customer, GiftCard, LoyaltyTransaction


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "cnic", "group", "loyalty_points", "store", "is_active")
    search_fields = ("name", "phone", "cnic", "email")
    list_filter = ("store", "group", "is_active")


@admin.register(LoyaltyTransaction)
class LoyaltyTransactionAdmin(admin.ModelAdmin):
    list_display = ("customer", "type", "points", "order", "created_at")
    list_filter = ("type",)


@admin.register(GiftCard)
class GiftCardAdmin(admin.ModelAdmin):
    list_display = ("code", "customer", "initial_balance", "current_balance", "expires_on", "is_active")
    search_fields = ("code",)
