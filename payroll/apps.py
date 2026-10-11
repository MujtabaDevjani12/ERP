from django.apps import AppConfig


class PayrollConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "payroll"
    verbose_name = "Payroll"

    def ready(self):
        from . import workflows  # noqa: F401
