"""
Sign-in for the owner console. It is deliberately independent of Django's user model, sessions and ERP login:

* accounts live in ownerconsole.OwnerAccount (owner database)
* the browser holds ONE signed, HttpOnly cookie (OWNER_COOKIE_NAME). It carries the account id, an expiry and a fingerprint of the
  password hash, so changing the password signs every other browser out
* password-reset links are signed tokens that also contain the password fingerprint, so a link works exactly once
"""
import hashlib
import time
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from .models import OwnerAccount

SESSION_SALT = "ownerconsole.session.v1"
RESET_SALT = "ownerconsole.reset.v1"
MAX_ATTEMPTS = 5
LOCK_MINUTES = 15


def cookie_name():
    return getattr(settings, "OWNER_COOKIE_NAME", "owner_auth")


def _fingerprint(owner):
    return hashlib.sha256(f"{settings.SECRET_KEY}:{owner.password}".encode()).hexdigest()[:24]


def client_ip(request):
    return request.META.get("REMOTE_ADDR") or None


# ---------------------------------------------------------------- session cookie
def _session_hours():
    """The owner's own 'sign-in lasts N hours' setting (Settings > Security), falling back to the server default."""
    from .models import PlatformSettings
    try:
        hours = PlatformSettings.peek().session_hours
    except Exception:                                        # noqa: BLE001 - never block a sign-in over a settings read
        hours = 0
    return hours or getattr(settings, "OWNER_SESSION_HOURS", 12)



def sign_in(response, owner, request, remember=False):
    hours = _session_hours()
    days = getattr(settings, "OWNER_REMEMBER_DAYS", 14)
    seconds = days * 86400 if remember else hours * 3600
    payload = {"o": owner.pk, "f": _fingerprint(owner), "x": int(time.time()) + seconds}
    response.set_cookie(cookie_name(), signing.dumps(payload, salt=SESSION_SALT, compress=True), max_age=seconds if remember else None,
                        httponly=True, samesite="Lax", secure=request.is_secure(), path="/")
    owner.last_login, owner.last_login_ip = timezone.now(), client_ip(request)
    owner.failed_attempts, owner.locked_until = 0, None
    owner.save(update_fields=["last_login", "last_login_ip", "failed_attempts", "locked_until", "updated_at"])
    return response


def sign_out(response):
    response.delete_cookie(cookie_name(), path="/")
    return response


def current_owner(request):
    """The signed-in OwnerAccount of this request, or None. Cached on the request."""
    if hasattr(request, "_owner_account"):
        return request._owner_account
    owner = None
    raw = request.COOKIES.get(cookie_name())
    if raw:
        try:
            data = signing.loads(raw, salt=SESSION_SALT, max_age=getattr(settings, "OWNER_REMEMBER_DAYS", 14) * 86400 + 60)
            if int(data.get("x", 0)) >= time.time():
                candidate = OwnerAccount.objects.filter(pk=data.get("o"), is_active=True).first()
                if candidate is not None and data.get("f") == _fingerprint(candidate):
                    owner = candidate
        except (signing.BadSignature, ValueError, TypeError):
            owner = None
    request._owner_account = owner
    return owner


# ---------------------------------------------------------------- login with throttling
class LoginError(Exception):
    pass


def authenticate(email, password):
    """Returns the OwnerAccount or raises LoginError (with a message that is safe to show)."""
    email = (email or "").strip().lower()
    owner = OwnerAccount.objects.filter(email=email).first()
    if owner is None:
        OwnerAccount(password="pbkdf2_sha256$1$x$y").check_password(password or "")      # same cost as a real check: no timing leak
        raise LoginError("The e-mail or password is not correct.")
    if owner.is_locked:
        minutes = max(1, int((owner.locked_until - timezone.now()).total_seconds() // 60) + 1)
        raise LoginError(f"Too many wrong attempts. Try again in {minutes} minute{'s' if minutes != 1 else ''} or use Forgot password.")
    if not owner.is_active:
        raise LoginError("This account is switched off. Ask the main owner to switch it on.")
    if not owner.check_password(password or ""):
        owner.failed_attempts += 1
        fields = ["failed_attempts", "updated_at"]
        if owner.failed_attempts >= MAX_ATTEMPTS:
            owner.failed_attempts = 0
            owner.locked_until = timezone.now() + timedelta(minutes=LOCK_MINUTES)
            fields += ["locked_until"]
        owner.save(update_fields=fields)
        raise LoginError("The e-mail or password is not correct.")
    return owner


# ---------------------------------------------------------------- password reset / invitation links
def make_reset_token(owner):
    return signing.dumps({"o": owner.pk, "f": _fingerprint(owner)}, salt=RESET_SALT)


def read_reset_token(token, max_hours=None):
    """The OwnerAccount a reset link belongs to, or None (bad, expired, already used, account switched off)."""
    hours = max_hours or getattr(settings, "OWNER_RESET_HOURS", 2)
    try:
        data = signing.loads(token, salt=RESET_SALT, max_age=hours * 3600)
    except signing.BadSignature:
        return None
    owner = OwnerAccount.objects.filter(pk=data.get("o"), is_active=True).first()
    if owner is None or data.get("f") != _fingerprint(owner):
        return None
    return owner


def reset_url(request, owner, invite=False):
    token = make_reset_token(owner)
    return request.build_absolute_uri(reverse("owner:reset", args=[token]))


# ---------------------------------------------------------------- view decorators
def owner_required(view=None, *, admin=False):
    """Only a signed-in owner account may open the view. admin=True also needs the full-access role."""
    def decorate(fn):
        @wraps(fn)
        def wrapper(request, *args, **kwargs):
            owner = current_owner(request)
            if owner is None:
                if request.headers.get("x-requested-with") == "XMLHttpRequest":
                    raise PermissionDenied
                target = reverse("owner:login")
                if request.method == "GET" and request.path != reverse("owner:dashboard"):
                    target += "?next=" + request.get_full_path()
                return redirect(target)
            if admin and not owner.is_admin:
                messages.error(request, "Only a full-access owner can open that page.")
                return redirect("owner:dashboard")
            request.owner = owner
            response = fn(request, *args, **kwargs)
            response["Cache-Control"] = "no-store"
            return response
        return wrapper
    return decorate(view) if view is not None else decorate
