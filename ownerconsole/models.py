"""
The owner's own database (alias "owner", file owner.sqlite3). Nothing here has a foreign key into the ERP database:
a company is only an integer `company_id` plus a snapshot of its name / e-mail, so the two databases never depend on each other.

OwnerAccount      the people who may sign in to the owner console (own e-mail + password, nothing to do with ERP users)
PlatformSettings  one row: business details, trial / grace / reminders, Stripe, bank / JazzCash / Easypaisa, Google, SMTP e-mail
Plan              what a company gets for the money: modules + limits + monthly / yearly price
Subscription      one per company: plan, interval, trial and paid-period dates, status
Invoice           one per billing period (month-wise history on the company's billing page)
Payment           an attempt to pay an invoice (Stripe, or bank / JazzCash / Easypaisa proof that an owner approves)
BillingEvent      short audit trail of what happened to a company's subscription
EmailLog          every e-mail the console sent (or failed to send)
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.hashers import check_password as _check_password, make_password
from django.db import models
from django.utils import timezone

from billing.modules import MODULES
from core.models import TimeStampedModel

ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------------------------------------- owner accounts
class OwnerAccount(TimeStampedModel):
    ADMIN, SUPPORT = "admin", "support"
    ROLES = [(ADMIN, "Owner - full access"), (SUPPORT, "Support - companies, payments and invoices only")]

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=30, blank=True)
    role = models.CharField(max_length=10, choices=ROLES, default=ADMIN)
    password = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)
    notify_email = models.BooleanField(default=True, help_text="E-mail me when a company signs up or sends a payment.")
    last_login = models.DateTimeField(null=True, blank=True)
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    password_changed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name", "email"]

    def __str__(self):
        return f"{self.name} <{self.email}>"

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)

    # ---- passwords (Django's hashers; no ERP user table involved)
    def set_password(self, raw):
        self.password = make_password(raw)
        self.password_changed_at = timezone.now()

    def check_password(self, raw):
        return bool(self.password) and _check_password(raw, self.password)

    @property
    def is_admin(self):
        return self.role == self.ADMIN

    @property
    def is_locked(self):
        return bool(self.locked_until and self.locked_until > timezone.now())

    @property
    def initials(self):
        parts = (self.name or self.email).split()
        return "".join(p[0] for p in parts[:2]).upper() or "O"


# ---------------------------------------------------------------------------------------------------------- settings
class PlatformSettings(models.Model):
    """The single settings row (pk=1). Use PlatformSettings.get() to edit, PlatformSettings.peek() to read without writing."""
    business_name = models.CharField(max_length=150, default="Multiple POS", help_text="Shown on invoices, e-mails and plan pages.")
    support_email = models.EmailField(blank=True)
    support_phone = models.CharField(max_length=40, blank=True)
    invoice_footer = models.CharField(max_length=255, blank=True)
    erp_base_url = models.CharField(max_length=200, blank=True, help_text="Where your customers open the ERP, e.g. https://erp.yourdomain.com. Used for links in e-mails.")

    trial_days = models.PositiveSmallIntegerField(default=0, help_text="Free demo days a new company gets automatically. 0 = nobody can use the ERP before paying (you can still give demo days to one company).")
    grace_days = models.PositiveSmallIntegerField(default=5, help_text="Days a PAYING company keeps working after its period ended while renewal is pending.")
    reminder_days = models.CharField(max_length=40, default="7,3,1", help_text="Remind the company this many days before the trial / period ends (comma separated).")
    renewal_invoice_days = models.PositiveSmallIntegerField(default=7, help_text="Create the next invoice this many days before the current period ends.")
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO, help_text="Added on top of the plan price (0 = no tax).")
    allow_public_signup = models.BooleanField(default=True, help_text="Let new companies create their own account from the ERP sign-up page.")

    stripe_enabled = models.BooleanField(default=False)
    stripe_publishable_key = models.CharField(max_length=200, blank=True)
    stripe_secret_key = models.CharField(max_length=200, blank=True)
    stripe_webhook_secret = models.CharField(max_length=200, blank=True)

    manual_enabled = models.BooleanField(default=True, help_text="Companies can pay by bank transfer / JazzCash / Easypaisa and upload the receipt.")
    bank_details = models.TextField(blank=True, help_text="Bank name, account title, account number / IBAN.")
    jazzcash_number = models.CharField(max_length=40, blank=True)
    easypaisa_number = models.CharField(max_length=40, blank=True)
    payment_instructions = models.TextField(blank=True, help_text="Anything else the company should know before paying.")

    invoice_prefix = models.CharField(max_length=10, default="SUB", help_text="Invoices are numbered PREFIX-YEAR-00001, for example SUB-2026-00001.")
    payment_due_days = models.PositiveSmallIntegerField(default=0, help_text="A new or plan-change invoice is due this many days after it is issued (0 = due immediately).")
    welcome_note = models.TextField(blank=True, help_text="Optional line added to the welcome e-mail a new company receives (support hours, WhatsApp number...).")
    terms_url = models.URLField(blank=True, help_text="Link to your Terms of service. Shown on the ERP sign-up page.")
    privacy_url = models.URLField(blank=True, help_text="Link to your Privacy policy. Shown on the ERP sign-up page.")

    ANNOUNCE_LEVELS = [("info", "Information"), ("warning", "Warning"), ("danger", "Important / urgent")]
    announcement_enabled = models.BooleanField(default=False, help_text="Show the message below as a banner on top of every page inside every company's ERP.")
    announcement_text = models.CharField(max_length=300, blank=True, help_text="For example: Planned maintenance tonight 11pm-12am. Please save your work.")
    announcement_level = models.CharField(max_length=10, choices=ANNOUNCE_LEVELS, default="info")

    session_hours = models.PositiveSmallIntegerField(default=12, help_text="How long an owner-console sign-in lasts without 'Remember me' (1-72 hours).")

    google_client_id = models.CharField(max_length=255, blank=True, help_text="OAuth 2.0 Web client ID from Google Cloud Console (enables Sign in / Sign up with Google on the ERP).")
    google_client_secret = models.CharField(max_length=255, blank=True, help_text="OAuth client secret. Kept private; the Google button itself only needs the Client ID.")

    # ---- e-mail (SMTP). Empty host = use the server's own EMAIL_* settings (prints to the console in development).
    email_host = models.CharField(max_length=150, blank=True, help_text="SMTP server, e.g. smtp.gmail.com")
    email_port = models.PositiveIntegerField(default=587)
    email_username = models.CharField(max_length=150, blank=True)
    email_password = models.CharField(max_length=200, blank=True, help_text="Stored in the owner database only. For Gmail use an App password.")
    email_use_tls = models.BooleanField(default=True, help_text="STARTTLS (port 587).")
    email_use_ssl = models.BooleanField(default=False, help_text="SSL from the start (port 465). Do not tick both.")
    email_from_name = models.CharField(max_length=100, blank=True)
    email_from_address = models.EmailField(blank=True, help_text="Leave empty to use the SMTP username.")
    email_reply_to = models.EmailField(blank=True)
    send_company_emails = models.BooleanField(default=True, help_text="E-mail companies about invoices, payments, reminders and suspension.")
    email_owners = models.BooleanField(default=True, help_text="E-mail owner accounts when a company signs up or sends a payment.")

    class Meta:
        verbose_name = "Platform settings"
        verbose_name_plural = "Platform settings"

    def __str__(self):
        return "Platform settings"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @classmethod
    def peek(cls):
        """Settings without writing anything: the saved row, or an unsaved default one."""
        return cls.objects.filter(pk=1).first() or cls(pk=1)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @property
    def reminder_list(self):
        days = []
        for part in (self.reminder_days or "").split(","):
            part = part.strip()
            if part.isdigit():
                days.append(int(part))
        return sorted(set(days), reverse=True)

    @property
    def stripe_ready(self):
        return bool(self.stripe_enabled and self.stripe_secret_key)

    @property
    def google_ready(self):
        return bool(self.google_client_id)

    @property
    def smtp_ready(self):
        return bool(self.email_host)

    @property
    def sender_address(self):
        return self.email_from_address or self.email_username or ""


# ---------------------------------------------------------------------------------------------------------- plans
class Plan(TimeStampedModel):
    name = models.CharField(max_length=80)
    slug = models.SlugField(unique=True)
    tagline = models.CharField(max_length=160, blank=True)
    description = models.TextField(blank=True)
    price_monthly = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    price_yearly = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO, help_text="Price for 12 months (usually cheaper than 12 x monthly).")
    currency = models.CharField(max_length=6, default="PKR")
    modules = models.JSONField(default=list, blank=True, help_text="Module keys this plan includes.")
    max_users = models.PositiveIntegerField(default=0, help_text="0 = unlimited")
    max_stores = models.PositiveIntegerField(default=0, help_text="Branches. 0 = unlimited")
    max_products = models.PositiveIntegerField(default=0, help_text="0 = unlimited")
    max_monthly_sales = models.PositiveIntegerField(default=0, help_text="POS / invoice records per month. 0 = unlimited")
    features = models.TextField(blank=True, help_text="Extra selling points, one per line (shown on the plan card).")
    trial_days = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Free demo days for this plan. Leave empty to use the platform default.")
    is_active = models.BooleanField(default=True, help_text="Inactive plans cannot be bought, existing subscribers keep it.")
    is_public = models.BooleanField(default=True, help_text="Show on the public plans page.")
    is_featured = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=10)

    class Meta:
        ordering = ["sort_order", "price_monthly", "name"]

    def __str__(self):
        return self.name

    def price_for(self, interval):
        return self.price_yearly if interval == Subscription.YEARLY else self.price_monthly

    @property
    def yearly_saving_percent(self):
        full = self.price_monthly * 12
        if full <= 0 or self.price_yearly <= 0 or self.price_yearly >= full:
            return 0
        return int(((full - self.price_yearly) / full * 100).quantize(Decimal("1")))

    @property
    def yearly_per_month(self):
        return (self.price_yearly / 12).quantize(Decimal("0.01")) if self.price_yearly else ZERO

    @property
    def module_keys(self):
        return [m for m in (self.modules or []) if m in MODULES]

    @property
    def module_labels(self):
        return [MODULES[m][0] for m in self.module_keys]

    @property
    def feature_lines(self):
        return [ln.strip() for ln in (self.features or "").splitlines() if ln.strip()]

    def limit_lines(self):
        def fmt(n, word):
            return f"Unlimited {word}" if not n else f"Up to {n:,} {word}"
        return [fmt(self.max_users, "users"), fmt(self.max_stores, "branches"), fmt(self.max_products, "products"),
                fmt(self.max_monthly_sales, "sales / invoices per month")]


# ---------------------------------------------------------------------------------------------------------- subscriptions
class Subscription(TimeStampedModel):
    MONTHLY, YEARLY = "monthly", "yearly"
    INTERVALS = [(MONTHLY, "Monthly"), (YEARLY, "Yearly")]
    PENDING, TRIALING, ACTIVE, PAST_DUE, EXPIRED, SUSPENDED, CANCELLED = (
        "pending", "trialing", "active", "past_due", "expired", "suspended", "cancelled")
    STATUSES = [(PENDING, "Awaiting first payment"), (TRIALING, "Free trial"), (ACTIVE, "Active"), (PAST_DUE, "Payment due"),
                (EXPIRED, "Expired"), (SUSPENDED, "Suspended"), (CANCELLED, "Cancelled")]

    company_id = models.PositiveIntegerField(unique=True, help_text="Id of the company in the ERP database.")
    company_name = models.CharField(max_length=200)
    company_email = models.EmailField(blank=True)
    company_phone = models.CharField(max_length=40, blank=True)

    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    interval = models.CharField(max_length=10, choices=INTERVALS, default=MONTHLY)
    status = models.CharField(max_length=12, choices=STATUSES, default=PENDING, db_index=True)
    started_at = models.DateTimeField(default=timezone.now)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    suspended_reason = models.CharField(max_length=200, blank=True)

    # custom deal for one company (leave empty to follow the plan)
    price_override = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text="Special price per period for this company.")
    max_users_override = models.PositiveIntegerField(null=True, blank=True)
    max_stores_override = models.PositiveIntegerField(null=True, blank=True)
    max_products_override = models.PositiveIntegerField(null=True, blank=True)
    max_monthly_sales_override = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True, help_text="Private notes for the owner.")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.company_name} - {self.plan}"

    # ---- limits (0 = unlimited)
    def limit(self, key):
        override = getattr(self, f"max_{key}_override")
        return override if override is not None else getattr(self.plan, f"max_{key}")

    def price(self):
        return self.price_override if self.price_override is not None else self.plan.price_for(self.interval)

    # ---- dates / state
    @property
    def paid_until(self):
        """When access ends (before grace): the end of the paid period, or the end of the free trial when nothing was ever paid."""
        return self.current_period_end or self.trial_ends_at

    def grace_end(self, settings_obj=None):
        if not self.current_period_end:
            return None
        s = settings_obj or PlatformSettings.peek()
        return self.current_period_end + timedelta(days=s.grace_days)

    @property
    def ever_paid(self):
        return self.current_period_end is not None

    def state(self, now=None, settings_obj=None):
        """What the company can do right now.

        'pending'   account created, nothing paid, no demo days -> locked until the first payment
        'trial'     free demo days given by the owner / platform default
        'active'    paid period running
        'grace'     a PAID period just ended; still works for `grace_days` while the renewal is pending
        'expired'   locked (trial ended unpaid, or grace over)
        'suspended' locked by the owner
        """
        now = now or timezone.now()
        if self.status == self.SUSPENDED:
            return "suspended"
        if self.current_period_end:
            if now <= self.current_period_end:
                return "active"
            s = settings_obj or PlatformSettings.peek()
            return "grace" if now <= self.current_period_end + timedelta(days=s.grace_days) else "expired"
        if self.trial_ends_at:
            return "trial" if now <= self.trial_ends_at else "expired"
        return "pending"

    def has_access(self, now=None):
        return self.state(now) in ("trial", "active", "grace")

    def days_left(self, now=None):
        """Whole days until access ends (negative once past); None when nothing was ever granted."""
        now = now or timezone.now()
        end = self.paid_until
        if end is None:
            return None
        return (end - now).days if end >= now else -((now - end).days + (1 if (now - end).seconds else 0))

    @property
    def label(self):
        return dict(self.STATUSES).get(self.status, self.status)


class Invoice(TimeStampedModel):
    OPEN, PAID, VOID = "open", "paid", "void"
    STATUSES = [(OPEN, "Unpaid"), (PAID, "Paid"), (VOID, "Void")]
    FIRST, RENEWAL, CHANGE = "first", "renewal", "plan_change"
    KINDS = [(FIRST, "First payment"), (RENEWAL, "Renewal"), (CHANGE, "Plan change")]

    number = models.CharField(max_length=30, unique=True)
    company_id = models.PositiveIntegerField(db_index=True)
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="invoices")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="invoices")
    plan_name = models.CharField(max_length=80)
    interval = models.CharField(max_length=10, choices=Subscription.INTERVALS)
    kind = models.CharField(max_length=12, choices=KINDS, default=RENEWAL)
    period_start = models.DateTimeField(null=True, blank=True, help_text="Filled in when the invoice is paid.")
    period_end = models.DateTimeField(null=True, blank=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    currency = models.CharField(max_length=6, default="PKR")
    status = models.CharField(max_length=6, choices=STATUSES, default=OPEN, db_index=True)
    issued_at = models.DateTimeField(default=timezone.now)
    due_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-issued_at", "-id"]

    def __str__(self):
        return self.number

    @property
    def company_name(self):
        return self.subscription.company_name

    @property
    def is_overdue(self):
        return self.status == self.OPEN and self.due_at is not None and self.due_at < timezone.now()

    @property
    def month_key(self):
        return timezone.localtime(self.issued_at).strftime("%Y-%m")


def proof_path(instance, filename):
    return f"billing_proofs/{timezone.now():%Y/%m}/{filename}"


class Payment(TimeStampedModel):
    STRIPE, BANK, JAZZCASH, EASYPAISA, CASH, OTHER = "stripe", "bank", "jazzcash", "easypaisa", "cash", "other"
    METHODS = [(STRIPE, "Card (Stripe)"), (BANK, "Bank transfer"), (JAZZCASH, "JazzCash"), (EASYPAISA, "Easypaisa"),
               (CASH, "Cash"), (OTHER, "Other")]
    PENDING, SUCCEEDED, REJECTED, FAILED = "pending", "succeeded", "rejected", "failed"
    STATUSES = [(PENDING, "Waiting"), (SUCCEEDED, "Received"), (REJECTED, "Rejected"), (FAILED, "Failed")]

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    company_id = models.PositiveIntegerField(db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=10, choices=METHODS)
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING, db_index=True)
    reference = models.CharField(max_length=120, blank=True, help_text="Transaction / receipt number.")
    proof = models.FileField(upload_to=proof_path, blank=True, null=True)
    note = models.CharField(max_length=255, blank=True)
    stripe_session_id = models.CharField(max_length=200, blank=True, db_index=True)
    stripe_payment_intent = models.CharField(max_length=200, blank=True)
    submitted_by_name = models.CharField(max_length=150, blank=True, help_text="ERP user who sent it.")
    reviewed_by = models.ForeignKey(OwnerAccount, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_payments")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.invoice.number} {self.get_method_display()} {self.amount}"

    @property
    def company_name(self):
        return self.invoice.subscription.company_name


class BillingEvent(models.Model):
    company_id = models.PositiveIntegerField(db_index=True)
    company_name = models.CharField(max_length=200, blank=True)
    kind = models.CharField(max_length=40)
    message = models.CharField(max_length=255)
    actor = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.kind}: {self.message}"


class EmailLog(models.Model):
    SENT, FAILED, SKIPPED = "sent", "failed", "skipped"
    STATUSES = [(SENT, "Sent"), (FAILED, "Failed"), (SKIPPED, "Not sent")]

    to_email = models.CharField(max_length=254)
    subject = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    kind = models.CharField(max_length=40, blank=True)
    key = models.CharField(max_length=190, blank=True, db_index=True)
    status = models.CharField(max_length=8, choices=STATUSES, default=SENT, db_index=True)
    error = models.CharField(max_length=300, blank=True)
    company_id = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.status}: {self.subject} -> {self.to_email}"
