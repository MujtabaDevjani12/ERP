from django.conf import settings
from django.db import models

from core.models import TimeStampedModel

METHOD_CHOICES = [
    ("cash", "Cash"),
    ("card", "Card"),
    ("bank_transfer", "Bank transfer"),
    ("easypaisa", "Easypaisa"),
    ("jazzcash", "JazzCash"),
    ("other", "Other"),
    ("cheque", "Cheque"),
    ("gift_card", "Gift card"),
]

CASH = "cash"


class PaymentMethod(TimeStampedModel):
    """A way a customer can pay at the POS (per branch). Admin switches them on/off,
    renames and re-orders them under Settings > Payment methods.

    `code` is what is stored on Payment.method / Refund.method, so a code is never reused for something else.
    The code 'cash' is the one that moves the cash drawer (expected cash at register close)."""
    store = models.ForeignKey("tenants.Store", on_delete=models.CASCADE, related_name="payment_methods")
    code = models.CharField(max_length=15)
    name = models.CharField(max_length=60)
    requires_reference = models.BooleanField(
        default=False, help_text="Cashier must type a reference / transaction ID before the sale can be completed.")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    # (code, name, requires_reference, is_active, sort_order) — the methods every branch starts with.
    DEFAULTS = [
        ("cash", "Cash", False, True, 10),
        ("card", "Card", True, True, 20),
        ("bank_transfer", "Bank transfer", True, True, 30),
        ("easypaisa", "Easypaisa", True, True, 40),
        ("jazzcash", "JazzCash", True, True, 50),
        ("other", "Other", True, True, 60),
        ("cheque", "Cheque", True, True, 70),
        ("gift_card", "Gift card", False, True, 80),
    ]

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [models.UniqueConstraint(fields=["store", "code"], name="uniq_payment_method_code_per_store")]

    def __str__(self):
        return self.name

    @property
    def is_cash(self):
        return self.code == CASH

    @classmethod
    def ensure_defaults(cls, store):
        """Create any default method the branch is still missing (never touches existing rows)."""
        have = set(cls.objects.filter(store=store).values_list("code", flat=True))
        missing = [
            cls(store=store, code=code, name=name, requires_reference=ref, is_active=active, sort_order=order)
            for code, name, ref, active, order in cls.DEFAULTS if code not in have
        ]
        if missing:
            cls.objects.bulk_create(missing, ignore_conflicts=True)

    @classmethod
    def active_for(cls, store):
        cls.ensure_defaults(store)
        return cls.objects.filter(store=store, is_active=True)

    @classmethod
    def label_map(cls, store):
        """{code: name} for every method of the branch (active or not), falling back to the built-in labels."""
        labels = dict(METHOD_CHOICES)
        labels.update({m.code: m.name for m in cls.objects.filter(store=store)})
        return labels


class Payment(TimeStampedModel):
    """One payment line against a sale — a sale can have several (split
    payment: part cash, part card)."""
    STATUS_CHOICES = [("completed", "Completed"), ("refunded", "Refunded"), ("voided", "Voided")]

    reference = models.CharField(max_length=30, unique=True)
    sale = models.ForeignKey("sales.Sale", on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=15, choices=METHOD_CHOICES, default="cash")
    note = models.CharField(max_length=150, blank=True, help_text="Card last 4, bank ref no., cheque no., ...")
    reference_no = models.CharField(max_length=100, blank=True, help_text="Card / bank / wallet transaction ID.")
    register_session = models.ForeignKey(
        "register.RegisterSession", on_delete=models.PROTECT, null=True, blank=True, related_name="payments",
        help_text="The till session that took this money (counter and cashier follow from it).")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="completed")
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    paid_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-paid_at"]
        indexes = [
            models.Index(fields=["register_session", "method"], name="pay_session_method_idx"),
            models.Index(fields=["paid_at"], name="pay_paid_at_idx"),
        ]

    def __str__(self):
        return f"{self.reference} — {self.amount}"


class Refund(TimeStampedModel):
    reference = models.CharField(max_length=30, unique=True)
    order = models.ForeignKey("sales.Sale", on_delete=models.PROTECT, related_name="refunds")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=15, choices=METHOD_CHOICES, default="cash")
    reason = models.CharField(max_length=255, blank=True)
    register_session = models.ForeignKey(
        "register.RegisterSession", on_delete=models.PROTECT, null=True, blank=True, related_name="refunds",
        help_text="The till session the refund was paid out of (cash refunds reduce that till's expected cash).")
    processed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference
