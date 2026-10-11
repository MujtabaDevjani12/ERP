from django.apps import AppConfig


class ProcurementConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "procurement"
    verbose_name = "Procurement"

    def ready(self):
        from . import workflows  # noqa: F401
