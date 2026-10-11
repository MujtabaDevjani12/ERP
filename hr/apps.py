from django.apps import AppConfig


class HrConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "hr"
    verbose_name = "Human resources"

    def ready(self):
        from . import workflows  # noqa: F401
        try:
            from masters import importing as imp
            from . import importers
            importers.register(imp)
        except Exception:      # HR must still load if the import framework is unavailable
            pass
