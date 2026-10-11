from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("read/", views.read, name="read_all"),
    path("read/<int:pk>/", views.read, name="read"),
]
