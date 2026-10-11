from django.db import models

from core.models import CreatedByModel, TimeStampedModel


class SalaryStructure(TimeStampedModel):
    employee = models.OneToOneField("hr.Employee", on_delete=models.CASCADE, related_name="salary")
    basic = models.DecimalField(max_digits=14, decimal_places=2)
    house_rent = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    transport = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    medical = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    other_allowance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    overtime_multiplier = models.DecimalField(max_digits=4, decimal_places=2, default=1, help_text="1.5 = time and a half. 0 = no overtime pay.")
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="Flat withholding % of gross. Set per employee (no rate is hard-coded).")

    class Meta:
        constraints = [models.CheckConstraint(check=models.Q(basic__gte=0), name="salary_basic_non_negative")]

    @property
    def fixed_total(self):
        return self.basic + self.house_rent + self.transport + self.medical + self.other_allowance

    def __str__(self):
        return f"Salary of {self.employee}"


class PayrollRun(TimeStampedModel, CreatedByModel):
    DRAFT, PENDING, APPROVED, PAID, REJECTED, CANCELLED = "draft", "pending_approval", "approved", "paid", "rejected", "cancelled"
    STATUS_CHOICES = [(DRAFT, "Draft"), (PENDING, "Pending approval"), (APPROVED, "Approved & posted"), (PAID, "Paid"),
                      (REJECTED, "Rejected"), (CANCELLED, "Cancelled")]

    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="payroll_runs")
    reference = models.CharField(max_length=30, unique=True)
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT, db_index=True)
    total_gross = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total_tax = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total_loan = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total_net = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    paid_on = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-year", "-month"]
        constraints = [models.UniqueConstraint(fields=["company", "year", "month"], condition=~models.Q(status__in=["cancelled", "rejected"]),
                                               name="one_active_payroll_per_month")]

    def __str__(self):
        return f"{self.reference} {self.year}-{self.month:02d}"


class PayrollLine(models.Model):
    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="lines")
    employee = models.ForeignKey("hr.Employee", on_delete=models.PROTECT, related_name="payroll_lines")
    basic = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    allowances = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    payable_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    unpaid_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    calendar_days = models.PositiveSmallIntegerField(default=30)
    absent_deduction = models.DecimalField(max_digits=14, decimal_places=2, default=0, help_text="Salary not paid for absent / unpaid / pro-rata days.")
    overtime_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    overtime_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    gross = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    loan_deduction = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    net = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "employee"], name="uniq_payroll_line_employee")]


class PayrollLoanDeduction(models.Model):
    line = models.ForeignKey(PayrollLine, on_delete=models.CASCADE, related_name="loan_deductions")
    loan = models.ForeignKey("hr.EmployeeLoan", on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
