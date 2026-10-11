from django.contrib import admin

from .models import Expense, ExpenseCategory


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "store")


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("reference", "category", "amount", "method", "expense_date", "store")
    list_filter = ("category", "method", "store")
