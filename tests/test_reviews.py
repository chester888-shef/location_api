import pytest
from django.db import IntegrityError, transaction

from core.models import CacheRevision
from locations.models import Location
from reviews.models import Review, ReviewVote

pytestmark = pytest.mark.django_db


@pytest.fixture
def review(location, crud_users):
    return Review.objects.create(
        location=location, author=crud_users["other"], rating=4, comment="Гарне місце."
    )


def revision():
    return CacheRevision.objects.get(name="locations").version


@pytest.mark.parametrize("role", ["guest", "owner", "other", "staff", "admin"])
def test_review_creation_uses_session_author_and_url_location(
    location, location_data, crud_users, crud_client, api_write, role
):
    another = Location.objects.create(
        author=crud_users["owner"], category_id=location_data["category"],
        **{key: value for key, value in location_data.items() if key != "category"},
    )
    before = revision()
    client = crud_client(crud_users.get(role))
    url = f"/api/locations/{location.pk}/reviews/"
    assert client.get(url).json() == []
    response = api_write(client, "post", url, {
        "rating": 5, "comment": "Варто відвідати.",
        "author": crud_users["admin"].pk, "location": another.pk,
    })
    assert response.status_code == (403 if role == "guest" else 201)
    if role == "guest":
        assert not Review.objects.exists()
        assert revision() == before
    else:
        created = Review.objects.get(pk=response.json()["id"])
        assert created.author == crud_users[role]
        assert created.location == location
        assert created.rating == 5
        assert revision() == before + 1
        assert crud_client().get(url).json()[0]["id"] == created.pk


def test_duplicate_review_is_409_without_revision_change(
    review, crud_users, crud_client, api_write
):
    client = crud_client(crud_users["other"])
    before = revision()
    response = api_write(client, "post", f"/api/locations/{review.location_id}/reviews/", {
        "rating": 2, "comment": "Другий відгук.",
    })
    assert response.status_code == 409
    assert response.json()["detail"]
    assert Review.objects.count() == 1
    assert revision() == before


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"comment": "Текст"}, "rating"), ({"rating": 4}, "comment"),
        ({"rating": 0, "comment": "Текст"}, "rating"),
        ({"rating": 6, "comment": "Текст"}, "rating"),
        ({"rating": 2.5, "comment": "Текст"}, "rating"),
        ({"rating": 4, "comment": ""}, "comment"),
        ({"rating": 4, "comment": "  "}, "comment"),
    ],
)
def test_review_validation(location, crud_users, crud_client, api_write, payload, field):
    client = crud_client(crud_users["owner"])
    before = revision()
    response = api_write(client, "post", f"/api/locations/{location.pk}/reviews/", payload)
    assert response.status_code == 400
    assert field in response.json()
    assert not Review.objects.exists()
    assert revision() == before


@pytest.mark.parametrize("role", ["guest", "owner", "other", "staff", "admin"])
@pytest.mark.parametrize("method", ["patch", "delete"])
def test_review_edit_delete_permissions(
    review, crud_users, crud_client, api_write, role, method
):
    vote = ReviewVote.objects.create(review=review, user=crud_users["owner"], value="like")
    client = crud_client(crud_users.get(role))
    before = revision()
    response = api_write(client, method, f"/api/reviews/{review.pk}/", {
        "rating": 5, "author": crud_users["owner"].pk, "location": 999999,
    })
    allowed = role in {"other", "admin"}
    assert response.status_code == ((200 if method == "patch" else 204) if allowed else 403)
    assert revision() == before + int(allowed)
    if allowed and method == "delete":
        assert not Review.objects.filter(pk=review.pk).exists()
        assert not ReviewVote.objects.filter(pk=vote.pk).exists()
    else:
        review.refresh_from_db()
        assert review.author == crud_users["other"]
        assert review.rating == (5 if allowed else 4)
        assert review.comment == "Гарне місце."


def test_vote_lifecycle_is_per_user_and_does_not_change_revision(
    review, crud_users, crud_client, api_write
):
    client = crud_client(crud_users["owner"])
    url = f"/api/reviews/{review.pk}/vote/"
    before = revision()
    vote_ids = []
    for value in ["like", "like", "dislike"]:
        response = api_write(client, "put", url, {
            "value": value, "user": crud_users["admin"].pk, "review": 999999,
        })
        assert response.status_code == 200
        assert response.json()["value"] == value
        assert response.json()["user"] == crud_users["owner"].pk
        assert response.json()["review"] == review.pk
        vote_ids.append(response.json()["id"])
    assert len(set(vote_ids)) == 1
    assert ReviewVote.objects.count() == 1

    other = crud_client(crud_users["staff"])
    assert api_write(other, "put", url, {"value": "like"}).status_code == 200
    assert ReviewVote.objects.count() == 2
    assert api_write(client, "delete", url).status_code == 204
    assert api_write(client, "delete", url).status_code == 204
    assert ReviewVote.objects.count() == 1
    assert ReviewVote.objects.get().user == crud_users["staff"]
    assert revision() == before


@pytest.mark.parametrize("role", ["other", "admin"])
@pytest.mark.parametrize("method", ["put", "delete"])
def test_self_vote_is_forbidden_even_for_superuser(
    location, crud_users, crud_client, api_write, role, method
):
    review = Review.objects.create(
        location=location, author=crud_users[role], rating=3, comment="Власний відгук."
    )
    client = crud_client(crud_users[role])
    before = revision()
    assert api_write(client, method, f"/api/reviews/{review.pk}/vote/", {"value": "like"}).status_code == 403
    assert not ReviewVote.objects.exists()
    assert revision() == before


@pytest.mark.parametrize("payload", [{}, {"value": "invalid"}, {"value": None}])
def test_vote_rejects_invalid_value(review, crud_users, crud_client, api_write, payload):
    client = crud_client(crud_users["owner"])
    assert api_write(client, "put", f"/api/reviews/{review.pk}/vote/", payload).status_code == 400
    assert not ReviewVote.objects.exists()


@pytest.mark.parametrize("role", ["guest", "owner", "other", "staff", "admin"])
def test_deleted_location_hides_reviews_and_disallows_writes(
    review, crud_users, crud_client, api_write, role
):
    Location.objects.filter(pk=review.location_id).update(is_deleted=True)
    client = crud_client(crud_users.get(role))
    list_url = f"/api/locations/{review.location_id}/reviews/"
    response = client.get(list_url)
    assert response.status_code == (200 if role == "admin" else 404)
    if role == "admin":
        assert response.json()[0]["id"] == review.pk
    before = revision()
    writes = [
        ("post", list_url, {"rating": 2, "comment": "Текст"}),
        ("patch", f"/api/reviews/{review.pk}/", {"rating": 2}),
        ("delete", f"/api/reviews/{review.pk}/", None),
        ("put", f"/api/reviews/{review.pk}/vote/", {"value": "like"}),
        ("delete", f"/api/reviews/{review.pk}/vote/", None),
    ]
    for method, url, data in writes:
        assert api_write(client, method, url, data).status_code == (403 if role == "guest" else 404)
    assert revision() == before
    assert Review.objects.filter(pk=review.pk).exists()


@pytest.mark.parametrize(
    ("method", "vote", "role"),
    [("post", False, "owner"), ("patch", False, "other"), ("delete", False, "other"),
     ("put", True, "owner"), ("delete", True, "owner")],
)
def test_review_and_vote_writes_require_session_csrf(
    review, crud_users, crud_client, api_write, method, vote, role
):
    client = crud_client(crud_users[role])
    if method == "post":
        url, payload = f"/api/locations/{review.location_id}/reviews/", {"rating": 3, "comment": "Текст"}
    elif vote:
        url, payload = f"/api/reviews/{review.pk}/vote/", {"value": "like"}
    else:
        url, payload = f"/api/reviews/{review.pk}/", {"rating": 3}
    before = revision()
    assert getattr(client, method)(url, data=payload, format="json").status_code == 403
    assert revision() == before
    expected = {"post": 201, "patch": 200, "put": 200, "delete": 204}[method]
    assert api_write(client, method, url, payload).status_code == expected


@pytest.mark.parametrize("case", ["review-unique", "rating-low", "rating-high", "vote-unique", "vote-value"])
def test_review_and_vote_constraints_are_enforced_in_postgresql(review, crud_users, case):
    ReviewVote.objects.create(review=review, user=crud_users["owner"], value="like")
    with pytest.raises(IntegrityError), transaction.atomic():
        if case == "review-unique":
            Review.objects.create(location=review.location, author=review.author, rating=1, comment="Дубль")
        elif case.startswith("rating-"):
            Review.objects.filter(pk=review.pk).update(rating=0 if case == "rating-low" else 6)
        elif case == "vote-unique":
            ReviewVote.objects.create(review=review, user=crud_users["owner"], value="dislike")
        else:
            ReviewVote.objects.filter(review=review).update(value="invalid")


def test_review_routes_expose_only_methods_from_spec(review, crud_users, crud_client, api_write):
    client = crud_client(crud_users["other"])
    assert client.get(f"/api/reviews/{review.pk}/").status_code == 405
    assert api_write(client, "put", f"/api/reviews/{review.pk}/", {"rating": 3}).status_code == 405
    assert client.get(f"/api/reviews/{review.pk}/vote/").status_code == 405
    assert api_write(client, "post", f"/api/reviews/{review.pk}/vote/", {"value": "like"}).status_code == 405
