import pytest
from django.db import IntegrityError, transaction

from categories.models import Category

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("role", ["guest", "owner", "other", "staff", "admin"])
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_category_crud_permissions(category, crud_users, crud_client, api_write, role, method):
    client = crud_client(crud_users.get(role))
    assert client.get("/api/categories/").status_code == 200
    detail = f"/api/categories/{category.pk}/"
    assert client.get(detail).status_code == 200

    url = "/api/categories/" if method == "post" else detail
    response = api_write(client, method, url, {"name": "Музеї"})
    allowed = role != "guest" if method == "post" else role == "admin"
    expected = {"post": 201, "put": 200, "patch": 200, "delete": 204}[method]
    assert response.status_code == (expected if allowed else 403)
    if method == "post":
        assert Category.objects.count() == (2 if allowed else 1)
    elif method == "delete" and allowed:
        assert not Category.objects.filter(pk=category.pk).exists()
    else:
        category.refresh_from_db()
        assert category.name == ("Музеї" if allowed else "Парки")


@pytest.mark.parametrize("payload", [{}, {"name": ""}, {"name": "  "}, {"name": "ПАРКИ"}])
def test_category_name_validation(category, crud_users, crud_client, api_write, payload):
    client = crud_client(crud_users["owner"])
    response = api_write(client, "post", "/api/categories/", payload)
    assert response.status_code == 400
    assert "name" in response.json()
    assert Category.objects.count() == 1


def test_category_update_allows_own_name_but_rejects_duplicates(
    category, crud_users, crud_client, api_write
):
    client = crud_client(crud_users["admin"])
    detail = f"/api/categories/{category.pk}/"
    assert api_write(client, "patch", detail, {"name": "ПАРКИ"}).status_code == 200
    Category.objects.create(name="Музеї")
    assert api_write(client, "patch", detail, {"name": "МУЗЕЇ"}).status_code == 400
    category.refresh_from_db()
    assert category.name == "ПАРКИ"


def test_category_name_is_case_insensitive_in_postgresql(category):
    with pytest.raises(IntegrityError), transaction.atomic():
        Category.objects.create(name="ПАРКИ")


def test_category_delete_preserves_location_and_requires_reassignment(
    category, location, crud_users, crud_client, api_write
):
    admin = crud_client(crud_users["admin"])
    assert api_write(admin, "delete", f"/api/categories/{category.pk}/").status_code == 204
    location.refresh_from_db()
    assert location.category_id is None
    assert not location.is_deleted
    url = f"/api/locations/{location.pk}/"
    assert crud_client().get(url).json()["category"] is None

    owner = crud_client(crud_users["owner"])
    response = api_write(owner, "patch", url, {"title": "Нова назва"})
    assert response.status_code == 400
    assert "category" in response.json()
    replacement = Category.objects.create(name="Сквери")
    response = api_write(owner, "patch", url, {"title": "Нова назва", "category": replacement.pk})
    assert response.status_code == 200
    location.refresh_from_db()
    assert location.category_id == replacement.pk
    assert location.title == "Нова назва"
