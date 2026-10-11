"""
Quality control. A QualityCheck points at ANY document through
(source_type, source_id) - e.g. ("grn_line", 12) or ("job_card", 3) - so the
quality app does not depend on procurement/production (they depend on it and
register a handler to learn the outcome).
"""
from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class DefectType(TimeStampedModel):
    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="defect_types")
    name = models.CharField(max_length=80)
    severity = models.CharField(max_length=10, choices=[("minor", "Minor"), ("major", "Major"), ("critical", "Critical")], default="minor")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_defect_type_name")]

    def __str__(self):
        return self.name


class QualityCheck(TimeStampedModel):
    INCOMING, IN_PROCESS, FINAL, RETURN = "incoming", "in_process", "final", "return"
    TYPE_CHOICES = [(INCOMING, "Incoming material"), (IN_PROCESS, "In-process"), (FINAL, "Final / finished goods"), (RETURN, "Customer return")]
    PENDING, PASSED, FAILED, PARTIAL, HOLD = "pending", "passed", "failed", "partial", "hold"
    STATUS_CHOICES = [(PENDING, "Pending"), (PASSED, "Passed"), (FAILED, "Failed"), (PARTIAL, "Partially accepted"), (HOLD, "On hold")]

    company = models.ForeignKey("company.Company", on_delete=models.PROTECT, related_name="quality_checks")
    reference = models.CharField(max_length=30, unique=True)
    check_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    source_type = models.CharField(max_length=30, db_index=True)
    source_id = models.CharField(max_length=30, db_index=True)
    source_reference = models.CharField(max_length=40, blank=True, help_text="Human-readable number of the source document.")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="quality_checks")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, help_text="Quantity presented for inspection.")
    accepted_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    rejected_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    inspector = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    inspected_at = models.DateTimeField(null=True, blank=True)
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["source_type", "source_id", "check_type"],
                                    condition=models.Q(status__in=["pending", "hold"]), name="one_open_check_per_source"),
        ]

    def __str__(self):
        return self.reference


class QualityParameter(models.Model):
    quality_check = models.ForeignKey(QualityCheck, on_delete=models.CASCADE, related_name="parameters")
    name = models.CharField(max_length=80)
    specification = models.CharField(max_length=120, blank=True)
    measured_value = models.CharField(max_length=120, blank=True)
    passed = models.BooleanField(null=True, blank=True)


class QualityDefect(models.Model):
    quality_check = models.ForeignKey(QualityCheck, on_delete=models.CASCADE, related_name="defects")
    defect_type = models.ForeignKey(DefectType, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    note = models.CharField(max_length=255, blank=True)
