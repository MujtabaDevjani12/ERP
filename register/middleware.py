from django.shortcuts import redirect
from django.urls import resolve, reverse

EXEMPT_PATH_PREFIXES = ("/admin/", "/static/", "/media/")

# Only these views actually require an open register — everything else
# (settings, reports, customers...) works fine without one.
GATED_URL_NAMES = {"dashboard:pos"}


class ActiveRegisterMiddleware:
    """If a cashier tries to open the POS billing screen with no register
    open, send them to 'open register' first instead of letting them bill
    against nothing."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not request.path.startswith(EXEMPT_PATH_PREFIXES):
            match = resolve(request.path_info)
            url_name = f"{match.namespace}:{match.url_name}" if match.namespace else match.url_name
            if url_name in GATED_URL_NAMES:
                from .views import get_active_session
                if not get_active_session(request):
                    return redirect(reverse("register:open"))
        return self.get_response(request)
