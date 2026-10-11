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

    # Designer (Settings > Receipt): paper, look and which parts are printed
    PAPER_CHOICES = [("a4", "A4 invoice"), ("thermal80", "Receipt roll 80 mm"), ("thermal58", "Receipt roll 58 mm")]
    STYLE_CHOICES = [("classic", "Classic"), ("modern", "Modern (coloured header)"), ("minimal", "Minimal")]
    FONT_STYLE_CHOICES = [("sans", "Sans-serif"), ("serif", "Serif"), ("mono", "Typewriter")]
    FONT_SIZE_CHOICES = [("small", "Small"), ("normal", "Normal"), ("large", "Large")]
    ALIGN_CHOICES = [("left", "Left"), ("center", "Centre")]
    paper = models.CharField(max_length=10, choices=PAPER_CHOICES, default="a4")
    style = models.CharField(max_length=10, choices=STYLE_CHOICES, default="classic")
    accent_color = models.CharField(max_length=7, blank=True, help_text="Leave empty to use the brand colour.")
    font_style = models.CharField(max_length=6, choices=FONT_STYLE_CHOICES, default="sans")
    font_size = models.CharField(max_length=6, choices=FONT_SIZE_CHOICES, default="normal")
    title_text = models.CharField(max_length=30, default="INVOICE")
    header_align = models.CharField(max_length=6, choices=ALIGN_CHOICES, default="left")
    header_note = models.CharField(max_length=255, blank=True)
    show_address = models.BooleanField(default=True)
    show_contact = models.BooleanField(default=True)
    show_tax_number = models.BooleanField(default=True)
    show_biller = models.BooleanField(default=True)
    show_warehouse = models.BooleanField(default=True)
    show_item_sku = models.BooleanField(default=False)
    show_item_discount = models.BooleanField(default=True)
    show_item_tax = models.BooleanField(default=True)
    show_payments = models.BooleanField(default=True)
    show_notes = models.BooleanField(default=True)

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
