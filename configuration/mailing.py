"""Outgoing-mail helpers shared by invoice emails, password reset and the Settings > Email delivery test.
The SMTP host / user / password are secrets and stay in .env (see config/settings.py); only the *sender identity* is editable in the UI."""
from email.utils import formataddr

from django.conf import settings

from .models import SystemConfiguration


def sender_identity():
    """-> (from_email, reply_to list) using Settings > Email delivery, falling back to DEFAULT_FROM_EMAIL / the business name."""
    cfg = SystemConfiguration.get()
    address = cfg.email_from_address or settings.DEFAULT_FROM_EMAIL
    name = cfg.email_from_name or cfg.site_name
    return formataddr((name, address)), ([cfg.email_reply_to] if cfg.email_reply_to else [])


def smtp_status():
    """What the server is actually set up to do — never includes the password."""
    live = bool(getattr(settings, "EMAIL_HOST", ""))
    return {
        "live": live,
        "host": settings.EMAIL_HOST if live else "",
        "port": settings.EMAIL_PORT if live else "",
        "user": settings.EMAIL_HOST_USER if live else "",
        "tls": settings.EMAIL_USE_TLS if live else False,
        "has_password": bool(getattr(settings, "EMAIL_HOST_PASSWORD", "")),
    }


def whatsapp_status():
    return {
        "token": bool(getattr(settings, "WHATSAPP_ACCESS_TOKEN", "")),
        "phone_id": bool(getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "")),
    }


def whatsapp_number(raw, country_code):
    """Digits only, with the country code added when the number was typed locally (0300… -> 92300…)."""
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = (country_code or "") + digits[1:]
    return digits
