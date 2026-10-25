"""Shared class-based-view mixins so every app's simple CRUD (Tax,
Discount, Device, Supplier, ExpenseCategory, ...) doesn't repeat the same
'filter by the logged-in user's store' + permission-check boilerplate."""
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


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
    permission_required = None           # defaults to "<app>.view_<model>"

    def dispatch(self, request, *args, **kwargs):
        """Lists are not world-readable: anonymous users go to login, and a signed-in user
        needs the model's view permission (Super Admins pass through the auth backend)."""
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        perm = self.permission_required
        if perm is None and getattr(self, "model", None) is not None:
            opts = self.model._meta
            perm = f"{opts.app_label}.view_{opts.model_name}"
        if perm and not request.user.has_perm(perm):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        """'Delete selected' on the list: rows ticked in the table are deleted (those other records use are kept)."""
        from django.shortcuts import redirect
        from core.bulk import bulk_delete
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        opts = self.model._meta
        if request.POST.get("action") != "bulk_delete" or not request.user.has_perm(f"{opts.app_label}.delete_{opts.model_name}"):
            raise PermissionDenied
        bulk_delete(request, self.get_queryset(), request.POST.getlist("ids"), opts.verbose_name, opts.verbose_name_plural)
        return redirect(request.get_full_path())

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        opts = self.model._meta
        user = self.request.user
        ctx.update({
            "can_add": user.has_perm(f"{opts.app_label}.add_{opts.model_name}"),
            "can_change": user.has_perm(f"{opts.app_label}.change_{opts.model_name}"),
            "can_delete": user.has_perm(f"{opts.app_label}.delete_{opts.model_name}"),
            "excel_key": self.excel_key,
            "title": self.page_title,
            "add_url_name": self.add_url_name,
            "edit_url_name": self.edit_url_name,
            "delete_url_name": self.delete_url_name,
        })
        return ctx
