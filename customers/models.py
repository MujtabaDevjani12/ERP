from django.core.validators import RegexValidator
from django.db import models

from core.models import TimeStampedModel
from tenants.models import Store

cnic_validator = RegexValidator(
    regex=r"^\d{5}-\d{7}-\d{1}$",
    message="CNIC must look like 35201-1234567-1.",
)


class Customer(TimeStampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="customers")
    group = models.ForeignKey("pricing.CustomerGroup", on_delete=models.SET_NULL, null=True, blank=True, related_name="customers")
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    cnic = models.CharField(max_length=15, blank=True, validators=[cnic_validator], help_text="35201-1234567-1")
    address = models.CharField(max_length=255, blank=True)
    loyalty_points = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class LoyaltyTransaction(TimeStampedModel):
    TYPE_CHOICES = [("earn", "Earned"), ("redeem", "Redeemed")]

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="loyalty_transactions")
    order = models.ForeignKey("sales.Sale", on_delete=models.SET_NULL, null=True, blank=True, related_name="loyalty_transactions")
    points = models.IntegerField()
    type = models.CharField(max_length=6, choices=TYPE_CHOICES)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.customer} {self.type} {self.points}"


class GiftCard(TimeStampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="gift_cards")
    code = models.CharField(max_length=30, unique=True)
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name="gift_cards")
    initial_balance = models.DecimalField(max_digits=12, decimal_places=2)
    current_balance = models.DecimalField(max_digits=12, decimal_places=2)
    expires_on = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.code


class GiftCardTransaction(TimeStampedModel):
    """Every change of a gift card balance, so the balance can always be explained: sold/topped up, spent at the POS, refunded."""
    ISSUE, TOPUP, REDEEM, REFUND, ADJUST = "issue", "topup", "redeem", "refund", "adjust"
    KIND_CHOICES = [(ISSUE, "Issued"), (TOPUP, "Top-up"), (REDEEM, "Spent"), (REFUND, "Refunded"), (ADJUST, "Adjusted")]

    card = models.ForeignKey(GiftCard, on_delete=models.CASCADE, related_name="transactions")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2, help_text="Positive adds to the balance, negative spends it.")
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    sale = models.ForeignKey("sales.Sale", on_delete=models.SET_NULL, null=True, blank=True, related_name="gift_card_transactions")
    user = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.card.code} {self.kind} {self.amount}"
