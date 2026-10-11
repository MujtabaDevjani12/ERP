from django.apps import AppConfig


class MastersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "masters"
    verbose_name = "Master data"

    def ready(self):
        from . import importing  # noqa: F401  (registers the built-in Excel import types)
