from django.apps import AppConfig


class ExpensesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "expenses"

    def ready(self):
        from django.db.models.signals import post_save, pre_delete

        from .models import Expense

        def _saved(sender, instance, raw=False, **kwargs):
            if not raw:
                from .accounting import sync_expense
                sync_expense(instance, user=None)

        def _deleting(sender, instance, **kwargs):
            from .accounting import void_expense
            void_expense(instance)

        post_save.connect(_saved, sender=Expense, dispatch_uid="expense_post_to_books")
        pre_delete.connect(_deleting, sender=Expense, dispatch_uid="expense_void_in_books")
