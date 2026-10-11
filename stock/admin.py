from django.contrib import admin, messages

from . import services
from .models import (AdjustmentOrder, AdjustmentOrderItem, Batch, BatchStock, ItemWarehouseCost, StockCount, StockCountLine,
                     StockLedgerEntry, StockPolicy, TransferOrder, TransferOrderItem)


@admin.register(StockPolicy)
class StockPolicyAdmin(admin.ModelAdmin):
    list_display = ("company", "allow_negative")


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ("batch_no", "variant", "manufacture_date", "expiry_date")
    search_fields = ("batch_no", "variant__sku")
    raw_id_fields = ("variant",)


@admin.register(BatchStock)
class BatchStockAdmin(admin.ModelAdmin):
    list_display = ("batch", "warehouse", "quantity")
    list_filter = ("warehouse",)


@admin.register(ItemWarehouseCost)
class ItemWarehouseCostAdmin(admin.ModelAdmin):
    list_display = ("variant", "warehouse", "quantity", "avg_cost")
    list_filter = ("warehouse",)
    search_fields = ("variant__sku", "variant__product__name")
    readonly_fields = ("variant", "warehouse", "quantity", "avg_cost")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StockLedgerEntry)
class StockLedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("posted_at", "variant", "warehouse", "quantity", "balance_after", "unit_cost", "reference_type", "reference_no", "user")
    list_filter = ("reference_type", "warehouse")
    search_fields = ("variant__sku", "variant__product__name", "reference_no")
    date_hierarchy = "posted_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class TransferItemInline(admin.TabularInline):
    model = TransferOrderItem
    extra = 1
    raw_id_fields = ("variant", "batch")
    readonly_fields = ("unit_cost", "received_quantity")


@admin.register(TransferOrder)
class TransferOrderAdmin(admin.ModelAdmin):
    list_display = ("reference", "from_warehouse", "to_warehouse", "status", "created_at")
    list_filter = ("status", "company")
    search_fields = ("reference",)
    inlines = [TransferItemInline]
    readonly_fields = ("reference", "status", "dispatched_at", "received_at", "created_by")
    actions = ["submit_for_approval", "dispatch", "receive"]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.reference = obj.reference or services.next_reference(obj.company, "stock_transfer")
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def _run(self, request, queryset, func, label):
        for order in queryset:
            try:
                func(order, request.user)
                self.message_user(request, f"{order.reference}: {label}.", messages.SUCCESS)
            except Exception as exc:
                self.message_user(request, f"{order.reference}: {exc}", messages.ERROR)

    @admin.action(description="Submit for approval")
    def submit_for_approval(self, request, queryset):
        self._run(request, queryset, services.submit_transfer, "submitted")

    @admin.action(description="Dispatch (approved transfers)")
    def dispatch(self, request, queryset):
        self._run(request, queryset, services.dispatch_transfer, "dispatched")

    @admin.action(description="Receive in full (in-transit transfers)")
    def receive(self, request, queryset):
        self._run(request, queryset, services.receive_transfer, "received")


class AdjustmentItemInline(admin.TabularInline):
    model = AdjustmentOrderItem
    extra = 1
    raw_id_fields = ("variant", "batch")


@admin.register(AdjustmentOrder)
class AdjustmentOrderAdmin(admin.ModelAdmin):
    list_display = ("reference", "warehouse", "reason", "status", "created_at")
    list_filter = ("status", "reason", "company")
    inlines = [AdjustmentItemInline]
    readonly_fields = ("reference", "status", "posted_at", "created_by", "source_count")
    actions = ["submit_for_approval"]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.reference = obj.reference or services.next_reference(obj.company, "stock_adjustment")
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Submit for approval")
    def submit_for_approval(self, request, queryset):
        for order in queryset:
            try:
                services.submit_adjustment(order, request.user)
                self.message_user(request, f"{order.reference}: submitted.", messages.SUCCESS)
            except Exception as exc:
                self.message_user(request, f"{order.reference}: {exc}", messages.ERROR)


class CountLineInline(admin.TabularInline):
    model = StockCountLine
    extra = 0
    raw_id_fields = ("variant",)
    readonly_fields = ("system_quantity",)


@admin.register(StockCount)
class StockCountAdmin(admin.ModelAdmin):
    list_display = ("reference", "warehouse", "status", "created_at")
    list_filter = ("status", "company")
    inlines = [CountLineInline]
    readonly_fields = ("reference", "status", "created_by")
    actions = ["submit_count"]

    def save_model(self, request, obj, form, change):
        new = not change
        if new:
            obj.reference = obj.reference or services.next_reference(obj.company, "stock_count")
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        if new:   # snapshot the system quantities when the sheet is first created
            from inventory.models import StockItem
            for si in StockItem.objects.filter(warehouse=obj.warehouse):
                StockCountLine.objects.get_or_create(count=obj, variant=si.variant, defaults={"system_quantity": si.quantity})

    @admin.action(description="Submit count (creates an adjustment for approval)")
    def submit_count(self, request, queryset):
        for count in queryset:
            try:
                services.submit_count(count, request.user)
                self.message_user(request, f"{count.reference}: submitted.", messages.SUCCESS)
            except Exception as exc:
                self.message_user(request, f"{count.reference}: {exc}", messages.ERROR)
