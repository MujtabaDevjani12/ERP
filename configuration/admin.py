from django.contrib import admin

from .models import Currency, NumberingPrefix, POSConfiguration, SystemConfiguration


@admin.register(POSConfiguration)
class POSConfigurationAdmin(admin.ModelAdmin):
    list_display = ("store", "default_biller", "printing_mode", "rounding")


@admin.register(SystemConfiguration)
class SystemConfigurationAdmin(admin.ModelAdmin):
    list_display = ("site_name", "default_currency_code", "maintenance_mode")

    def has_add_permission(self, request):
        return not SystemConfiguration.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(NumberingPrefix)
class NumberingPrefixAdmin(admin.ModelAdmin):
    list_display = ("document_type", "prefix", "next_number", "padding")


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("code", "symbol", "rate", "store", "is_active")
