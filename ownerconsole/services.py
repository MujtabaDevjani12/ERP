"""
Subscription business rules. Views stay thin; everything that moves money or dates lives here.

Money rules
-----------
* A company signs up -> subscription in state "pending" and the first Invoice is created. Until that invoice is paid (or the
  owner gives demo days) the company cannot use the ERP, only its Billing page.
* Paying an invoice (Stripe, or a bank / JazzCash / Easypaisa receipt an owner approves) sets the paid period:
    - same plan, still running  -> the new period starts when the current one ends (no days are lost)
    - same plan, already ended  -> starts now
    - different plan            -> starts now (the unused part of the old plan is not credited)
* Demo days (platform default or given by the owner) give the full plan without paying. Nothing is invoiced as "due" during a demo
  until its last days; when the demo ends unpaid the company is locked at once.
* After a PAID period ends the company keeps working for `grace_days`, then it is locked until it pays.
"""
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Max
from django.utils import timezone

from core.errors import ERPError
from . import db, emailing
from .models import BillingEvent, Invoice, Payment, PlatformSettings, Plan, Subscription

logger = logging.getLogger(__name__)
TWO = Decimal("0.01")


def q2(value):
    return Decimal(value).quantize(TWO, rounding=ROUND_HALF_UP)


def add_months(dt, months):
    """Same day next month(s); month-end safe (31 Jan + 1 month = 28/29 Feb)."""
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    last_day = [31, 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return dt.replace(year=year, month=month, day=min(dt.day, last_day))


def period_end(start, interval):
    return add_months(start, 12 if interval == Subscription.YEARLY else 1)


def actor_name(by):
    """Who did it, as text: an ERP user -> username, an OwnerAccount -> e-mail, a string -> itself."""
    if by is None:
        return ""
    if isinstance(by, str):
        return by[:150]
    if hasattr(by, "get_username"):
        return by.get_username()[:150]
    return (getattr(by, "email", "") or str(by))[:150]


def log_event(sub, kind, message, by=None):
    BillingEvent.objects.create(company_id=sub.company_id, company_name=sub.company_name, kind=kind, message=message[:255], actor=actor_name(by))


# ---------------------------------------------------------------- telling people
def notify_company(sub, title, body="", *, level="info", key="", link="/billing/", email=True, kind="company"):
    """In-app notification (ERP bell) for the company's billing admins + an e-mail to them. Each `key` is delivered once."""
    from billing import bridge
    sent = bridge.notify_admins(sub.company_id, title, body, level=level, link=link, key=key)
    if email:
        email_company(sub, title, body, key=key, kind=kind, link=link)
    return sent


def email_company(sub, subject, body, *, key="", kind="company", link="/billing/", button_label="Open Billing"):
    s = PlatformSettings.peek()
    if not s.send_company_emails:
        return 0
    from billing import bridge
    addresses = list(bridge.admin_emails(sub.company_id))
    if sub.company_email and sub.company_email.lower() not in addresses:
        addresses.append(sub.company_email.lower())
    url = emailing.link(link)
    n = 0
    for address in addresses:
        emailing.send_message(address, f"{s.business_name}: {subject}", subject, [body] if body else [],
                              button=(button_label, url) if url else None, kind=kind, key=f"{key}:{address}" if key else "",
                              company_id=sub.company_id)
        n += 1
    return n


def alert_owners(subject, body, *, key="", company_id=None, path=""):
    """E-mail the owner accounts (the console dashboard shows the same things as counters)."""
    from django.conf import settings
    url = (getattr(settings, "OWNER_CONSOLE_URL", "") or "").rstrip("/")
    button = ("Open owner console", url + path) if url else None
    return emailing.email_owners(subject, subject, [body], button=button, key=key, company_id=company_id)


# ---------------------------------------------------------------- plans / signing a company up
def default_plan():
    qs = Plan.objects.filter(is_active=True)
    return qs.filter(is_featured=True).first() or qs.order_by("sort_order", "price_monthly").first()


def trial_days_for(plan):
    return plan.trial_days if plan.trial_days is not None else PlatformSettings.peek().trial_days


@db.atomic()
def start_subscription(*, company_id, company_name, company_email="", company_phone="", plan, interval=Subscription.MONTHLY,
                       trial_days=None, by=None):
    """Create the subscription. 0 demo days (the default) = state "pending": nothing works until the first invoice is paid."""
    now = timezone.now()
    days = trial_days_for(plan) if trial_days is None else trial_days
    sub = Subscription.objects.create(
        company_id=company_id, company_name=company_name[:200], company_email=company_email or "", company_phone=company_phone or "",
        plan=plan, interval=interval, started_at=now,
        status=Subscription.TRIALING if days else Subscription.PENDING, trial_ends_at=now + timedelta(days=days) if days else None)
    log_event(sub, "subscription_started", f"{plan.name} {interval}" + (f", {days} demo day(s)." if days else ", waiting for the first payment."), by)
    return sub


def register_company(*, company_id, company_name, company_email="", company_phone="", plan, interval=Subscription.MONTHLY, by=None):
    """Called by the ERP sign-up: subscription + first invoice, a welcome e-mail to the company and an alert to the owners."""
    sub = start_subscription(company_id=company_id, company_name=company_name, company_email=company_email, company_phone=company_phone,
                             plan=plan, interval=interval, by=by)
    inv = open_invoice(sub)
    log_event(sub, "signup", f"Company registered. First invoice {inv.number} ({inv.total} {inv.currency}).", by)
    s = PlatformSettings.peek()
    if s.send_company_emails and company_email:
        url = emailing.link("/billing/")
        demo = sub.state() == "trial"
        lines = [f"Your {company_name} account is ready." if demo else f"Your {company_name} account has been created.",
                 (f"You can try the {plan.name} plan free until {sub.trial_ends_at:%d %b %Y}." if demo
                  else f"To start using the ERP, pay your first invoice {inv.number} ({inv.total} {inv.currency}) from the Billing page. "
                       "Your account opens as soon as the payment is confirmed.")]
        if s.welcome_note.strip():
            lines.append(s.welcome_note.strip())
        emailing.send_message(company_email, f"Welcome to {s.business_name}", f"Welcome, {company_name}", lines,
                              button=("Open Billing", url) if url else None, kind="welcome", key=f"welcome:{company_id}", company_id=company_id)
    alert_owners(f"New company: {company_name}", f"{company_name} ({company_email or 'no e-mail'}) signed up for {plan.name} ({interval}).",
                 key=f"signup:{company_id}", company_id=company_id, path=f"/companies/{company_id}/")
    return sub


def sync_company_info(sub, info):
    """Keep the stored name / e-mail / phone in step with the ERP (best effort, `info` comes from billing.bridge)."""
    if not info:
        return sub
    changed = []
    for field, value in (("company_name", info["name"][:200]), ("company_email", info["email"]), ("company_phone", info["phone"][:40])):
        if value and getattr(sub, field) != value:
            setattr(sub, field, value)
            changed.append(field)
    if changed:
        sub.save(update_fields=changed + ["updated_at"])
    return sub


# ---------------------------------------------------------------- invoices
def next_invoice_number(now=None):
    now = now or timezone.now()
    prefix = f"{PlatformSettings.peek().invoice_prefix or 'SUB'}-{now.year}-"
    last = Invoice.objects.filter(number__startswith=prefix).aggregate(m=Max("number"))["m"]
    seq = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{seq:05d}"


def invoice_amounts(sub, plan, interval):
    s = PlatformSettings.peek()
    if plan.pk == sub.plan_id and interval == sub.interval and sub.price_override is not None:
        subtotal = q2(sub.price_override)
    else:
        subtotal = q2(plan.price_for(interval))
    tax = q2(subtotal * s.tax_percent / 100)
    return subtotal, tax, subtotal + tax


@db.atomic()
def create_invoice(sub, plan=None, interval=None, kind=Invoice.RENEWAL, due_at=None):
    plan = plan or sub.plan
    interval = interval or sub.interval
    subtotal, tax, total = invoice_amounts(sub, plan, interval)
    return Invoice.objects.create(
        number=next_invoice_number(), company_id=sub.company_id, subscription=sub, plan=plan, plan_name=plan.name, interval=interval, kind=kind,
        subtotal=subtotal, tax_amount=tax, total=total, currency=plan.currency, due_at=due_at or sub.paid_until or timezone.now() + timedelta(days=PlatformSettings.peek().payment_due_days))


def open_invoice(sub, plan=None, interval=None, kind=None):
    """The unpaid invoice for this plan/interval (re-used so repeated clicks do not pile up invoices), or a new one."""
    plan = plan or sub.plan
    interval = interval or sub.interval
    existing = sub.invoices.filter(status=Invoice.OPEN, plan=plan, interval=interval).order_by("-issued_at").first()
    if existing:
        return existing
    if kind is None:
        if not sub.invoices.filter(status=Invoice.PAID).exists():
            kind = Invoice.FIRST
        elif plan.pk != sub.plan_id or interval != sub.interval:
            kind = Invoice.CHANGE
        else:
            kind = Invoice.RENEWAL
    return create_invoice(sub, plan, interval, kind)


def mark_invoice_paid(invoice, *, by=None, payment=None, when=None):
    """Settle an invoice and move the subscription forward. Safe to call twice (the second call does nothing)."""
    with db.atomic():
        invoice = Invoice.objects.select_for_update().select_related("subscription", "plan").get(pk=invoice.pk)
        if invoice.status == Invoice.PAID:
            return invoice
        if invoice.status == Invoice.VOID:
            raise ERPError("This invoice was cancelled.")
        now = when or timezone.now()
        sub = Subscription.objects.select_for_update().select_related("plan").get(pk=invoice.subscription_id)
        same_plan = sub.plan_id == invoice.plan_id and sub.interval == invoice.interval
        start = sub.current_period_end if (same_plan and sub.current_period_end and sub.current_period_end > now) else now
        end = period_end(start, invoice.interval)

        invoice.status, invoice.paid_at, invoice.period_start, invoice.period_end = Invoice.PAID, now, start, end
        invoice.save(update_fields=["status", "paid_at", "period_start", "period_end", "updated_at"])

        sub.plan, sub.interval = invoice.plan, invoice.interval
        if not same_plan:
            sub.price_override = None                   # a special price belonged to the old plan
        sub.current_period_start, sub.current_period_end = start, end
        sub.status = Subscription.ACTIVE
        sub.cancel_at_period_end, sub.cancelled_at, sub.suspended_reason = False, None, ""
        sub.save()

        if payment is not None and payment.status != Payment.SUCCEEDED:
            payment.status = Payment.SUCCEEDED
            payment.save(update_fields=["status", "updated_at"])
        log_event(sub, "payment", f"Invoice {invoice.number} paid ({invoice.total} {invoice.currency}); active until {end:%d %b %Y}.", by)
    notify_company(sub, "Payment received - thank you",
                   f"Invoice {invoice.number} is paid. Your {invoice.plan_name} plan is active until {end:%d %b %Y}.",
                   key=f"paid:{invoice.pk}", kind="payment_received")
    return invoice


def submit_manual_payment(invoice, *, user_name="", method, reference="", proof=None, note=""):
    invoice.refresh_from_db(fields=["status"])                       # never trust a copy that may be stale
    if invoice.status != Invoice.OPEN:
        raise ERPError("This invoice is not waiting for payment.")
    if method not in dict(Payment.METHODS) or method == Payment.STRIPE:
        raise ERPError("Choose how you paid.")
    if not reference.strip() and not proof:
        raise ERPError("Enter the transaction number or attach the receipt so we can find your payment.")
    sub = invoice.subscription
    pay = Payment.objects.create(invoice=invoice, company_id=invoice.company_id, amount=invoice.total, method=method, reference=reference.strip()[:120],
                                 proof=proof, note=note[:255], submitted_by_name=user_name[:150])
    log_event(sub, "payment_submitted", f"{invoice.number}: {pay.get_method_display()} receipt submitted.", user_name)
    alert_owners(f"Payment to review: {sub.company_name}",
                 f"{pay.get_method_display()} {invoice.total} {invoice.currency} for {invoice.number}"
                 + (f" (ref {pay.reference})." if pay.reference else "."),
                 key=f"review:{pay.pk}", company_id=sub.company_id, path="/payments/")
    return pay


def approve_payment(payment, by):
    with db.atomic():
        payment = Payment.objects.select_for_update().select_related("invoice").get(pk=payment.pk)
        if payment.status == Payment.SUCCEEDED:
            return payment
        payment.reviewed_by, payment.reviewed_at = by, timezone.now()
        payment.save(update_fields=["reviewed_by", "reviewed_at", "updated_at"])
    mark_invoice_paid(payment.invoice, by=by, payment=payment)
    payment.refresh_from_db()
    return payment


def reject_payment(payment, by, note=""):
    with db.atomic():
        payment = Payment.objects.select_for_update().select_related("invoice__subscription").get(pk=payment.pk)
        if payment.status != Payment.PENDING:
            raise ERPError("Only a waiting payment can be rejected.")
        payment.status, payment.reviewed_by, payment.reviewed_at, payment.review_note = Payment.REJECTED, by, timezone.now(), note[:255]
        payment.save()
        sub = payment.invoice.subscription
        log_event(sub, "payment_rejected", f"{payment.invoice.number}: receipt rejected. {note}", by)
    notify_company(sub, "Payment could not be verified",
                   note or f"We could not match your payment for {payment.invoice.number}. Please check the details and submit again.",
                   level="critical", key=f"rejected:{payment.pk}", kind="payment_rejected")
    return payment


# ---------------------------------------------------------------- plan changes / owner tools
def request_plan_change(sub, plan, interval, by=None):
    """The company picks another plan or interval -> an invoice for it (paid = switched)."""
    if not plan.is_active:
        raise ERPError("This plan is not available.")
    if interval not in dict(Subscription.INTERVALS):
        raise ERPError("Choose monthly or yearly.")
    sub.invoices.filter(status=Invoice.OPEN).exclude(plan=plan, interval=interval).update(status=Invoice.VOID)
    inv = open_invoice(sub, plan, interval)
    log_event(sub, "plan_requested", f"Asked for {plan.name} ({interval}). Invoice {inv.number}.", by)
    return inv


@db.atomic()
def owner_set_plan(sub, plan, interval, by=None, price_override=None):
    """The owner switches a company's plan right away, no invoice (a gift, a correction or an offline deal)."""
    sub.plan, sub.interval = plan, interval
    sub.price_override = price_override
    sub.save()
    log_event(sub, "plan_set", f"Plan set to {plan.name} ({interval}) by the owner.", by)
    return sub


@db.atomic()
def extend_trial(sub, days, by=None):
    """Free demo days. On a company that never paid this opens the ERP; on a paying company the days go on top of the paid period."""
    if sub.current_period_end:
        return grant_days(sub, days, by)
    now = timezone.now()
    base = sub.trial_ends_at if sub.trial_ends_at and sub.trial_ends_at > now else now
    sub.trial_ends_at = base + timedelta(days=days)
    if sub.status in (Subscription.PENDING, Subscription.EXPIRED, Subscription.PAST_DUE, Subscription.CANCELLED):
        sub.status = Subscription.TRIALING
    sub.save()
    log_event(sub, "trial_extended", f"Free access given for {days} day(s) until {sub.trial_ends_at:%d %b %Y}.", by)
    return sub


@db.atomic()
def grant_days(sub, days, by=None):
    """The owner gives paid-up days (e.g. a settled cash deal): moves the paid period forward."""
    now = timezone.now()
    end = sub.paid_until
    base = end if end and end > now else now            # a remaining free trial is kept, the days are added after it
    if not sub.current_period_start or not sub.current_period_end or sub.current_period_end <= now:
        sub.current_period_start = now
    sub.current_period_end = base + timedelta(days=days)
    sub.status = Subscription.ACTIVE
    sub.save()
    log_event(sub, "days_granted", f"{days} day(s) added; active until {sub.current_period_end:%d %b %Y}.", by)
    return sub


def suspend(sub, reason="", by=None):
    with db.atomic():
        sub.status, sub.suspended_reason = Subscription.SUSPENDED, reason[:200]
        sub.save()
        log_event(sub, "suspended", reason or "Suspended by the owner.", by)
    notify_company(sub, "Your account is suspended", reason or "Please contact support.", level="critical", key=f"suspended:{sub.pk}:{timezone.now():%Y%m%d%H%M}",
                   kind="suspended")


def unsuspend(sub, by=None):
    with db.atomic():
        sub.status, sub.suspended_reason = Subscription.ACTIVE, ""
        sub.save()
        sync_status(sub)
        log_event(sub, "unsuspended", "Access restored.", by)
    notify_company(sub, "Your account is active again", "Thank you for your patience.", key=f"unsuspended:{sub.pk}:{timezone.now():%Y%m%d%H%M}",
                   kind="unsuspended")


@db.atomic()
def cancel_at_period_end(sub, by=None):
    sub.cancel_at_period_end, sub.cancelled_at = True, timezone.now()
    sub.save(update_fields=["cancel_at_period_end", "cancelled_at", "updated_at"])
    sub.invoices.filter(status=Invoice.OPEN, kind=Invoice.RENEWAL).update(status=Invoice.VOID)
    end = sub.paid_until
    log_event(sub, "cancel_requested", f"Will not renew after {end:%d %b %Y}." if end else "Renewal switched off.", by)


@db.atomic()
def resume(sub, by=None):
    sub.cancel_at_period_end, sub.cancelled_at = False, None
    sub.save(update_fields=["cancel_at_period_end", "cancelled_at", "updated_at"])
    log_event(sub, "resumed", "Renewal switched back on.", by)


def void_invoice(invoice, by=None):
    if invoice.status != Invoice.OPEN:
        raise ERPError("Only an unpaid invoice can be cancelled.")
    invoice.status = Invoice.VOID
    invoice.save(update_fields=["status", "updated_at"])
    log_event(invoice.subscription, "invoice_void", f"{invoice.number} cancelled.", by)


# ---------------------------------------------------------------- status sync / reminders / cycle
def sync_status(sub, now=None):
    """Write the real-time state back to Subscription.status (used for filters, lists and the dashboard)."""
    state = sub.state(now)
    wanted = {"pending": Subscription.PENDING, "trial": Subscription.TRIALING, "active": Subscription.ACTIVE, "grace": Subscription.PAST_DUE,
              "expired": Subscription.EXPIRED, "suspended": Subscription.SUSPENDED}[state]
    if sub.cancel_at_period_end and state in ("grace", "expired"):
        wanted = Subscription.CANCELLED               # asked not to renew and the period is over
    if wanted != sub.status:
        sub.status = wanted
        sub.save(update_fields=["status", "updated_at"])
    return state


def _reminder(sub, now, s):
    """Create the 'N days left' / 'payment due' / 'expired' notices for one subscription (each only once). Returns how many were sent."""
    if sub.status == Subscription.SUSPENDED:
        return 0
    state = sub.state(now, s)
    today = timezone.localtime(now).date()                    # business days are local days, not UTC days
    if state == "pending":
        age = (today - timezone.localtime(sub.started_at).date()).days
        if age in (1, 3, 7, 14):
            inv = sub.invoices.filter(status=Invoice.OPEN).first()
            return notify_company(sub, "Finish setting up your account",
                                  f"Pay invoice {inv.number} from Billing to open your ERP." if inv else "Open Billing, choose a plan and pay to open your ERP.",
                                  level="warning", key=f"pending:{sub.pk}:{age}", kind="reminder")
        return 0
    end = sub.paid_until
    if end is None:
        return 0
    end_day = timezone.localtime(end).date()
    days = (end_day - today).days
    trial = state == "trial"
    what = "free trial" if trial else f"{sub.plan.name} plan"
    if sub.cancel_at_period_end and not trial:
        if 0 <= days <= max(s.reminder_list or [7]):
            return notify_company(sub, f"Your plan ends in {days} day(s)", "Renewal is switched off. Resume it from Billing to keep working.",
                                  level="warning", key=f"end:{sub.pk}:{end_day}:{days}", kind="reminder")
        return 0
    if days > 0 and days in s.reminder_list:
        return notify_company(sub, f"Your {what} ends in {days} day{'s' if days != 1 else ''}",
                              "Pay your invoice from Billing before it ends to avoid interruption.", level="warning",
                              key=f"remind:{sub.pk}:{end_day}:{days}", kind="reminder")
    if days == 0:
        return notify_company(sub, f"Your {what} ends today", "Pay now from Billing." + (" You get a short grace period after this." if not trial else ""),
                              level="critical", key=f"remind:{sub.pk}:{end_day}:0", kind="reminder")
    if days < 0:
        if state == "grace":
            left = timezone.localtime(end + timedelta(days=s.grace_days)).date() - today
            return notify_company(sub, "Payment overdue", f"Your access will be locked in {left.days} day(s) unless you pay.",
                                  level="critical", key=f"overdue:{sub.pk}:{end_day}:{today}", kind="reminder")
        if state == "expired":
            return notify_company(sub, "Your subscription has expired" if not trial else "Your free trial has ended",
                                  "Open Billing and pay to unlock your account.", level="critical", key=f"expired:{sub.pk}:{end_day}", kind="reminder")
    return 0


def refresh_subscription(sub, now=None, settings_obj=None):
    """Bring one subscription up to date: status, the next invoice, reminders. Returns a dict of what was done."""
    now = now or timezone.now()
    s = settings_obj or PlatformSettings.peek()
    done = {"invoice": False, "reminders": 0}
    state = sync_status(sub, now)
    if state == "pending" and not sub.invoices.filter(status=Invoice.OPEN).exists():
        open_invoice(sub)
        done["invoice"] = True
    elif state not in ("suspended", "pending") and not sub.cancel_at_period_end:
        end = sub.paid_until
        lead = timedelta(days=s.renewal_invoice_days)
        if end is not None and now >= end - lead and not sub.invoices.filter(status=Invoice.OPEN).exists():
            kind = Invoice.RENEWAL if sub.invoices.filter(status=Invoice.PAID).exists() else Invoice.FIRST
            create_invoice(sub, kind=kind, due_at=end)
            done["invoice"] = True
    done["reminders"] = _reminder(sub, now, s)
    return done


def refresh_company(company_id):
    """Cheap; called when a company admin opens Billing so reminders and invoices exist even without a scheduler."""
    sub = Subscription.objects.select_related("plan").filter(company_id=company_id).first()
    if sub is not None:
        try:
            refresh_subscription(sub)
        except Exception:                                # noqa: BLE001
            logger.warning("Billing refresh failed for company %s", company_id, exc_info=True)
    return sub


def run_cycle(now=None):
    """The daily job (`manage.py run_billing_cycle`). Returns counters for the log / dashboard."""
    now = now or timezone.now()
    s = PlatformSettings.peek()
    totals = {"checked": 0, "invoices": 0, "reminders": 0, "expired": 0}
    for sub in Subscription.objects.select_related("plan"):
        totals["checked"] += 1
        before = sub.status
        res = refresh_subscription(sub, now, s)
        totals["invoices"] += int(res["invoice"])
        totals["reminders"] += res["reminders"]
        if before not in (Subscription.EXPIRED, Subscription.CANCELLED) and sub.status in (Subscription.EXPIRED, Subscription.CANCELLED):
            totals["expired"] += 1
    return totals


# ---------------------------------------------------------------- owner dashboard numbers
def monthly_equivalent(sub):
    price = sub.price()
    return q2(price / 12) if sub.interval == Subscription.YEARLY else q2(price)


def platform_metrics(now=None):
    now = now or timezone.now()
    s = PlatformSettings.peek()
    subs = list(Subscription.objects.select_related("plan"))
    counts = {"pending": 0, "trial": 0, "active": 0, "grace": 0, "expired": 0, "suspended": 0}
    mrr = Decimal("0.00")
    for sub in subs:
        st = sub.state(now, s)
        counts[st] += 1
        if st in ("active", "grace") and sub.current_period_end:
            mrr += monthly_equivalent(sub)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    paid = Invoice.objects.filter(status=Invoice.PAID)
    month_revenue = sum((i.total for i in paid.filter(paid_at__gte=start)), Decimal("0.00"))
    total_revenue = sum((i.total for i in paid), Decimal("0.00"))
    outstanding = sum((i.total for i in Invoice.objects.filter(status=Invoice.OPEN)), Decimal("0.00"))
    return {
        "companies": len(subs), "counts": counts, "mrr": q2(mrr), "arr": q2(mrr * 12), "month_revenue": q2(month_revenue),
        "total_revenue": q2(total_revenue), "outstanding": q2(outstanding),
        "pending_payments": Payment.objects.filter(status=Payment.PENDING).count(),
        "locked": counts["pending"] + counts["expired"] + counts["suspended"],
        "expiring_soon": sum(1 for sub in subs if sub.state(now, s) in ("trial", "active") and sub.days_left(now) is not None and 0 <= sub.days_left(now) <= 7),
    }


def revenue_by_month(months=12, now=None):
    """[(YYYY-MM, label, total)] for the last N months, oldest first - for the dashboard bars."""
    now = timezone.localtime(now or timezone.now())
    y, m = now.year, now.month
    keys = []
    for _ in range(months):
        keys.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    keys.reverse()
    totals = {k: Decimal("0.00") for k in keys}
    first = timezone.make_aware(datetime(keys[0][0], keys[0][1], 1))
    for inv in Invoice.objects.filter(status=Invoice.PAID, paid_at__gte=first):
        d = timezone.localtime(inv.paid_at)
        if (d.year, d.month) in totals:
            totals[(d.year, d.month)] += inv.total
    return [(f"{yy}-{mm:02d}", date(yy, mm, 1).strftime("%b %y"), q2(totals[(yy, mm)])) for (yy, mm) in keys]


STATE_LABELS = [("active", "Active (paid)"), ("trial", "Free demo"), ("pending", "Awaiting payment"), ("grace", "Payment due"),
                ("expired", "Expired"), ("suspended", "Suspended")]


def dashboard_insights(now=None):
    """Extra numbers for the owner dashboard: month-on-month change, who pays what, status mix, sign-ups per week, best customers."""
    now = now or timezone.now()
    s = PlatformSettings.peek()
    subs = list(Subscription.objects.select_related("plan"))
    local = timezone.localtime(now)
    this_start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_start = (this_start - timedelta(days=1)).replace(day=1)
    paid = list(Invoice.objects.filter(status=Invoice.PAID, paid_at__gte=last_start))
    this_month = sum((i.total for i in paid if i.paid_at >= this_start), Decimal("0.00"))
    last_month = sum((i.total for i in paid if i.paid_at < this_start), Decimal("0.00"))
    delta = None if not last_month else int((this_month - last_month) * 100 / last_month)

    states = {k: 0 for k, _ in STATE_LABELS}
    plan_mix, paying = {}, 0
    for sub in subs:
        st = sub.state(now, s)
        states[st] += 1
        row = plan_mix.setdefault(sub.plan_id, {"name": sub.plan.name, "count": 0, "mrr": Decimal("0.00")})
        row["count"] += 1
        if st in ("active", "grace") and sub.current_period_end:
            row["mrr"] += monthly_equivalent(sub)
            paying += 1
    total = len(subs) or 1
    state_mix = [{"key": k, "label": label, "count": states[k], "pct": round(states[k] * 100 / total)} for k, label in STATE_LABELS if states[k]]
    mix = sorted(plan_mix.values(), key=lambda r: (-r["count"], r["name"]))
    top = max((r["count"] for r in mix), default=0) or 1
    for r in mix:
        r["pct"] = int(r["count"] * 100 / top)

    weeks = []
    for back in range(7, -1, -1):                                      # last 8 weeks, oldest first
        end = now - timedelta(days=7 * back)
        start = end - timedelta(days=7)
        weeks.append({"label": timezone.localtime(end).strftime("%d %b"),
                      "count": sum(1 for x in subs if start < x.created_at <= end)})
    peak = max((w["count"] for w in weeks), default=0) or 1
    for w in weeks:
        w["pct"] = int(w["count"] * 100 / peak)

    totals = {}
    for inv in Invoice.objects.filter(status=Invoice.PAID).select_related("subscription"):
        row = totals.setdefault(inv.company_id, {"company_id": inv.company_id, "name": inv.subscription.company_name, "total": Decimal("0.00"), "currency": inv.currency})
        row["total"] += inv.total
    best = sorted(totals.values(), key=lambda r: -r["total"])[:5]

    open_total = Invoice.objects.filter(status=Invoice.OPEN).count()
    paid_total = Invoice.objects.filter(status=Invoice.PAID).count()
    return {
        "this_month": q2(this_month), "last_month": q2(last_month), "delta": delta,
        "arpa": q2(sum((r["mrr"] for r in mix), Decimal("0.00")) / paying) if paying else Decimal("0.00"),
        "paying": paying, "new_30d": sum(1 for x in subs if x.created_at >= now - timedelta(days=30)),
        "state_mix": state_mix, "plan_mix": mix, "weeks": weeks, "best": best,
        "collection_rate": int(paid_total * 100 / (paid_total + open_total)) if (paid_total + open_total) else None,
    }
