"""
Attaches request.company (the Company of the logged-in user's branch),
next to request.store set by tenants.middleware.CurrentStoreMiddleware.
Must come AFTER CurrentStoreMiddleware in settings.MIDDLEWARE.
"""


class CurrentCompanyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        store = getattr(request, "store", None)
        request.company = getattr(store, "company", None) if store is not None else None
        return self.get_response(request)
