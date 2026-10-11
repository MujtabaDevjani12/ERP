from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only viewer: nobody can add, edit or delete audit rows from the admin."""
    list_display = ("created_at", "user_repr", "action", "content_type", "object_repr", "document_number", "ip_address")
    list_filter = ("action", "content_type", "company")
    search_fields = ("user_repr", "object_repr", "document_number", "object_id", "note")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
