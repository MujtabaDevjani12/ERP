from django.urls import path

from . import views

app_name = "reports_erp"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("report/<slug:key>/", views.report, name="report"),
]
