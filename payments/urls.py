from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("methods/", views.method_list, name="method_list"),
    path("methods/new/", views.method_form, name="method_add"),
    path("methods/<int:pk>/edit/", views.method_form, name="method_edit"),
    path("methods/<int:pk>/toggle/", views.method_toggle, name="method_toggle"),
]
