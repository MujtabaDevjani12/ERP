from django.apps import AppConfig


class StockConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "stock"
    verbose_name = "Stock & warehouse"

    def ready(self):
        from . import workflows  # noqa: F401  (binds approval statuses)
