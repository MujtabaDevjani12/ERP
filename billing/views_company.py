"""A company's own billing page: plan, usage, invoices month by month, paying, changing plan."""
import logging
from collections import OrderedDict
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.errors import ERPError
from ownerconsole import services as owner
from ownerconsole.models import BillingEvent, Invoice, Payment, Plan, Subscription
from . import access, stripe_gateway
from .forms import ManualPaymentForm

logger = logging.getLogger(__name__)


def _need(request, perm):
    if not request.user.has_perm(perm):
        raise PermissionDenied


def _own_invoice(request, pk):
    return get_object_or_404(Invoice.objects.select_related("subscription"), pk=pk, company_id=request.company.pk if request.company else 0)


@login_required
def locked(request):
    """Shown to people who cannot manage billing when the subscription has run out."""
    return render(request, "billing/company/locked.html", {"subscription": getattr(request, "subscription", None)}, status=402)


@login_required
def home(request):
    company = getattr(request, "company", None)
    sub = owner.refresh_company(company.pk) if company else None
    if sub is None:
        return render(request, "billing/company/none.html", {"company": company})
    if not request.user.has_perm("billing.view_subscription"):
        return render(request, "billing/company/locked.html", {"subscription": sub}, status=402)
    s = access.platform_settings()
    state = sub.state()
    invoices = list(sub.invoices.select_related("plan").prefetch_related("payments"))
    months = OrderedDict()
    for inv in invoices:
        key = timezone.localtime(inv.issued_at).strftime("%B %Y")
        bucket = months.setdefault(key, {"label": key, "invoices": [], "paid": Decimal("0.00"), "due": Decimal("0.00")})
        bucket["invoices"].append(inv)
        if inv.status == Invoice.PAID:
            bucket["paid"] += inv.total
        elif inv.status == Invoice.OPEN:
            bucket["due"] += inv.total
    open_invoice = next((i for i in invoices if i.status == Invoice.OPEN), None)
    pending = open_invoice.payments.filter(status=Payment.PENDING).first() if open_invoice else None
    can_pay = request.user.has_perm("billing.add_payment")
    return render(request, "billing/company/home.html", {
        "subscription": sub, "state": state, "days_left": sub.days_left(), "limits": access.limit_rows(company),
        "months": list(months.values()), "open_invoice": open_invoice, "pending_payment": pending,
        "plans": list(Plan.objects.filter(is_active=True, is_public=True)), "settings_obj": s, "payment_form": ManualPaymentForm(),
        "can_pay": can_pay, "can_change": request.user.has_perm("billing.change_subscription"),
        "events": list(BillingEvent.objects.filter(company_id=company.pk)[:8]), "total_paid": sum((i.total for i in invoices if i.status == Invoice.PAID), Decimal("0.00")),
        "module_labels": sub.plan.module_labels, "stripe_on": s.stripe_ready, "manual_on": s.manual_enabled,
        "locked": state in ("pending", "expired", "suspended"), "never_paid": not sub.ever_paid,
    })


@login_required
@require_POST
def change_plan(request):
    _need(request, "billing.change_subscription")
    sub = access.subscription_of(request.company)
    if sub is None:
        raise PermissionDenied
    plan = get_object_or_404(Plan, pk=request.POST.get("plan") or 0, is_active=True)
    interval = request.POST.get("interval", Subscription.MONTHLY)
    try:
        inv = owner.request_plan_change(sub, plan, interval, by=request.user)
    except ERPError as exc:
        messages.error(request, str(exc))
        return redirect("billing:home")
    messages.success(request, f"Invoice {inv.number} for the {plan.name} plan ({inv.get_interval_display().lower()}) is ready. Pay it to switch.")
    return redirect(reverse("billing:home") + "#pay")


@login_required
@require_POST
def cancel(request):
    _need(request, "billing.change_subscription")
    sub = access.subscription_of(request.company)
    if sub is None:
        raise PermissionDenied
    if request.POST.get("action") == "resume":
        owner.resume(sub, request.user)
        messages.success(request, "Renewal is back on.")
    else:
        owner.cancel_at_period_end(sub, request.user)
        messages.success(request, f"Your plan will not renew. You keep access until {sub.paid_until:%d %b %Y}.")
    return redirect("billing:home")


@login_required
@require_POST
def pay_manual(request, pk):
    _need(request, "billing.add_payment")
    inv = _own_invoice(request, pk)
    form = ManualPaymentForm(request.POST, request.FILES)
    if not access.platform_settings().manual_enabled:
        messages.error(request, "Manual payments are switched off.")
    elif form.is_valid():
        d = form.cleaned_data
        try:
            owner.submit_manual_payment(inv, user_name=request.user.get_username(), method=d["method"], reference=d["reference"], proof=d["proof"], note=d["note"])
            messages.success(request, "Thank you. We received your receipt and will activate your plan as soon as we confirm the payment.")
        except ERPError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect("billing:home")


@login_required
@require_POST
def pay_stripe(request, pk):
    _need(request, "billing.add_payment")
    inv = _own_invoice(request, pk)
    s = access.platform_settings()
    if not s.stripe_ready:
        messages.error(request, "Card payment is not available right now. Use bank transfer instead.")
        return redirect("billing:home")
    if inv.status != Invoice.OPEN:
        messages.info(request, "This invoice is already settled.")
        return redirect("billing:home")
    base = request.build_absolute_uri("/").rstrip("/")
    try:
        session = stripe_gateway.create_checkout_session(
            inv, secret=s.stripe_secret_key, customer_email=request.user.email,
            success_url=f"{base}{reverse('billing:stripe_return')}?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base}{reverse('billing:home')}")
    except stripe_gateway.StripeError as exc:
        messages.error(request, str(exc))
        return redirect("billing:home")
    Payment.objects.create(invoice=inv, company_id=inv.company_id, amount=inv.total, method=Payment.STRIPE, status=Payment.PENDING,
                           stripe_session_id=session["id"], submitted_by_name=request.user.get_username(), reference=session["id"][:120])
    return redirect(session["url"])


def _settle_stripe(payment, session):
    if session.get("payment_status") == "paid":
        payment.stripe_payment_intent = str(session.get("payment_intent") or "")
        payment.save(update_fields=["stripe_payment_intent", "updated_at"])
        owner.mark_invoice_paid(payment.invoice, payment=payment)
        return True
    return False


@login_required
def stripe_return(request):
    """Stripe sends the customer back here. We ask Stripe directly, so activation does not depend on the webhook arriving first."""
    sid = request.GET.get("session_id", "")
    payment = Payment.objects.filter(stripe_session_id=sid, company_id=request.company.pk if request.company else 0).select_related("invoice").first()
    if payment is None:
        return HttpResponseBadRequest("Unknown payment session.")
    s = access.platform_settings()
    try:
        session = stripe_gateway.retrieve_session(sid, secret=s.stripe_secret_key)
        if _settle_stripe(payment, session):
            messages.success(request, "Payment received. Your plan is active - thank you!")
        else:
            messages.info(request, "We are waiting for the card payment to complete. This page will update shortly.")
    except stripe_gateway.StripeError as exc:
        messages.warning(request, f"We could not confirm the payment yet ({exc}). If you were charged it will activate automatically.")
    return redirect("billing:home")


@csrf_exempt
@require_POST
def stripe_webhook(request):
    s = access.platform_settings()
    try:
        event = stripe_gateway.verify_webhook(request.body, request.META.get("HTTP_STRIPE_SIGNATURE", ""), s.stripe_webhook_secret)
    except stripe_gateway.StripeError as exc:
        logger.warning("Rejected Stripe webhook: %s", exc)
        return HttpResponse(status=400)
    if event.get("type") in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        session = event["data"]["object"]
        payment = Payment.objects.filter(stripe_session_id=session.get("id", "")).select_related("invoice").first()
        if payment is not None:
            _settle_stripe(payment, session)
    elif event.get("type") == "checkout.session.async_payment_failed":
        Payment.objects.filter(stripe_session_id=event["data"]["object"].get("id", ""), status=Payment.PENDING).update(status=Payment.FAILED)
    return JsonResponse({"received": True})


@login_required
def invoice_print(request, pk):
    _need(request, "billing.view_subscription")
    inv = _own_invoice(request, pk)
    return render(request, "billing/company/invoice.html", {"invoice": inv, "company": request.company, "settings_obj": access.platform_settings(),
                                                              "payments": inv.payments.all()})
