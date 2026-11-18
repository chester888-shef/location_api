from uuid import uuid4

import pytest
from django.contrib.auth import authenticate, get_user_model
from django.core.cache import cache
from django.db import IntegrityError, connection, transaction


@pytest.mark.django_db
def test_user_uses_normalized_email_and_hashed_password():
    user = get_user_model().objects.create_user(
        email="  Owner@EXAMPLE.COM  ", password="test-password-123"
    )

    assert user.email == "owner@example.com"
    assert user.password != "test-password-123"
    assert user.check_password("test-password-123")
    assert authenticate(email="OWNER@example.com", password="test-password-123") == user


@pytest.mark.django_db
def test_postgresql_prevents_case_insensitive_duplicate_email():
    user_model = get_user_model()
    user_model.objects.create_user(email="owner@example.com", password="test-password")
    other = user_model.objects.create_user(
        email="other@example.com", password="test-password"
    )

    # QuerySet.update bypasses save(), exercising the database constraint itself.
    with pytest.raises(IntegrityError), transaction.atomic():
        user_model.objects.filter(pk=other.pk).update(email="OWNER@EXAMPLE.COM")


@pytest.mark.django_db
def test_superuser_can_be_created_without_username():
    user = get_user_model().objects.create_superuser(
        email="admin@example.com", password="test-password-123"
    )

    assert user.is_superuser
    assert user.is_staff
    assert user.email == "admin@example.com"


@pytest.mark.django_db
def test_tests_use_a_separate_postgresql_database(settings):
    assert connection.vendor == "postgresql"
    assert connection.settings_dict["NAME"].startswith("test_")
    assert settings.SESSION_ENGINE == "django.contrib.sessions.backends.db"


def test_redis_cache_round_trip_is_isolated(settings):
    assert settings.CACHES["default"]["LOCATION"] == settings.TEST_REDIS_URL
    key = f"setup:{uuid4().hex}"
    try:
        cache.set(key, {"ready": True}, timeout=30)
        assert cache.get(key) == {"ready": True}
    finally:
        cache.delete(key)
