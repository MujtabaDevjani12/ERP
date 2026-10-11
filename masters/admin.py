from django.contrib import admin, messages

from . import importing
from .models import CustomerProfile, ImportJob, ItemProfile, PaymentTerm, TaxRate, VendorProfile


@admin.register(PaymentTerm)
class PaymentTermAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "days", "is_active")
    list_filter = ("company", "is_active")


@admin.register(TaxRate)
class TaxRateAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "rate", "tax_type", "is_inclusive", "is_active")
    list_filter = ("company", "tax_type", "is_active")


@admin.register(ItemProfile)
class ItemProfileAdmin(admin.ModelAdmin):
    list_display = ("variant", "item_type", "batch_required", "qc_required_on_receipt", "reorder_level", "standard_cost")
    list_filter = ("item_type", "batch_required", "qc_required_on_receipt")
    search_fields = ("variant__sku", "variant__product__name")
    autocomplete_fields = ()
    raw_id_fields = ("variant",)


@admin.register(VendorProfile)
class VendorProfileAdmin(admin.ModelAdmin):
    list_display = ("vendor_code", "supplier", "company", "city", "payment_term", "credit_limit")
    list_filter = ("company",)
    search_fields = ("vendor_code", "supplier__name")
    raw_id_fields = ("supplier",)


@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    list_display = ("customer_code", "customer", "company", "customer_type", "credit_limit")
    list_filter = ("company", "customer_type")
    search_fields = ("customer_code", "customer__name", "company_name")
    raw_id_fields = ("customer",)


@admin.register(ImportJob)
class ImportJobAdmin(admin.ModelAdmin):
    """Imports are created from code/management command (see import_excel).
    This screen shows the preview / error list and lets staff confirm a validated job."""
    list_display = ("id", "import_type", "company", "status", "summary", "created_at")
    list_filter = ("import_type", "status", "company")
    readonly_fields = ("company", "import_type", "status", "rows", "errors", "summary", "uploaded_by", "created_at")
    actions = ["confirm_selected"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Confirm and import selected validated files")
    def confirm_selected(self, request, queryset):
        for job in queryset:
            try:
                importing.confirm_import(job, request.user)
                self.message_user(request, f"Job {job.pk}: imported.", messages.SUCCESS)
            except Exception as exc:
                self.message_user(request, f"Job {job.pk}: {exc}", messages.ERROR)
