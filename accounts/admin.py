from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "get_full_name", "store", "is_biller", "is_staff", "is_active")
    list_filter = ("store", "is_biller", "is_staff", "is_active", "groups")
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("POS", {"fields": ("store", "phone", "photo", "is_biller", "pos_pin")}),
    )
