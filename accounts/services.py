import logging
from smtplib import SMTPException
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

logger = logging.getLogger(__name__)


def send_password_reset_email(email):
    user = get_user_model().objects.filter(email__iexact=email, is_active=True).first()
    if user is None or not user.has_usable_password():
        return

    url = urlsplit(settings.PASSWORD_RESET_URL)
    query = dict(parse_qsl(url.query, keep_blank_values=True))
    query.update(
        uid=urlsafe_base64_encode(force_bytes(user.pk)),
        token=default_token_generator.make_token(user),
    )
    reset_url = urlunsplit(url._replace(query=urlencode(query)))

    try:
        sent = send_mail(
            subject="Відновлення пароля — Мапа популярності локацій",
            message=(
                "Щоб змінити пароль, скористайтеся посиланням:\n"
                f"{reset_url}\n\n"
                "Якщо ви не запитували зміну пароля, ігноруйте цей лист."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
    except (SMTPException, OSError) as error:
        logger.warning("Password reset email submission failed (%s).", type(error).__name__)
    else:
        if sent == 0:
            logger.warning("Password reset email backend accepted no messages.")
