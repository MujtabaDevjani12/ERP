"""
E-mail for the owner console and for the messages it sends to companies.

Uses the SMTP details saved in Owner console > Settings > E-mail. When no SMTP host is saved it falls back to the server's own
EMAIL_* settings (which print to the console in development), so nothing breaks before e-mail is set up.
Every message is written to EmailLog. A mail problem never stops the thing that triggered it (a payment, a sign-up ...).
"""
import logging

from django.core.mail import EmailMultiAlternatives, get_connection
from django.template.loader import render_to_string
from django.utils.html import escape

from .models import EmailLog, OwnerAccount, PlatformSettings

logger = logging.getLogger(__name__)


def connection_for(s):
    if s.email_host:
        return get_connection(backend="django.core.mail.backends.smtp.EmailBackend", host=s.email_host, port=s.email_port,
                              username=s.email_username or None, password=s.email_password or None,
                              use_tls=bool(s.email_use_tls and not s.email_use_ssl), use_ssl=bool(s.email_use_ssl), timeout=15,
                              fail_silently=False)
    return get_connection(fail_silently=False)


def from_header(s):
    from django.conf import settings
    address = s.sender_address or getattr(settings, "DEFAULT_FROM_EMAIL", "") or "noreply@localhost"
    name = (s.email_from_name or s.business_name or "").replace('"', "").replace("\n", " ").strip()
    return f'"{name}" <{address}>' if name else address


def render(title, paragraphs, *, button=None, footer="", business=""):
    """Returns (text, html) for a simple branded message. `button` = (label, url)."""
    text_lines = [title, ""] + list(paragraphs)
    if button:
        text_lines += ["", f"{button[0]}: {button[1]}"]
    if footer:
        text_lines += ["", "--", footer]
    html = render_to_string("ownerconsole/email/message.html", {
        "title": title, "paragraphs": paragraphs, "button": button, "footer": footer, "business": business})
    return "\n".join(text_lines), html


def send(to, subject, body, *, html=None, kind="", key="", company_id=None, raise_errors=False):
    """Send one message to one address. Returns the EmailLog row. With a `key`, the same key is never sent twice to the same address."""
    to = (to or "").strip()
    s = PlatformSettings.peek()
    if not to:
        return None
    if key:
        done = EmailLog.objects.filter(key=key, to_email__iexact=to, status=EmailLog.SENT).first()
        if done:
            return done
    log = EmailLog(to_email=to[:254], subject=subject[:255], body=body, kind=kind[:40], key=key[:190], company_id=company_id)
    try:
        msg = EmailMultiAlternatives(subject=subject[:255], body=body, from_email=from_header(s), to=[to],
                                     reply_to=[s.email_reply_to] if s.email_reply_to else None, connection=connection_for(s))
        if html:
            msg.attach_alternative(html, "text/html")
        msg.send()
        log.status = EmailLog.SENT
    except Exception as exc:                                       # noqa: BLE001 - mail must never break billing
        logger.warning("E-mail to %s failed: %s", to, exc)
        log.status, log.error = EmailLog.FAILED, str(exc)[:300]
        if raise_errors:
            log.save()
            raise
    log.save()
    return log


def send_message(to, subject, title, paragraphs, *, button=None, footer="", kind="", key="", company_id=None, raise_errors=False):
    s = PlatformSettings.peek()
    text, html = render(title, paragraphs, button=button, footer=footer or s.invoice_footer, business=s.business_name)
    return send(to, subject, text, html=html, kind=kind, key=key, company_id=company_id, raise_errors=raise_errors)


def owner_recipients():
    s = PlatformSettings.peek()
    if not s.email_owners:
        return []
    return list(OwnerAccount.objects.filter(is_active=True, notify_email=True).values_list("email", flat=True))


def email_owners(subject, title, paragraphs, *, button=None, kind="owner_alert", key="", company_id=None):
    """Tell every owner account that wants e-mail. Returns how many were attempted."""
    n = 0
    for address in owner_recipients():
        send_message(address, subject, title, paragraphs, button=button, kind=kind, key=f"{key}:{address}" if key else "", company_id=company_id)
        n += 1
    return n


def link(path):
    """Absolute link into the ERP (Owner console > Settings > ERP address). Empty when that is not set."""
    base = (PlatformSettings.peek().erp_base_url or "").rstrip("/")
    return f"{base}{path}" if base else ""


def safe(text):
    return escape(text)
