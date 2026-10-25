"""
Database router: the ownerconsole app lives in its own database (alias "owner"), every other app stays in "default".
When OWNER_DB_ALIAS is "default" (single-database install or the test-suite) the router does nothing.
"""
from django.conf import settings

LABEL = "ownerconsole"


class OwnerRouter:
    def _alias(self):
        return getattr(settings, "OWNER_DB_ALIAS", "default")

    def db_for_read(self, model, **hints):
        if model._meta.app_label == LABEL and self._alias() != "default":
            return self._alias()
        return None

    db_for_write = db_for_read

    def allow_relation(self, obj1, obj2, **hints):
        a, b = obj1._meta.app_label == LABEL, obj2._meta.app_label == LABEL
        if a or b:
            return a and b or self._alias() == "default"
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        alias = self._alias()
        if alias == "default":
            return None
        if app_label == LABEL:
            return db == alias
        if db == alias:                      # the owner database holds the owner tables only
            return False
        return None
