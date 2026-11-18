import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from categories.models import Category
from core.cache import bump_location_revision
from core.models import CacheRevision
from locations.models import Location
from reviews.models import Review, ReviewVote

pytestmark = pytest.mark.django_db


@pytest.fixture
def mutations(location, category, location_data, crud_users):
    review = Review.objects.create(
        location=location, author=crud_users["other"], rating=4, comment="Текст."
    )
    ReviewVote.objects.create(review=review, user=crud_users["admin"], value="like")
    return {
        "location-create": ("post", "/api/locations/", location_data, "locations.services"),
        "location-update": ("patch", f"/api/locations/{location.pk}/", {"title": "Зміна"}, "locations.services"),
        "location-delete": ("delete", f"/api/locations/{location.pk}/", None, "locations.services"),
        "category-create": ("post", "/api/categories/", {"name": "Нова"}, None),
        "category-update": ("patch", f"/api/categories/{category.pk}/", {"name": "Зміна"}, "categories.services"),
        "category-delete": ("delete", f"/api/categories/{category.pk}/", None, "categories.services"),
        "review-create": ("post", f"/api/locations/{location.pk}/reviews/", {"rating": 3, "comment": "Новий"}, "reviews.services"),
        "review-update": ("patch", f"/api/reviews/{review.pk}/", {"rating": 2}, "reviews.services"),
        "review-delete": ("delete", f"/api/reviews/{review.pk}/", None, "reviews.services"),
        "vote": ("put", f"/api/reviews/{review.pk}/vote/", {"value": "dislike"}, None),
    }


@pytest.mark.parametrize("case", [
    "location-create", "location-update", "location-delete", "category-create",
    "category-update", "category-delete", "review-create", "review-update", "review-delete", "vote",
])
def test_relevant_mutations_increment_revision(mutations, crud_users, crud_client, api_write, case):
    client = crud_client(crud_users["admin"])
    before = CacheRevision.objects.get(name="locations").version
    method, url, data, _ = mutations[case]
    response = api_write(client, method, url, data)
    assert response.status_code == {"post": 201, "patch": 200, "put": 200, "delete": 204}[method]
    increment = 0 if case in {"category-create", "vote"} else 1
    assert CacheRevision.objects.get(name="locations").version == before + increment


def database_state():
    models = [Category, Location, Review, ReviewVote, CacheRevision]
    return [list(model.objects.order_by("pk").values()) for model in models]


@pytest.mark.parametrize("case", [
    "location-create", "location-update", "location-delete", "category-update",
    "category-delete", "review-create", "review-update", "review-delete",
])
def test_revision_failure_rolls_back_data_and_revision(
    mutations, crud_users, crud_client, api_write, monkeypatch, case
):
    client = crud_client(crud_users["admin"])
    before = database_state()
    method, url, data, module = mutations[case]
    revision = CacheRevision.objects.get(name="locations").version
    observed_versions = []

    def fail_revision():
        bump_location_revision()
        observed_versions.append(CacheRevision.objects.get(name="locations").version)
        raise RuntimeError("Simulated revision write failure")

    monkeypatch.setattr(f"{module}.bump_location_revision", fail_revision)
    with pytest.raises(RuntimeError, match="Simulated revision write failure"):
        api_write(client, method, url, data)
    assert observed_versions == [revision + 1]
    assert database_state() == before


def test_revision_fixture_has_one_row_and_only_allows_locations():
    assert CacheRevision.objects.count() == 1
    assert CacheRevision.objects.get(name="locations").version == 1
    with pytest.raises(IntegrityError), transaction.atomic():
        CacheRevision.objects.create(name="other")


def test_data_migration_seeds_an_empty_revision_table():
    # Reversing 0002 is a noop, so explicitly remove fixture/migration data.
    CacheRevision.objects.all().delete()
    assert not CacheRevision.objects.exists()
    executor = MigrationExecutor(connection)
    state = executor.loader.project_state([("core", "0001_initial")])
    migration = executor.loader.get_migration("core", "0002_seed_cache_revision")
    with connection.schema_editor() as schema_editor:
        migration.apply(state.clone(), schema_editor)
    assert list(CacheRevision.objects.values("name", "version")) == [
        {"name": "locations", "version": 1}
    ]
