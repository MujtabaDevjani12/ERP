from django.contrib.auth import views as auth_views
from django.urls import path

from . import auth_views as signin, views

app_name = "accounts"

urlpatterns = [
    path("login/", signin.SignInView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path(
        "password-change/",
        auth_views.PasswordChangeView.as_view(template_name="registration/password_change.html", success_url="/"),
        name="password_change",
    ),

    # Forgot password (emails a one-time link; mail settings: Settings > Email delivery + EMAIL_* in .env)
    path("password-reset/", signin.ResetRequestView.as_view(), name="password_reset"),
    path("password-reset/sent/", signin.ResetDoneView.as_view(), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", signin.ResetConfirmView.as_view(), name="password_reset_confirm"),
    path("reset/done/", signin.ResetCompleteView.as_view(), name="password_reset_complete"),

    # Roles & permissions — an in-app page over django.contrib.auth Groups.
    path("roles/", views.role_list, name="role_list"),
    path("roles/add/", views.role_form, name="role_add"),
    path("roles/<int:pk>/edit/", views.role_form, name="role_edit"),
    path("roles/<int:pk>/delete/", views.role_delete, name="role_delete"),

    path("profile/", views.profile, name="profile"),
    path("billers/", views.biller_list, name="biller_list"),
    path("billers/<int:pk>/default/", views.biller_default, name="biller_default"),

    # Staff users.
    path("users/", views.staff_user_list, name="staff_user_list"),
    path("users/add/", views.staff_user_form, name="staff_user_add"),
    path("users/<int:pk>/edit/", views.staff_user_form, name="staff_user_edit"),
    path("users/<int:pk>/delete/", views.staff_user_delete, name="staff_user_delete"),
]
