"""
Secure upload checks: extension allow-list + size limit + a "magic bytes"
check so a renamed .exe cannot pose as a .pdf. Allowed: PDF, JPG, PNG,
DOCX, XLSX.
"""
import os

from django.conf import settings
from django.core.exceptions import ValidationError

_ZIP = (b"PK\x03\x04",)
ALLOWED_SIGNATURES = {
    ".pdf": (b"%PDF",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".docx": _ZIP,
    ".xlsx": _ZIP,
}


def max_upload_bytes() -> int:
    return int(getattr(settings, "ATTACHMENT_MAX_MB", 10)) * 1024 * 1024


def validate_attachment(file) -> None:
    ext = os.path.splitext(file.name or "")[1].lower()
    if ext not in ALLOWED_SIGNATURES:
        allowed = ", ".join(sorted(ALLOWED_SIGNATURES))
        raise ValidationError(f"File type '{ext or 'unknown'}' is not allowed. Allowed: {allowed}.")

    if file.size > max_upload_bytes():
        raise ValidationError(f"File is too large (max {max_upload_bytes() // (1024 * 1024)} MB).")

    file.seek(0)
    head = file.read(8)
    file.seek(0)
    if not any(head.startswith(sig) for sig in ALLOWED_SIGNATURES[ext]):
        raise ValidationError("File content does not match its extension.")
