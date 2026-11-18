from django.db import transaction
from rest_framework.exceptions import NotFound, PermissionDenied

from core.cache import bump_location_revision
from locations.policies import can_modify_location


@transaction.atomic
def soft_delete_location(location, *, actor):
    if location.is_deleted:
        raise NotFound("Локацію не знайдено.")
    if not can_modify_location(actor, location):
        raise PermissionDenied("Змінювати можна лише власні локації.")
    location.is_deleted = True
    location.save(update_fields=["is_deleted", "updated_at"])
    bump_location_revision()
