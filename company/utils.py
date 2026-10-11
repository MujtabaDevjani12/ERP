from core.errors import ERPError


def company_of_store(store):
    company = getattr(store, "company", None)
    if company is None:
        raise ERPError(
            f"Branch '{store}' has no company. Run: python manage.py bootstrap_company"
        )
    return company


def company_of_warehouse(warehouse):
    return company_of_store(warehouse.store)
