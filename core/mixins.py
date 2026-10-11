"""Shared class-based-view mixins so every app's simple CRUD (Tax,
Discount, Device, Supplier, ExpenseCategory, ...) doesn't repeat the same
'filter by the logged-in user's store' + permission-check boilerplate."""
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin


class StoreScopedMixin(LoginRequiredMixin):
    def get_queryset(self):
        return super().get_queryset().filter(store=self.request.store)

    def form_valid(self, form):
        if hasattr(form, "instance"):                    # Create/Update forms; a DeleteView's form has no instance
            form.instance.store = self.request.store
        return super().form_valid(form)


class StoreCrudMixin(StoreScopedMixin, PermissionRequiredMixin):
    """Adds the Django permission check on top of StoreScopedMixin. Set
    `permission_required` as usual on the view."""
    pass


class DeleteContextMixin:
    """partials/confirm_delete.html needs a Cancel link — expose
    success_url in the template context (DeleteView doesn't by default)."""
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["success_url"] = self.get_success_url()
        return ctx


class SimpleListMixin:
    """Feeds partials/generic_list.html: set page_title / add_url_name /
    edit_url_name / delete_url_name as class attributes and the list
    (each row rendered by its __str__) just works, no per-model template."""
    template_name = "partials/generic_list.html"
    context_object_name = "rows"
    page_title = ""
    add_url_name = ""
    edit_url_name = ""
    delete_url_name = ""
    excel_key = ""                       # key of reports/exchange.py: shows Excel export / import buttons

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            "excel_key": self.excel_key,
            "title": self.page_title,
            "add_url_name": self.add_url_name,
            "edit_url_name": self.edit_url_name,
            "delete_url_name": self.delete_url_name,
        })
        return ctx
