from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "billing"
    verbose_name = "Subscriptions & billing"

    def ready(self):
        from . import tenancy
        tenancy.install()
