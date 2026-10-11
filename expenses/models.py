from django.db import models

from core.models import CreatedByModel, TimeStampedModel
from payments.models import METHOD_CHOICES
from register.models import RegisterSession
from tenants.models import Store


class ExpenseCategory(TimeStampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="expense_categories")
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Expense categories"
        constraints = [models.UniqueConstraint(fields=["store", "name"], name="uniq_expense_category_per_store")]

    def __str__(self):
        return self.name


class Expense(TimeStampedModel, CreatedByModel):
    reference = models.CharField(max_length=30, unique=True)
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="expenses")
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name="expenses")
    register_session = models.ForeignKey(
        RegisterSession, on_delete=models.SET_NULL, null=True, blank=True, related_name="expenses",
        help_text="Set when paid out of the till, so register close can account for it.",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=15, choices=METHOD_CHOICES, default="cash")
    paid_to = models.CharField(max_length=150, blank=True)
    expense_date = models.DateField()
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-expense_date"]

    def __str__(self):
        return self.reference
