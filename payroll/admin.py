from django.contrib import admin, messages
from django.http import HttpResponse

from . import services
from .models import PayrollLine, PayrollRun, SalaryStructure


@admin.register(SalaryStructure)
class SalaryStructureAdmin(admin.ModelAdmin):
    list_display = ("employee", "basic", "house_rent", "transport", "medical", "other_allowance", "tax_percent")
    search_fields = ("employee__employee_code", "employee__full_name")
    raw_id_fields = ("employee",)


class LineInline(admin.TabularInline):
    model = PayrollLine
    extra = 0
    can_delete = False
    fields = ("employee", "payable_days", "unpaid_days", "absent_deduction", "overtime_amount", "gross", "tax", "loan_deduction", "net")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(PayrollRun)
class PayrollRunAdmin(admin.ModelAdmin):
    """Create a run with year/month, then use the actions: Calculate -> Submit -> (approve) -> Pay."""
    list_display = ("reference", "year", "month", "status", "total_gross", "total_net")
    list_filter = ("status", "year", "company")
    readonly_fields = ("reference", "status", "total_gross", "total_tax", "total_loan", "total_net", "paid_on", "note", "created_by")
    inlines = [LineInline]
    actions = ["calculate", "submit", "pay_bank", "pay_cash", "bank_csv", "payslips"]

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if change:
            return super().save_model(request, obj, form, change)
        try:
            run = services.calculate_payroll(obj.company, obj.year, obj.month, user=request.user)
            obj.pk, obj.reference = run.pk, run.reference
            self.message_user(request, f"{run.reference} calculated. {run.note}", messages.SUCCESS)
        except Exception as exc:
            self.message_user(request, f"Not created: {exc}", messages.ERROR)

    def _each(self, request, queryset, func, label):
        for run in queryset:
            try:
                func(run, request.user)
                self.message_user(request, f"{run.reference}: {label}.", messages.SUCCESS)
            except Exception as exc:
                self.message_user(request, f"{run.reference}: {exc}", messages.ERROR)

    @admin.action(description="(Re)calculate draft payroll")
    def calculate(self, request, queryset):
        self._each(request, queryset, lambda r, u: services.calculate_payroll(r.company, r.year, r.month, user=u), "calculated")

    @admin.action(description="Submit for approval")
    def submit(self, request, queryset):
        self._each(request, queryset, services.submit_payroll, "submitted")

    @admin.action(description="Mark paid by BANK transfer")
    def pay_bank(self, request, queryset):
        self._each(request, queryset, lambda r, u: services.pay_payroll(r, u, method="bank"), "paid")

    @admin.action(description="Mark paid in CASH")
    def pay_cash(self, request, queryset):
        self._each(request, queryset, lambda r, u: services.pay_payroll(r, u, method="cash"), "paid")

    @admin.action(description="Download bank transfer CSV")
    def bank_csv(self, request, queryset):
        run = queryset.first()
        response = HttpResponse(services.bank_transfer_csv(run), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{run.reference}-bank.csv"'
        return response

    @admin.action(description="Open printable payslips")
    def payslips(self, request, queryset):
        return HttpResponse(services.payslips_html(queryset.first()))
