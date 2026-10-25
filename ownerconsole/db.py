"""Which database holds the owner data. 'owner' normally; 'default' when OWNER_DB_SAME=True (and in the test-suite)."""
from django.conf import settings
from django.db import transaction


def alias():
    return getattr(settings, "OWNER_DB_ALIAS", "default")


def atomic():
    """transaction.atomic() on the owner database."""
    return transaction.atomic(using=alias())
