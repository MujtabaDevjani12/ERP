from django.contrib import admin

from .models import Rack, StockAdjustment, StockAdjustmentItem, StockItem, StockTransfer, StockTransferItem


@admin.register(StockItem)
class StockItemAdmin(admin.ModelAdmin):
    list_display = ("variant", "warehouse", "rack", "quantity")
    list_filter = ("warehouse",)
    search_fields = ("variant__sku", "variant__barcode", "variant__product__name")


@admin.register(Rack)
class RackAdmin(admin.ModelAdmin):
    list_display = ("name", "warehouse")


class StockAdjustmentItemInline(admin.TabularInline):
    model = StockAdjustmentItem
    extra = 0


@admin.register(StockAdjustment)
class StockAdjustmentAdmin(admin.ModelAdmin):
    list_display = ("reference", "warehouse", "reason", "created_by", "created_at")
    inlines = [StockAdjustmentItemInline]


class StockTransferItemInline(admin.TabularInline):
    model = StockTransferItem
    extra = 0


@admin.register(StockTransfer)
class StockTransferAdmin(admin.ModelAdmin):
    list_display = ("reference", "from_warehouse", "to_warehouse", "status")
    inlines = [StockTransferItemInline]
