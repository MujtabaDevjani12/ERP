from django.conf import settings
from django.db import models

from core.models import TimeStampedModel
from tenants.models import Store


class Device(TimeStampedModel):
    TYPE_CHOICES = [
        ("printer", "Receipt printer"), ("scanner", "Barcode scanner"), ("scale", "Weighing scale"),
    ]

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="devices")
    type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    name = models.CharField(max_length=100)
    connection_info = models.CharField(
        max_length=150, blank=True,
        help_text="IP:port for a network printer, a COM port, or a browser device name.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["store", "type", "name"]

    def __str__(self):
        return f"{self.get_type_display()} — {self.name}"


class MediaFile(TimeStampedModel):
    """The shared media library — logos, product photos, receipt
    watermarks — anything uploaded once and reused across the app."""
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="media_files")
    file = models.FileField(upload_to="library/%Y/%m/")
    caption = models.CharField(max_length=150, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.caption or self.file.name
