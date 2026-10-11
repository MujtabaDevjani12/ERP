from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from configuration.models import NumberingPrefix
from core.mixins import DeleteContextMixin, SimpleListMixin, StoreCrudMixin
from .models import Expense, ExpenseCategory


# ---------------------------------------------------------------- Categories
class ExpenseCategoryListView(SimpleListMixin, ListView):
    model = ExpenseCategory
    page_title = "Expense categories"
    add_url_name = "expenses:category_add"
    edit_url_name = "expenses:category_edit"
    delete_url_name = "expenses:category_delete"


class ExpenseCategoryCreateView(StoreCrudMixin, CreateView):
    model = ExpenseCategory
    fields = ["name"]
    template_name = "partials/simple_form.html"
    success_url = reverse_lazy("expenses:category_list")
    permission_required = "expenses.add_expensecategory"

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Add expense category", back_url=self.success_url, **kwargs)


class ExpenseCategoryUpdateView(StoreCrudMixin, UpdateView):
    model = ExpenseCategory
    fields = ["name"]
    template_name = "partials/simple_form.html"
    success_url = reverse_lazy("expenses:category_list")
    permission_required = "expenses.change_expensecategory"

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Edit expense category", back_url=self.success_url, **kwargs)


class ExpenseCategoryDeleteView(StoreCrudMixin, DeleteContextMixin, DeleteView):
    model = ExpenseCategory
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("expenses:category_list")
    permission_required = "expenses.delete_expensecategory"


# ---------------------------------------------------------------- Expense
class ExpenseListView(SimpleListMixin, ListView):
    model = Expense
    page_title = "Expenses"
    excel_key = "expenses"
    add_url_name = "expenses:expense_add"
    edit_url_name = "expenses:expense_edit"
    delete_url_name = "expenses:expense_delete"

    def get_queryset(self):
        return Expense.objects.filter(store=self.request.store).select_related("category").order_by("-expense_date")


class ExpenseCreateView(StoreCrudMixin, CreateView):
    model = Expense
    fields = ["category", "amount", "method", "paid_to", "expense_date", "note"]
    template_name = "partials/simple_form.html"
    success_url = reverse_lazy("expenses:expense_list")
    permission_required = "expenses.add_expense"

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["category"].queryset = ExpenseCategory.objects.filter(store=self.request.store)
        return form

    def form_valid(self, form):
        form.instance.reference = NumberingPrefix.next_reference(NumberingPrefix.EXPENSE)
        form.instance.created_by = self.request.user
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Add expense", back_url=self.success_url, **kwargs)


class ExpenseUpdateView(StoreCrudMixin, UpdateView):
    model = Expense
    fields = ["category", "amount", "method", "paid_to", "expense_date", "note"]
    template_name = "partials/simple_form.html"
    success_url = reverse_lazy("expenses:expense_list")
    permission_required = "expenses.change_expense"

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["category"].queryset = ExpenseCategory.objects.filter(store=self.request.store)
        return form

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Edit expense", back_url=self.success_url, **kwargs)


class ExpenseDeleteView(StoreCrudMixin, DeleteContextMixin, DeleteView):
    model = Expense
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("expenses:expense_list")
    permission_required = "expenses.delete_expense"
