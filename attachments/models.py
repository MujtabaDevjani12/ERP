import os
import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel


def attachment_upload_path(instance, filename):
    """Random file name on disk (original name is kept in `original_name`),
    so uploads can never overwrite each other or smuggle odd characters."""
    ext = os.path.splitext(filename)[1].lower()
    # NOTE: strftime codes are only expanded by Django for a *string*
    # upload_to, not for a callable's return value, so format the date here.
    return f"attachments/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{ext}"


class Attachment(TimeStampedModel):
    """A file attached to ANY document (vendor, purchase invoice, employee,
    QC check, production order...) through a generic relation."""
    CATEGORY_CHOICES = [
        ("vendor_document", "Vendor document"),
        ("purchase_invoice", "Purchase invoice"),
        ("employee_cnic", "Employee CNIC"),
        ("employee_contract", "Employee contract"),
        ("qc_image", "QC image"),
        ("production_document", "Production document"),
        ("customer_document", "Customer document"),
        ("other", "Other"),
    ]

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    file = models.FileField(upload_to=attachment_upload_path)
    original_name = models.CharField(max_length=255)
    category = models.CharField(max_length=25, choices=CATEGORY_CHOICES, default="other")
    size_bytes = models.PositiveIntegerField(default=0)
    description = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["content_type", "object_id"], name="attach_obj_idx")]

    def __str__(self):
        return self.original_name
