from django.conf import settings
from django.db import models

from core.errors import ERPError
from core.models import TimeStampedModel

POSTED_STATUSES = ("posted", "reversed")


class Account(TimeStampedModel):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"
    TYPE_CHOICES = [
        (ASSET, "Asset"), (LIABILITY, "Liability"), (EQUITY, "Equity"),
        (REVENUE, "Revenue"), (EXPENSE, "Expense"),
    ]

    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="accounts")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=150)
    account_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    parent = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="children")
    is_group = models.BooleanField(default=False, help_text="Group accounts only organise the tree; nothing can be posted to them.")
    system_key = models.CharField(
        max_length=40, blank=True,
        help_text="Machine name used by ERP automation (e.g. cash, ar, ap, inventory, cogs). Do not change.",
    )
    is_active = models.BooleanField(default=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["company", "code"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_account_code_per_company"),
            models.UniqueConstraint(
                fields=["company", "system_key"], condition=~models.Q(system_key=""),
                name="uniq_account_system_key_per_company",
            ),
        ]

    def __str__(self):
        return f"{self.code} {self.name}"

    @property
    def normal_balance(self) -> str:
        return "debit" if self.account_type in (self.ASSET, self.EXPENSE) else "credit"


class CostCenter(TimeStampedModel):
    TYPE_CHOICES = [
        ("company", "Company"), ("branch", "Branch"), ("department", "Department"),
        ("production_unit", "Production unit"), ("outlet", "Outlet"), ("project", "Project"),
    ]
    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="cost_centers")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=120)
    center_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default="department")
    parent = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="children")
    store = models.ForeignKey("tenants.Store", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company", "code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="uniq_costcenter_code")]

    def __str__(self):
        return f"{self.code} {self.name}"


class AccountingPeriodLock(models.Model):
    """No journal entry can be dated on or before `locked_through`."""
    company = models.OneToOneField("company.Company", on_delete=models.CASCADE, related_name="period_lock")
    locked_through = models.DateField()

    def __str__(self):
        return f"{self.company} locked through {self.locked_through}"


class JournalEntry(TimeStampedModel):
    JV, PV, RV, CONTRA, DEBIT_NOTE, CREDIT_NOTE, OPENING, SYSTEM = (
        "JV", "PV", "RV", "CONTRA", "DEBIT_NOTE", "CREDIT_NOTE", "OPENING", "SYSTEM",
    )
    VOUCHER_CHOICES = [
        (JV, "Journal voucher"), (PV, "Payment voucher"), (RV, "Receipt voucher"), (CONTRA, "Contra voucher"),
        (DEBIT_NOTE, "Debit note"), (CREDIT_NOTE, "Credit note"), (OPENING, "Opening balance"),
        (SYSTEM, "System generated"),
    ]
    DRAFT, POSTED, REVERSED = "draft", "posted", "reversed"
    STATUS_CHOICES = [(DRAFT, "Draft"), (POSTED, "Posted"), (REVERSED, "Reversed")]

    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="journal_entries")
    store = models.ForeignKey("tenants.Store", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    entry_number = models.CharField(max_length=40, blank=True)
    voucher_type = models.CharField(max_length=12, choices=VOUCHER_CHOICES, default=JV)
    date = models.DateField(db_index=True)
    narration = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT, db_index=True)
    source_type = models.CharField(max_length=40, blank=True, help_text="Which document created this entry (idempotency key part 1).")
    source_id = models.CharField(max_length=40, blank=True, help_text="Id of that document (idempotency key part 2).")
    reversal_of = models.OneToOneField("self", on_delete=models.PROTECT, null=True, blank=True, related_name="reversed_by")
    meta = models.JSONField(default=dict, blank=True)
    total_debit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total_credit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name_plural = "Journal entries"
        constraints = [
            models.UniqueConstraint(fields=["company", "entry_number"], condition=~models.Q(entry_number=""),
                                    name="uniq_journal_number_per_company"),
            models.UniqueConstraint(fields=["company", "source_type", "source_id"], condition=~models.Q(source_type=""),
                                    name="uniq_journal_source_per_company"),
        ]
        indexes = [models.Index(fields=["company", "date"], name="journal_company_date_idx")]

    def __str__(self):
        return self.entry_number or f"Draft #{self.pk}"

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            current = type(self).objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if current in POSTED_STATUSES:
                raise ERPError("A posted journal entry cannot be edited. Reverse it instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != self.DRAFT:
            raise ERPError("A posted journal entry cannot be deleted. Reverse it instead.")
        return super().delete(*args, **kwargs)


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    debit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    cost_center = models.ForeignKey(CostCenter, on_delete=models.PROTECT, null=True, blank=True, related_name="lines")
    description = models.CharField(max_length=255, blank=True)
    party_type = models.CharField(max_length=20, blank=True, help_text="customer / vendor / employee (sub-ledger)")
    party_id = models.PositiveBigIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(check=models.Q(debit__gte=0) & models.Q(credit__gte=0), name="journal_line_non_negative"),
            models.CheckConstraint(check=models.Q(debit=0) | models.Q(credit=0), name="journal_line_one_side_only"),
        ]
        indexes = [
            models.Index(fields=["account", "entry"], name="jline_account_entry_idx"),
            models.Index(fields=["party_type", "party_id"], name="jline_party_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.entry.status != JournalEntry.DRAFT:
            raise ERPError("Lines cannot be added or changed on a posted journal entry.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.entry.status != JournalEntry.DRAFT:
            raise ERPError("Lines of a posted journal entry cannot be deleted.")
        return super().delete(*args, **kwargs)


class BankAccount(TimeStampedModel):
    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="bank_accounts")
    name = models.CharField(max_length=120)
    bank_name = models.CharField(max_length=120, blank=True)
    account_number = models.CharField(max_length=60, blank=True)
    ledger_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="bank_accounts",
                                       help_text="The asset account in the chart of accounts for this bank / cash book.")
    opening_statement_balance = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company", "name"]

    def __str__(self):
        return self.name


class BankStatementLine(TimeStampedModel):
    bank_account = models.ForeignKey(BankAccount, on_delete=models.CASCADE, related_name="statement_lines")
    date = models.DateField()
    description = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=80, blank=True)
    amount = models.DecimalField(max_digits=16, decimal_places=2, help_text="+ deposit, - withdrawal")
    matched_line = models.OneToOneField(JournalLine, on_delete=models.SET_NULL, null=True, blank=True, related_name="statement_line")
    import_batch = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["bank_account", "date", "id"]

    def __str__(self):
        return f"{self.date} {self.amount}"


class FixedAsset(TimeStampedModel):
    SL, DB = "straight_line", "declining_balance"
    METHOD_CHOICES = [(SL, "Straight line"), (DB, "Declining balance")]

    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="fixed_assets")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=150)
    purchase_date = models.DateField()
    purchase_cost = models.DecimalField(max_digits=16, decimal_places=2)
    useful_life_months = models.PositiveIntegerField()
    residual_value = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default=SL)
    declining_rate_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                                                 help_text="Annual % for declining-balance.")
    accumulated_depreciation = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    last_depreciated_through = models.DateField(null=True, blank=True)
    location = models.CharField(max_length=120, blank=True)
    department = models.CharField(max_length=120, blank=True)
    cost_center = models.ForeignKey(CostCenter, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=10, default="active", choices=[("active", "Active"), ("disposed", "Disposed")])

    class Meta:
        ordering = ["company", "code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="uniq_asset_code")]

    def __str__(self):
        return f"{self.code} {self.name}"

    @property
    def current_value(self):
        return self.purchase_cost - self.accumulated_depreciation


class DepreciationEntry(models.Model):
    asset = models.ForeignKey(FixedAsset, on_delete=models.CASCADE, related_name="depreciation_entries")
    period_end = models.DateField()
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["asset", "period_end"]
        constraints = [models.UniqueConstraint(fields=["asset", "period_end"], name="uniq_depreciation_period")]


class Budget(TimeStampedModel):
    SCOPES = [("company", "Annual company"), ("branch", "Branch"), ("department", "Department"), ("cost_center", "Cost center")]
    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="budgets")
    name = models.CharField(max_length=120)
    fiscal_year = models.PositiveSmallIntegerField(help_text="Year the fiscal year STARTS in.")
    scope = models.CharField(max_length=15, choices=SCOPES, default="company")
    store = models.ForeignKey("tenants.Store", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    cost_center = models.ForeignKey(CostCenter, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company", "-fiscal_year", "name"]

    def __str__(self):
        return f"{self.name} FY{self.fiscal_year}"


class BudgetLine(models.Model):
    budget = models.ForeignKey(Budget, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    month_index = models.PositiveSmallIntegerField(help_text="1 = first month of the fiscal year ... 12.")
    amount = models.DecimalField(max_digits=16, decimal_places=2)

    class Meta:
        ordering = ["budget", "account", "month_index"]
        constraints = [models.UniqueConstraint(fields=["budget", "account", "month_index"], name="uniq_budget_line")]
