from datetime import datetime, timedelta
from smtplib import SMTPException
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.contrib.sessions.models import Session
from django.core.mail import get_connection
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient

PASSWORD = "Map-Dnipro!2026-Reader"
NEW_PASSWORD = "Map-Dnipro!2026-Updated"
AUTH = "/api/auth/"


@pytest.fixture
def client():
    client = APIClient(enforce_csrf_checks=True)
    response = client.get(f"{AUTH}csrf/")
    assert response.status_code == 200
    assert response.json()["csrfToken"]
    assert client.cookies["csrftoken"].value
    return client


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(
        email="reader@example.com", password=PASSWORD
    )


def post(client, endpoint, data):
    return client.post(
        f"{AUTH}{endpoint}/",
        data,
        format="json",
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
    )


@pytest.mark.django_db
def test_registration_normalizes_email_and_protects_server_fields(client):
    password = f"  {PASSWORD}  "
    response = post(
        client,
        "register",
        {
            "email": "  New.Reader@EXAMPLE.COM  ",
            "password": password,
            "is_superuser": True,
            "is_staff": True,
        },
    )
    assert response.status_code == 201
    assert set(response.json()) == {"id", "email"}
    assert response.json()["email"] == "new.reader@example.com"
    created = get_user_model().objects.get(pk=response.json()["id"])
    assert created.check_password(password)
    assert not created.is_superuser
    assert not created.is_staff
    assert "_auth_user_id" not in client.session

    duplicate = post(
        client, "register", {"email": "NEW.READER@example.com", "password": password}
    )
    assert duplicate.status_code == 400
    assert "email" in duplicate.json()
    assert get_user_model().objects.count() == 1
    assert post(
        client, "login", {"email": "new.reader@example.com", "password": password}
    ).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({}, "email"),
        ({"email": "invalid", "password": PASSWORD}, "email"),
        ({"email": "reader@example.com"}, "password"),
        ({"email": "reader@example.com", "password": "short"}, "password"),
        ({"email": "reader@example.com", "password": "123456789012"}, "password"),
        ({"email": "reader@example.com", "password": "password"}, "password"),
        (
            {"email": "reader@example.com", "password": "reader@example.com"},
            "password",
        ),
    ],
)
def test_registration_rejects_invalid_input(client, payload, field):
    response = post(client, "register", payload)
    assert response.status_code == 400
    assert field in response.json()
    assert not get_user_model().objects.exists()


@pytest.mark.django_db
def test_session_login_and_logout(client, user):
    old_csrf = client.cookies["csrftoken"].value
    response = post(
        client, "login", {"email": "READER@EXAMPLE.COM", "password": PASSWORD}
    )
    assert response.status_code == 200
    assert response.json() == {"id": user.pk, "email": user.email}
    assert client.session["_auth_user_id"] == str(user.pk)
    assert response.cookies["sessionid"]["httponly"]
    assert response.cookies["sessionid"]["samesite"] == "Lax"
    assert client.cookies["csrftoken"].value != old_csrf
    session_key = client.session.session_key

    assert post(client, "logout", {}).status_code == 204
    assert not Session.objects.filter(session_key=session_key).exists()
    assert post(client, "logout", {}).status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("case", ["wrong-password", "unknown-email", "inactive-user"])
def test_login_rejects_invalid_credentials(client, user, case):
    email, password = user.email, PASSWORD
    if case == "wrong-password":
        password = "Wrong-Password!64"
    elif case == "unknown-email":
        email = "unknown@example.com"
    else:
        user.is_active = False
        user.save(update_fields=["is_active"])

    response = post(client, "login", {"email": email, "password": password})
    assert response.status_code == 400
    assert "sessionid" not in response.cookies
    assert response.json() == {"non_field_errors": ["Невірний email або пароль."]}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        ("register", {"email": "new@example.com", "password": PASSWORD}),
        ("login", {"email": "reader@example.com", "password": PASSWORD}),
        ("password-reset", {"email": "reader@example.com"}),
        (
            "password-reset/confirm",
            {"uid": "MQ", "token": "invalid", "new_password": NEW_PASSWORD},
        ),
    ],
)
@pytest.mark.parametrize("failure", ["no-cookie", "no-header", "wrong-token", "foreign-origin"])
def test_anonymous_auth_posts_require_csrf(client, endpoint, payload, failure):
    headers = {"HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value}
    if failure == "no-cookie":
        client.cookies.clear()
    elif failure == "no-header":
        headers.clear()
    elif failure == "wrong-token":
        headers["HTTP_X_CSRFTOKEN"] = "x" * 32
    else:
        headers["HTTP_ORIGIN"] = "https://foreign.example"

    response = client.post(f"{AUTH}{endpoint}/", payload, format="json", **headers)
    assert response.status_code == 403
    assert not get_user_model().objects.exists()


@pytest.mark.django_db
def test_authenticated_logout_requires_current_csrf(client, user):
    old_csrf = client.cookies["csrftoken"].value
    assert post(
        client, "login", {"email": user.email, "password": PASSWORD}
    ).status_code == 200

    assert client.post(f"{AUTH}logout/", {}, format="json").status_code == 403
    assert client.session["_auth_user_id"] == str(user.pk)
    assert client.post(
        f"{AUTH}logout/", {}, format="json", HTTP_X_CSRFTOKEN=old_csrf
    ).status_code == 403
    assert post(client, "logout", {}).status_code == 204


@pytest.mark.django_db
def test_reset_responses_do_not_disclose_email_and_skip_ineligible_users(
    client, user, mailoutbox
):
    known = post(client, "password-reset", {"email": "READER@EXAMPLE.COM"})
    unknown = post(client, "password-reset", {"email": "unknown@example.com"})
    user.is_active = False
    user.save(update_fields=["is_active"])
    inactive = post(client, "password-reset", {"email": user.email})
    user.is_active = True
    user.set_unusable_password()
    user.save(update_fields=["is_active", "password"])
    unusable = post(client, "password-reset", {"email": user.email})

    assert all(
        response.status_code == 200
        for response in [known, unknown, inactive, unusable]
    )
    assert known.content == unknown.content == inactive.content == unusable.content
    assert set(known.json()) == {"detail"}
    assert user.email not in known.content.decode()
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [user.email]
    assert post(client, "password-reset", {"email": "invalid"}).status_code == 400


@pytest.mark.django_db
def test_reset_changes_password_invalidates_token_and_old_session(
    client, user, mailoutbox, settings
):
    settings.PASSWORD_RESET_URL = "https://client.example/reset/?source=api#form"
    assert post(
        client, "login", {"email": user.email, "password": PASSWORD}
    ).status_code == 200
    assert post(client, "password-reset", {"email": user.email}).status_code == 200
    assert len(mailoutbox) == 1
    reset_url = next(
        line for line in mailoutbox[0].body.splitlines() if line.startswith("https://")
    )
    url = urlsplit(reset_url)
    assert (url.netloc, url.path, url.fragment) == ("client.example", "/reset/", "form")
    query = parse_qs(url.query)
    assert query["source"] == ["api"]
    payload = {
        "uid": query["uid"][0],
        "token": query["token"][0],
        "new_password": NEW_PASSWORD,
    }
    assert post(client, "password-reset/confirm", payload).status_code == 200
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)
    assert not user.check_password(PASSWORD)
    assert post(client, "password-reset/confirm", payload).status_code == 400
    assert post(client, "logout", {}).status_code == 403
    assert post(
        client, "login", {"email": user.email, "password": PASSWORD}
    ).status_code == 400
    assert post(
        client, "login", {"email": user.email, "password": NEW_PASSWORD}
    ).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize(
    "failure",
    [
        "malformed-uid", "missing-user", "wrong-token", "expired", "inactive",
        "unusable", "weak-password",
    ],
)
def test_reset_confirmation_rejects_invalid_input(client, user, settings, failure):
    payload = {
        "uid": urlsafe_base64_encode(force_bytes(user.pk)),
        "token": default_token_generator.make_token(user),
        "new_password": NEW_PASSWORD,
    }
    if failure == "malformed-uid":
        payload["uid"] = "!!!!"
    elif failure == "missing-user":
        payload["uid"] = urlsafe_base64_encode(force_bytes(user.pk + 10000))
    elif failure == "wrong-token":
        payload["token"] = "invalid-token"
    elif failure == "expired":
        expired_at = datetime.now() - timedelta(seconds=settings.PASSWORD_RESET_TIMEOUT + 1)
        with patch.object(default_token_generator, "_now", return_value=expired_at):
            payload["token"] = default_token_generator.make_token(user)
    elif failure == "inactive":
        user.is_active = False
        user.save(update_fields=["is_active"])
    elif failure == "unusable":
        user.set_unusable_password()
        user.save(update_fields=["password"])
        payload["token"] = default_token_generator.make_token(user)
    else:
        payload["new_password"] = "123"

    previous_password = user.password
    response = post(client, "password-reset/confirm", payload)
    assert response.status_code == 400
    user.refresh_from_db()
    assert user.password == previous_password


@pytest.mark.django_db
@pytest.mark.parametrize("error_class", [SMTPException, TimeoutError])
def test_smtp_failure_keeps_neutral_response_and_allows_retry(
    client, user, mailoutbox, caplog, error_class
):
    secret = "sensitive-smtp-details"
    unknown = post(client, "password-reset", {"email": "unknown@example.com"})
    with patch("accounts.services.send_mail", side_effect=error_class(secret)) as send:
        failed = post(client, "password-reset", {"email": user.email})
        assert send.call_args.kwargs["fail_silently"] is False

    assert failed.status_code == 200
    assert failed.content == unknown.content
    assert len(mailoutbox) == 0
    assert "Password reset email submission failed" in caplog.text
    assert secret not in caplog.text
    assert user.email not in caplog.text
    assert post(client, "password-reset", {"email": user.email}).status_code == 200
    assert len(mailoutbox) == 1


def test_smtp_backend_uses_configured_timeout(settings):
    assert settings.EMAIL_TIMEOUT == 5
    connection = get_connection(backend="django.core.mail.backends.smtp.EmailBackend")
    assert connection.timeout == 5
