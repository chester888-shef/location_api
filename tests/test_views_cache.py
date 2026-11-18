from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from copy import deepcopy
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.db import IntegrityError, OperationalError, close_old_connections, connections, transaction
from django.utils import timezone
from redis import Redis
from redis.exceptions import RedisError
from rest_framework.test import APIClient

from core.cache import remember_view_hint
from core.models import CacheRevision
from locations.models import Location, LocationView, LocationViewGate
from locations.services import visitor_hash

pytestmark = pytest.mark.django_db


def test_authenticated_views_use_rolling_3600_seconds(location, crud_users, crud_client):
    client = crud_client(crud_users["owner"])
    url = f"/api/locations/{location.pk}/"
    now = timezone.now()
    revision = CacheRevision.objects.get(name="locations").version
    with patch("locations.services.view_hint_exists", return_value=False):
        for offset, expected in [(0, 1), (3599.999, 1), (3600, 2), (3600.001, 2)]:
            with patch("locations.services.timezone.now", return_value=now + timedelta(seconds=offset)):
                response = client.get(url)
            assert response.status_code == 200
            assert response.json()["views_count"] == expected
            assert response.json()["views_7d"] == expected
    assert LocationView.objects.count() == 2
    assert LocationViewGate.objects.get().last_counted_at == now + timedelta(seconds=3600)
    assert CacheRevision.objects.get(name="locations").version == revision


def test_user_identity_is_stable_across_sessions(location, crud_users, crud_client):
    first = crud_client(crud_users["owner"])
    second = crud_client(crud_users["owner"])
    other = crud_client(crud_users["other"])
    url = f"/api/locations/{location.pk}/"
    assert first.get(url).json()["views_count"] == 1
    assert second.get(url).json()["views_count"] == 1
    assert other.get(url).json()["views_count"] == 2
    assert LocationViewGate.objects.count() == 2
    gate = LocationViewGate.objects.get(visitor_hash=visitor_hash(SimpleNamespace(user=crud_users["owner"])))
    assert len(gate.visitor_hash) == 64


def test_guests_use_distinct_stable_sessions_not_ip(location):
    first = APIClient(enforce_csrf_checks=True)
    second = APIClient(enforce_csrf_checks=True)
    url = f"/api/locations/{location.pk}/"
    assert first.get(url).json()["views_count"] == 1
    first_session = first.cookies["sessionid"].value
    assert first.get(url).json()["views_count"] == 1
    assert first.cookies["sessionid"].value == first_session
    assert second.get(url).json()["views_count"] == 2
    assert second.cookies["sessionid"].value != first_session
    assert all(len(value) == 64 and first_session not in value for value in LocationViewGate.objects.values_list("visitor_hash", flat=True))


def test_head_options_list_and_deleted_objects_do_not_count(location, crud_users, crud_client):
    client = crud_client(crud_users["admin"])
    url = f"/api/locations/{location.pk}/"
    assert client.head(url).status_code == 200
    assert client.options(url).status_code == 200
    assert client.get("/api/locations/").status_code == 200
    Location.objects.filter(pk=location.pk).update(is_deleted=True)
    assert client.get(url).status_code == 200
    guest = APIClient()
    assert guest.get(url).status_code == 404
    assert "sessionid" not in guest.cookies
    assert not LocationView.objects.exists()
    assert not LocationViewGate.objects.exists()


def test_hint_is_scheduled_after_commit_and_skips_gate_queries(
    location, crud_users, crud_client, django_capture_on_commit_callbacks
):
    client = crud_client(crud_users["owner"])
    url = f"/api/locations/{location.pk}/"
    with patch("locations.services.remember_view_hint", wraps=remember_view_hint) as remember:
        with django_capture_on_commit_callbacks(execute=True):
            assert client.get(url).json()["views_count"] == 1
            remember.assert_not_called()
        remember.assert_called_once()

    gate = LocationViewGate.objects.get()
    key = f"view:{location.pk}:{gate.visitor_hash}"
    assert cache.get(key) is not None
    with patch("locations.services.LocationViewGate.objects.get_or_create", side_effect=AssertionError("Hint hit must skip the gate")):
        assert client.get(url).json()["views_count"] == 1
    cache.delete(key)
    assert client.get(url).json()["views_count"] == 1
    assert LocationView.objects.count() == 1


def test_hint_ttl_does_not_exceed_remaining_window(
    location, crud_users, crud_client, django_capture_on_commit_callbacks, settings
):
    client = crud_client(crud_users["owner"])
    visitor = visitor_hash(SimpleNamespace(user=crud_users["owner"]))
    now = timezone.now()
    previous = now - timedelta(seconds=3479.1)
    LocationView.objects.create(location=location, viewed_at=previous)
    LocationViewGate.objects.create(location=location, visitor_hash=visitor, last_counted_at=previous)
    with patch("locations.services.timezone.now", return_value=now):
        with django_capture_on_commit_callbacks(execute=True):
            assert client.get(f"/api/locations/{location.pk}/").json()["views_count"] == 1
    key = cache.make_key(f"view:{location.pk}:{visitor}")
    redis = Redis.from_url(settings.TEST_REDIS_URL)
    try:
        assert 0 < redis.ttl(key) <= 120
    finally:
        redis.close()


@pytest.mark.parametrize("failure", ["read", "write", "both"])
def test_redis_failure_falls_back_to_postgresql(
    location, crud_users, crud_client, django_capture_on_commit_callbacks, failure
):
    client = crud_client(crud_users["owner"])
    url = f"/api/locations/{location.pk}/"
    with patch("core.cache.cache.get", side_effect=RedisError("offline") if failure != "write" else None, return_value=None):
        with patch("core.cache.cache.set", side_effect=RedisError("offline") if failure != "read" else None):
            with django_capture_on_commit_callbacks(execute=True):
                assert client.get(url).json()["views_count"] == 1
                assert client.get(url).json()["views_count"] == 1
    assert LocationView.objects.count() == LocationViewGate.objects.count() == 1


def test_database_failure_rolls_back_gate_and_view(
    location, crud_users, crud_client, django_capture_on_commit_callbacks
):
    client = crud_client(crud_users["owner"])
    with patch("locations.services.LocationView.objects.create", side_effect=OperationalError("write failed")):
        with patch("locations.services.remember_view_hint") as remember:
            with django_capture_on_commit_callbacks(execute=True):
                with pytest.raises(OperationalError):
                    client.get(f"/api/locations/{location.pk}/")
            remember.assert_not_called()
    assert not LocationView.objects.exists()
    assert not LocationViewGate.objects.exists()


def test_gate_pair_is_unique_in_postgresql(location):
    LocationViewGate.objects.create(location=location, visitor_hash="a" * 64)
    with pytest.raises(IntegrityError), transaction.atomic():
        LocationViewGate.objects.create(location=location, visitor_hash="a" * 64)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("role", ["owner", "guest"])
@pytest.mark.parametrize("redis_down", [False, True])
def test_concurrent_views_count_once(location, crud_users, crud_client, role, redis_down):
    client = crud_client(crud_users.get(role))
    if role == "guest":
        client.session.save()
    cookie_jar = deepcopy(client.cookies)
    barrier = Barrier(2)

    def fetch():
        close_old_connections()
        try:
            worker = APIClient(enforce_csrf_checks=True)
            worker.cookies = deepcopy(cookie_jar)
            barrier.wait(timeout=10)
            return worker.get(f"/api/locations/{location.pk}/").status_code
        finally:
            connections.close_all()

    with ExitStack() as stack:
        if redis_down:
            reads = stack.enter_context(patch(
                "django.core.cache.backends.redis.RedisCache.get", side_effect=RedisError("offline")
            ))
            stack.enter_context(patch(
                "django.core.cache.backends.redis.RedisCache.set", side_effect=RedisError("offline")
            ))
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert list(pool.map(lambda _: fetch(), range(2))) == [200, 200]
        if redis_down:
            assert reads.call_count == 2
    assert LocationView.objects.filter(location=location).count() == 1
    assert LocationViewGate.objects.filter(location=location).count() == 1
