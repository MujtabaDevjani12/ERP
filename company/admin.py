from django.contrib import admin

from .models import Company


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "legal_name", "tax_number", "currency_code", "is_active")
    search_fields = ("name", "legal_name", "registration_number", "tax_number")
    list_filter = ("is_active",)
