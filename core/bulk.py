"""Delete several records at once (list screens: tick rows -> Delete selected). Records that other documents still use are kept."""
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError


def bulk_delete(request, queryset, ids, noun="record", plural=None):
    """Deletes the rows of `queryset` whose pk is in `ids` one by one (each in its own savepoint).
    Returns (deleted, kept) where kept is a list of names that could not be deleted because something depends on them.
    Adds one summary message to the request."""
    wanted = [i for i in ids if str(i).isdigit()]
    rows = list(queryset.filter(pk__in=wanted))
    deleted, kept = 0, []
    for obj in rows:
        label = str(obj)
        try:
            with transaction.atomic():
                obj.delete()
            deleted += 1
        except (ProtectedError, IntegrityError):
            kept.append(label)
    plural = str(plural or (noun if noun.endswith("s") else f"{noun}s")).lower()
    noun = str(noun).lower()
    if not rows:
        messages.warning(request, f"Tick at least one {noun} first.")
    else:
        if deleted:
            messages.success(request, f"Deleted {deleted} {noun if deleted == 1 else plural}.")
        if kept:
            shown = ", ".join(kept[:5]) + (f" and {len(kept) - 5} more" if len(kept) > 5 else "")
            messages.error(request, f"{len(kept)} {noun if len(kept) == 1 else plural} could not be deleted because they are in use: {shown}.")
    return deleted, kept
