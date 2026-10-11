from django.db import models

from core.models import TimeStampedModel
from .constants import DOCUMENT_TYPE_CHOICES


class DocumentSequence(TimeStampedModel):
    """One running counter. Scope: a company, optionally narrowed to one
    branch (store), per document type, per fiscal year.

    fiscal_year = 0 means "never resets" (used for e.g. employee ids).
    Always obtain numbers through numbering.services.next_reference().
    """
    company = models.ForeignKey("company.Company", on_delete=models.CASCADE, related_name="document_sequences")
    store = models.ForeignKey(
        "tenants.Store", on_delete=models.CASCADE, null=True, blank=True, related_name="document_sequences",
        help_text="Leave empty for a company-wide sequence; set to give one branch its own counter.",
    )
    document_type = models.CharField(max_length=30, choices=DOCUMENT_TYPE_CHOICES)
    fiscal_year = models.PositiveSmallIntegerField(default=0, help_text="0 = never resets.")
    prefix = models.CharField(max_length=15)
    separator = models.CharField(max_length=2, default="-", blank=True)
    include_year = models.BooleanField(default=True)
    padding = models.PositiveSmallIntegerField(default=5)
    next_number = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["company", "document_type", "-fiscal_year"]
        constraints = [
            # NULL store values never collide in a plain unique constraint, so
            # company-wide and branch-specific rows get one constraint each.
            models.UniqueConstraint(
                fields=["company", "document_type", "fiscal_year"],
                condition=models.Q(store__isnull=True),
                name="uniq_seq_company_wide",
            ),
            models.UniqueConstraint(
                fields=["company", "store", "document_type", "fiscal_year"],
                condition=models.Q(store__isnull=False),
                name="uniq_seq_per_branch",
            ),
        ]

    def __str__(self):
        scope = self.store.name if self.store_id else "all branches"
        return f"{self.document_type} / {scope} / FY{self.fiscal_year or '-'}"

    def format(self, number: int, fiscal_year: int) -> str:
        parts = [self.prefix]
        if self.include_year:
            parts.append(str(fiscal_year))
        parts.append(str(number).zfill(self.padding))
        return self.separator.join(parts)
