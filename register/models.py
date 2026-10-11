from django.conf import settings
from django.db import models
from django.db.models import Q, Sum

from core.models import TimeStampedModel
from tenants.models import Store, Warehouse


class Register(TimeStampedModel):
    """A physical counter / POS terminal — 'Counter 1', 'Gold Counter', 'Takeaway Till'. A store can run more
    than one at once. A counter is NOT a cash session: every time it is opened it gets a new RegisterSession."""
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="registers")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="registers")
    name = models.CharField(max_length=80)
    code = models.CharField(max_length=20, blank=True, help_text="Short code, e.g. C01. Unique within the branch.")
    default_payment_method = models.CharField(
        max_length=15, blank=True, help_text="Payment method preselected at this counter (code, e.g. cash).")
    assigned_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="assigned_registers",
        help_text="Only these users can open this counter. Leave empty to allow every cashier of the branch.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["store", "name"]
        constraints = [
            models.UniqueConstraint(fields=["store", "name"], name="uniq_register_name_per_store"),
            models.UniqueConstraint(fields=["store", "code"], condition=~Q(code=""), name="uniq_register_code_per_store"),
        ]

    def __str__(self):
        return f"{self.name} ({self.store.name})"

    def open_session_for(self, user):
        return self.sessions.filter(status="open").first()

    def can_be_used_by(self, user):
        """Counter managers (and superusers) can use any counter; everybody else only the ones assigned to them."""
        if not self.is_active:
            return False
        if user.is_superuser or user.has_perm("register.change_register"):
            return True
        if user.store_id and user.store_id != self.store_id:
            return False
        if not self.assigned_users.exists():
            return True
        return self.assigned_users.filter(pk=user.pk).exists()


class RegisterSession(TimeStampedModel):
    """One open→close shift on a register. Every sale/expense/payment made
    while it's open links back to it, so 'expected cash' can be computed
    instead of just trusted."""
    STATUS_CHOICES = [("open", "Open"), ("closed", "Closed")]

    register = models.ForeignKey(Register, on_delete=models.PROTECT, related_name="sessions")
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="opened_sessions")
    opened_at = models.DateTimeField(auto_now_add=True)
    opening_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    opening_note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=6, choices=STATUS_CHOICES, default="open")

    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="closed_sessions")
    closed_at = models.DateTimeField(null=True, blank=True)
    closing_cash_counted = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    note = models.TextField(blank=True, help_text="Closing note.")
    # Frozen copy of the closing figures, so the record stays exactly as the cashier saw it even if data changes later.
    expected_cash_at_close = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    total_sales_at_close = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    transactions_at_close = models.PositiveIntegerField(null=True, blank=True)
    closing_summary = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-opened_at"]
        indexes = [models.Index(fields=["status", "opened_at"], name="regsession_status_opened_idx")]
        constraints = [
            # a register can only have one open session at a time
            models.UniqueConstraint(fields=["register"], condition=models.Q(status="open"), name="uniq_open_session_per_register"),
        ]

    def __str__(self):
        return f"{self.register.name} — {self.opened_at:%Y-%m-%d %H:%M}"

    @property
    def number(self):
        return f"#{self.pk:03d}" if self.pk else ""

    @property
    def cash_sales_total(self):
        """Cash taken for sales in this session (change already excluded — payments are stored net of change)."""
        from payments.models import CASH, Payment
        return Payment.objects.filter(
            Q(register_session=self) | Q(register_session__isnull=True, sale__register_session=self),
            method=CASH,
        ).exclude(status="voided").aggregate(t=Sum("amount"))["t"] or 0

    @property
    def cash_expenses_total(self):
        """Cash paid out of the till for expenses (only expenses paid in cash leave the drawer)."""
        return self.expenses.filter(method="cash").aggregate(t=Sum("amount"))["t"] or 0

    @property
    def expected_cash(self):
        expected = self.opening_cash + self.cash_sales_total - self.cash_expenses_total
        # ERP POS upgrade: cash refunds and cash drops to the safe also leave the till
        from django.apps import apps
        if apps.is_installed("pos_erp"):
            from pos_erp.services import shift_cash_adjustments
            expected -= shift_cash_adjustments(self)
        else:                                                 # without the ERP POS layer: cash refunds linked to this session
            expected -= self.refunds.filter(method="cash").aggregate(t=Sum("amount"))["t"] or 0
        return expected

    @property
    def variance(self):
        if self.closing_cash_counted is None:
            return None
        expected = self.expected_cash_at_close if self.expected_cash_at_close is not None else self.expected_cash
        return self.closing_cash_counted - expected
