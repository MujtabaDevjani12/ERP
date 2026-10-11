from django.contrib.auth.models import AbstractUser
from django.db import models

from tenants.models import Store


class User(AbstractUser):
    """
    Every staff member — owner, manager, cashier/biller, inventory clerk.
    Permissions are Django's own Group/Permission system (Settings > Roles
    in the dashboard is a friendly wrapper around django.contrib.auth
    groups) — no separate Role table to keep in sync by hand.
    """
    store = models.ForeignKey(
        Store, on_delete=models.SET_NULL, null=True, blank=True, related_name="staff",
        help_text="Branch this user works at. Leave blank for an owner/admin who can switch stores.",
    )
    phone = models.CharField(max_length=30, blank=True)
    photo = models.ImageField(upload_to="staff_photos/", blank=True, null=True)
    is_biller = models.BooleanField(
        default=False, help_text="Show this user in the POS 'Biller' dropdown."
    )
    pos_pin = models.CharField(
        max_length=6, blank=True,
        help_text="Optional short PIN for fast register/POS switching between staff.",
    )

    def __str__(self):
        return self.get_full_name() or self.username
