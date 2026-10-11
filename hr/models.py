from django.conf import settings
from django.db import models

from core.models import CreatedByModel, TimeStampedModel


class Department(TimeStampedModel):
    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="departments")
    name = models.CharField(max_length=80)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_department_name")]

    def __str__(self):
        return self.name


class Designation(TimeStampedModel):
    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="designations")
    name = models.CharField(max_length=80)

    class Meta:
        ordering = ["company", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_designation_name")]

    def __str__(self):
        return self.name


class Shift(TimeStampedModel):
    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="shifts")
    name = models.CharField(max_length=60)
    start_time = models.TimeField()
    end_time = models.TimeField()
    grace_minutes = models.PositiveSmallIntegerField(default=10, help_text="Arrival after start + grace counts as late.")
    standard_hours = models.DecimalField(max_digits=4, decimal_places=2, default=8, help_text="Hours beyond this are overtime.")
    half_day_hours = models.DecimalField(max_digits=4, decimal_places=2, default=4, help_text="Worked less than this = absent; less than standard = half day.")

    class Meta:
        ordering = ["company", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_shift_name")]

    def __str__(self):
        return f"{self.name} ({self.start_time:%H:%M}-{self.end_time:%H:%M})"


class Holiday(TimeStampedModel):
    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="holidays")
    date = models.DateField()
    name = models.CharField(max_length=80)

    class Meta:
        ordering = ["date"]
        constraints = [models.UniqueConstraint(fields=["company", "date"], name="uniq_holiday_date")]

    def __str__(self):
        return f"{self.date} {self.name}"


class Employee(TimeStampedModel):
    ACTIVE, INACTIVE, TERMINATED = "active", "inactive", "terminated"
    STATUS_CHOICES = [(ACTIVE, "Active"), (INACTIVE, "Inactive"), (TERMINATED, "Terminated")]
    TYPE_CHOICES = [("permanent", "Permanent"), ("contract", "Contract"), ("daily", "Daily wage"), ("probation", "Probation")]

    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="employees")
    store = models.ForeignKey("tenants.Store", on_delete=models.PROTECT, null=True, blank=True, related_name="employees")
    employee_code = models.CharField(max_length=20)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="employee_profile")
    full_name = models.CharField(max_length=150)
    cnic = models.CharField(max_length=15, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    emergency_contact = models.CharField(max_length=150, blank=True)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="employees")
    designation = models.ForeignKey(Designation, on_delete=models.SET_NULL, null=True, blank=True, related_name="employees")
    shift = models.ForeignKey(Shift, on_delete=models.SET_NULL, null=True, blank=True, related_name="employees")
    employment_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default="permanent")
    joining_date = models.DateField()
    leaving_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=ACTIVE, db_index=True)
    bank_name = models.CharField(max_length=80, blank=True)
    bank_account_no = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["employee_code"]
        constraints = [models.UniqueConstraint(fields=["company", "employee_code"], name="uniq_employee_code")]

    def __str__(self):
        return f"{self.employee_code} {self.full_name}"


class Attendance(TimeStampedModel):
    PRESENT, ABSENT, LATE, HALF_DAY, LEAVE, HOLIDAY, WEEKLY_OFF = "present", "absent", "late", "half_day", "leave", "holiday", "weekly_off"
    STATUS_CHOICES = [(PRESENT, "Present"), (ABSENT, "Absent"), (LATE, "Late"), (HALF_DAY, "Half day"), (LEAVE, "On leave"),
                      (HOLIDAY, "Holiday"), (WEEKLY_OFF, "Weekly off")]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="attendance")
    date = models.DateField()
    check_in = models.TimeField(null=True, blank=True)
    check_out = models.TimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PRESENT, db_index=True)
    worked_hours = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    late_minutes = models.PositiveSmallIntegerField(default=0)
    overtime_hours = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    source = models.CharField(max_length=10, default="manual")
    remarks = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["-date", "employee"]
        constraints = [models.UniqueConstraint(fields=["employee", "date"], name="uniq_attendance_day")]

    def __str__(self):
        return f"{self.employee.employee_code} {self.date} {self.status}"


class LeaveType(TimeStampedModel):
    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="leave_types")
    name = models.CharField(max_length=60)
    days_per_year = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    is_paid = models.BooleanField(default=True)

    class Meta:
        ordering = ["company", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_leave_type_name")]

    def __str__(self):
        return self.name


class LeaveBalance(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="leave_balances")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE, related_name="+")
    year = models.PositiveSmallIntegerField()
    allocated = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    used = models.DecimalField(max_digits=5, decimal_places=1, default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["employee", "leave_type", "year"], name="uniq_leave_balance")]

    @property
    def remaining(self):
        return self.allocated - self.used


class LeaveRequest(TimeStampedModel, CreatedByModel):
    DRAFT, PENDING, APPROVED, REJECTED, CANCELLED = "draft", "pending_approval", "approved", "rejected", "cancelled"
    STATUS_CHOICES = [(DRAFT, "Draft"), (PENDING, "Pending approval"), (APPROVED, "Approved"), (REJECTED, "Rejected"), (CANCELLED, "Cancelled")]

    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="leave_requests")
    reference = models.CharField(max_length=30, unique=True)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="leave_requests")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="+")
    from_date = models.DateField()
    to_date = models.DateField()
    days = models.DecimalField(max_digits=5, decimal_places=1)
    reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT, db_index=True)

    class Meta:
        ordering = ["-from_date"]
        constraints = [models.CheckConstraint(check=models.Q(to_date__gte=models.F("from_date")), name="leave_dates_ordered")]

    def __str__(self):
        return self.reference


class PerformanceReview(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="reviews")
    period = models.CharField(max_length=30, help_text="e.g. 2026-Q3")
    rating = models.PositiveSmallIntegerField(help_text="1 (poor) to 5 (excellent)")
    kpi_score = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    strengths = models.TextField(blank=True)
    improvements = models.TextField(blank=True)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "period"], name="uniq_review_period"),
            models.CheckConstraint(check=models.Q(rating__gte=1, rating__lte=5), name="review_rating_1_5"),
        ]


class EmployeeLoan(TimeStampedModel, CreatedByModel):
    DRAFT, PENDING, ACTIVE, CLOSED, REJECTED = "draft", "pending_approval", "active", "closed", "rejected"
    STATUS_CHOICES = [(DRAFT, "Draft"), (PENDING, "Pending approval"), (ACTIVE, "Active (disbursed)"), (CLOSED, "Closed"), (REJECTED, "Rejected")]

    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="employee_loans")
    reference = models.CharField(max_length=30, unique=True)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="loans")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    installments = models.PositiveSmallIntegerField(default=1)
    installment_amount = models.DecimalField(max_digits=14, decimal_places=2)
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT, db_index=True)
    purpose = models.CharField(max_length=255, blank=True)
    disbursed_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(check=models.Q(amount__gt=0), name="loan_amount_positive"),
            models.CheckConstraint(check=models.Q(balance__gte=0), name="loan_balance_non_negative"),
        ]

    def __str__(self):
        return self.reference


class LoanRepayment(TimeStampedModel):
    loan = models.ForeignKey(EmployeeLoan, on_delete=models.CASCADE, related_name="repayments")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    date = models.DateField()
    source = models.CharField(max_length=30, blank=True, help_text="e.g. the payroll run that deducted it")

    class Meta:
        ordering = ["date", "id"]
