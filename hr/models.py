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

    # personal & statutory details
    GENDER_CHOICES = [("male", "Male"), ("female", "Female"), ("other", "Other")]
    MARITAL_CHOICES = [("single", "Single"), ("married", "Married"), ("divorced", "Divorced"), ("widowed", "Widowed")]
    father_name = models.CharField("Father / husband name", max_length=150, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True)
    marital_status = models.CharField(max_length=10, choices=MARITAL_CHOICES, blank=True)
    permanent_address = models.CharField(max_length=255, blank=True)
    emergency_contact_phone = models.CharField(max_length=30, blank=True)
    reporting_to = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="team",
                                     help_text="Line manager.")
    probation_end_date = models.DateField(null=True, blank=True)
    confirmation_date = models.DateField(null=True, blank=True)
    ntn = models.CharField("NTN / tax no.", max_length=20, blank=True)
    eobi_number = models.CharField("EOBI no.", max_length=20, blank=True)
    social_security_number = models.CharField("Social security no.", max_length=20, blank=True)

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


# ---------------------------------------------------------------------------------------------- employee file
class EmployeeDocument(TimeStampedModel):
    """CNIC copy, contract, degree, experience letter ... with an expiry date so renewals are not missed."""
    TYPE_CHOICES = [("cnic", "CNIC / ID"), ("contract", "Employment contract"), ("offer", "Offer / appointment letter"),
                    ("degree", "Degree / certificate"), ("experience", "Experience letter"), ("license", "License / permit"),
                    ("medical", "Medical / fitness"), ("other", "Other")]
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField("Type", max_length=12, choices=TYPE_CHOICES, default="other")
    title = models.CharField(max_length=120)
    file = models.FileField(upload_to="hr/documents/", blank=True, null=True)
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["employee", "doc_type", "title"]

    def __str__(self):
        return f"{self.employee.employee_code} {self.title}"


class EmployeeMovement(TimeStampedModel, CreatedByModel):
    """Promotion, transfer, confirmation, increment ... history. Saving a movement also updates the employee's
    current department / designation (see hr.services.apply_movement)."""
    PROMOTION, TRANSFER, CONFIRMATION, INCREMENT, DEMOTION, OTHER = "promotion", "transfer", "confirmation", "increment", "demotion", "other"
    TYPE_CHOICES = [(PROMOTION, "Promotion"), (TRANSFER, "Transfer"), (CONFIRMATION, "Confirmation"), (INCREMENT, "Salary increment"),
                    (DEMOTION, "Demotion"), (OTHER, "Other")]
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="movements")
    movement_type = models.CharField(max_length=12, choices=TYPE_CHOICES, default=PROMOTION)
    effective_date = models.DateField()
    from_department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    to_department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    from_designation = models.ForeignKey(Designation, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    to_designation = models.ForeignKey(Designation, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    old_salary = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    new_salary = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    remarks = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-effective_date", "-id"]

    def __str__(self):
        return f"{self.employee.employee_code} {self.get_movement_type_display()} {self.effective_date}"


class DisciplinaryAction(TimeStampedModel, CreatedByModel):
    VERBAL, WRITTEN, SHOW_CAUSE, SUSPENSION, FINE, TERMINATION = "verbal", "written", "show_cause", "suspension", "fine", "termination"
    TYPE_CHOICES = [(VERBAL, "Verbal warning"), (WRITTEN, "Written warning"), (SHOW_CAUSE, "Show-cause notice"),
                    (SUSPENSION, "Suspension"), (FINE, "Fine"), (TERMINATION, "Termination notice")]
    OPEN, CLOSED = "open", "closed"
    STATUS_CHOICES = [(OPEN, "Open"), (CLOSED, "Closed")]
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="disciplinary_actions")
    date = models.DateField()
    action_type = models.CharField(max_length=12, choices=TYPE_CHOICES, default=VERBAL)
    reason = models.CharField(max_length=255)
    details = models.TextField(blank=True)
    employee_response = models.TextField(blank=True)
    outcome = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default=OPEN, db_index=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.employee.employee_code} {self.get_action_type_display()} {self.date}"


# ---------------------------------------------------------------------------------------------- recruitment
class Vacancy(TimeStampedModel):
    OPEN, ON_HOLD, FILLED, CLOSED = "open", "on_hold", "filled", "closed"
    STATUS_CHOICES = [(OPEN, "Open"), (ON_HOLD, "On hold"), (FILLED, "Filled"), (CLOSED, "Closed")]
    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="vacancies")
    title = models.CharField(max_length=120)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="vacancies")
    designation = models.ForeignKey(Designation, on_delete=models.SET_NULL, null=True, blank=True, related_name="vacancies")
    positions = models.PositiveSmallIntegerField(default=1)
    employment_type = models.CharField(max_length=10, choices=Employee.TYPE_CHOICES, default="permanent")
    description = models.TextField(blank=True)
    requirements = models.TextField(blank=True)
    salary_min = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    salary_max = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    opened_on = models.DateField(null=True, blank=True)
    closing_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=OPEN, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Vacancies"

    def __str__(self):
        return self.title


class Applicant(TimeStampedModel):
    APPLIED, SCREENING, INTERVIEW, OFFERED, HIRED, REJECTED = "applied", "screening", "interview", "offered", "hired", "rejected"
    STAGE_CHOICES = [(APPLIED, "Applied"), (SCREENING, "Screening"), (INTERVIEW, "Interview"), (OFFERED, "Offer made"),
                     (HIRED, "Hired"), (REJECTED, "Rejected")]
    vacancy = models.ForeignKey(Vacancy, on_delete=models.CASCADE, related_name="applicants")
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    cnic = models.CharField(max_length=15, blank=True)
    current_employer = models.CharField(max_length=150, blank=True)
    experience_years = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    expected_salary = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    resume = models.FileField(upload_to="hr/resumes/", blank=True, null=True)
    source = models.CharField(max_length=60, blank=True, help_text="Referral, job portal, walk-in ...")
    stage = models.CharField(max_length=10, choices=STAGE_CHOICES, default=APPLIED, db_index=True)
    interview_date = models.DateTimeField(null=True, blank=True)
    interview_notes = models.TextField(blank=True)
    rating = models.PositiveSmallIntegerField(null=True, blank=True, help_text="1 (weak) to 5 (excellent)")
    hired_as = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["vacancy", "-created_at"]

    def __str__(self):
        return f"{self.full_name} - {self.vacancy}"


# ---------------------------------------------------------------------------------------------- training
class TrainingProgram(TimeStampedModel):
    PLANNED, RUNNING, COMPLETED, CANCELLED = "planned", "running", "completed", "cancelled"
    STATUS_CHOICES = [(PLANNED, "Planned"), (RUNNING, "In progress"), (COMPLETED, "Completed"), (CANCELLED, "Cancelled")]
    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="training_programs")
    title = models.CharField(max_length=150)
    trainer = models.CharField(max_length=120, blank=True)
    venue = models.CharField(max_length=120, blank=True)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PLANNED, db_index=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return self.title


class TrainingParticipant(TimeStampedModel):
    program = models.ForeignKey(TrainingProgram, on_delete=models.CASCADE, related_name="participants")
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="trainings")
    attended = models.BooleanField(default=False)
    score = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    certificate_issued = models.BooleanField(default=False)
    feedback = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["program", "employee"]
        constraints = [models.UniqueConstraint(fields=["program", "employee"], name="uniq_training_participant")]

    def __str__(self):
        return f"{self.employee.employee_code} @ {self.program}"
