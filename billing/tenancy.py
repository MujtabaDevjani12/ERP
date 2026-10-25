"""
Keeps one company's data away from another's in the ERP screens, and applies plan limits to "Add" buttons.

The ERP screens are built on Django ModelAdmin (erp_ui). Rather than touching every admin class we wrap three ModelAdmin
methods once, here:
  get_queryset            -> rows of the user's own company only
  formfield_for_foreignkey -> drop-downs only offer the user's own company's records
  has_add_permission      -> false when the plan's limit (users / branches / products) is used up
Scoping only switches on when the database holds MORE THAN ONE company, so a normal single-company install behaves exactly as
before. The platform owner always sees everything.
"""
from django.apps import apps
from django.contrib import admin
from django.core.exceptions import FieldDoesNotExist

_SCOPE_CACHE = {}


def _is_company_model(model):
    return model._meta.label == "company.Company"


def company_path(model):
    """ORM path from `model` to its Company (e.g. 'company', 'store__company'), '' for Company itself, None if unscoped."""
    label = model._meta.label
    if label in _SCOPE_CACHE:
        return _SCOPE_CACHE[label]
    path = None
    if _is_company_model(model):
        path = ""
    else:
        for name in ("company", "store"):
            try:
                field = model._meta.get_field(name)
            except FieldDoesNotExist:
                continue
            if not (field.is_relation and field.many_to_one):
                continue
            if name == "company" and _is_company_model(field.related_model):
                path = "company"
                break
            if name == "store":
                path = "store__company"
                break
    _SCOPE_CACHE[label] = path
    return path


def multi_company():
    from company.models import Company
    return Company.objects.count() > 1


def tenant_company(request):
    """The company to restrict this request to, or None when no restriction applies."""
    from .access import company_of, is_platform_owner
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or is_platform_owner(user):
        return None
    company = company_of(request)
    if company is None:
        return None
    cache = getattr(request, "_tenant_multi", None)
    if cache is None:
        cache = request._tenant_multi = multi_company()
    return company if cache else None


def scope_queryset(request, qs):
    company = tenant_company(request)
    if company is None:
        return qs
    path = company_path(qs.model)
    if path is None:
        return qs
    if path == "":
        return qs.filter(pk=company.pk)
    return qs.filter(**{path: company})


# ---------------------------------------------------------------- limits on "Add"
LIMITED = {"tenants.store": "stores", "accounts.user": "users", "catalog.product": "products"}


def install():
    if getattr(admin.ModelAdmin, "_billing_patched", False):
        return
    orig_qs = admin.ModelAdmin.get_queryset
    orig_fk = admin.ModelAdmin.formfield_for_foreignkey
    orig_add = admin.ModelAdmin.has_add_permission

    def get_queryset(self, request):
        return scope_queryset(request, orig_qs(self, request))

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = orig_fk(self, db_field, request, **kwargs)
        if field is not None and getattr(field, "queryset", None) is not None:
            company = tenant_company(request)
            if company is not None:
                scoped = scope_queryset(request, field.queryset)
                field.queryset = scoped
                if _is_company_model(db_field.related_model) and scoped.count() == 1:
                    field.initial = company.pk
        return field

    def has_add_permission(self, request, *args, **kwargs):
        allowed = orig_add(self, request, *args, **kwargs)
        key = LIMITED.get(self.model._meta.label_lower)
        if allowed and key:
            from .access import company_of, is_platform_owner, limit_reached
            user = getattr(request, "user", None)
            if user is not None and user.is_authenticated and not is_platform_owner(user):
                company = company_of(request)
                if company is not None and limit_reached(company, key)[0]:
                    return False
        return allowed

    admin.ModelAdmin.get_queryset = get_queryset
    admin.ModelAdmin.formfield_for_foreignkey = formfield_for_foreignkey
    admin.ModelAdmin.has_add_permission = has_add_permission
    admin.ModelAdmin._billing_patched = True
