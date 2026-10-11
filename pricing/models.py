from django.db import models

from core.models import TimeStampedModel
from tenants.models import Store


class Tax(TimeStampedModel):
    SCOPE_CHOICES = [("sales", "Sales only"), ("purchases", "Purchases only"), ("both", "Sales & purchases")]

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="taxes")
    name = models.CharField(max_length=60)
    rate = models.DecimalField(max_digits=5, decimal_places=2, help_text="Percentage, e.g. 17 for 17% GST.")
    applies_to = models.CharField(max_length=10, choices=SCOPE_CHOICES, default="both")
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["store", "name"], name="uniq_tax_name_per_store")]

    def __str__(self):
        return f"{self.name} ({self.rate}%)"


class Discount(TimeStampedModel):
    TYPE_CHOICES = [("percent", "Percentage"), ("fixed", "Fixed amount")]

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="discounts")
    code = models.CharField(max_length=40)
    type = models.CharField(max_length=8, choices=TYPE_CHOICES, default="percent")
    value = models.DecimalField(max_digits=10, decimal_places=2)
    valid_from = models.DateField()
    valid_to = models.DateField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-valid_from"]
        constraints = [models.UniqueConstraint(fields=["store", "code"], name="uniq_discount_code_per_store")]

    def __str__(self):
        return self.code

    def is_valid_today(self) -> bool:
        from django.utils import timezone
        today = timezone.localdate()
        return self.is_active and self.valid_from <= today <= self.valid_to


class CustomerGroup(TimeStampedModel):
    """Wholesale/retail-style pricing tiers — a % adjustment applied on top
    of the product's base price (negative = discount, positive = markup)."""
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="customer_groups")
    name = models.CharField(max_length=60)
    price_adjustment_percentage = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["store", "name"], name="uniq_group_name_per_store")]

    def __str__(self):
        return f"{self.name} ({self.price_adjustment_percentage:+}%)"
