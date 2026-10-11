from django.contrib import admin, messages

from numbering.services import next_reference
from . import services
from .models import (BOM, BOMItem, BOMOperation, JobCard, Machine, MaterialIssue, MaterialIssueLine, Operation, ProductionReceipt,
                     WorkOrder, WorkOrderMaterial)


def _each(modeladmin, request, queryset, func, label):
    for obj in queryset:
        try:
            func(obj, request.user)
            modeladmin.message_user(request, f"{obj}: {label}.", messages.SUCCESS)
        except Exception as exc:
            modeladmin.message_user(request, f"{obj}: {exc}", messages.ERROR)


@admin.register(Machine)
class MachineAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "company", "is_active")
    list_filter = ("company", "is_active")


@admin.register(Operation)
class OperationAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "qc_required")
    list_filter = ("company",)


class BOMItemInline(admin.TabularInline):
    model = BOMItem
    extra = 1
    raw_id_fields = ("component",)


class BOMOperationInline(admin.TabularInline):
    model = BOMOperation
    extra = 0


@admin.register(BOM)
class BOMAdmin(admin.ModelAdmin):
    list_display = ("variant", "version", "status", "output_quantity", "company")
    list_filter = ("status", "company")
    search_fields = ("variant__sku", "variant__product__name")
    raw_id_fields = ("variant",)
    inlines = [BOMItemInline, BOMOperationInline]
    readonly_fields = ("status", "created_by")
    actions = ["activate", "new_version", "show_cost"]

    def save_model(self, request, obj, form, change):
        if not change:
            last = BOM.objects.filter(variant=obj.variant).order_by("-version").first()
            obj.version = (last.version + 1) if last else 1
            obj.created_by = request.user
        elif obj.status != BOM.DRAFT:
            self.message_user(request, "Only draft BOMs can be edited. Use 'Create new version'.", messages.ERROR)
            return
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        if form.instance.status != BOM.DRAFT:
            self.message_user(request, "Components of an active/archived BOM cannot be changed.", messages.ERROR)
            return
        super().save_formset(request, form, formset, change)

    @admin.action(description="Activate (archives the current active version)")
    def activate(self, request, queryset):
        _each(self, request, queryset, services.activate_bom, "activated")

    @admin.action(description="Create new draft version")
    def new_version(self, request, queryset):
        _each(self, request, queryset, services.new_bom_version, "new version created")

    @admin.action(description="Show standard cost of 1 unit")
    def show_cost(self, request, queryset):
        for bom in queryset:
            c = services.bom_cost(bom)
            self.message_user(request, f"{bom}: material {c['material']} + labour {c['labor']} + overhead {c['overhead']} = {c['total']}", messages.INFO)


class MaterialInline(admin.TabularInline):
    model = WorkOrderMaterial
    extra = 0
    can_delete = False
    readonly_fields = ("component", "required_quantity", "issued_quantity", "issued_value")

    def has_add_permission(self, request, obj=None):
        return False


class JobCardInline(admin.TabularInline):
    model = JobCard
    extra = 0
    can_delete = False
    fields = ("reference", "operation", "sequence", "status", "completed_quantity", "rejected_quantity", "actual_minutes")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    """Create the order here (choose an ACTIVE BOM, quantity, warehouses). The actions drive the production flow."""
    list_display = ("reference", "variant", "planned_quantity", "received_quantity", "status", "priority", "due_date")
    list_filter = ("status", "priority", "company")
    search_fields = ("reference", "variant__sku")
    raw_id_fields = ("bom",)
    readonly_fields = ("reference", "variant", "status", "received_quantity", "material_cost", "labor_cost", "overhead_cost",
                       "fg_value_received", "variance_written_off", "started_at", "closed_at", "created_by")
    inlines = [MaterialInline, JobCardInline]
    actions = ["release", "issue_all", "complete", "receive_all_remaining", "close", "cancel"]

    def save_model(self, request, obj, form, change):
        if change:
            if obj.status != WorkOrder.DRAFT:
                self.message_user(request, "Only a draft work order can be edited.", messages.ERROR)
                return
            return super().save_model(request, obj, form, change)
        try:
            wo = services.create_work_order(obj.bom, obj.planned_quantity, obj.source_warehouse, obj.fg_warehouse, user=request.user,
                                            planned_start=obj.planned_start, due_date=obj.due_date, priority=obj.priority, note=obj.note)
            obj.pk, obj.reference, obj.variant_id = wo.pk, wo.reference, wo.variant_id
        except Exception as exc:
            self.message_user(request, f"Not created: {exc}", messages.ERROR)

    def save_related(self, request, form, formsets, change):
        if form.instance.pk:
            super().save_related(request, form, formsets, change)

    @admin.action(description="Submit / release")
    def release(self, request, queryset):
        _each(self, request, queryset, services.submit_work_order, "submitted for release")

    @admin.action(description="Issue ALL pending materials")
    def issue_all(self, request, queryset):
        _each(self, request, queryset, lambda w, u: services.issue_materials(w, None, user=u), "materials issued")

    @admin.action(description="Mark completed")
    def complete(self, request, queryset):
        _each(self, request, queryset, services.complete_work_order, "completed")

    @admin.action(description="Receive remaining finished goods")
    def receive_all_remaining(self, request, queryset):
        def run(wo, user):
            last = wo.job_cards.order_by("-sequence").first()
            ready = (last.completed_quantity if last else wo.planned_quantity) - wo.received_quantity
            return services.receive_finished_goods(wo, ready, user=user)
        _each(self, request, queryset, run, "finished goods received")

    @admin.action(description="Close (write off leftover WIP)")
    def close(self, request, queryset):
        _each(self, request, queryset, services.close_work_order, "closed")

    @admin.action(description="Cancel")
    def cancel(self, request, queryset):
        _each(self, request, queryset, services.cancel_work_order, "cancelled")


@admin.register(JobCard)
class JobCardAdmin(admin.ModelAdmin):
    list_display = ("reference", "work_order", "operation", "sequence", "operator", "machine", "status", "completed_quantity", "rejected_quantity")
    list_filter = ("status", "operation", "machine")
    search_fields = ("reference", "work_order__reference")
    raw_id_fields = ("operator",)
    readonly_fields = tuple(f.name for f in JobCard._meta.fields if f.name not in ("operator", "machine"))
    actions = ["start", "complete_all_good"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)     # only operator / machine can be edited here

    @admin.action(description="Start")
    def start(self, request, queryset):
        _each(self, request, queryset, lambda j, u: services.start_job_card(j, user=u), "started")

    @admin.action(description="Complete (all planned units good, standard time)")
    def complete_all_good(self, request, queryset):
        _each(self, request, queryset, lambda j, u: services.complete_job_card(j, j.planned_quantity, 0, user=u), "completed")


class IssueLineInline(admin.TabularInline):
    model = MaterialIssueLine
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return ["material", "quantity", "unit_cost"]


@admin.register(MaterialIssue)
class MaterialIssueAdmin(admin.ModelAdmin):
    list_display = ("reference", "work_order", "kind", "total_value", "created_at")
    list_filter = ("kind", "company")
    inlines = [IssueLineInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ProductionReceipt)
class ProductionReceiptAdmin(admin.ModelAdmin):
    list_display = ("reference", "work_order", "quantity", "accepted_quantity", "rejected_quantity", "value", "status")
    list_filter = ("status", "company")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
