from django.contrib import admin, messages

from numbering.services import next_reference
from . import services
from .models import (Attendance, Department, Designation, Employee, EmployeeLoan, Holiday, LeaveBalance, LeaveRequest, LeaveType,
                     LoanRepayment, PerformanceReview, Shift)


def _each(modeladmin, request, queryset, func, label):
    for obj in queryset:
        try:
            func(obj, request.user)
            modeladmin.message_user(request, f"{obj}: {label}.", messages.SUCCESS)
        except Exception as exc:
            modeladmin.message_user(request, f"{obj}: {exc}", messages.ERROR)


for _model in (Department, Designation, Holiday, LeaveType):
    admin.site.register(_model)


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "start_time", "end_time", "grace_minutes", "standard_hours")


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("employee_code", "full_name", "department", "designation", "status", "joining_date")
    list_filter = ("company", "status", "department", "employment_type")
    search_fields = ("employee_code", "full_name", "cnic", "phone")
    raw_id_fields = ("user",)

    def save_model(self, request, obj, form, change):
        if not change and not obj.employee_code:
            obj.employee_code = next_reference(obj.company, "employee")
        super().save_model(request, obj, form, change)


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("date", "employee", "check_in", "check_out", "status", "worked_hours", "late_minutes", "overtime_hours")
    list_filter = ("status", "date", "employee__department")
    search_fields = ("employee__employee_code", "employee__full_name")
    date_hierarchy = "date"
    raw_id_fields = ("employee",)
    readonly_fields = ("worked_hours", "late_minutes", "overtime_hours", "source")

    def save_model(self, request, obj, form, change):
        """Always go through record_attendance so late/overtime/status rules apply."""
        try:
            row = services.record_attendance(obj.employee, obj.date, check_in=obj.check_in, check_out=obj.check_out,
                                       status=obj.status if (not obj.check_in or not obj.check_out or "status" in form.changed_data) else None,
                                       remarks=obj.remarks)
            obj.pk = row.pk
        except Exception as exc:
            self.message_user(request, f"Not saved: {exc}", messages.ERROR)


@admin.register(LeaveBalance)
class LeaveBalanceAdmin(admin.ModelAdmin):
    list_display = ("employee", "leave_type", "year", "allocated", "used")
    list_filter = ("year", "leave_type")
    raw_id_fields = ("employee",)


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ("reference", "employee", "leave_type", "from_date", "to_date", "days", "status")
    list_filter = ("status", "leave_type", "company")
    readonly_fields = ("reference", "days", "status", "created_by")
    raw_id_fields = ("employee",)
    actions = ["submit", "cancel"]

    def save_model(self, request, obj, form, change):
        if change:
            self.message_user(request, "Leave requests cannot be edited; cancel and create a new one.", messages.WARNING)
            return
        try:
            req = services.request_leave(obj.employee, obj.leave_type, obj.from_date, obj.to_date, user=request.user, reason=obj.reason)
            obj.pk = req.pk
        except Exception as exc:
            self.message_user(request, f"Not saved: {exc}", messages.ERROR)

    @admin.action(description="Submit for approval")
    def submit(self, request, queryset):
        _each(self, request, queryset, services.submit_leave, "submitted")

    @admin.action(description="Cancel")
    def cancel(self, request, queryset):
        _each(self, request, queryset, services.cancel_leave, "cancelled")


@admin.register(PerformanceReview)
class PerformanceReviewAdmin(admin.ModelAdmin):
    list_display = ("employee", "period", "rating", "kpi_score", "reviewer")
    list_filter = ("period", "rating")
    raw_id_fields = ("employee",)

    def save_model(self, request, obj, form, change):
        obj.reviewer = obj.reviewer or request.user
        super().save_model(request, obj, form, change)


class RepaymentInline(admin.TabularInline):
    model = LoanRepayment
    extra = 0
    can_delete = False
    readonly_fields = ("amount", "date", "source")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(EmployeeLoan)
class EmployeeLoanAdmin(admin.ModelAdmin):
    list_display = ("reference", "employee", "amount", "installments", "installment_amount", "balance", "status")
    list_filter = ("status", "company")
    readonly_fields = ("reference", "installment_amount", "balance", "status", "disbursed_on", "created_by")
    raw_id_fields = ("employee",)
    inlines = [RepaymentInline]
    actions = ["submit"]

    def save_model(self, request, obj, form, change):
        if change:
            self.message_user(request, "Loans cannot be edited once created.", messages.WARNING)
            return
        try:
            loan = services.create_loan(obj.employee, obj.amount, obj.installments, user=request.user, purpose=obj.purpose)
            obj.pk = loan.pk
        except Exception as exc:
            self.message_user(request, f"Not saved: {exc}", messages.ERROR)

    @admin.action(description="Submit for approval (pays out on approval)")
    def submit(self, request, queryset):
        _each(self, request, queryset, services.submit_loan, "submitted")
