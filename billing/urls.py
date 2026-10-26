"""Public sign-up/plans pages and every company's own billing pages. Mounted at the site root."""
from django.urls import path

from . import views_company as company, views_public as public

app_name = "billing"

urlpatterns = [
    # public
    path("plans/", public.plans_page, name="plans"),
    path("signup/", public.signup, name="signup"),
    path("signup/verify/", public.signup_verify, name="signup_verify"),
    path("signup/google/", public.google_auth, name="google_auth"),
    # a company's own billing
    path("billing/", company.home, name="home"),
    path("billing/locked/", company.locked, name="locked"),
    path("billing/change-plan/", company.change_plan, name="change_plan"),
    path("billing/cancel/", company.cancel, name="cancel"),
    path("billing/invoices/<int:pk>/", company.invoice_print, name="invoice"),
    path("billing/invoices/<int:pk>/pay/manual/", company.pay_manual, name="pay_manual"),
    path("billing/invoices/<int:pk>/pay/card/", company.pay_stripe, name="pay_stripe"),
    path("billing/stripe/return/", company.stripe_return, name="stripe_return"),
    path("billing/webhooks/stripe/", company.stripe_webhook, name="stripe_webhook"),
]
