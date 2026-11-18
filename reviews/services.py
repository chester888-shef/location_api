from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied

from core.cache import bump_location_revision
from core.exceptions import Conflict
from locations.models import Location
from reviews.models import Review, ReviewVote
from reviews.policies import can_modify_review


def _lock_active_review(review):
    # Always lock the parent first, including vote operations and Soft Delete races.
    get_object_or_404(
        Location.objects.select_for_update(), pk=review.location_id, is_deleted=False
    )
    return get_object_or_404(Review.objects.select_for_update(), pk=review.pk)


@transaction.atomic
def create_review(*, location_id, actor, rating, comment):
    location = get_object_or_404(
        Location.objects.select_for_update(), pk=location_id, is_deleted=False
    )
    try:
        review = Review.objects.create(
            location=location, author=actor, rating=rating, comment=comment
        )
    except IntegrityError as error:
        diagnostic = getattr(error.__cause__, "diag", None)
        if getattr(diagnostic, "constraint_name", None) == "review_location_author_unique":
            raise Conflict("Ви вже залишили відгук до цієї локації.") from error
        raise
    bump_location_revision()
    return review


@transaction.atomic
def update_review(review, *, actor, data):
    review = _lock_active_review(review)
    if not can_modify_review(actor, review):
        raise PermissionDenied("Змінювати можна лише власні відгуки.")
    for field, value in data.items():
        setattr(review, field, value)
    review.save(update_fields=[*data, "updated_at"])
    bump_location_revision()
    return review


@transaction.atomic
def delete_review(review, *, actor):
    review = _lock_active_review(review)
    if not can_modify_review(actor, review):
        raise PermissionDenied("Видаляти можна лише власні відгуки.")
    review.delete()
    bump_location_revision()


@transaction.atomic
def put_vote(review, *, actor, value):
    review = _lock_active_review(review)
    if review.author_id == actor.pk:
        raise PermissionDenied("Не можна голосувати за власний відгук.")
    vote, _ = ReviewVote.objects.update_or_create(
        review=review, user=actor, defaults={"value": value}
    )
    return vote


@transaction.atomic
def delete_vote(review, *, actor):
    review = _lock_active_review(review)
    if review.author_id == actor.pk:
        raise PermissionDenied("Не можна голосувати за власний відгук.")
    ReviewVote.objects.filter(review=review, user=actor).delete()
