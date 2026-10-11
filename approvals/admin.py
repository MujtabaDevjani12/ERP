from django.contrib import admin

from .models import ApprovalAction, ApprovalLevel, ApprovalRequest, ApprovalWorkflow


class LevelInline(admin.TabularInline):
    model = ApprovalLevel
    extra = 1


@admin.register(ApprovalWorkflow)
class ApprovalWorkflowAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "document_type", "min_amount", "is_active")
    list_filter = ("company", "document_type", "is_active")
    inlines = [LevelInline]


class ActionInline(admin.TabularInline):
    model = ApprovalAction
    extra = 0
    can_delete = False
    readonly_fields = ("level", "actor", "action", "comment", "delegated_to", "created_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ApprovalRequest)
class ApprovalRequestAdmin(admin.ModelAdmin):
    list_display = ("document_type", "document_number", "amount", "status", "current_level", "submitted_by", "submitted_at")
    list_filter = ("status", "document_type", "company")
    search_fields = ("document_number",)
    inlines = [ActionInline]
    readonly_fields = ("company", "workflow", "content_type", "object_id", "document_type", "document_number",
                       "amount", "status", "current_level", "submitted_by", "submitted_at", "completed_at")
