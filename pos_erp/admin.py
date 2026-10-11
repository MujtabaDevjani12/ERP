from django.contrib import admin, messages

from .models import CashDrop, PosPostingIssue, PosReturn, PosSettings
from . import services


@admin.register(PosSettings)
class PosSettingsAdmin(admin.ModelAdmin):
    list_display = ("company", "post_to_accounting", "return_approval_threshold", "safe_account")


@admin.register(PosReturn)
class PosReturnAdmin(admin.ModelAdmin):
    list_display = ("sale_return", "register_session", "net_amount", "tax_amount", "refund_amount", "refund_method", "restocked", "approved_by")
    list_filter = ("refund_method", "restocked")
    search_fields = ("sale_return__reference", "sale_return__sale__reference")

    def has_add_permission(self, request):            # created by services.process_pos_return only
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CashDrop)
class CashDropAdmin(admin.ModelAdmin):
    list_display = ("register_session", "amount", "reason", "created_by", "created_at")
    list_filter = ("register_session__register",)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PosPostingIssue)
class PosPostingIssueAdmin(admin.ModelAdmin):
    list_display = ("doc_type", "reference", "error", "resolved", "created_at")
    list_filter = ("doc_type", "resolved")
    actions = ["retry"]

    @admin.action(description="Retry posting the selected documents")
    def retry(self, request, queryset):
        from .retry import retry_issue
        ok = 0
        for issue in queryset.filter(resolved=False):
            if retry_issue(issue, user=request.user):
                ok += 1
        self.message_user(request, f"{ok} document(s) posted.", messages.SUCCESS if ok else messages.WARNING)
