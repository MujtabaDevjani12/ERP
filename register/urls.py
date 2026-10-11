from django.urls import path

from . import views

app_name = "register"

urlpatterns = [
    path("open/", views.register_open_view, name="open"),
    path("close/", views.register_close_view, name="close"),
    path("summary/", views.register_summary_api, name="summary"),
    path("sessions/", views.RegisterSessionListView.as_view(), name="session_list"),
    path("sessions/<int:pk>/", views.register_session_detail, name="session_detail"),
    path("counters/", views.counter_list, name="counter_list"),
    path("counters/new/", views.counter_form, name="counter_add"),
    path("counters/<int:pk>/edit/", views.counter_form, name="counter_edit"),
    path("counters/<int:pk>/toggle/", views.counter_toggle, name="counter_toggle"),
]
