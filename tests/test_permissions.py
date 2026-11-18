from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser

from core.permissions import IsSuperuserOrReadOnly
from locations.policies import LocationPermission


@pytest.mark.parametrize(
    ("user", "can_edit_location", "can_edit_category"),
    [
        (AnonymousUser(), False, False),
        (
            SimpleNamespace(pk=1, is_authenticated=True, is_superuser=False),
            True,
            False,
        ),
        (
            SimpleNamespace(pk=2, is_authenticated=True, is_superuser=False),
            False,
            False,
        ),
        (
            SimpleNamespace(pk=3, is_authenticated=True, is_superuser=True),
            True,
            True,
        ),
    ],
    ids=["anonymous", "author", "other-user", "superuser"],
)
@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"])
def test_permissions_follow_role_and_method_matrix(
    user, can_edit_location, can_edit_category, method
):
    request = SimpleNamespace(user=user, method=method)
    location = SimpleNamespace(author_id=1)
    public_read = method in {"GET", "HEAD", "OPTIONS"}

    assert LocationPermission().has_object_permission(request, None, location) == (
        public_read or can_edit_location
    )
    assert IsSuperuserOrReadOnly().has_permission(request, None) == (
        public_read or can_edit_category
    )
