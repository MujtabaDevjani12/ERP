"""
    from attachments.services import attach, attachments_for

    attach(vendor, request.FILES["file"], user=request.user, category="vendor_document")
    attachments_for(vendor)
"""
from django.contrib.contenttypes.models import ContentType

from .models import Attachment
from .validators import validate_attachment


def attach(obj, uploaded_file, *, user=None, category="other", description=""):
    """Validate (type / size / content) then store. Raises
    django.core.exceptions.ValidationError on a bad upload."""
    if obj is None or obj.pk is None:
        raise ValueError("Attach files only to a saved object.")
    validate_attachment(uploaded_file)
    return Attachment.objects.create(
        content_type=ContentType.objects.get_for_model(obj, for_concrete_model=False),
        object_id=obj.pk,
        file=uploaded_file,
        original_name=(uploaded_file.name or "file")[:255],
        category=category,
        size_bytes=uploaded_file.size,
        description=description[:255],
        uploaded_by=user if getattr(user, "is_authenticated", False) else None,
    )


def attachments_for(obj):
    return Attachment.objects.filter(
        content_type=ContentType.objects.get_for_model(obj, for_concrete_model=False),
        object_id=obj.pk,
    )
