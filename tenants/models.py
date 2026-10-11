from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class Store(TimeStampedModel):
    """
    One branch/shop. business_type drives POS wording and which optional
    product attributes (karat/weight for gold, size/color for clothing,
    dine-in/takeaway for restaurant...) make sense for it — the schema
    itself stays the same for every type; see catalog.ProductAttribute.
    """
    name = models.CharField(max_length=150)
    business_type = models.CharField(max_length=20, choices=settings.BUSINESS_TYPES, default="general")
    address = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    logo = models.ImageField(upload_to="store_logos/", blank=True, null=True)
    timezone = models.CharField(max_length=50, default="Asia/Karachi")
    currency_code = models.CharField(max_length=6, default="PKR")
    currency_symbol = models.CharField(max_length=6, default="Rs")
    is_active = models.BooleanField(default=True)

    # ERP: the legal entity this branch belongs to. Nullable so existing POS
    # data stays valid; run `manage.py bootstrap_company` to backfill.
    company = models.ForeignKey(
        "company.Company", on_delete=models.PROTECT, null=True, blank=True, related_name="branches",
    )
    code = models.CharField(max_length=20, blank=True, help_text="Short branch code, e.g. KHI-01.")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Warehouse(TimeStampedModel):
    """A stock-holding location under a store — the sales floor, a back
    store-room, a central warehouse feeding several shop branches."""
    RAW_MATERIAL = "RAW_MATERIAL"
    WIP = "WIP"
    FINISHED_GOODS = "FINISHED_GOODS"
    GENERAL = "GENERAL"
    OUTLET = "OUTLET"
    WAREHOUSE_TYPES = [
        (RAW_MATERIAL, "Raw material"),
        (WIP, "Work in progress"),
        (FINISHED_GOODS, "Finished goods"),
        (GENERAL, "General"),
        (OUTLET, "Retail outlet"),
    ]

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="warehouses")
    name = models.CharField(max_length=120)
    is_default = models.BooleanField(default=False)
    code = models.CharField(max_length=20, blank=True)
    warehouse_type = models.CharField(max_length=15, choices=WAREHOUSE_TYPES, default=GENERAL)

    class Meta:
        ordering = ["store", "name"]
        constraints = [
            models.UniqueConstraint(fields=["store", "name"], name="uniq_warehouse_name_per_store"),
        ]

    def __str__(self):
        return f"{self.name} ({self.store.name})"
