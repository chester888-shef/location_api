import pytest
from django.db import IntegrityError, connection
from rest_framework.exceptions import ValidationError

from categories import services as category_services
from categories.models import Category
from core.models import CacheRevision
from locations.services import create_location, update_location

pytestmark = pytest.mark.django_db


def test_category_services_write_without_serializers():
    before = CacheRevision.objects.get(name="locations").version
    category = category_services.create_category(data={"name": "Парки"})
    assert category.name == "Парки"
    assert CacheRevision.objects.get(name="locations").version == before

    updated = category_services.update_category(category, data={"name": "Музеї"})
    category.refresh_from_db()
    assert updated.pk == category.pk
    assert category.name == "Музеї"
    assert CacheRevision.objects.get(name="locations").version == before + 1


def test_location_services_write_and_return_statistics_without_serializers(
    location_data, category, crud_users
):
    data = {**location_data, "category": category, "author": crud_users["owner"]}
    before = CacheRevision.objects.get(name="locations").version
    location = create_location(data=data)
    assert location.author == crud_users["owner"]
    assert location.category == category
    assert location.rating is None
    assert location.popularity == location.reviews_count == location.views_count == location.views_7d == 0

    updated = update_location(location, data={"title": "Нова назва"})
    location.refresh_from_db()
    assert updated.pk == location.pk
    assert location.title == "Нова назва"
    assert updated.rating is None
    assert updated.popularity == updated.reviews_count == updated.views_count == updated.views_7d == 0
    assert CacheRevision.objects.get(name="locations").version == before + 2


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("operation", ["create", "update"])
def test_category_duplicate_is_translated_only_after_atomic(
    category, monkeypatch, operation
):
    other = Category.objects.create(name="Музеї")
    before = CacheRevision.objects.get(name="locations").version
    translated = []

    def validation_error(detail):
        # A real uniqueness violation must leave the transaction before translation.
        assert not connection.in_atomic_block
        assert Category.objects.count() == 2
        translated.append(detail)
        return ValidationError(detail)

    monkeypatch.setattr(category_services, "ValidationError", validation_error)
    with pytest.raises(ValidationError) as error:
        if operation == "create":
            category_services.create_category(data={"name": "ПАРКИ"})
        else:
            category_services.update_category(other, data={"name": "ПАРКИ"})
    assert error.value.status_code == 400
    assert len(translated) == 1
    assert "name" in error.value.detail
    other.refresh_from_db()
    assert other.name == "Музеї"
    assert CacheRevision.objects.get(name="locations").version == before


@pytest.mark.parametrize("operation", ["create", "update"])
def test_category_services_preserve_unrelated_integrity_errors(category, operation):
    before = CacheRevision.objects.get(name="locations").version
    with pytest.raises(IntegrityError) as error:
        if operation == "create":
            category_services.create_category(data={"name": None})
        else:
            category_services.update_category(category, data={"name": None})
    assert error.value.__cause__.diag.constraint_name != "category_name_ci_unique"
    category.refresh_from_db()
    assert category.name == "Парки"
    assert Category.objects.count() == 1
    assert CacheRevision.objects.get(name="locations").version == before
