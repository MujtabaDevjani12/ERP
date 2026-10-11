from django.contrib import admin, messages

from . import services
from .models import DefectType, QualityCheck, QualityDefect, QualityParameter


@admin.register(DefectType)
class DefectTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "severity", "is_active")
    list_filter = ("company", "severity")


class ParameterInline(admin.TabularInline):
    model = QualityParameter
    extra = 0


class DefectInline(admin.TabularInline):
    model = QualityDefect
    extra = 0


@admin.register(QualityCheck)
class QualityCheckAdmin(admin.ModelAdmin):
    list_display = ("reference", "check_type", "variant", "quantity", "accepted_quantity", "rejected_quantity", "status", "source_reference")
    list_filter = ("status", "check_type", "company")
    search_fields = ("reference", "source_reference", "variant__sku")
    readonly_fields = ("reference", "status", "source_type", "source_id", "inspector", "inspected_at")
    inlines = [ParameterInline, DefectInline]
    actions = ["accept_all", "put_on_hold"]

    def has_add_permission(self, request):
        return False        # checks are created by the documents that need them

    def save_model(self, request, obj, form, change):
        """Typing accepted/rejected quantities (that add up to the inspected quantity) completes the check."""
        if change and obj.status in (QualityCheck.PENDING, QualityCheck.HOLD) and (
                "accepted_quantity" in form.changed_data or "rejected_quantity" in form.changed_data):
            try:
                services.complete_check(obj, request.user, accepted=obj.accepted_quantity, rejected=obj.rejected_quantity,
                                        remarks=obj.remarks)
            except Exception as exc:
                self.message_user(request, f"Not saved: {exc}", messages.ERROR)
            return
        super().save_model(request, obj, form, change)

    @admin.action(description="Pass the whole quantity")
    def accept_all(self, request, queryset):
        for check in queryset:
            try:
                services.complete_check(check, request.user, accepted=check.quantity, rejected=0)
                self.message_user(request, f"{check.reference}: passed.", messages.SUCCESS)
            except Exception as exc:
                self.message_user(request, f"{check.reference}: {exc}", messages.ERROR)

    @admin.action(description="Put on hold")
    def put_on_hold(self, request, queryset):
        for check in queryset:
            try:
                services.put_on_hold(check, request.user)
            except Exception as exc:
                self.message_user(request, f"{check.reference}: {exc}", messages.ERROR)
