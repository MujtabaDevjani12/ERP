from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel


class ApprovalWorkflow(TimeStampedModel):
    """Which documents need approval and through how many levels.
    A document type with NO active workflow is auto-approved on submit."""
    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="approval_workflows")
    name = models.CharField(max_length=100)
    document_type = models.CharField(
        max_length=40,
        help_text="e.g. purchase_requisition, purchase_order, stock_transfer, stock_adjustment, "
                  "work_order, sales_discount, credit_note, vendor_payment, employee_loan, leave, payroll, journal_entry",
    )
    min_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0,
                                     help_text="Workflow applies when the document amount is at least this.")
    allow_self_approval = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company", "document_type", "-min_amount"]
        constraints = [
            models.UniqueConstraint(fields=["company", "document_type", "name"], name="uniq_workflow_name"),
        ]

    def __str__(self):
        return f"{self.name} ({self.document_type})"


class ApprovalLevel(models.Model):
    workflow = models.ForeignKey(ApprovalWorkflow, on_delete=models.CASCADE, related_name="levels")
    level = models.PositiveSmallIntegerField(help_text="1, 2, 3 ... evaluated in this order.")
    name = models.CharField(max_length=80, blank=True)
    approver_group = models.ForeignKey(Group, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    approver_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["workflow", "level"]
        constraints = [models.UniqueConstraint(fields=["workflow", "level"], name="uniq_level_per_workflow")]

    def __str__(self):
        return f"{self.workflow.name} / L{self.level}"

    def clean(self):
        if not self.approver_group_id and not self.approver_user_id:
            raise ValidationError("Choose an approver group or an approver user.")


class ApprovalRequest(TimeStampedModel):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    RETURNED = "returned"
    CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (PENDING, "Pending"), (APPROVED, "Approved"), (REJECTED, "Rejected"),
        (RETURNED, "Returned for correction"), (CANCELLED, "Cancelled"),
    ]

    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="approval_requests")
    workflow = models.ForeignKey(ApprovalWorkflow, on_delete=models.SET_NULL, null=True, blank=True, related_name="requests")
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")
    document_type = models.CharField(max_length=40)
    document_number = models.CharField(max_length=40, blank=True)
    amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    current_level = models.PositiveSmallIntegerField(default=0)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    submitted_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-submitted_at", "-id"]
        indexes = [models.Index(fields=["content_type", "object_id"], name="approval_obj_idx")]
        constraints = [
            models.UniqueConstraint(
                fields=["content_type", "object_id", "document_type"],
                condition=models.Q(status="pending"),
                name="uniq_pending_approval_per_document",
            ),
        ]

    def __str__(self):
        return f"{self.document_type} {self.document_number} [{self.status}]"


class ApprovalAssignment(models.Model):
    """Extra approver added by delegation for one level of one request."""
    request = models.ForeignKey(ApprovalRequest, on_delete=models.CASCADE, related_name="assignments")
    level = models.PositiveSmallIntegerField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")


class ApprovalAction(models.Model):
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    RETURNED = "returned"
    DELEGATED = "delegated"
    CANCELLED = "cancelled"
    ACTION_CHOICES = [
        (SUBMITTED, "Submitted"), (APPROVED, "Approved"), (REJECTED, "Rejected"),
        (RETURNED, "Returned for correction"), (DELEGATED, "Delegated"), (CANCELLED, "Cancelled"),
    ]

    request = models.ForeignKey(ApprovalRequest, on_delete=models.CASCADE, related_name="actions")
    level = models.PositiveSmallIntegerField(default=0)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    action = models.CharField(max_length=10, choices=ACTION_CHOICES)
    comment = models.TextField(blank=True)
    delegated_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
