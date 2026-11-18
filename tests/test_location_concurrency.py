from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from threading import Event
from time import monotonic

import pytest
from django.db import close_old_connections, connection, connections
from rest_framework.test import APIClient

from categories.models import Category
from categories.views import CategoryViewSet
from core.models import CacheRevision
from locations.models import Location
from locations.views import LocationViewSet

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def seed_revision(db):
    # Transactional tests flush migration data between cases.
    CacheRevision.objects.get_or_create(name="locations")


def send_from_connection(client, method, url, payload, *, wrapper=None, started=None, backend=None):
    close_old_connections()
    try:
        worker = APIClient(enforce_csrf_checks=True)
        worker.cookies = deepcopy(client.cookies)
        worker.raise_request_exception = client.raise_request_exception
        if backend is not None:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                backend.append(cursor.fetchone()[0])
        if started is not None:
            started.set()
        with connection.execute_wrapper(wrapper) if wrapper else nullcontext():
            return getattr(worker, method)(
                url, data=payload, format="json",
                HTTP_X_CSRFTOKEN=worker.cookies["csrftoken"].value,
            )
    finally:
        connections.close_all()


@contextmanager
def pause_after_validation(monkeypatch, view, hook, client, method, url, payload):
    ready, resume = Event(), Event()
    original = getattr(view, hook)

    def pause(self, serializer):
        ready.set()
        assert resume.wait(10), "Write was not resumed"
        return original(self, serializer)

    monkeypatch.setattr(view, hook, pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(send_from_connection, client, method, url, payload)
        try:
            assert ready.wait(10), "Request did not reach validated write"
            yield resume, future
        finally:
            resume.set()


def revision():
    return CacheRevision.objects.get(name="locations").version


@pytest.mark.parametrize("method", ["patch", "put"])
def test_location_update_loses_to_soft_delete_without_changing_data(
    location, location_data, crud_users, crud_client, api_write, monkeypatch, method
):
    owner = crud_client(crud_users["owner"])
    url = f"/api/locations/{location.pk}/"
    payload = {"title": "Must not be saved"}
    if method == "put":
        payload = {**location_data, **payload}
    with pause_after_validation(monkeypatch, LocationViewSet, "perform_update", owner, method, url, payload) as (resume, future):
        assert api_write(owner, "delete", url).status_code == 204
        before = Location.objects.values().get(pk=location.pk)
        after_delete_revision = revision()
        resume.set()
        response = future.result(timeout=10)
    assert response.status_code == 404
    assert Location.objects.values().get(pk=location.pk) == before
    assert before["is_deleted"] is True
    assert revision() == after_delete_revision


def test_location_patch_requires_category_deleted_after_validation(
    location, crud_users, crud_client, api_write, monkeypatch
):
    owner, admin = crud_client(crud_users["owner"]), crud_client(crud_users["admin"])
    url = f"/api/locations/{location.pk}/"
    with pause_after_validation(monkeypatch, LocationViewSet, "perform_update", owner, "patch", url, {"title": "Must not be saved"}) as (resume, future):
        assert api_write(admin, "delete", f"/api/categories/{location.category_id}/").status_code == 204
        before = Location.objects.values().get(pk=location.pk)
        after_delete_revision = revision()
        resume.set()
        response = future.result(timeout=10)
    assert response.status_code == 400
    assert "category" in response.json()
    assert before["category_id"] is None
    assert Location.objects.values().get(pk=location.pk) == before
    assert revision() == after_delete_revision


@pytest.mark.parametrize("method", ["patch", "put"])
def test_category_update_cannot_resurrect_deleted_row(
    location, crud_users, crud_client, api_write, monkeypatch, method
):
    admin = crud_client(crud_users["admin"])
    category_id = location.category_id
    url = f"/api/categories/{category_id}/"
    with pause_after_validation(monkeypatch, CategoryViewSet, "perform_update", admin, method, url, {"name": "Must not return"}) as (resume, future):
        assert api_write(admin, "delete", url).status_code == 204
        after_delete_revision = revision()
        resume.set()
        response = future.result(timeout=10)
    assert response.status_code == 404
    assert not Category.objects.filter(pk=category_id).exists()
    location.refresh_from_db()
    assert location.category_id is None
    assert revision() == after_delete_revision


@pytest.mark.parametrize("method", ["post", "put", "patch"])
def test_location_rejects_category_deleted_after_fk_validation(
    location, location_data, crud_users, crud_client, api_write, monkeypatch, method
):
    owner, admin = crud_client(crud_users["owner"]), crud_client(crud_users["admin"])
    target = Category.objects.create(name="Temporary target")
    payload = {"category": target.pk}
    if method != "patch":
        payload = {**location_data, **payload, "title": "Must not be saved"}
    url = "/api/locations/" if method == "post" else f"/api/locations/{location.pk}/"
    hook = "perform_create" if method == "post" else "perform_update"
    owner.raise_request_exception = False
    with pause_after_validation(monkeypatch, LocationViewSet, hook, owner, method, url, payload) as (resume, future):
        assert api_write(admin, "delete", f"/api/categories/{target.pk}/").status_code == 204
        before = list(Location.objects.values())
        after_delete_revision = revision()
        resume.set()
        response = future.result(timeout=10)
    assert response.status_code == 400
    assert "category" in response.json()
    assert list(Location.objects.values()) == before
    assert revision() == after_delete_revision


def wait_for_database_lock(backend, future):
    deadline = monotonic() + 5
    pause = Event()
    while monotonic() < deadline:
        if future.done():
            future.result()  # Surface unexpected errors instead of hiding them in a timeout.
            pytest.fail("Category deletion finished before the location write committed")
        with connection.cursor() as cursor:
            cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [backend])
            row = cursor.fetchone()
        if row and row[0] == "Lock":
            return
        pause.wait(0.01)
    pytest.fail("Category deletion did not reach a PostgreSQL row lock")


@pytest.mark.parametrize(("method", "change_category"), [
    ("post", True), ("patch", True), ("patch", False),
])
def test_location_write_holds_category_until_commit(
    location, location_data, crud_users, crud_client, method, change_category
):
    owner, admin = crud_client(crud_users["owner"]), crud_client(crud_users["admin"])
    target = (
        Category.objects.create(name="Locked target") if change_category
        else Category.objects.get(pk=location.category_id)
    )
    ready, resume, delete_started = Event(), Event(), Event()
    backend = []
    operation = 'INSERT INTO "locations_location"' if method == "post" else 'UPDATE "locations_location"'

    def pause_write(execute, sql, params, many, context):
        if sql.startswith(operation):
            ready.set()
            assert resume.wait(10), "Location write was not resumed"
        return execute(sql, params, many, context)

    payload = {"title": "Saved before category deletion"}
    if change_category:
        payload["category"] = target.pk
    if method == "post":
        payload = {**location_data, **payload}
    url = "/api/locations/" if method == "post" else f"/api/locations/{location.pk}/"
    before_revision = revision()
    with ThreadPoolExecutor(max_workers=2) as pool:
        write = pool.submit(send_from_connection, owner, method, url, payload, wrapper=pause_write)
        try:
            assert ready.wait(10), "Location request did not reach its write"
            delete = pool.submit(
                send_from_connection, admin, "delete", f"/api/categories/{target.pk}/", None,
                started=delete_started, backend=backend,
            )
            assert delete_started.wait(10)
            wait_for_database_lock(backend[0], delete)
        finally:
            resume.set()
        response = write.result(timeout=10)
        assert delete.result(timeout=10).status_code == 204
    assert response.status_code == (201 if method == "post" else 200)
    saved = Location.objects.get(pk=response.json()["id"])
    assert saved.title == payload["title"]
    assert saved.category_id is None
    assert not Category.objects.filter(pk=target.pk).exists()
    assert revision() == before_revision + 2
