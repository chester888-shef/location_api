from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from redis import Redis
from redis.exceptions import RedisError
from rest_framework.test import APIClient

from categories.models import Category
from core.cache import location_list_cache_key
from locations.models import Location, LocationView
from locations.selectors import validate_location_params
from reviews.models import Review

pytestmark = pytest.mark.django_db


def test_location_cache_hit_needs_only_revision_query(many_locations, django_assert_num_queries):
    client = APIClient()
    with django_assert_num_queries(3):
        first = client.get("/api/locations/")
    with django_assert_num_queries(1):
        second = client.get("/api/locations/")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["count"] == 35
    assert len(first.json()["results"]) == 10
    assert first.json()["previous"] is None


def test_all_pages_are_cached_with_300_second_ttl(many_locations, settings, django_assert_num_queries):
    client = APIClient()
    params = validate_location_params({})
    redis = Redis.from_url(settings.TEST_REDIS_URL)
    for page in range(1, 5):
        first = client.get("/api/locations/", {"page": page})
        assert first.status_code == 200
        key = location_list_cache_key(params, page)
        assert 0 < redis.ttl(cache.make_key(key)) <= 300
        with django_assert_num_queries(1):
            second = client.get("/api/locations/", {"page": page})
        assert second.json() == first.json()
    assert len(second.json()["results"]) == 5
    assert second.json()["next"] is None


def test_canonical_keys_normalize_validated_values_and_ignore_unused_params():
    first = validate_location_params({"rating_min": "1.00", "search": " парк ", "category": "02", "unused": "one"})
    second = validate_location_params({"category": "2", "search": "парк", "rating_min": "1", "unused": "two"})
    assert location_list_cache_key(first, 1) == location_list_cache_key(second, 1)
    assert location_list_cache_key(first, 1).startswith("loc:list:v1:")
    assert len(location_list_cache_key(first, 1).rsplit(":", 1)[1]) == 64
    assert location_list_cache_key(first, 1) != location_list_cache_key(first, 2)
    for field, value in {"search": "інше", "category": 3, "author": 1, "rating_min": "2", "rating_max": "5", "ordering": "-rating"}.items():
        changed = validate_location_params({"category": 2, "search": "парк", "rating_min": 1, field: value})
        assert location_list_cache_key(changed, 1) != location_list_cache_key(first, 1)


def test_cached_links_use_current_host_and_query(many_locations, settings):
    settings.ALLOWED_HOSTS = ["testserver", "other.example"]
    client = APIClient()
    client.get("/api/locations/", {"unused": "first"})
    response = client.get("/api/locations/", {"unused": "second"}, HTTP_HOST="other.example")
    link = urlsplit(response.json()["next"])
    assert link.netloc == "other.example"
    assert parse_qs(link.query) == {"unused": ["second"], "page": ["2"]}
    client.get("/api/locations/", {"page": 2, "unused": "first"})
    response = client.get("/api/locations/", {"page": "02", "unused": "second"})
    assert parse_qs(urlsplit(response.json()["previous"]).query) == {"unused": ["second"]}


def test_guest_and_authenticated_user_share_public_cache(location, crud_users, django_assert_num_queries):
    client = APIClient()
    first = client.get("/api/locations/").json()
    client.force_authenticate(crud_users["owner"])
    with django_assert_num_queries(1):
        second = client.get("/api/locations/").json()
    assert first == second


def test_admin_bypasses_cache_and_deleted_records_never_leak(location, crud_users):
    Location.objects.filter(pk=location.pk).update(is_deleted=True)
    guest = APIClient()
    assert guest.get("/api/locations/").json()["results"] == []
    admin = APIClient()
    admin.force_authenticate(crud_users["admin"])
    with patch("locations.views.get_location_list_cache", side_effect=AssertionError("Admin cache read")), patch("locations.views.cache_location_list", side_effect=AssertionError("Admin cache write")):
        assert admin.get("/api/locations/").json()["results"][0]["id"] == location.pk
    assert guest.get("/api/locations/").json()["results"] == []


@pytest.mark.parametrize("failure", ["read", "write", "both"])
def test_list_redis_failure_falls_back_to_orm(location, failure):
    with patch("core.cache.cache.get", side_effect=RedisError("offline") if failure != "write" else None, return_value=None), patch("core.cache.cache.set", side_effect=RedisError("offline") if failure != "read" else None):
        response = APIClient().get("/api/locations/")
    assert response.status_code == 200
    assert response.json()["results"][0]["id"] == location.pk


def test_evicted_or_expired_cache_is_rebuilt(location, settings, django_assert_num_queries):
    client = APIClient()
    first = client.get("/api/locations/").json()
    key = location_list_cache_key(validate_location_params({}), 1)
    redis = Redis.from_url(settings.TEST_REDIS_URL)
    for remove in (lambda: cache.delete(key), lambda: redis.expire(cache.make_key(key), 0)):
        remove()
        with django_assert_num_queries(3):
            assert client.get("/api/locations/").json() == first


@pytest.mark.parametrize("mutation", [
    "location_create", "location_update", "location_delete",
    "review_create", "review_update", "review_delete", "category_update", "category_delete",
])
def test_mutation_changes_revision_and_refreshes_cached_data(
    location, location_data, crud_users, crud_client, api_write, mutation
):
    if mutation in {"review_update", "review_delete"}:
        review = Review.objects.create(location=location, author=crud_users["owner"], rating=2, comment="Текст")
    guest = APIClient()
    before = guest.get("/api/locations/").json()
    params = validate_location_params({})
    old_key = location_list_cache_key(params, 1)
    client = crud_client(crud_users["admin"] if mutation.startswith("category") else crud_users["owner"])
    operations = {
        "location_create": ("post", "/api/locations/", {**location_data, "title": "Нова"}),
        "location_update": ("patch", f"/api/locations/{location.pk}/", {"title": "Зміна"}),
        "location_delete": ("delete", f"/api/locations/{location.pk}/", None),
        "review_create": ("post", f"/api/locations/{location.pk}/reviews/", {"rating": 5, "comment": "Новий"}),
        "category_update": ("patch", f"/api/categories/{location.category_id}/", {"name": "Нова назва"}),
        "category_delete": ("delete", f"/api/categories/{location.category_id}/", None),
    }
    if mutation in {"review_update", "review_delete"}:
        operations["review_update"] = ("patch", f"/api/reviews/{review.pk}/", {"rating": 5})
        operations["review_delete"] = ("delete", f"/api/reviews/{review.pk}/", None)
    response = api_write(client, *operations[mutation])
    assert response.status_code in {200, 201, 204}
    new_key = location_list_cache_key(params, 1)
    assert old_key != new_key
    assert cache.get(old_key) is not None
    with patch("locations.views.get_location_list_cache", return_value=None) as read:
        after = guest.get("/api/locations/").json()
    read.assert_called_once_with(new_key)
    assert cache.get(new_key) == after
    if mutation != "category_update":
        assert before != after
    if mutation == "category_delete":
        assert after["results"][0]["category"] is None


def test_views_leave_list_cached_but_details_and_exports_are_fresh(location):
    client = APIClient()
    initial = client.get("/api/locations/").json()
    key = location_list_cache_key(validate_location_params({}), 1)
    detail = client.get(f"/api/locations/{location.pk}/").json()
    assert detail["views_count"] == 1
    assert detail["popularity"] > 0
    assert client.get("/api/locations/").json() == initial
    assert location_list_cache_key(validate_location_params({}), 1) == key
    assert client.get("/api/locations/export/json/").json()[0]["views_count"] == 1
    assert LocationView.objects.count() == 1


@pytest.mark.parametrize("resource", ["locations", "categories", "reviews"])
def test_lists_use_ten_items_and_stable_pagination(many_locations, crud_users, resource):
    if resource == "locations":
        url = "/api/locations/"
        expected = [item.pk for item in many_locations]
    elif resource == "categories":
        Category.objects.bulk_create([Category(name=f"Категорія {i}") for i in range(14)])
        url = "/api/categories/"
        expected = list(Category.objects.values_list("id", flat=True))
    else:
        location = many_locations[0]
        for i in range(15):
            user = get_user_model().objects.create_user(f"reviewer-{i}@example.com", "test-password")
            Review.objects.create(location=location, author=user, rating=3, comment="Текст")
        url = f"/api/locations/{location.pk}/reviews/"
        expected = list(Review.objects.values_list("id", flat=True))
    client = APIClient()
    seen = []
    while url:
        response = client.get(url)
        assert response.status_code == 200
        data = response.json()
        assert set(data) == {"count", "next", "previous", "results"}
        assert data["count"] == len(expected)
        assert len(data["results"]) <= 10
        seen.extend(row["id"] for row in data["results"])
        url = data["next"]
    assert seen == expected


@pytest.mark.parametrize("page", ["0", "-1", "1.5", "text", "last", "", "999999", "9" * 100])
@pytest.mark.parametrize("url", ["/api/locations/", "/api/categories/", "/api/locations/{id}/reviews/"])
def test_invalid_pages_return_400(location, page, url):
    response = APIClient().get(url.format(id=location.pk), {"page": page})
    assert response.status_code == 400
    assert "page" in response.json()


def test_invalid_filters_are_checked_before_cache_lookup(location):
    APIClient().get("/api/locations/")
    with patch("locations.views.get_location_list_cache", side_effect=AssertionError("Invalid cache lookup")):
        response = APIClient().get("/api/locations/", {"ordering": "title"})
    assert response.status_code == 400
