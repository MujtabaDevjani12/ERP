"""E-mail one-time codes (OTP) for company sign-up, Google sign-up and forgot-password.

The code is never stored: the session keeps only a keyed hash of it, the e-mail address, an expiry and a try counter (so no table and
no migration is needed, and it works with several gunicorn workers). The person must finish in the same browser they started in.

    start(request, "signup", email, payload)   -> e-mails a new 6-digit code
    resend(request, "signup")                  -> new code (cool-down + limit)
    verify(request, "signup", "123456")        -> returns the saved state (with its payload) or raises OtpError
"""
import hashlib
import hmac
import logging
import secrets
import time

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives

logger = logging.getLogger(__name__)

TTL = 10 * 60                 # a code works for 10 minutes
MAX_TRIES = 5                 # wrong guesses before the code is thrown away
RESEND_WAIT = 45              # seconds before another code can be asked for
MAX_SENDS = 5                 # codes per started flow
PER_EMAIL_PER_HOUR = 6        # codes one address can receive per hour (stops the form being used to spam someone)

WHAT = {"signup": "finish creating your account", "reset": "reset your password"}


class OtpError(Exception):
    """Something the person can read and act on."""


def _key(purpose):
    return f"otp:{purpose}"


def _digest(purpose, email, code, nonce):
    msg = f"{purpose}|{email}|{code}|{nonce}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def _new_code():
    return f"{secrets.randbelow(10 ** 6):06d}"


def mask(email):
    name, _, domain = (email or "").partition("@")
    if not domain:
        return email or ""
    return f"{name[:1]}{'*' * max(len(name) - 1, 2)}@{domain}"


# ------------------------------------------------------------------------------------------------------------------ delivery
def _deliver(email, code, purpose):
    """Send the code with the owner's e-mail settings (Owner console > Settings > E-mail), else the server's own EMAIL_* settings."""
    from billing import access
    from ownerconsole import emailing

    s = access.platform_settings()
    business = s.business_name or "ERP"
    action = WHAT.get(purpose, "continue")
    text, html = emailing.render(
        "Your verification code",
        [f"Use this code to {action}:", " ".join([code[:3], code[3:]]),
         "It works for 10 minutes and only once. If you did not ask for it, you can ignore this e-mail - nothing will change."],
        footer="Never share this code with anyone.", business=business)
    if s.sender_address:
        sender = emailing.from_header(s)
    else:
        from configuration.mailing import sender_identity
        sender = sender_identity()[0]
    try:
        msg = EmailMultiAlternatives(subject=f"{code} is your {business} verification code", body=text, from_email=sender, to=[email],
                                     connection=emailing.connection_for(s))
        msg.attach_alternative(html, "text/html")
        msg.send()
    except Exception:                                              # noqa: BLE001 - mail server down / not set up
        logger.exception("Verification code e-mail to %s could not be sent", email)
        raise OtpError("We could not send the email right now. Please check the address and try again in a few minutes.")


def _budget(email):
    key = "otp-sends:" + hashlib.sha256(email.encode()).hexdigest()[:24]
    count = cache.get(key, 0)
    if count >= PER_EMAIL_PER_HOUR:
        raise OtpError("Too many codes were requested for this address. Please try again in an hour.")
    cache.set(key, count + 1, 60 * 60)


# ------------------------------------------------------------------------------------------------------------------ the flow
def start(request, purpose, email, payload=None, *, real=True):
    """Begin a flow and e-mail the first code. real=False keeps the same screens without sending anything (an unknown address on
    "forgot password": the page looks identical so nobody can find out which addresses have an account)."""
    email = (email or "").strip().lower()
    code, nonce = _new_code(), secrets.token_hex(8)
    if real:
        _budget(email)
        _deliver(email, code, purpose)
    now = time.time()
    request.session[_key(purpose)] = {
        "email": email, "nonce": nonce, "hash": _digest(purpose, email, code if real else secrets.token_hex(8), nonce),
        "exp": now + TTL, "tries": 0, "sends": 1, "last": now, "real": real, "payload": payload or {}}
    request.session.modified = True


def peek(request, purpose):
    """The running flow, or None when there is none / it has expired."""
    state = request.session.get(_key(purpose))
    if not state:
        return None
    if state["exp"] < time.time():
        clear(request, purpose)
        return None
    return state


def clear(request, purpose):
    request.session.pop(_key(purpose), None)
    request.session.modified = True


def seconds_to_resend(state):
    return max(0, int(RESEND_WAIT - (time.time() - state["last"])))


def resend(request, purpose):
    state = peek(request, purpose)
    if state is None:
        raise OtpError("Your code has expired. Please start again.")
    wait = seconds_to_resend(state)
    if wait:
        raise OtpError(f"Please wait {wait} seconds before asking for another code.")
    if state["sends"] >= MAX_SENDS:
        raise OtpError("Too many codes were sent. Please start again in a little while.")
    code, nonce = _new_code(), secrets.token_hex(8)
    if state["real"]:
        _budget(state["email"])
        _deliver(state["email"], code, purpose)
    now = time.time()
    state.update(nonce=nonce, hash=_digest(purpose, state["email"], code if state["real"] else secrets.token_hex(8), nonce),
                 exp=now + TTL, tries=0, sends=state["sends"] + 1, last=now)
    request.session[_key(purpose)] = state
    request.session.modified = True


def verify(request, purpose, code):
    """Check what the person typed. Returns the state (email + payload) and ends the flow, or raises OtpError."""
    state = peek(request, purpose)
    if state is None:
        raise OtpError("Your code has expired. Please start again.")
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != 6:
        raise OtpError("Enter the 6-digit code from the e-mail.")
    if state["tries"] >= MAX_TRIES:
        clear(request, purpose)
        raise OtpError("Too many wrong codes. Please start again.")
    if state["real"] and hmac.compare_digest(state["hash"], _digest(purpose, state["email"], code, state["nonce"])):
        clear(request, purpose)
        return state
    state["tries"] += 1
    request.session[_key(purpose)] = state
    request.session.modified = True
    left = MAX_TRIES - state["tries"]
    if left <= 0:
        clear(request, purpose)
        raise OtpError("Too many wrong codes. Please start again.")
    raise OtpError(f"That code is not correct. {left} {'try' if left == 1 else 'tries'} left.")
