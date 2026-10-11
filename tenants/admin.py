from django.contrib import admin

from .models import Store, Warehouse


class WarehouseInline(admin.TabularInline):
    model = Warehouse
    extra = 1


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ("name", "business_type", "phone", "currency_code", "is_active")
    list_filter = ("business_type", "is_active")
    search_fields = ("name", "phone", "email")
    inlines = [WarehouseInline]


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ("name", "store", "is_default")
    list_filter = ("store",)
