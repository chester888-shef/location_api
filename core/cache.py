from django.db.models import F

from core.models import CacheRevision


def bump_location_revision():
    """Call inside the transaction that changes location list data."""
    revision, _ = CacheRevision.objects.get_or_create(name="locations")
    CacheRevision.objects.filter(pk=revision.pk).update(version=F("version") + 1)
