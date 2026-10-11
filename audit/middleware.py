"""
Stores user / IP / user-agent for the duration of the request.
Place AFTER AuthenticationMiddleware in settings.MIDDLEWARE.

By default the IP is REMOTE_ADDR. Only set AUDIT_TRUST_PROXY_HEADERS = True
in settings if the app runs behind a reverse proxy you control that sets
X-Forwarded-For — otherwise clients could spoof their logged IP.
"""
from django.conf import settings

from .context import reset_context, set_context


def client_ip(request):
    if getattr(settings, "AUDIT_TRUST_PROXY_HEADERS", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


class AuditContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and not user.is_authenticated:
            user = None
        token = set_context(
            user=user,
            ip_address=client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
        )
        try:
            return self.get_response(request)
        finally:
            reset_context(token)
