from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class Notification(TimeStampedModel):
    INFO, WARNING, CRITICAL = "info", "warning", "critical"
    LEVEL_CHOICES = [(INFO, "Info"), (WARNING, "Warning"), (CRITICAL, "Critical")]

    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="notifications")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=160)
    body = models.TextField(blank=True)
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default=INFO)
    link = models.CharField(max_length=255, blank=True)
    key = models.CharField(max_length=120, help_text="De-duplication key: the same alert is not sent to the same person twice.")
    is_read = models.BooleanField(default=False, db_index=True)
    emailed = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["user", "key"], name="uniq_notification_per_user_key")]

    def __str__(self):
        return self.title
