from datetime import timedelta

import pandas as pd
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework.exceptions import PermissionDenied

from core.cache import bump_location_revision, remember_view_hint, view_hint_exists
from locations.models import Location, LocationView, LocationViewGate
from locations.policies import can_modify_location
from locations.serializers import LocationExportSerializer


def export_locations_csv(queryset):
    records = LocationExportSerializer(queryset, many=True).data
    columns = LocationExportSerializer.Meta.fields
    frame = pd.DataFrame(records, columns=columns, dtype=object)
    for field in ("title", "description", "address"):
        frame[field] = frame[field].map(
            lambda value: "'" + value
            if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
        )
    return frame.to_csv(index=False, lineterminator="\r\n").encode("utf-8-sig")


@transaction.atomic
def soft_delete_location(location, *, actor):
    location = get_object_or_404(
        Location.objects.select_for_update(), pk=location.pk, is_deleted=False
    )
    if not can_modify_location(actor, location):
        raise PermissionDenied("Змінювати можна лише власні локації.")
    location.is_deleted = True
    location.save(update_fields=["is_deleted", "updated_at"])
    bump_location_revision()


def visitor_hash(request):
    if request.user.is_authenticated:
        identity = f"user:{request.user.pk}"
    else:
        if request.session.session_key is None:
            request.session.create()
        identity = f"session:{request.session.session_key}"
    return salted_hmac("locations.visitor", identity, algorithm="sha256").hexdigest()


def count_location_view(location, request):
    if location.is_deleted:
        return False
    visitor = visitor_hash(request)
    if view_hint_exists(location.pk, visitor):
        return False

    with transaction.atomic():
        # Match the parent-first locking order used by review services.
        active = get_object_or_404(
            Location.objects.select_for_update(), pk=location.pk, is_deleted=False
        )
        gate, _ = LocationViewGate.objects.get_or_create(location=active, visitor_hash=visitor)
        gate = LocationViewGate.objects.select_for_update().get(pk=gate.pk)
        now = timezone.now()
        counted = gate.last_counted_at is None or now - gate.last_counted_at >= timedelta(seconds=3600)
        if counted:
            LocationView.objects.create(location=active, viewed_at=now)
            gate.last_counted_at = now
            gate.save(update_fields=["last_counted_at"])
        expires_at = gate.last_counted_at + timedelta(seconds=3600)
        transaction.on_commit(lambda: remember_view_hint(location.pk, visitor, expires_at))
    return counted
