"""
POS upgrade layer. The existing POS tables are untouched; this app adds:
  * PosSettings      - the switch for accounting postings (ON by default: every POS sale lands in Journal entries)
  * PosReturn        - links a legacy SaleReturn to the till session, refund and approval
  * CashDrop         - cash moved from the till to the safe during a shift
  * PosPostingIssue  - any sale/payment that could not be posted, to retry later (the sale itself is never blocked)
"""
from django.conf import settings
from django.db import models

from core.models import CreatedByModel, TimeStampedModel


class PosSettings(TimeStampedModel):
    company = models.OneToOneField("company.Company", on_delete=models.CASCADE, related_name="pos_settings")
    post_to_accounting = models.BooleanField(
        default=True, help_text="When ON, every POS sale, payment, return and shift close also posts accounting entries.")
    return_approval_threshold = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, help_text="Returns above this amount need a user with approval rights. 0 = no limit.")
    safe_account = models.ForeignKey("accounting.Account", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
                                     help_text="Optional 'Cash in safe' account. Cash drops move money from Cash in Hand into it.")

    class Meta:
        verbose_name = "POS settings"
        verbose_name_plural = "POS settings"

    def __str__(self):
        return f"POS settings of {self.company}"


class PosReturn(TimeStampedModel):
    sale_return = models.OneToOneField("sales.SaleReturn", on_delete=models.CASCADE, related_name="erp")
    register_session = models.ForeignKey("register.RegisterSession", on_delete=models.PROTECT, null=True, blank=True, related_name="pos_returns")
    refund = models.OneToOneField("payments.Refund", on_delete=models.SET_NULL, null=True, blank=True, related_name="erp")
    net_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    refund_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    refund_method = models.CharField(max_length=15, blank=True)
    restocked = models.BooleanField(default=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    def __str__(self):
        return f"POS return {self.sale_return}"


class CashDrop(TimeStampedModel, CreatedByModel):
    register_session = models.ForeignKey("register.RegisterSession", on_delete=models.PROTECT, related_name="cash_drops")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.CheckConstraint(check=models.Q(amount__gt=0), name="cash_drop_amount_positive")]

    def __str__(self):
        return f"Cash drop {self.amount}"


class PosPostingIssue(TimeStampedModel):
    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="+")
    doc_type = models.CharField(max_length=20)
    doc_id = models.PositiveIntegerField()
    reference = models.CharField(max_length=40, blank=True)
    error = models.TextField()
    resolved = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.doc_type} {self.reference}: {self.error[:60]}"
