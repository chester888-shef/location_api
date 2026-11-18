from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

import pytest
from django.db import close_old_connections, connections
from rest_framework.test import APIClient

from core.models import CacheRevision
from locations.models import Location
from reviews.models import Review, ReviewVote

pytestmark = pytest.mark.django_db(transaction=True)


def parallel_writes(client, calls):
    cookie_jar = deepcopy(client.cookies)
    barrier = Barrier(len(calls))

    def send(call):
        close_old_connections()
        try:
            worker = APIClient(enforce_csrf_checks=True)
            worker.cookies = deepcopy(cookie_jar)
            method, url, data = call
            barrier.wait(timeout=10)
            response = getattr(worker, method)(
                url, data=data, format="json",
                HTTP_X_CSRFTOKEN=worker.cookies["csrftoken"].value,
            )
            return response.status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(send, calls))


def test_concurrent_duplicate_review_is_created_once(location, crud_users, crud_client):
    client = crud_client(crud_users["owner"])
    revision, _ = CacheRevision.objects.get_or_create(name="locations")
    call = ("post", f"/api/locations/{location.pk}/reviews/", {"rating": 4, "comment": "Текст"})
    assert sorted(parallel_writes(client, [call, call])) == [201, 409]
    assert Review.objects.count() == 1
    assert CacheRevision.objects.get(name="locations").version == revision.version + 1


def test_concurrent_vote_updates_keep_one_vote(location, crud_users, crud_client):
    review = Review.objects.create(
        location=location, author=crud_users["other"], rating=4, comment="Текст"
    )
    client = crud_client(crud_users["owner"])
    revision, _ = CacheRevision.objects.get_or_create(name="locations")
    url = f"/api/reviews/{review.pk}/vote/"
    assert parallel_writes(client, [
        ("put", url, {"value": "like"}), ("put", url, {"value": "dislike"}),
    ]) == [200, 200]
    assert ReviewVote.objects.count() == 1
    assert ReviewVote.objects.get().value in {"like", "dislike"}
    assert CacheRevision.objects.get(name="locations").version == revision.version


def test_concurrent_reviews_do_not_lose_revision_increments(
    location, location_data, crud_users, crud_client
):
    second = Location.objects.create(
        author=crud_users["owner"], category_id=location_data["category"],
        **{key: value for key, value in location_data.items() if key != "category"},
    )
    client = crud_client(crud_users["owner"])
    revision, _ = CacheRevision.objects.get_or_create(name="locations")
    calls = [
        ("post", f"/api/locations/{item.pk}/reviews/", {"rating": 4, "comment": "Текст"})
        for item in [location, second]
    ]
    assert parallel_writes(client, calls) == [201, 201]
    assert Review.objects.count() == 2
    assert CacheRevision.objects.get(name="locations").version == revision.version + 2
