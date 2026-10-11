from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models


class ImmutableQuerySet(models.QuerySet):
    def update(self, *args, **kwargs):
        raise RuntimeError("Audit log entries are immutable and cannot be updated.")

    def delete(self):
        raise RuntimeError("Audit log entries are immutable and cannot be deleted.")


class AuditLog(models.Model):
    """Append-only record of who did what, when, from where, and what changed.
    Write rows only through audit.services.log_action()."""
    CREATED = "created"
    UPDATED = "updated"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    POSTED = "posted"
    REVERSED = "reversed"
    DELETED = "deleted"
    LOGIN = "login"
    LOGOUT = "logout"
    LOGIN_FAILED = "login_failed"
    OTHER = "other"
    ACTION_CHOICES = [
        (CREATED, "Created"), (UPDATED, "Updated"), (APPROVED, "Approved"), (REJECTED, "Rejected"),
        (CANCELLED, "Cancelled"), (POSTED, "Posted"), (REVERSED, "Reversed"), (DELETED, "Deleted"),
        (LOGIN, "Login"), (LOGOUT, "Logout"), (LOGIN_FAILED, "Login failed"), (OTHER, "Other"),
    ]

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    user_repr = models.CharField(max_length=150, blank=True, help_text="Username at the time (kept even if the user is deleted).")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    company = models.ForeignKey("company.Company", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    action = models.CharField(max_length=15, choices=ACTION_CHOICES, db_index=True)
    content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    object_id = models.CharField(max_length=64, blank=True)
    object_repr = models.CharField(max_length=255, blank=True)
    document_number = models.CharField(max_length=40, blank=True, db_index=True)

    old_values = models.JSONField(encoder=DjangoJSONEncoder, null=True, blank=True)
    new_values = models.JSONField(encoder=DjangoJSONEncoder, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    objects = ImmutableQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["content_type", "object_id"], name="audit_obj_idx")]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.user_repr or 'system'} {self.action} {self.object_repr}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise RuntimeError("Audit log entries are immutable and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Audit log entries are immutable and cannot be deleted.")
