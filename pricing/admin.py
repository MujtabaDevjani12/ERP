from django.contrib import admin

from .models import CustomerGroup, Discount, Tax


@admin.register(Tax)
class TaxAdmin(admin.ModelAdmin):
    list_display = ("name", "rate", "applies_to", "is_default")


@admin.register(Discount)
class DiscountAdmin(admin.ModelAdmin):
    list_display = ("code", "type", "value", "valid_from", "valid_to", "is_active")
    list_filter = ("type", "is_active")
    search_fields = ("code",)


@admin.register(CustomerGroup)
class CustomerGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "price_adjustment_percentage", "is_default")
