from django.apps import AppConfig


class DistributionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "distribution"
    verbose_name = "Sales & distribution"

    def ready(self):
        from . import workflows  # noqa: F401
