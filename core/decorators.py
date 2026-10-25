"""View decorators shared by the older (non-ERP) screens."""
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse


def needs_any_perm(*perms, json=False):
    """Signed-in users who hold at least one of `perms` get through (Super Admins always do through the auth backend).
    Anonymous users go to the login page (as @login_required does); everyone else gets 403 (a JSON 403 for the AJAX endpoints with json=True)."""
    def deco(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not any(user.has_perm(p) for p in perms):
                if json:
                    return JsonResponse({"ok": False, "error": "You do not have permission for this."}, status=403)
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapper
    return deco
