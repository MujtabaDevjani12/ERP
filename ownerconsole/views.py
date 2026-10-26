"""The owner console: sign-in, forgot / reset password, dashboard, companies, plans, payments, invoices, settings, e-mail, team, account."""
import csv
import logging
import mimetypes
import secrets
from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings as dj_settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from billing import bridge
from core.errors import ERPError
from . import auth, emailing, services
from .auth import owner_required
from .forms import (ChangePasswordForm, EmailSettingsForm, ForgotForm, LoginForm, PlanForm, PlatformSettingsForm, ProfileForm,
                    SetPasswordForm, SubscriptionControlForm, TeamMemberForm, TestEmailForm)
from .models import BillingEvent, EmailLog, Invoice, OwnerAccount, Payment, PlatformSettings, Plan, Subscription

STATE_FILTERS = [("pending", "Awaiting payment"), ("trial", "Free demo"), ("active", "Active"), ("grace", "Payment due"),
                 ("expired", "Expired"), ("suspended", "Suspended")]


# ---------------------------------------------------------------- helpers
def nav_for(owner):
    items = [("dashboard", "Dashboard", "owner:dashboard", "home", False), ("companies", "Companies", "owner:companies", "building", False),
             ("payments", "Payments", "owner:payments", "cash", False), ("invoices", "Invoices", "owner:invoices", "receipt", False),
             ("plans", "Plans", "owner:plans", "tag", True), ("activity", "Activity", "owner:activity", "clock", False),
             ("emails", "E-mail log", "owner:emails", "mail", False), ("team", "Team", "owner:team", "users", True),
             ("settings", "Settings", "owner:settings", "cog", True)]
    return [{"key": k, "label": label, "url": reverse(url), "icon": icon} for k, label, url, icon, admin_only in items
            if owner.is_admin or not admin_only]

logger = logging.getLogger(__name__)


def page(request, template, ctx=None, *, active="", status=200):
    owner = getattr(request, "owner", None)
    base = {"owner": owner, "active": active, "biz": PlatformSettings.peek(), "debug": dj_settings.DEBUG}
    if owner is not None:
        base["nav"] = nav_for(owner)
        base["pending_total"] = Payment.objects.filter(status=Payment.PENDING).count()
    base.update(ctx or {})
    return render(request, template, base, status=status)


def paginate(request, items, per=25):
    return Paginator(items, per).get_page(request.GET.get("page"))


def safe_next(request, default="owner:dashboard"):
    nxt = request.POST.get("next") or request.GET.get("next") or ""
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return nxt
    return reverse(default)


# ---------------------------------------------------------------- sign in / out, forgot and reset password
def login_view(request):
    if auth.current_owner(request) is not None:
        return redirect("owner:dashboard")
    form = LoginForm(request.POST or None)
    error = ""
    if request.method == "POST" and form.is_valid():
        try:
            owner = auth.authenticate(form.cleaned_data["email"], form.cleaned_data["password"])
        except auth.LoginError as exc:
            error = str(exc)
        else:
            resp = redirect(safe_next(request))
            return auth.sign_in(resp, owner, request, remember=form.cleaned_data["remember"])
    return page(request, "ownerconsole/login.html", {"form": form, "error": error, "next": request.GET.get("next", "")})


@require_POST
def logout_view(request):
    resp = redirect("owner:login")
    messages.success(request, "You are signed out.")
    return auth.sign_out(resp)


def forgot_view(request):
    form = ForgotForm(request.POST or None)
    sent = False
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        owner = OwnerAccount.objects.filter(email=email, is_active=True).first()
        if owner is not None:
            url = auth.reset_url(request, owner)
            hours = getattr(dj_settings, "OWNER_RESET_HOURS", 2)
            emailing.send_message(
                owner.email, "Reset your owner console password", "Reset your password",
                [f"Hello {owner.name},", "We received a request to reset the password of your owner console account.",
                 f"The link below works once and expires in {hours} hours. If you did not ask for it, ignore this message; your password stays the same."],
                button=("Choose a new password", url), kind="owner_reset", key=f"reset:{owner.pk}:{timezone.now():%Y%m%d%H%M}")
        sent = True                                     # same answer whether or not the address exists
    return page(request, "ownerconsole/forgot.html", {"form": form, "sent": sent, "reset_hours": getattr(dj_settings, "OWNER_RESET_HOURS", 2)})


def reset_view(request, token):
    owner = auth.read_reset_token(token, max_hours=getattr(dj_settings, "OWNER_INVITE_HOURS", 48))
    if owner is None:
        return page(request, "ownerconsole/reset.html", {"invalid": True}, status=400)
    form = SetPasswordForm(request.POST or None, owner=owner)
    if request.method == "POST" and form.is_valid():
        owner.set_password(form.cleaned_data["password1"])
        owner.failed_attempts, owner.locked_until = 0, None
        owner.save()
        emailing.send_message(owner.email, "Your owner console password was changed", "Password changed",
                              [f"Hello {owner.name},", "The password of your owner console account was just changed. If this was not you, reset it again and tell the other owners."],
                              kind="owner_password_changed")
        messages.success(request, "Your password is changed. Sign in with the new password.")
        return redirect("owner:login")
    return page(request, "ownerconsole/reset.html", {"form": form, "owner_email": owner.email})


# ---------------------------------------------------------------- dashboard
@owner_required
def dashboard(request):
    now = timezone.now()
    s = PlatformSettings.peek()
    metrics = services.platform_metrics(now)
    bars = services.revenue_by_month(12, now)
    raw_peak = max((b[2] for b in bars), default=0)
    peak = raw_peak or 1
    bars = [{"key": k, "label": lbl, "value": v, "pct": int(v * 100 / peak) if peak else 0, "now": i == len(bars) - 1} for i, (k, lbl, v) in enumerate(bars)]
    subs = list(Subscription.objects.select_related("plan"))
    expiring = sorted([x for x in subs if x.state(now, s) in ("trial", "active", "grace") and x.days_left(now) is not None and x.days_left(now) <= 7],
                      key=lambda x: x.days_left(now))[:8]
    awaiting = [x for x in subs if x.state(now, s) == "pending"][:6]
    unmanaged = bridge.companies_without_subscription([x.company_id for x in subs])
    checklist = [
        ("Business name and support contact", bool(s.support_email), "owner:settings"),
        ("ERP address (for links in e-mails)", bool(s.erp_base_url), "owner:settings"),
        ("At least one active plan", Plan.objects.filter(is_active=True).exists(), "owner:plans"),
        ("Way to receive money (bank details or Stripe)", bool((s.manual_enabled and (s.bank_details or s.jazzcash_number or s.easypaisa_number)) or s.stripe_ready), "owner:settings"),
        ("E-mail (SMTP) set up", s.smtp_ready, "owner:email_settings"),
        ("Google sign-in (optional)", s.google_ready, "owner:settings"),
    ]
    insights = services.dashboard_insights(now)
    failed_emails = EmailLog.objects.filter(status=EmailLog.FAILED, created_at__gte=now - timedelta(days=7)).count()
    # one list of "what needs me today", most urgent first
    attention = []
    if metrics["pending_payments"]:
        attention.append({"tone": "warning", "icon": "cash", "count": metrics["pending_payments"], "url": reverse("owner:payments"),
                          "text": f"payment{'s' if metrics['pending_payments'] != 1 else ''} waiting for your approval"})
    if metrics["counts"]["grace"]:
        attention.append({"tone": "danger", "icon": "alert", "count": metrics["counts"]["grace"], "url": reverse("owner:companies") + "?state=grace",
                          "text": "paying compan" + ("ies are" if metrics["counts"]["grace"] != 1 else "y is") + " overdue"})
    if expiring:
        attention.append({"tone": "warning", "icon": "clock", "count": len(expiring), "url": reverse("owner:companies"),
                          "text": f"compan{'ies' if len(expiring) != 1 else 'y'} ending within 7 days"})
    if metrics["counts"]["pending"]:
        attention.append({"tone": "info", "icon": "lock", "count": metrics["counts"]["pending"], "url": reverse("owner:companies") + "?state=pending",
                          "text": "signed up but have not paid yet"})
    if failed_emails:
        attention.append({"tone": "danger", "icon": "mail", "count": failed_emails, "url": reverse("owner:emails") + "?status=failed",
                          "text": "e-mails failed this week"})
    if unmanaged:
        attention.append({"tone": "info", "icon": "building", "count": len(unmanaged), "url": reverse("owner:companies"),
                          "text": "ERP companies have no subscription"})
    hour = timezone.localtime(now).hour
    return page(request, "ownerconsole/dashboard.html", {
        "m": metrics, "bars": bars, "peak": raw_peak, "half_peak": raw_peak / 2, "expiring": expiring, "awaiting": awaiting, "now": now,
        "checklist": checklist, "checklist_done": sum(1 for _, ok, _ in checklist if ok), "insights": insights, "attention": attention,
        "greeting": "Good morning" if hour < 12 else ("Good afternoon" if hour < 18 else "Good evening"),
        "has_revenue": any(b["value"] for b in bars),
        "donut": ", ".join(f"var(--c-{x['key']}) {a}% {b}%" for x, a, b in _donut_stops(insights["state_mix"])),
        "recent": Subscription.objects.select_related("plan").order_by("-created_at")[:6],
        "pending": Payment.objects.filter(status=Payment.PENDING).select_related("invoice__subscription")[:6],
        "unmanaged": unmanaged[:5], "unmanaged_count": len(unmanaged),
        "events": BillingEvent.objects.all()[:8], "failed_emails": failed_emails,
    }, active="dashboard")


def _donut_stops(state_mix):
    """[(row, start%, end%)] for the status donut's conic-gradient; the last slice always ends at exactly 100."""
    total = sum(x["count"] for x in state_mix) or 1
    out, acc = [], 0.0
    for i, x in enumerate(state_mix):
        end = 100.0 if i == len(state_mix) - 1 else acc + x["count"] * 100 / total
        out.append((x, round(acc, 2), round(end, 2)))
        acc = end
    return out


@owner_required(admin=True)
@require_POST
def run_cycle(request):
    t = services.run_cycle()
    messages.success(request, f"Billing check done: {t['checked']} companies checked, {t['invoices']} invoice(s) created, {t['reminders']} reminder(s) sent.")
    return redirect("owner:dashboard")


# ---------------------------------------------------------------- companies
@owner_required
def companies(request):
    q = request.GET.get("q", "").strip()
    state_filter = request.GET.get("state", "")
    s = PlatformSettings.peek()
    qs = Subscription.objects.select_related("plan")
    if q:
        qs = qs.filter(Q(company_name__icontains=q) | Q(company_email__icontains=q))
    now = timezone.now()
    rows = []
    for sub in qs.order_by("company_name"):
        state = sub.state(now, s)
        if state_filter and state != state_filter:
            continue
        rows.append({"sub": sub, "state": state, "days": sub.days_left(now)})
    page_obj = paginate(request, rows)
    counts = bridge.user_counts([r["sub"].company_id for r in page_obj.object_list])
    for r in page_obj.object_list:
        r["users"] = counts.get(r["sub"].company_id)
    unmanaged = bridge.companies_without_subscription(Subscription.objects.values_list("company_id", flat=True)) if not state_filter and not q else []
    return page(request, "ownerconsole/companies.html", {"page": page_obj, "q": q, "state_filter": state_filter, "states": STATE_FILTERS,
                                                          "unmanaged": unmanaged[:20]}, active="companies")


ADMIN_ACTIONS = {"start", "save", "extend_trial", "grant_days", "suspend", "unsuspend", "mark_paid", "void"}


def _limit_rows(sub, used):
    from billing.access import rows_from
    return rows_from(sub, used) if used else []


@owner_required
def company_detail(request, company_id):
    sub = Subscription.objects.select_related("plan").filter(company_id=company_id).first()
    info = bridge.company_info(company_id)
    if sub is None and info is None:
        raise Http404
    if request.method == "POST":
        return _company_action(request, company_id, sub, info)
    if sub is not None:
        services.sync_company_info(sub, info)
    ctx = {"company_id": company_id, "sub": sub, "info": info, "plans": Plan.objects.all(), "now": timezone.now(),
           "email": (info or {}).get("email") or (sub.company_email if sub else ""), "phone": (info or {}).get("phone") or (sub.company_phone if sub else ""),
           "name": sub.company_name if sub else (info or {}).get("name", f"Company {company_id}")}
    if sub:
        ctx.update({
            "state": sub.state(), "days_left": sub.days_left(), "limits": _limit_rows(sub, bridge.usage(company_id)),
            "invoices": sub.invoices.select_related("plan").prefetch_related("payments")[:30],
            "events": BillingEvent.objects.filter(company_id=company_id)[:15],
            "grace_end": sub.grace_end(),
            "form": SubscriptionControlForm(initial={
                "plan": sub.plan_id, "interval": sub.interval, "price_override": sub.price_override, "notes": sub.notes,
                "max_users_override": sub.max_users_override, "max_stores_override": sub.max_stores_override,
                "max_products_override": sub.max_products_override, "max_monthly_sales_override": sub.max_monthly_sales_override}),
        })
    return page(request, "ownerconsole/company_detail.html", ctx, active="companies")


def _company_action(request, company_id, sub, info):
    action = request.POST.get("action", "")
    owner = request.owner
    if action in ADMIN_ACTIONS and not owner.is_admin:
        messages.error(request, "Only a full-access owner can change a subscription.")
        return redirect("owner:company_detail", company_id=company_id)
    try:
        if action == "start":
            if sub is not None:
                raise ERPError("This company already has a subscription.")
            plan = get_object_or_404(Plan, pk=request.POST.get("plan") or 0)
            days = request.POST.get("days")
            new = services.start_subscription(
                company_id=company_id, company_name=info["name"], company_email=info["email"], company_phone=info["phone"], plan=plan,
                interval=request.POST.get("interval", "monthly"), trial_days=int(days) if (days or "").isdigit() else None, by=owner)
            if new.state() == "pending":
                services.open_invoice(new)
            messages.success(request, f"Subscription started for {new.company_name}.")
        elif sub is None:
            raise ERPError("This company has no subscription yet.")
        elif action == "save":
            form = SubscriptionControlForm(request.POST)
            if not form.is_valid():
                raise ERPError("Please correct the form: " + "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
            d = form.cleaned_data
            for f in ("max_users", "max_stores", "max_products", "max_monthly_sales"):
                setattr(sub, f"{f}_override", d[f"{f}_override"])
            sub.notes = d["notes"]
            services.owner_set_plan(sub, d["plan"], d["interval"], owner, price_override=d["price_override"])
            messages.success(request, "Subscription updated.")
        elif action == "extend_trial":
            days = int(request.POST.get("days") or 0)
            if not 1 <= days <= 365:
                raise ERPError("Enter between 1 and 365 days.")
            services.extend_trial(sub, days, owner)
            messages.success(request, f"Free access given for {days} day(s).")
        elif action == "grant_days":
            days = int(request.POST.get("days") or 0)
            if not 1 <= days <= 3650:
                raise ERPError("Enter between 1 and 3650 days.")
            services.grant_days(sub, days, owner)
            messages.success(request, f"{days} day(s) added to the paid period.")
        elif action == "suspend":
            services.suspend(sub, request.POST.get("reason", ""), owner)
            messages.success(request, "Company suspended.")
        elif action == "unsuspend":
            services.unsuspend(sub, owner)
            messages.success(request, "Company is active again.")
        elif action == "new_invoice":
            inv = services.open_invoice(sub)
            messages.success(request, f"Invoice {inv.number} is open for {sub.company_name}.")
        elif action == "mark_paid":
            inv = get_object_or_404(Invoice, pk=request.POST.get("invoice") or 0, subscription=sub)
            services.mark_invoice_paid(inv, by=owner)
            messages.success(request, f"Invoice {inv.number} marked as paid.")
        elif action == "void":
            inv = get_object_or_404(Invoice, pk=request.POST.get("invoice") or 0, subscription=sub, status=Invoice.OPEN)
            services.void_invoice(inv, owner)
            messages.success(request, f"Invoice {inv.number} cancelled.")
        else:
            raise ERPError("Unknown action.")
    except (ERPError, ValueError) as exc:
        messages.error(request, str(exc) or "Could not do that.")
    return redirect("owner:company_detail", company_id=company_id)


# ---------------------------------------------------------------- plans
@owner_required(admin=True)
def plans(request):
    rows = Plan.objects.annotate(companies=Count("subscriptions")).order_by("sort_order", "price_monthly")
    return page(request, "ownerconsole/plans.html", {"plans": rows}, active="plans")


@owner_required(admin=True)
def plan_form(request, pk=None):
    instance = get_object_or_404(Plan, pk=pk) if pk else None
    form = PlanForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        plan = form.save()
        if plan.is_featured:
            Plan.objects.exclude(pk=plan.pk).update(is_featured=False)
        messages.success(request, f"Plan “{plan.name}” saved.")
        return redirect("owner:plans")
    return page(request, "ownerconsole/plan_form.html", {"form": form, "plan": instance}, active="plans")


@owner_required(admin=True)
@require_POST
def plan_delete(request, pk):
    plan = get_object_or_404(Plan, pk=pk)
    if plan.subscriptions.exists() or plan.invoices.exists():
        plan.is_active = plan.is_public = False
        plan.save(update_fields=["is_active", "is_public", "updated_at"])
        messages.info(request, f"“{plan.name}” is in use, so it was hidden instead of deleted. Current subscribers keep it.")
    else:
        plan.delete()
        messages.success(request, "Plan deleted.")
    return redirect("owner:plans")


# ---------------------------------------------------------------- payments & invoices
@owner_required
def payments(request):
    status = request.GET.get("status", "pending")
    qs = Payment.objects.select_related("invoice__subscription", "reviewed_by")
    if status in dict(Payment.STATUSES):
        qs = qs.filter(status=status)
    return page(request, "ownerconsole/payments.html", {"page": paginate(request, qs), "status": status, "statuses": Payment.STATUSES}, active="payments")


@owner_required
@require_POST
def payment_review(request, pk):
    payment = get_object_or_404(Payment.objects.select_related("invoice__subscription"), pk=pk)
    try:
        if request.POST.get("action") == "approve":
            services.approve_payment(payment, request.owner)
            messages.success(request, f"Payment approved - {payment.invoice.subscription.company_name} is active.")
        else:
            services.reject_payment(payment, request.owner, request.POST.get("note", ""))
            messages.success(request, "Payment rejected and the company was told.")
    except ERPError as exc:
        messages.error(request, str(exc))
    return redirect(safe_next(request, "owner:payments"))


@owner_required
def payment_proof(request, pk):
    payment = get_object_or_404(Payment, pk=pk)
    if not payment.proof:
        raise Http404
    try:
        handle = payment.proof.open("rb")
    except (FileNotFoundError, ValueError) as exc:
        raise Http404 from exc
    kind = mimetypes.guess_type(payment.proof.name)[0] or "application/octet-stream"
    return FileResponse(handle, content_type=kind)


@owner_required
def invoices(request):
    status = request.GET.get("status", "")
    q = request.GET.get("q", "").strip()
    qs = Invoice.objects.select_related("subscription")
    if status in dict(Invoice.STATUSES):
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(subscription__company_name__icontains=q))
    total = sum((i.total for i in qs.filter(status=Invoice.PAID)), 0)
    return page(request, "ownerconsole/invoices.html", {"page": paginate(request, qs), "status": status, "q": q, "statuses": Invoice.STATUSES,
                                                        "paid_total": total}, active="invoices")


@owner_required
def invoice_view(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("subscription"), pk=pk)
    sub = inv.subscription
    company = SimpleNamespace(name=sub.company_name, email=sub.company_email, address="")
    return render(request, "billing/company/invoice.html", {"invoice": inv, "company": company, "settings_obj": PlatformSettings.peek(),
                                                            "payments": inv.payments.all()})


@owner_required
def activity(request):
    q = request.GET.get("q", "").strip()
    qs = BillingEvent.objects.all()
    if q:
        qs = qs.filter(Q(company_name__icontains=q) | Q(message__icontains=q) | Q(actor__icontains=q))
    return page(request, "ownerconsole/activity.html", {"page": paginate(request, qs, 40), "q": q}, active="activity")


# ---------------------------------------------------------------- CSV export
def _csv_safe(value):
    """Spreadsheet programs run cells that start with = + - @ as formulas; prefix them so a company name can never do that."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@owner_required(admin=True)
def export_csv(request, kind):
    """Download companies, invoices or payments as a spreadsheet-friendly CSV (Settings > Data and backup)."""
    now = timezone.now()
    s = PlatformSettings.peek()
    if kind == "companies":
        header = ["Company", "E-mail", "Phone", "Plan", "Billing", "Status", "Paid until / demo ends", "Days left", "Signed up"]
        rows = [[x.company_name, x.company_email, x.company_phone, x.plan.name, x.get_interval_display(), x.state(now, s),
                 f"{x.paid_until:%Y-%m-%d}" if x.paid_until else "", x.days_left(now) if x.days_left(now) is not None else "", f"{x.created_at:%Y-%m-%d}"]
                for x in Subscription.objects.select_related("plan").order_by("company_name")]
    elif kind == "invoices":
        header = ["Number", "Company", "Plan", "Billing", "Subtotal", "Tax", "Total", "Currency", "Status", "Issued", "Due", "Paid"]
        rows = [[i.number, i.subscription.company_name, i.plan_name, i.interval, i.subtotal, i.tax_amount, i.total, i.currency, i.status,
                 f"{i.issued_at:%Y-%m-%d}", f"{i.due_at:%Y-%m-%d}" if i.due_at else "", f"{i.paid_at:%Y-%m-%d}" if i.paid_at else ""]
                for i in Invoice.objects.select_related("subscription").order_by("-issued_at")]
    elif kind == "payments":
        header = ["Invoice", "Company", "Method", "Amount", "Reference", "Status", "Submitted", "Reviewed by"]
        rows = [[p.invoice.number, p.invoice.subscription.company_name, p.get_method_display(), p.amount, p.reference, p.status,
                 f"{p.created_at:%Y-%m-%d %H:%M}", p.reviewed_by.name if p.reviewed_by_id else ""]
                for p in Payment.objects.select_related("invoice__subscription", "reviewed_by").order_by("-created_at")]
    else:
        raise Http404("Unknown export")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{kind}-{now:%Y%m%d}.csv"'
    response.write("\ufeff")                                                      # lets Excel open Urdu / accented text correctly
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow([_csv_safe(c) for c in row])
    return response


# ---------------------------------------------------------------- settings and e-mail
@owner_required(admin=True)
def settings_view(request):
    obj = PlatformSettings.get()
    form = PlatformSettingsForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except DatabaseError as exc:                                  # e.g. "attempt to write a readonly database": show the reason instead of a 500 page
            logger.exception("Owner settings could not be saved")
            messages.error(request, f"The settings could not be saved: {exc}. If it says 'readonly database', give the web service's user write "
                                    "access to owner.sqlite3 and to the folder it is in (for example: sudo chown -R ubuntu:ubuntu ~/ERP).")
            return redirect("owner:settings")
        messages.success(request, "Settings saved.")
        return redirect("owner:settings")
    from core import backup
    return page(request, "ownerconsole/settings.html", {"form": form, "obj": obj, "erp_url": obj.erp_base_url or "http://127.0.0.1:8000",
                                                        "saved_copies": backup.saved_copies("owner")}, active="settings")


@owner_required(admin=True)
def email_settings(request):
    obj = PlatformSettings.get()
    form = EmailSettingsForm(request.POST or None, instance=obj)
    test = TestEmailForm(initial={"to": request.owner.email})
    if request.method == "POST":
        if request.POST.get("action") == "test":
            test = TestEmailForm(request.POST)
            if test.is_valid():
                try:
                    emailing.send_message(test.cleaned_data["to"], "Test message from the owner console", "It works",
                                          ["This message was sent with the e-mail settings saved in your owner console.",
                                           "If you can read it, invoices, reminders and password-reset links will reach your customers and your team."],
                                          kind="test", raise_errors=True)
                    messages.success(request, f"Test message sent to {test.cleaned_data['to']}." + ("" if obj.smtp_ready else " (No SMTP host is saved, so it was only printed in the server console.)"))
                except Exception as exc:                            # noqa: BLE001
                    messages.error(request, f"The message could not be sent: {exc}")
                return redirect("owner:email_settings")
        elif form.is_valid():
            form.save()
            messages.success(request, "E-mail settings saved. Send a test message to check them.")
            return redirect("owner:email_settings")
    return page(request, "ownerconsole/email_settings.html", {"form": form, "test": test, "obj": obj,
                                                              "recent": EmailLog.objects.all()[:6]}, active="settings")


# ---------------------------------------------------------------- backup & restore of the owner database
@owner_required(admin=True)
def backup_download(request):
    from core import backup
    from . import db
    data, manifest = backup.owner_backup(db.alias())
    try:
        name = backup.save_copy(data, manifest).name                  # kept on the server too: it appears in the history on the settings page
    except backup.BackupError:
        name = backup.filename_for(manifest)
    resp = HttpResponse(data, content_type="application/zip")
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp


@owner_required(admin=True)
@require_POST
def backup_import(request):
    from core import backup
    from . import db
    upload = request.FILES.get("backup_file")
    if upload is None:
        messages.error(request, "Choose the backup .zip file first.")
    elif (request.POST.get("confirm") or "").strip().upper() != "RESTORE":
        messages.error(request, "Type the word RESTORE to confirm. Nothing was changed.")
    elif not request.owner.check_password(request.POST.get("password") or ""):
        messages.error(request, "Your password is not correct. Nothing was changed.")
    else:
        try:
            backup.read_backup(upload, kind="owner")                 # a wrong file is refused before anything is touched
            upload.seek(0)
            current, manifest = backup.owner_backup(db.alias())
            safety = backup.save_safety_copy(current, manifest)
            counts = backup.restore_owner(upload, db.alias())
            messages.success(request, f"Backup restored: {sum(counts.values())} records in {len(counts)} tables. Your previous owner data was saved first as {safety.name}. "
                                      "If your own account is not in the backup you will be asked to sign in again.")
        except backup.BackupError as exc:
            messages.error(request, str(exc))
    return redirect(reverse("owner:settings") + "#backup")


@owner_required(admin=True)
@require_POST
def backup_restore_saved(request, name):
    from core import backup
    from . import db
    if not request.owner.check_password(request.POST.get("password") or ""):
        messages.error(request, "Your password is not correct. Nothing was changed.")
    else:
        try:
            path = backup.saved_copy_path(name, "owner")
            current, manifest = backup.owner_backup(db.alias())
            safety = backup.save_safety_copy(current, manifest)
            with open(path, "rb") as fh:
                counts = backup.restore_owner(fh, db.alias())
            messages.success(request, f"Backup {name} restored: {sum(counts.values())} records in {len(counts)} tables. Your previous owner data was saved first as "
                                      f"{safety.name}. If your own account is not in that backup you will be asked to sign in again.")
        except backup.BackupError as exc:
            messages.error(request, str(exc))
    return redirect(reverse("owner:settings") + "#backup")


@owner_required(admin=True)
def backup_saved(request, name):
    from core import backup
    try:
        path = backup.saved_copy_path(name, "owner")
    except backup.BackupError:
        raise Http404
    return FileResponse(open(path, "rb"), as_attachment=True, filename=path.name)


@owner_required
def email_log(request):
    status = request.GET.get("status", "")
    qs = EmailLog.objects.all()
    if status in dict(EmailLog.STATUSES):
        qs = qs.filter(status=status)
    return page(request, "ownerconsole/emails.html", {"page": paginate(request, qs, 30), "status": status, "statuses": EmailLog.STATUSES}, active="emails")


# ---------------------------------------------------------------- team and account
@owner_required(admin=True)
def team(request):
    return page(request, "ownerconsole/team.html", {"members": OwnerAccount.objects.all()}, active="team")


@owner_required(admin=True)
def team_form(request, pk=None):
    instance = get_object_or_404(OwnerAccount, pk=pk) if pk else None
    form = TeamMemberForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        member = form.save(commit=False)
        if instance is not None and instance.pk == request.owner.pk and (not member.is_active or not member.is_admin):
            messages.error(request, "You cannot switch off or downgrade your own account.")
            return redirect("owner:team")
        creating = instance is None
        if creating:
            member.set_password(secrets.token_urlsafe(24))
        member.save()
        if creating:
            url = auth.reset_url(request, member, invite=True)
            emailing.send_message(member.email, "You were added to the owner console", "Welcome to the owner console",
                                  [f"Hello {member.name},", f"{request.owner.name} added you as {member.get_role_display().lower()}.",
                                   "Choose your password with the button below. The link works once and stays valid for 48 hours."],
                                  button=("Choose my password", url), kind="owner_invite")
            messages.success(request, f"{member.name} was added and an invitation was e-mailed. " + ("" if PlatformSettings.peek().smtp_ready else "(E-mail is not set up yet: use “Copy reset link” on their row.)"))
        else:
            messages.success(request, "Saved.")
        return redirect("owner:team")
    return page(request, "ownerconsole/team_form.html", {"form": form, "member": instance}, active="team")


@owner_required(admin=True)
@require_POST
def team_reset_link(request, pk):
    member = get_object_or_404(OwnerAccount, pk=pk)
    url = auth.reset_url(request, member)
    emailing.send_message(member.email, "Reset your owner console password", "Reset your password",
                          [f"Hello {member.name},", f"{request.owner.name} asked for a password reset for your owner console account."],
                          button=("Choose a new password", url), kind="owner_reset", key=f"reset:{member.pk}:{timezone.now():%Y%m%d%H%M}")
    messages.success(request, f"A reset link was e-mailed to {member.email}.")
    messages.info(request, f"Link (valid {getattr(dj_settings, 'OWNER_INVITE_HOURS', 48)} hours, works once): {url}")
    return redirect("owner:team")


@owner_required
def account(request):
    owner = request.owner
    profile = ProfileForm(instance=owner)
    pw = ChangePasswordForm(owner=owner)
    if request.method == "POST":
        if request.POST.get("action") == "password":
            pw = ChangePasswordForm(request.POST, owner=owner)
            if pw.is_valid():
                owner.set_password(pw.cleaned_data["password1"])
                owner.save()
                resp = redirect("owner:account")
                messages.success(request, "Password changed. Other browsers were signed out.")
                return auth.sign_in(resp, owner, request, remember=False)
        else:
            profile = ProfileForm(request.POST, instance=owner)
            if profile.is_valid():
                profile.save()
                messages.success(request, "Profile saved.")
                return redirect("owner:account")
    return page(request, "ownerconsole/account.html", {"profile": profile, "pw": pw}, active="account")


def health(request):
    from django.http import JsonResponse
    try:
        Plan.objects.exists()
        return JsonResponse({"ok": True})
    except Exception:                                    # noqa: BLE001
        return JsonResponse({"ok": False}, status=503)
