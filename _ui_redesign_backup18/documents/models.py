from django.db import models

from core.models import TimeStampedModel
from tenants.models import Store


class InvoiceTemplate(TimeStampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="invoice_templates")
    name = models.CharField(max_length=100)
    show_logo = models.BooleanField(default=True)
    footer_text = models.CharField(max_length=255, blank=True)
    terms_and_conditions = models.TextField(blank=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_default", "name"]

    def __str__(self):
        return self.name


class EmailTemplate(TimeStampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="email_templates")
    name = models.CharField(max_length=100)
    subject = models.CharField(max_length=200, default="Your invoice {{ reference }}")
    body = models.TextField(
        default="Hi {{ customer_name }},\n\nThanks for shopping with {{ store_name }}. "
        "Your invoice {{ reference }} for {{ total }} is attached.\n\n{{ store_name }}",
        help_text="Placeholders: {{ customer_name }}, {{ reference }}, {{ total }}, {{ store_name }}",
    )
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_default", "name"]

    def __str__(self):
        return self.name


class DocumentLog(TimeStampedModel):
    """Audit trail of every invoice sent out, whichever channel."""
    CHANNEL_CHOICES = [("email", "Email"), ("whatsapp", "WhatsApp"), ("pdf_download", "PDF download")]
    STATUS_CHOICES = [("sent", "Sent"), ("failed", "Failed"), ("pending", "Pending")]

    sale = models.ForeignKey("sales.Sale", on_delete=models.CASCADE, related_name="document_logs")
    channel = models.CharField(max_length=15, choices=CHANNEL_CHOICES)
    recipient = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    error_message = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_channel_display()} → {self.recipient} ({self.status})"
