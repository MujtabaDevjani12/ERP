"""Public pages: plans, company sign-up, Sign in / up with Google."""
import json

from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.db import DatabaseError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.errors import ERPError
from ownerconsole.models import Plan, Subscription
from . import access, services
from .forms import SignupForm
from .google_auth import GoogleAuthError, verify_id_token

BACKEND = "rbac.backends.SuperAdminBackend"


def _public_plans():
    try:
        return list(Plan.objects.filter(is_active=True, is_public=True))
    except DatabaseError:
        return []


def _after_signup(request, sub):
    """Where a brand-new company goes: straight into the ERP if it has demo days, otherwise to Billing to pick/pay."""
    if sub.state() == "trial":
        messages.success(request, f"Welcome! Your free demo of the {sub.plan.name} plan runs until {sub.trial_ends_at:%d %b %Y}.")
        return reverse("dashboard:home")
    messages.success(request, "Your account is created. Pay your first invoice below to open your ERP. It starts as soon as the payment is confirmed.")
    return reverse("billing:home")


def plans_page(request):
    if request.user.is_authenticated:                      # signed-in people compare and change plans inside their billing page
        return redirect("billing:home")
    interval = request.GET.get("interval") if request.GET.get("interval") in ("monthly", "yearly") else "monthly"
    s = access.platform_settings()
    return render(request, "billing/public/plans.html", {"plans": _public_plans(), "interval": interval, "settings_obj": s,
                                                          "trial_days": s.trial_days, "signup_open": s.allow_public_signup})


def signup(request):
    s = access.platform_settings()
    if request.user.is_authenticated:
        return redirect("billing:home")
    plans = _public_plans()
    initial = {"interval": request.GET.get("interval", "monthly")}
    chosen = next((p for p in plans if p.slug == request.GET.get("plan", "")), None) or services.default_plan()
    if chosen:
        initial["plan"] = chosen.pk
    if request.method == "POST":
        if not s.allow_public_signup:
            messages.error(request, "Sign-up is closed right now. Please contact us.")
            return redirect("billing:plans")
        form = SignupForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                company, user, sub = services.create_company_with_owner(
                    company_name=d["company_name"], owner_name=d["owner_name"], email=d["email"], password=d["password1"],
                    phone=d["phone"], plan=d["plan"], interval=d["interval"])
            except ERPError as exc:
                form.add_error(None, str(exc))
            else:
                login(request, user, backend=BACKEND)
                return redirect(_after_signup(request, sub))
    else:
        form = SignupForm(initial=initial)
    return render(request, "billing/public/signup.html", {"form": form, "plans": plans, "settings_obj": s, "trial_days": s.trial_days,
                                                           "google_client_id": s.google_client_id if s.google_ready else "",
                                                           "signup_open": s.allow_public_signup})


@require_POST
def google_auth(request):
    """Receives the credential from Google's button. mode=signin finds the existing account; mode=signup also creates the company."""
    s = access.platform_settings()
    try:
        data = json.loads(request.body.decode() or "{}") if request.content_type == "application/json" else request.POST
        info = verify_id_token(data.get("credential", ""), s.google_client_id)
    except (GoogleAuthError, ValueError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    User = get_user_model()
    from .models import GoogleIdentity
    identity = GoogleIdentity.objects.select_related("user").filter(sub=info["sub"]).first()
    user = identity.user if identity else User.objects.filter(email__iexact=info["email"]).first()
    mode = data.get("mode", "signin")

    if user is None:
        if mode != "signup":
            return JsonResponse({"ok": False, "error": "No account uses this Google email yet. Open the Sign up tab to create your company.",
                                 "signup_url": reverse("billing:signup")}, status=404)
        if not s.allow_public_signup:
            return JsonResponse({"ok": False, "error": "Sign-up is closed right now."}, status=403)
        plan = Plan.objects.filter(pk=data.get("plan") or 0, is_active=True, is_public=True).first() or services.default_plan()
        interval = data.get("interval") if data.get("interval") in ("monthly", "yearly") else Subscription.MONTHLY
        name = (data.get("company_name") or "").strip() or f"{info['name'] or info['email'].split('@')[0]}'s company"
        try:
            company, user, sub = services.create_company_with_owner(
                company_name=name, owner_name=info["name"] or info["email"], email=info["email"], plan=plan, interval=interval,
                google_sub=info["sub"])
        except ERPError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        login(request, user, backend=BACKEND)
        return JsonResponse({"ok": True, "redirect": _after_signup(request, sub)})

    if not user.is_active:
        return JsonResponse({"ok": False, "error": "This account is switched off. Please contact your administrator."}, status=403)
    if identity is None:
        GoogleIdentity.objects.get_or_create(user=user, defaults={"sub": info["sub"], "email": info["email"]})
    login(request, user, backend=BACKEND)
    nxt = data.get("next") or ""
    return JsonResponse({"ok": True, "redirect": nxt if nxt.startswith("/") and not nxt.startswith("//") else reverse("dashboard:home")})
