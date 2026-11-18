from datetime import timedelta
from math import log1p

import pytest
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone
from rest_framework.test import APIClient

from categories.models import Category
from locations.models import Location, LocationView
from locations.selectors import build_location_queryset, validate_location_params
from reviews.models import Review

pytestmark = pytest.mark.django_db


def test_orm_statistics_do_not_multiply_reviews_and_include_seven_day_boundary(
    location, crud_users
):
    now = timezone.now()
    for user, rating in zip(list(crud_users.values())[:3], [1, 3, 5]):
        Review.objects.create(location=location, author=user, rating=rating, comment="Текст")
    times = [
        now - timedelta(days=7, microseconds=1), now - timedelta(days=7),
        now - timedelta(days=7) + timedelta(microseconds=1), now, now,
    ]
    LocationView.objects.bulk_create([LocationView(location=location, viewed_at=at) for at in times])
    result = Location.objects.with_statistics(at=now).get(pk=location.pk)
    assert result.rating == 3
    assert result.reviews_count == 3
    assert result.views_count == 5
    assert result.views_7d == 4
    assert result.popularity == pytest.approx(0.5 * 3 + 0.3 * log1p(3) + 0.2 * log1p(4))


def test_location_without_reviews_has_null_rating_and_zero_popularity(location):
    result = Location.objects.with_statistics().get(pk=location.pk)
    assert result.rating is None
    assert result.reviews_count == result.views_count == result.views_7d == 0
    assert result.popularity == 0
    assert not {"rating", "popularity"} & {field.name for field in Location._meta.concrete_fields}


@pytest.fixture
def catalog(location, location_data, crud_users):
    Location.objects.filter(pk=location.pk).update(title="Високий рейтинг", description="Тихий парк")
    values = {key: value for key, value in location_data.items() if key != "category"}
    museum = Category.objects.create(name="Музеї")
    second = Location.objects.create(
        **{**values, "title": "Музей", "description": "Зала експозицій"},
        category=museum, author=crud_users["other"],
    )
    third = Location.objects.create(
        **{**values, "title": "Озеро", "description": "Тихе місце"},
        category_id=location_data["category"], author=crud_users["staff"],
    )
    for user, rating in zip(list(crud_users.values())[:3], [4, 5, 5]):
        Review.objects.create(location=location, author=user, rating=rating, comment="Текст")
    Review.objects.create(location=second, author=crud_users["owner"], rating=2, comment="Текст")
    now = timezone.now()
    for index, item in enumerate([location, second, third]):
        Location.objects.filter(pk=item.pk).update(created_at=now - timedelta(days=index))
    return [location, second, third]


def test_rating_filter_uses_exact_average_while_response_rounds(catalog):
    client = APIClient()
    location_id = catalog[0].pk
    for params in ({}, {"rating_min": "4.66"}):
        response = client.get("/api/locations/", params)
        assert response.status_code == 200
        row = next(item for item in response.json()["results"] if item["id"] == location_id)
        assert row["rating"] == 4.67
    response = client.get("/api/locations/", {"rating_min": "4.67"})
    assert response.status_code == 200
    assert location_id not in [item["id"] for item in response.json()["results"]]


@pytest.mark.parametrize(
    ("params", "positions"),
    [
        ({"search": "МУЗЕЙ"}, [1]), ({"search": "тихий"}, [0]),
        ({"rating_min": "3"}, [0]), ({"rating_max": "3"}, [1]),
        ({"rating_max": "5"}, [0, 1]),
        ({"rating_min": "4.668"}, []),
        ({"rating_min": "2", "rating_max": "5"}, [0, 1]),
        ({"category": "999999"}, []),
    ],
)
def test_list_search_and_rating_filters_run_in_sql(catalog, params, positions):
    response = APIClient().get("/api/locations/", params)
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["results"]] == [catalog[index].pk for index in positions]


def test_filters_by_category_author_and_combined_parameters(catalog, crud_users):
    client = APIClient()
    first, second, third = catalog
    response = client.get("/api/locations/", {"category": first.category_id})
    assert [row["id"] for row in response.json()["results"]] == [first.pk, third.pk]
    response = client.get("/api/locations/", {"author": crud_users["other"].pk})
    assert [row["id"] for row in response.json()["results"]] == [second.pk]
    response = client.get("/api/locations/", {
        "category": first.category_id, "rating_min": 4, "search": "парк", "ordering": "-rating",
    })
    assert [row["id"] for row in response.json()["results"]] == [first.pk]


@pytest.mark.parametrize(
    ("ordering", "positions"),
    [
        ("created_at", [2, 1, 0]), ("-created_at", [0, 1, 2]),
        ("rating", [1, 0, 2]), ("-rating", [0, 1, 2]),
        ("popularity", [2, 1, 0]), ("-popularity", [0, 1, 2]),
    ],
)
def test_list_ordering_is_stable_and_null_ratings_are_last(catalog, ordering, positions):
    response = APIClient().get("/api/locations/", {"ordering": ordering})
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["results"]] == [catalog[index].pk for index in positions]
    if ordering == "-rating":
        assert response.json()["results"][0]["rating"] == 4.67


@pytest.mark.parametrize("ordering", ["created_at", "-created_at"])
def test_ordering_ties_use_id(catalog, ordering):
    Location.objects.all().update(created_at=timezone.now())
    response = APIClient().get("/api/locations/", {"ordering": ordering})
    assert [row["id"] for row in response.json()["results"]] == sorted(item.pk for item in catalog)


@pytest.mark.parametrize("params", [
    {"ordering": "title"}, {"ordering": "rating,popularity"},
    {"rating_min": "0"}, {"rating_max": "6"}, {"rating_min": "text"},
    {"rating_min": "NaN"}, {"rating_max": "Infinity"},
    {"rating_min": "4", "rating_max": "2"},
    {"category": "text"}, {"category": "1.5"}, {"category": "0"}, {"author": "-1"},
])
def test_invalid_list_parameters_return_400(params):
    assert APIClient().get("/api/locations/", params).status_code == 400


def test_selector_is_lazy_and_shared_statistics_need_one_sql_query(
    catalog, django_assert_num_queries
):
    with django_assert_num_queries(0):
        queryset = build_location_queryset(AnonymousUser(), validate_location_params({"rating_min": 2}))
    with django_assert_num_queries(1):
        results = list(queryset)
    assert len(results) == 2


def test_location_list_has_no_n_plus_one_queries(
    location, location_data, crud_users, django_assert_num_queries
):
    client = APIClient()
    client.force_authenticate(crud_users["admin"])
    with django_assert_num_queries(2):
        assert client.get("/api/locations/").status_code == 200
    values = {key: value for key, value in location_data.items() if key != "category"}
    Location.objects.bulk_create([
        Location(**values, category_id=location_data["category"], author=crud_users["owner"])
        for _ in range(20)
    ])
    with django_assert_num_queries(2):
        response = client.get("/api/locations/")
    assert response.json()["count"] == 21
    assert len(response.json()["results"]) == 10


def test_computed_fields_are_server_controlled_in_write_responses(
    location_data, crud_users, crud_client, api_write
):
    client = crud_client(crud_users["owner"])
    response = api_write(client, "post", "/api/locations/", {
        **location_data, "rating": 5, "popularity": 999,
        "reviews_count": 99, "views_count": 99, "views_7d": 99,
    })
    assert response.status_code == 201
    assert response.json()["rating"] is None
    assert response.json()["popularity"] == 0
    assert response.json()["reviews_count"] == response.json()["views_count"] == response.json()["views_7d"] == 0
    assert not LocationView.objects.exists()


def test_filtered_lists_do_not_reveal_deleted_locations(catalog, crud_users, crud_client):
    Location.objects.filter(pk=catalog[0].pk).update(is_deleted=True)
    params = {"rating_min": 4}
    assert APIClient().get("/api/locations/", params).json()["results"] == []
    admin = crud_client(crud_users["admin"])
    assert admin.get("/api/locations/", params).json()["results"][0]["id"] == catalog[0].pk
