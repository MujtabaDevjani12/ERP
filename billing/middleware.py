"""
Applies the subscription to every request of a signed-in company user:
  * a company that has not paid yet (new sign-up), whose trial / paid period ran out, or that the owner suspended
    -> everything except Billing, sign-out and static files redirects to the Billing page
  * a module the plan does not include -> a friendly "not in your plan" page (HTTP 403)
Super Admins of the installation and companies without a subscription (an older single-company install) are never restricted.
"""
from django.contrib import messages
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import reverse

from . import access

ALWAYS_OPEN = ("/billing/", "/accounts/login/", "/accounts/logout/", "/accounts/password-change/", "/accounts/password-reset/",
               "/accounts/reset/", "/static/", "/media/", "/plans/", "/signup/", "/admin/", "/favicon")


MESSAGES = {
    "pending": "Your account is created. Choose a plan and pay the invoice to start using the ERP.",
    "expired": "Your subscription has expired. Pay your invoice to unlock your account.",
    "suspended": "Your account is suspended. Please contact support.",
}


def app_label_for(match, view_kwargs):
    """Which app the matched URL belongs to (ERP screens carry it in the URL, the older screens in their namespace)."""
    if match is None:
        return None
    if view_kwargs and view_kwargs.get("app_label"):
        return view_kwargs["app_label"]
    if match.view_name == "dashboard:pos":
        return "sales"
    names = match.app_names or match.namespaces
    return names[0] if names else None


class SubscriptionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.subscription = None
        request.subscription_state = None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and not access.is_platform_owner(user):
            sub = access.subscription_of(getattr(request, "company", None))
            request.subscription = sub
            if sub is not None:
                request.subscription_state = sub.state()
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        sub = getattr(request, "subscription", None)
        if sub is None:
            return None
        if request.path.startswith(ALWAYS_OPEN):
            return None
        state = request.subscription_state
        if state in ("pending", "expired", "suspended"):
            if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.path.endswith(".json"):
                return JsonResponse({"ok": False, "error": "Your subscription is not active. Open Billing to pay or renew."}, status=402)
            text = MESSAGES[state]
            if state == "expired" and not sub.ever_paid:
                text = "Your free trial has ended. Choose a plan and pay to keep using the ERP."
            if state == "suspended" and sub.suspended_reason:
                text = f"Your account is suspended: {sub.suspended_reason}"
            messages.error(request, text)
            return HttpResponseRedirect(reverse("billing:home"))
        app = app_label_for(request.resolver_match, view_kwargs)
        if app and not access.module_allowed(request.company, app):
            from .modules import MODULES, module_for_app
            key = module_for_app(app)
            return render(request, "billing/module_locked.html", {"module_label": MODULES[key][0], "plan": sub.plan}, status=403)
        return None
