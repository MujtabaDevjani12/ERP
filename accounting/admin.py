from django.contrib import admin

from .models import (Account, AccountingPeriodLock, BankAccount, BankStatementLine, Budget, BudgetLine, CostCenter,
                     DepreciationEntry, FixedAsset, JournalEntry, JournalLine)


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "account_type", "system_key", "is_group", "is_active", "company")
    list_filter = ("company", "account_type", "is_group", "is_active")
    search_fields = ("code", "name", "system_key")


class LineInline(admin.TabularInline):
    model = JournalLine
    extra = 0
    fields = ("account", "debit", "credit", "cost_center", "description", "party_type", "party_id")


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    """Posted entries are read-only. Create a draft here, then post it with the
    'Post selected drafts' action; fix mistakes with 'Reverse selected entries'."""
    list_display = ("entry_number", "date", "voucher_type", "status", "total_debit", "total_credit", "narration", "company")
    list_filter = ("company", "status", "voucher_type")
    search_fields = ("entry_number", "narration", "source_type", "source_id")
    date_hierarchy = "date"
    inlines = [LineInline]
    actions = ["post_selected", "reverse_selected"]

    def get_readonly_fields(self, request, obj=None):
        base = ("entry_number", "status", "source_type", "source_id", "total_debit", "total_credit",
                "posted_by", "posted_at", "reversal_of", "meta", "created_by")
        if obj is not None and obj.status != JournalEntry.DRAFT:
            return base + ("company", "store", "voucher_type", "date", "narration")
        return base

    def has_delete_permission(self, request, obj=None):
        return obj is None or obj.status == JournalEntry.DRAFT

    def has_change_permission(self, request, obj=None):
        return obj is None or obj.status == JournalEntry.DRAFT

    @admin.action(description="Post selected drafts")
    def post_selected(self, request, queryset):
        from core.errors import ERPError
        from .services import post_entry
        done = 0
        for entry in queryset.filter(status=JournalEntry.DRAFT):
            try:
                post_entry(entry, user=request.user)
                done += 1
            except (ERPError, Exception) as exc:   # show the reason instead of a 500
                self.message_user(request, f"{entry}: {exc}", level="error")
        self.message_user(request, f"{done} entr(y/ies) posted.")

    @admin.action(description="Reverse selected entries")
    def reverse_selected(self, request, queryset):
        from .services import reverse_entry
        done = 0
        for entry in queryset.filter(status=JournalEntry.POSTED):
            try:
                reverse_entry(entry, user=request.user, reason="Reversed from admin")
                done += 1
            except Exception as exc:
                self.message_user(request, f"{entry}: {exc}", level="error")
        self.message_user(request, f"{done} entr(y/ies) reversed.")


for model in (CostCenter, AccountingPeriodLock, BankAccount, BankStatementLine, FixedAsset, DepreciationEntry, Budget, BudgetLine):
    admin.site.register(model)
