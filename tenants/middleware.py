"""
Attaches request.store — the branch the logged-in user works at — so
every view/template can filter by it without repeating the lookup.
Placed after AuthenticationMiddleware in settings.MIDDLEWARE.

An owner / superuser who is not tied to one branch (User.store is empty — "an owner/admin who can switch stores")
works in the first active branch. Without this every page saw "no store": the dashboard showed zeros and empty
charts, and forms that need a branch (new counter -> warehouse list) had nothing to offer.
"""


class CurrentStoreMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.store = None
        user = request.user
        if user.is_authenticated:
            request.store = getattr(user, "store", None)
            if request.store is None and user.is_superuser:
                request.store = self._default_store()
        return self.get_response(request)

    @staticmethod
    def _default_store():
        from .models import Store
        return Store.objects.filter(is_active=True).order_by("pk").first()
