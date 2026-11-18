import csv
from io import StringIO
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from categories.models import Category
from locations.models import Location, LocationView, LocationViewGate
from reviews.models import Review

pytestmark = pytest.mark.django_db

# Public contract from specification v2.0, section 8.3; independent of serializer Meta.
EXPORT_FIELDS = [
    "id", "title", "description", "category", "address", "latitude", "longitude",
    "author", "created_at", "updated_at", "rating", "popularity",
    "reviews_count", "views_count", "views_7d",
]


def csv_rows(response):
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert response["Content-Disposition"] == 'attachment; filename="locations.csv"'
    assert response.content.startswith(b"\xef\xbb\xbf")
    return list(csv.DictReader(StringIO(response.content.decode("utf-8-sig"), newline="")))


def test_exports_include_all_records_and_exact_fields(many_locations):
    client = APIClient()
    data = client.get("/api/locations/export/json/", {"page": "invalid"}).json()
    assert isinstance(data, list)
    assert [row["id"] for row in data] == [item.pk for item in many_locations]
    assert all(list(row) == EXPORT_FIELDS for row in data)
    rows = csv_rows(client.get("/api/locations/export/csv/", HTTP_ACCEPT="text/csv"))
    assert [int(row["id"]) for row in rows] == [item.pk for item in many_locations]
    assert list(rows[0]) == EXPORT_FIELDS
    assert rows[0]["title"] == "Парк Шевченка"
    assert not LocationView.objects.exists()
    assert not LocationViewGate.objects.exists()


@pytest.mark.parametrize("suffix", ["json", "csv"])
@pytest.mark.parametrize("filter_name", ["search", "category", "author", "rating_min", "rating_max"])
def test_export_uses_same_filters_and_order_as_list(location, location_data, crud_users, suffix, filter_name):
    other_category = Category.objects.create(name="Музеї")
    values = {key: value for key, value in location_data.items() if key != "category"}
    other = Location.objects.create(**{**values, "title": "Музей", "description": "Експозиція"}, category=other_category, author=crud_users["other"])
    Review.objects.create(location=location, author=crud_users["owner"], rating=5, comment="Парк")
    Review.objects.create(location=other, author=crud_users["owner"], rating=2, comment="Музей")
    filter_value = {"search": "експозиція", "category": other_category.pk, "author": crud_users["other"].pk, "rating_min": "4", "rating_max": "3"}[filter_name]
    params = {filter_name: filter_value, "ordering": "-popularity"}
    client = APIClient()
    expected = client.get("/api/locations/", params).json()["results"]
    response = client.get(f"/api/locations/export/{suffix}/", params)
    rows = response.json() if suffix == "json" else csv_rows(response)
    assert [int(row["id"]) for row in rows] == [row["id"] for row in expected]
    assert len(rows) == 1


@pytest.mark.parametrize("suffix", ["json", "csv"])
@pytest.mark.parametrize("role", ["guest", "owner", "admin"])
def test_export_visibility_matches_list_and_bypasses_cache(location, crud_users, suffix, role):
    Location.objects.filter(pk=location.pk).update(is_deleted=True)
    client = APIClient()
    if role != "guest":
        client.force_authenticate(crud_users[role])
    with patch("locations.views.get_location_list_cache", side_effect=AssertionError("Export cache read")), patch("locations.views.cache_location_list", side_effect=AssertionError("Export cache write")):
        response = client.get(f"/api/locations/export/{suffix}/")
    rows = response.json() if suffix == "json" else csv_rows(response)
    assert len(rows) == (1 if role == "admin" else 0)


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
@pytest.mark.parametrize("body", ["1+1", 'Формула,"рядок"\nнаступний рядок'])
def test_csv_escapes_text_formulas_without_changing_numbers(location, prefix, body):
    text = prefix + body
    Location.objects.filter(pk=location.pk).update(title=text, description=text, address=text, latitude=-48.45, longitude=-34.98)
    client = APIClient()
    row = csv_rows(client.get("/api/locations/export/csv/"))[0]
    for field in ("title", "description", "address"):
        assert row[field] == "'" + text
    assert row["latitude"] == "-48.45"
    assert row["longitude"] == "-34.98"
    assert client.get("/api/locations/export/json/").json()[0]["title"] == text


def test_csv_preserves_null_category_and_numeric_ids(location, location_data, crud_users):
    values = {key: value for key, value in location_data.items() if key != "category"}
    other = Location.objects.create(**values, category=None, author=crud_users["other"])
    rows = csv_rows(APIClient().get("/api/locations/export/csv/"))
    assert rows[0]["category"] == str(location.category_id)
    assert rows[1]["id"] == str(other.pk)
    assert rows[1]["category"] == rows[1]["rating"] == ""
    assert rows[1]["reviews_count"] == rows[1]["views_count"] == rows[1]["views_7d"] == "0"


def test_empty_exports_have_array_and_csv_header():
    client = APIClient()
    assert client.get("/api/locations/export/json/").json() == []
    response = client.get("/api/locations/export/csv/")
    assert csv_rows(response) == []
    header = next(csv.reader(StringIO(response.content.decode("utf-8-sig"))))
    assert header == EXPORT_FIELDS


@pytest.mark.parametrize("suffix", ["json", "csv"])
@pytest.mark.parametrize("params", [{"ordering": "title"}, {"rating_min": 5, "rating_max": 2}, {"author": "bad"}])
def test_invalid_export_filters_return_standard_json_errors(suffix, params):
    response = APIClient().get(f"/api/locations/export/{suffix}/", params)
    assert response.status_code == 400
    assert response["Content-Type"].startswith("application/json")
    assert response.json()


def test_exports_use_fresh_annotations_and_do_not_count_views(location):
    client = APIClient()
    assert client.get("/api/locations/").json()["results"][0]["views_count"] == 0
    LocationView.objects.create(location=location)
    assert client.get("/api/locations/export/json/").json()[0]["views_count"] == 1
    assert csv_rows(client.get("/api/locations/export/csv/"))[0]["views_count"] == "1"
    assert client.get("/api/locations/").json()["results"][0]["views_count"] == 0
    assert LocationView.objects.count() == 1
    assert not LocationViewGate.objects.exists()
