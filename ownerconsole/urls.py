"""URLs of the owner console. Mounted at the root of the owner site (config/owner_urls.py)."""
from django.urls import path

from . import views as v

app_name = "owner"

urlpatterns = [
    path("login/", v.login_view, name="login"),
    path("logout/", v.logout_view, name="logout"),
    path("forgot-password/", v.forgot_view, name="forgot"),
    path("reset/<str:token>/", v.reset_view, name="reset"),
    path("health/", v.health, name="health"),

    path("", v.dashboard, name="dashboard"),
    path("run-cycle/", v.run_cycle, name="run_cycle"),
    path("companies/", v.companies, name="companies"),
    path("companies/<int:company_id>/", v.company_detail, name="company_detail"),
    path("plans/", v.plans, name="plans"),
    path("plans/add/", v.plan_form, name="plan_add"),
    path("plans/<int:pk>/", v.plan_form, name="plan_edit"),
    path("plans/<int:pk>/delete/", v.plan_delete, name="plan_delete"),
    path("payments/", v.payments, name="payments"),
    path("payments/<int:pk>/review/", v.payment_review, name="payment_review"),
    path("payments/<int:pk>/proof/", v.payment_proof, name="payment_proof"),
    path("invoices/", v.invoices, name="invoices"),
    path("invoices/<int:pk>/", v.invoice_view, name="invoice"),
    path("activity/", v.activity, name="activity"),
    path("emails/", v.email_log, name="emails"),
    path("settings/", v.settings_view, name="settings"),
    path("settings/email/", v.email_settings, name="email_settings"),
    path("backup/download/", v.backup_download, name="backup_download"),
    path("backup/import/", v.backup_import, name="backup_import"),
    path("backup/saved/<str:name>/", v.backup_saved, name="backup_saved"),
    path("export/<str:kind>.csv", v.export_csv, name="export"),
    path("team/", v.team, name="team"),
    path("team/add/", v.team_form, name="team_add"),
    path("team/<int:pk>/", v.team_form, name="team_edit"),
    path("team/<int:pk>/reset-link/", v.team_reset_link, name="team_reset_link"),
    path("account/", v.account, name="account"),
]
