from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .models import AuditLog
from .services import log_action


@receiver(user_logged_in)
def _on_login(sender, request, user, **kwargs):
    log_action(AuditLog.LOGIN, user, user=user)


@receiver(user_logged_out)
def _on_logout(sender, request, user, **kwargs):
    if user is not None:
        log_action(AuditLog.LOGOUT, user, user=user)


@receiver(user_login_failed)
def _on_login_failed(sender, credentials, request=None, **kwargs):
    # Never store the password; only which username was tried. The client IP
    # comes from AuditContextMiddleware's per-request context.
    attempted = str((credentials or {}).get("username", ""))[:100]
    log_action(AuditLog.LOGIN_FAILED, None, note=f"username={attempted}")
