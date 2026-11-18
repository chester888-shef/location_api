import pytest
from django.db import IntegrityError, transaction
from rest_framework.exceptions import NotFound

from core.models import CacheRevision
from locations.models import Location
from locations.serializers import LocationSerializer

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("role", ["guest", "owner", "other", "staff", "admin"])
def test_location_creation_sets_author_and_server_fields(
    location_data, crud_users, crud_client, api_write, role
):
    user = crud_users.get(role)
    client = crud_client(user)
    payload = {
        **location_data,
        "author": crud_users["other"].pk,
        "is_deleted": True,
        "created_at": "2000-01-01T00:00:00Z",
    }
    response = api_write(client, "post", "/api/locations/", payload)
    assert response.status_code == (403 if user is None else 201)
    if user is None:
        assert not Location.objects.exists()
    else:
        created = Location.objects.get(pk=response.json()["id"])
        assert created.author == user
        assert not created.is_deleted
        assert created.created_at.year != 2000
        for field, value in location_data.items():
            assert response.json()[field] == value


@pytest.mark.parametrize("role", ["guest", "owner", "other", "staff", "admin"])
@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_location_owner_permissions(
    location, location_data, crud_users, crud_client, api_write, role, method
):
    client = crud_client(crud_users.get(role))
    payload = {"title": "Змінена назва"}
    if method == "put":
        payload = {**location_data, **payload}
    response = api_write(client, method, f"/api/locations/{location.pk}/", payload)
    allowed = role in {"owner", "admin"}
    assert response.status_code == ((204 if method == "delete" else 200) if allowed else 403)
    location.refresh_from_db()
    assert location.is_deleted == (allowed and method == "delete")
    expected_title = "Змінена назва" if allowed and method != "delete" else location_data["title"]
    assert location.title == expected_title
    assert location.description == location_data["description"]
    assert location.author == crud_users["owner"]


@pytest.mark.parametrize("role", ["guest", "owner", "other", "staff", "admin"])
def test_soft_delete_visibility(location, crud_users, crud_client, api_write, role):
    url = f"/api/locations/{location.pk}/"
    owner = crud_client(crud_users["owner"])
    assert api_write(owner, "delete", url).status_code == 204
    assert Location.objects.filter(pk=location.pk, is_deleted=True).exists()

    client = crud_client(crud_users.get(role))
    response = client.get(url)
    assert response.status_code == (200 if role == "admin" else 404)
    ids = [item["id"] for item in client.get("/api/locations/").json()["results"]]
    assert (location.pk in ids) == (role == "admin")
    if role == "admin":
        assert response.json()["is_deleted"] is True
        assert api_write(client, "patch", url, {"title": "Зміна"}).status_code == 404
        assert api_write(client, "delete", url).status_code == 404


@pytest.mark.parametrize("field", ["title", "description", "category", "address", "latitude", "longitude"])
@pytest.mark.parametrize("method", ["post", "put"])
def test_create_and_put_require_all_user_fields(
    location, location_data, crud_users, crud_client, api_write, field, method
):
    client = crud_client(crud_users["owner"])
    payload = location_data.copy()
    payload.pop(field)
    url = "/api/locations/" if method == "post" else f"/api/locations/{location.pk}/"
    response = api_write(client, method, url, payload)
    assert response.status_code == 400
    assert field in response.json()
    assert Location.objects.count() == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "  "), ("description", ""), ("address", ""),
        ("category", None), ("category", 999999),
        ("latitude", -90.1), ("latitude", 90.1),
        ("longitude", -180.1), ("longitude", 180.1),
        ("latitude", "NaN"), ("longitude", "NaN"),
        ("latitude", "Infinity"), ("longitude", "-Infinity"),
    ],
)
def test_location_patch_rejects_invalid_values(
    location, crud_users, crud_client, api_write, field, value
):
    client = crud_client(crud_users["owner"])
    response = api_write(client, "patch", f"/api/locations/{location.pk}/", {field: value})
    assert response.status_code == 400
    assert field in response.json()


@pytest.mark.parametrize(("latitude", "longitude"), [(-90, -180), (-90, 180), (90, -180), (90, 180)])
def test_location_accepts_coordinate_boundaries(
    location_data, crud_users, crud_client, api_write, latitude, longitude
):
    client = crud_client(crud_users["owner"])
    response = api_write(client, "post", "/api/locations/", {
        **location_data, "latitude": latitude, "longitude": longitude,
    })
    assert response.status_code == 201
    assert response.json()["latitude"] == latitude
    assert response.json()["longitude"] == longitude


@pytest.mark.parametrize(
    ("field", "value"),
    [("latitude", 91), ("longitude", 181), ("latitude", float("nan")), ("longitude", float("inf"))],
)
def test_coordinate_ranges_are_enforced_in_postgresql(location, field, value):
    with pytest.raises(IntegrityError), transaction.atomic():
        Location.objects.filter(pk=location.pk).update(**{field: value})


def test_partial_update_rejects_concurrently_deleted_location(location):
    serializer = LocationSerializer(instance=location, data={"title": "Нова назва"}, partial=True)
    assert serializer.is_valid(), serializer.errors
    Location.objects.filter(pk=location.pk).update(is_deleted=True)
    before = Location.objects.filter(pk=location.pk).values().get()
    revision = CacheRevision.objects.get(name="locations").version
    with pytest.raises(NotFound):
        serializer.save()
    location.refresh_from_db()
    assert location.is_deleted
    assert Location.objects.filter(pk=location.pk).values().get() == before
    assert CacheRevision.objects.get(name="locations").version == revision


@pytest.mark.parametrize("method", ["post", "put", "patch"])
@pytest.mark.parametrize(("field", "value"), [
    ("category", "fractional"),
    ("latitude", True), ("latitude", False),
    ("longitude", True), ("longitude", False),
])
def test_location_rejects_lossy_numeric_types(
    location, location_data, crud_users, crud_client, api_write, method, field, value
):
    client = crud_client(crud_users["owner"])
    payload = {} if method == "patch" else location_data.copy()
    payload[field] = location.category_id + 0.75 if value == "fractional" else value
    before = list(Location.objects.values())
    revision = CacheRevision.objects.get(name="locations").version
    url = "/api/locations/" if method == "post" else f"/api/locations/{location.pk}/"
    response = api_write(client, method, url, payload)
    assert response.status_code == 400
    assert field in response.json()
    assert list(Location.objects.values()) == before
    assert CacheRevision.objects.get(name="locations").version == revision


@pytest.mark.parametrize(
    ("resource", "method"),
    [
        ("categories", "post"), ("categories", "put"),
        ("categories", "patch"), ("categories", "delete"),
        ("locations", "post"), ("locations", "put"),
        ("locations", "patch"), ("locations", "delete"),
    ],
)
def test_crud_write_methods_require_session_csrf(
    category, location, location_data, crud_users, crud_client, api_write, resource, method
):
    client = crud_client(crud_users["admin"])
    pk = category.pk if resource == "categories" else location.pk
    url = f"/api/{resource}/" if method == "post" else f"/api/{resource}/{pk}/"
    payload = {"name": "Музеї"} if resource == "categories" else {**location_data, "title": "Нова назва"}
    assert getattr(client, method)(url, data=payload, format="json").status_code == 403
    response = api_write(client, method, url, payload)
    assert response.status_code == {"post": 201, "put": 200, "patch": 200, "delete": 204}[method]
