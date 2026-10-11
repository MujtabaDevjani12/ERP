"""
Company — the legal entity that sits ABOVE the existing tenants.Store
(branch). Existing POS code keeps working with Store/request.store; the
ERP layers (accounting, numbering, approvals...) scope by Company.
"""
from datetime import date

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from core.models import TimeStampedModel


class Company(TimeStampedModel):
    name = models.CharField(max_length=150)
    legal_name = models.CharField(max_length=200, blank=True)
    registration_number = models.CharField(max_length=60, blank=True)
    tax_number = models.CharField(max_length=60, blank=True)
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=80, blank=True)
    country = models.CharField(max_length=80, default="Pakistan")
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    logo = models.ImageField(upload_to="company_logos/", blank=True, null=True)
    currency_code = models.CharField(max_length=6, default="PKR")
    fiscal_year_start_month = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
        help_text="Month the fiscal year starts (1 = January, 7 = July).",
    )
    timezone = models.CharField(max_length=50, default="Asia/Karachi")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Companies"

    def __str__(self):
        return self.name

    def fiscal_year_for(self, on_date: date) -> int:
        """Fiscal year label for a date = the calendar year in which that
        fiscal year STARTS. Start month 7: 2026-08-01 -> 2026, 2026-03-01 -> 2025.
        Start month 1 (calendar year): always on_date.year."""
        if on_date.month >= self.fiscal_year_start_month:
            return on_date.year
        return on_date.year - 1
