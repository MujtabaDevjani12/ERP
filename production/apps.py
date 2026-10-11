from django.apps import AppConfig


class ProductionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "production"
    verbose_name = "Production"

    def ready(self):
        from . import workflows  # noqa: F401
