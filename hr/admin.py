from django.contrib import admin, messages

from numbering.services import next_reference
from . import services
from .models import (Applicant, Attendance, Department, Designation, DisciplinaryAction, Employee, EmployeeDocument, EmployeeLoan,
                     EmployeeMovement, Holiday, LeaveBalance, LeaveRequest, LeaveType, LoanRepayment, PerformanceReview, Shift,
                     TrainingParticipant, TrainingProgram, Vacancy)


def _each(modeladmin, request, queryset, func, label):
    for obj in queryset:
        try:
            func(obj, request.user)
            modeladmin.message_user(request, f"{obj}: {label}.", messages.SUCCESS)
        except Exception as exc:
            modeladmin.message_user(request, f"{obj}: {exc}", messages.ERROR)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "is_active")
    list_filter = ("company", "is_active")
    search_fields = ("name",)


@admin.register(Designation)
class DesignationAdmin(admin.ModelAdmin):
    list_display = ("name", "company")
    list_filter = ("company",)
    search_fields = ("name",)


@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ("date", "name", "company")
    list_filter = ("company",)
    search_fields = ("name",)
    date_hierarchy = "date"


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "days_per_year", "is_paid")
    list_filter = ("company", "is_paid")
    search_fields = ("name",)


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "start_time", "end_time", "grace_minutes", "standard_hours")


class EmployeeDocumentInline(admin.TabularInline):
    model = EmployeeDocument
    extra = 0
    fields = ("doc_type", "title", "file", "issue_date", "expiry_date")


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("employee_code", "full_name", "department", "designation", "employment_type", "status", "joining_date")
    list_filter = ("company", "status", "department", "employment_type", "gender")
    search_fields = ("employee_code", "full_name", "cnic", "phone", "email")
    raw_id_fields = ("user", "reporting_to")
    inlines = [EmployeeDocumentInline]
    fieldsets = (
        ("Job", {"fields": ("company", "store", "employee_code", "full_name", "user", "department", "designation", "shift",
                            "employment_type", "reporting_to", "joining_date", "probation_end_date", "confirmation_date",
                            "leaving_date", "status")}),
        ("Personal", {"fields": ("father_name", "date_of_birth", "gender", "marital_status", "cnic", "phone", "email", "address",
                                 "permanent_address", "emergency_contact", "emergency_contact_phone")}),
        ("Bank & statutory", {"fields": ("bank_name", "bank_account_no", "ntn", "eobi_number", "social_security_number")}),
    )

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


@admin.register(EmployeeDocument)
class EmployeeDocumentAdmin(admin.ModelAdmin):
    list_display = ("employee", "doc_type", "title", "issue_date", "expiry_date")
    list_filter = ("doc_type", "employee__company")
    search_fields = ("employee__employee_code", "employee__full_name", "title")
    raw_id_fields = ("employee",)
    date_hierarchy = "expiry_date"


@admin.register(EmployeeMovement)
class EmployeeMovementAdmin(admin.ModelAdmin):
    """Promotion / transfer / confirmation / increment history. Saving one also updates the employee's current post."""
    list_display = ("employee", "movement_type", "effective_date", "from_department", "to_department", "from_designation", "to_designation", "new_salary")
    list_filter = ("movement_type", "employee__company")
    search_fields = ("employee__employee_code", "employee__full_name", "remarks")
    raw_id_fields = ("employee",)
    readonly_fields = ("created_by",)
    date_hierarchy = "effective_date"

    def save_model(self, request, obj, form, change):
        if change:
            self.message_user(request, "Movements are history and cannot be edited; add a new one instead.", messages.WARNING)
            return
        obj.created_by = request.user
        try:
            services.apply_movement(obj, user=request.user)
            obj.save()
        except Exception as exc:
            self.message_user(request, f"Not saved: {exc}", messages.ERROR)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DisciplinaryAction)
class DisciplinaryActionAdmin(admin.ModelAdmin):
    list_display = ("employee", "date", "action_type", "reason", "status")
    list_filter = ("action_type", "status", "employee__company")
    search_fields = ("employee__employee_code", "employee__full_name", "reason")
    raw_id_fields = ("employee",)
    readonly_fields = ("created_by",)
    date_hierarchy = "date"
    actions = ["close_cases"]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Mark selected cases as closed")
    def close_cases(self, request, queryset):
        n = queryset.filter(status=DisciplinaryAction.OPEN).update(status=DisciplinaryAction.CLOSED)
        self.message_user(request, f"{n} case(s) closed.")


class ApplicantInline(admin.TabularInline):
    model = Applicant
    extra = 0
    fields = ("full_name", "phone", "stage", "expected_salary", "interview_date")
    show_change_link = False


@admin.register(Vacancy)
class VacancyAdmin(admin.ModelAdmin):
    list_display = ("title", "department", "designation", "positions", "opened_on", "closing_date", "status")
    list_filter = ("status", "company", "department")
    search_fields = ("title", "description")
    inlines = [ApplicantInline]
    actions = ["close_vacancies"]

    @admin.action(description="Close selected vacancies")
    def close_vacancies(self, request, queryset):
        n = queryset.exclude(status__in=[Vacancy.FILLED, Vacancy.CLOSED]).update(status=Vacancy.CLOSED)
        self.message_user(request, f"{n} vacancy(ies) closed.")


@admin.register(Applicant)
class ApplicantAdmin(admin.ModelAdmin):
    list_display = ("full_name", "vacancy", "phone", "stage", "interview_date", "expected_salary", "rating")
    list_filter = ("stage", "vacancy")
    search_fields = ("full_name", "phone", "email", "cnic")
    raw_id_fields = ("hired_as",)
    readonly_fields = ("hired_as",)
    actions = ["to_screening", "to_interview", "to_offered", "reject", "hire"]

    def _stage(self, request, queryset, stage):
        n = queryset.exclude(stage=Applicant.HIRED).update(stage=stage)
        self.message_user(request, f"{n} applicant(s) moved to {dict(Applicant.STAGE_CHOICES)[stage]}.")

    @admin.action(description="Move to Screening")
    def to_screening(self, request, queryset):
        self._stage(request, queryset, Applicant.SCREENING)

    @admin.action(description="Move to Interview")
    def to_interview(self, request, queryset):
        self._stage(request, queryset, Applicant.INTERVIEW)

    @admin.action(description="Move to Offer made")
    def to_offered(self, request, queryset):
        self._stage(request, queryset, Applicant.OFFERED)

    @admin.action(description="Reject")
    def reject(self, request, queryset):
        self._stage(request, queryset, Applicant.REJECTED)

    @admin.action(description="Hire (create the employee record, joining today)")
    def hire(self, request, queryset):
        from django.utils import timezone
        for applicant in queryset:
            try:
                emp = services.hire_applicant(applicant, timezone.localdate(), user=request.user)
                self.message_user(request, f"{applicant.full_name}: hired as {emp}.", messages.SUCCESS)
            except Exception as exc:
                self.message_user(request, f"{applicant.full_name}: {exc}", messages.ERROR)


class ParticipantInline(admin.TabularInline):
    model = TrainingParticipant
    extra = 0
    fields = ("employee", "attended", "score", "certificate_issued", "feedback")
    raw_id_fields = ("employee",)


@admin.register(TrainingProgram)
class TrainingProgramAdmin(admin.ModelAdmin):
    list_display = ("title", "trainer", "start_date", "end_date", "cost", "status")
    list_filter = ("status", "company")
    search_fields = ("title", "trainer")
    date_hierarchy = "start_date"
    inlines = [ParticipantInline]


@admin.register(TrainingParticipant)
class TrainingParticipantAdmin(admin.ModelAdmin):
    list_display = ("program", "employee", "attended", "score", "certificate_issued")
    list_filter = ("attended", "certificate_issued", "program")
    search_fields = ("employee__employee_code", "employee__full_name", "program__title")
    raw_id_fields = ("employee",)
