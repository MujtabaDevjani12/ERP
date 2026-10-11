"""
Sending an invoice out — PDF render, email, WhatsApp. Every function here
logs a DocumentLog row so 'was this sent?' is always answerable from the
sale's page, even before real credentials are configured.

WIRING NEEDED before these actually deliver anything (Phase 2):
  - EMAIL_HOST / EMAIL_HOST_USER / EMAIL_HOST_PASSWORD in .env
  - WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID in .env (Meta Cloud API)
Until then, send_invoice_email uses Django's console backend (prints to
the terminal instead of actually sending) and send_whatsapp_invoice raises
a clear "not configured" error instead of failing silently.
"""
from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string

from .models import DocumentLog, EmailTemplate


def render_invoice_pdf(sale) -> bytes:
    """Render the sale's invoice HTML (templates/sales/invoice_pdf.html) to
    PDF bytes via WeasyPrint."""
    from weasyprint import HTML

    html = render_to_string("sales/invoice_pdf.html", {"sale": sale})
    return HTML(string=html, base_url=settings.BASE_DIR.as_uri()).write_pdf()


def _invoice_email_content(sale):
    """(subject, body) for the invoice email: the branch's default template (Settings > Email templates), else the standard wording."""
    from django.template import Context, Template

    template = EmailTemplate.objects.filter(store=sale.store, is_default=True).first()
    if template is None:
        return f"Invoice {sale.reference}", f"Your invoice {sale.reference} is attached."
    values = {
        "customer_name": sale.customer.name if sale.customer_id else "Customer",
        "reference": sale.reference,
        "total": f"{sale.store.currency_symbol} {sale.total_amount}",
        "store_name": sale.store.name,
    }
    context = Context(values, autoescape=False)             # plain-text email: no HTML escaping
    return Template(template.subject).render(context).strip()[:200], Template(template.body).render(context)


def send_invoice_email(sale, to_email: str) -> DocumentLog:
    from configuration.mailing import sender_identity

    log = DocumentLog.objects.create(sale=sale, channel="email", recipient=to_email, status="pending")
    try:
        pdf_bytes = render_invoice_pdf(sale)
        subject, body = _invoice_email_content(sale)
        from_email, reply_to = sender_identity()                # Settings > Email delivery
        message = EmailMessage(subject=subject, body=body, from_email=from_email, to=[to_email], reply_to=reply_to)
        message.attach(f"{sale.reference}.pdf", pdf_bytes, "application/pdf")
        message.send(fail_silently=False)
        log.status = "sent"
    except Exception as exc:  # noqa: BLE001 — logged for the user to see in Document Log
        log.status = "failed"
        log.error_message = str(exc)[:255]
    log.save(update_fields=["status", "error_message", "updated_at"])
    return log


def _whatsapp_text(cfg, sale) -> str:
    """The message from Settings > WhatsApp; falls back to the plain default if the saved text can't be filled in."""
    values = {
        "customer_name": sale.customer.name if sale.customer_id else "Customer",
        "store_name": sale.store.name,
        "reference": sale.reference,
        "total": f"{sale.store.currency_symbol} {sale.total_amount}",
    }
    try:
        return (cfg.whatsapp_message or "").format(**values) or f"Invoice {sale.reference} — total {sale.total_amount}. Thank you!"
    except (KeyError, IndexError, ValueError):
        return f"Invoice {sale.reference} — total {sale.total_amount}. Thank you!"


def send_whatsapp_invoice(sale, to_phone: str) -> DocumentLog:
    """Sends the invoice PDF as a WhatsApp document via Meta's Cloud API.
    Needs WHATSAPP_ACCESS_TOKEN + WHATSAPP_PHONE_NUMBER_ID configured."""
    log = DocumentLog.objects.create(sale=sale, channel="whatsapp", recipient=to_phone, status="pending")
    if not settings.WHATSAPP_ACCESS_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID:
        log.status = "failed"
        log.error_message = "WhatsApp not configured — set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID in .env"
        log.save(update_fields=["status", "error_message", "updated_at"])
        return log

    import requests

    from configuration.mailing import whatsapp_number
    from configuration.models import SystemConfiguration

    cfg = SystemConfiguration.get()
    to_phone = whatsapp_number(to_phone, cfg.whatsapp_country_code) or to_phone       # 0300… -> 92300… (Settings > WhatsApp)
    url = f"https://graph.facebook.com/v19.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"}
    try:
        # A real integration uploads the PDF via /media first, then sends a
        # document message referencing the returned media id. Kept as a
        # simple text notification here — swap in the media upload once
        # WhatsApp credentials are in place.
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "text",
            "text": {"body": _whatsapp_text(cfg, sale)},
        }
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        log.status = "sent"
    except Exception as exc:  # noqa: BLE001
        log.status = "failed"
        log.error_message = str(exc)[:255]
    log.save(update_fields=["status", "error_message", "updated_at"])
    return log
