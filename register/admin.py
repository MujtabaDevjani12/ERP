from django.contrib import admin

from .models import Register, RegisterSession


@admin.register(Register)
class RegisterAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "store", "warehouse", "is_active")
    filter_horizontal = ("assigned_users",)


@admin.register(RegisterSession)
class RegisterSessionAdmin(admin.ModelAdmin):
    list_display = ("register", "opened_by", "opened_at", "status", "opening_cash", "closing_cash_counted", "expected_cash_at_close")
    readonly_fields = ("closing_summary",)
    list_filter = ("status", "register")
