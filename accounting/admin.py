from django.contrib import admin, messages
from django.utils import timezone

from .models import (Account, AccountingPeriodLock, BankAccount, BankStatementLine, Budget, BudgetLine, Cheque, CostCenter,
                     DepreciationEntry, FiscalYear, FixedAsset, JournalEntry, JournalLine, RecurringJournal, RecurringJournalLine)


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


@admin.register(CostCenter)
class CostCenterAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "center_type", "parent", "store", "is_active", "company")
    list_filter = ("company", "center_type", "is_active")
    search_fields = ("code", "name")


@admin.register(AccountingPeriodLock)
class AccountingPeriodLockAdmin(admin.ModelAdmin):
    list_display = ("company", "locked_through")


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "bank_name", "account_number", "ledger_account", "opening_statement_balance", "is_active", "company")
    list_filter = ("company", "is_active")
    search_fields = ("name", "bank_name", "account_number")


@admin.register(BankStatementLine)
class BankStatementLineAdmin(admin.ModelAdmin):
    list_display = ("date", "bank_account", "description", "reference", "amount", "matched_line")
    list_filter = ("bank_account",)
    search_fields = ("description", "reference")
    date_hierarchy = "date"
    raw_id_fields = ("matched_line",)


@admin.register(FixedAsset)
class FixedAssetAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "purchase_date", "purchase_cost", "accumulated_depreciation", "status", "company")
    list_filter = ("company", "status", "method")
    search_fields = ("code", "name", "location")


@admin.register(DepreciationEntry)
class DepreciationEntryAdmin(admin.ModelAdmin):
    list_display = ("asset", "period_end", "amount", "journal_entry")
    list_filter = ("asset__company",)


class BudgetLineInline(admin.TabularInline):
    model = BudgetLine
    extra = 0


@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = ("name", "fiscal_year", "scope", "store", "cost_center", "is_active", "company")
    list_filter = ("company", "fiscal_year", "scope", "is_active")
    search_fields = ("name",)
    inlines = [BudgetLineInline]


@admin.register(BudgetLine)
class BudgetLineAdmin(admin.ModelAdmin):
    list_display = ("budget", "account", "month_index", "amount")
    list_filter = ("budget",)


# ---------------------------------------------------------------------------------------------- fiscal years
@admin.register(FiscalYear)
class FiscalYearAdmin(admin.ModelAdmin):
    """Define each accounting year, then close it when the books are final. Closing locks every date on/before the last
    day (nothing can be posted or backdated into it) - 'Reopen' undoes the latest close."""
    list_display = ("name", "company", "start_date", "end_date", "is_closed", "closed_on")
    list_filter = ("company", "is_closed")
    search_fields = ("name",)
    readonly_fields = ("is_closed", "closed_on", "closed_by")
    actions = ["close_years", "reopen_years"]

    def has_delete_permission(self, request, obj=None):
        return obj is None or not obj.is_closed

    @admin.action(description="Close selected years (lock the books)")
    def close_years(self, request, queryset):
        from . import periods
        for year in queryset.order_by("start_date"):
            try:
                periods.close_fiscal_year(year, user=request.user)
                self.message_user(request, f"{year}: closed. Books are locked through {year.end_date}.", messages.SUCCESS)
            except Exception as exc:
                self.message_user(request, f"{year}: {exc}", messages.ERROR)

    @admin.action(description="Reopen selected years")
    def reopen_years(self, request, queryset):
        from . import periods
        for year in queryset.order_by("-start_date"):
            try:
                periods.reopen_fiscal_year(year, user=request.user)
                self.message_user(request, f"{year}: reopened.", messages.SUCCESS)
            except Exception as exc:
                self.message_user(request, f"{year}: {exc}", messages.ERROR)


# ---------------------------------------------------------------------------------------------- cheques
@admin.register(Cheque)
class ChequeAdmin(admin.ModelAdmin):
    """Register every cheque you issue or receive. 'Mark as cleared' posts the voucher on the clearing date."""
    list_display = ("cheque_number", "cheque_type", "party_name", "bank_account", "cheque_date", "amount", "status", "cleared_on")
    list_filter = ("status", "cheque_type", "bank_account", "company")
    search_fields = ("cheque_number", "party_name", "narration")
    date_hierarchy = "cheque_date"
    readonly_fields = ("status", "cleared_on", "journal_entry", "created_by")
    actions = ["clear_selected", "bounce_selected", "cancel_selected"]

    def get_readonly_fields(self, request, obj=None):
        base = self.readonly_fields
        if obj is not None and obj.status != Cheque.PENDING:
            return base + ("company", "cheque_type", "bank_account", "cheque_number", "party_name", "cheque_date", "amount",
                           "counter_account", "narration")
        return base

    def has_delete_permission(self, request, obj=None):
        return obj is None or obj.status in (Cheque.PENDING, Cheque.CANCELLED)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def _each(self, request, queryset, func, label, **kw):
        for cheque in queryset:
            try:
                func(cheque, user=request.user, **kw)
                self.message_user(request, f"{cheque}: {label}.", messages.SUCCESS)
            except Exception as exc:
                self.message_user(request, f"{cheque}: {exc}", messages.ERROR)

    @admin.action(description="Mark as cleared (post the voucher today)")
    def clear_selected(self, request, queryset):
        from . import cheques
        self._each(request, queryset, cheques.clear_cheque, "cleared and posted", on=timezone.localdate())

    @admin.action(description="Mark as bounced / returned")
    def bounce_selected(self, request, queryset):
        from . import cheques
        self._each(request, queryset, cheques.bounce_cheque, "marked as bounced")

    @admin.action(description="Cancel")
    def cancel_selected(self, request, queryset):
        from . import cheques
        self._each(request, queryset, cheques.cancel_cheque, "cancelled")


# ---------------------------------------------------------------------------------------------- recurring journals
class RecurringLineInline(admin.TabularInline):
    model = RecurringJournalLine
    extra = 2
    fields = ("account", "debit", "credit", "cost_center", "description")


@admin.register(RecurringJournal)
class RecurringJournalAdmin(admin.ModelAdmin):
    list_display = ("name", "voucher_type", "frequency", "next_run_date", "end_date", "last_run_date", "is_active", "company")
    list_filter = ("company", "frequency", "is_active")
    search_fields = ("name", "narration")
    inlines = [RecurringLineInline]
    actions = ["generate_due"]

    @admin.action(description="Generate due entries now (posts a voucher for each period that has come due)")
    def generate_due(self, request, queryset):
        from . import periods
        today = timezone.localdate()
        for template in queryset:
            try:
                made = periods.generate_recurring(template, as_of=today, user=request.user)
                self.message_user(request, f"{template}: {len(made)} entr{'y' if len(made) == 1 else 'ies'} posted." if made
                                  else f"{template}: nothing due yet (next {template.next_run_date}).",
                                  messages.SUCCESS if made else messages.INFO)
            except Exception as exc:
                self.message_user(request, f"{template}: {exc}", messages.ERROR)
