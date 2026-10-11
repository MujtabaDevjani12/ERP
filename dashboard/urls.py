from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.home_view, name="home"),
    path("pos/", views.pos_view, name="pos"),
]
