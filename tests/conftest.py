import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from categories.models import Category
from locations.models import Location

CRUD_PASSWORD = "Map-Dnipro!2026-Reader"


@pytest.fixture
def crud_users(db):
    users = get_user_model().objects
    return {
        "owner": users.create_user("owner@example.com", CRUD_PASSWORD),
        "other": users.create_user("other@example.com", CRUD_PASSWORD),
        "staff": users.create_user("staff@example.com", CRUD_PASSWORD, is_staff=True),
        "admin": users.create_superuser("admin@example.com", CRUD_PASSWORD),
    }


@pytest.fixture
def crud_client():
    def create(user=None):
        client = APIClient(enforce_csrf_checks=True)
        assert client.get("/api/auth/csrf/").status_code == 200
        if user is not None:
            response = client.post(
                "/api/auth/login/",
                {"email": user.email, "password": CRUD_PASSWORD},
                format="json",
                HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
            )
            assert response.status_code == 200
        return client

    return create


@pytest.fixture
def api_write():
    def write(client, method, url, data=None):
        return getattr(client, method)(
            url, data=data, format="json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        )

    return write


@pytest.fixture
def category(db):
    return Category.objects.create(name="Парки")


@pytest.fixture
def location_data(category):
    return {
        "title": "Парк Шевченка",
        "description": "Місце для прогулянок.",
        "category": category.pk,
        "address": "Київ, бульвар Тараса Шевченка",
        "latitude": 50.4416,
        "longitude": 30.5116,
    }


@pytest.fixture
def location(crud_users, location_data):
    values = {**location_data, "category_id": location_data["category"]}
    values.pop("category")
    return Location.objects.create(author=crud_users["owner"], **values)
