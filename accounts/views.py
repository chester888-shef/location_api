from django.contrib.auth import login, logout
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.serializers import (
    LoginSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetSerializer,
    RegisterSerializer,
    UserSerializer,
)
from accounts.services import send_password_reset_email

CSRF_RESPONSE = inline_serializer(
    name="CsrfResponse",
    fields={"csrfToken": serializers.CharField(read_only=True)},
)
MESSAGE_RESPONSE = inline_serializer(
    name="MessageResponse",
    fields={"detail": serializers.CharField(read_only=True)},
)
VALIDATION_ERROR = OpenApiResponse(
    response=OpenApiTypes.OBJECT,
    description="Помилка валідації вхідних полів або облікових даних.",
)
CSRF_ERRORS = {
    (403, "text/html"): OpenApiResponse(
        response=OpenApiTypes.STR,
        description="Django відхилив запит через відсутній або невірний CSRF-токен.",
    ),
    (403, "application/json"): OpenApiResponse(
        response=OpenApiTypes.OBJECT,
        description="Немає доступу або запит відхилено SessionAuthentication.",
    ),
}
AUTH_EXAMPLE = OpenApiExample(
    "Email та пароль",
    value={"email": "swagger@example.com", "password": "Map-Dnipro!2026-Reader"},
    request_only=True,
)


@method_decorator(never_cache, name="dispatch")
class CsrfView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="auth_csrf",
        tags=["Автентифікація"],
        summary="Отримати CSRF cookie",
        description="Виконайте перед POST-запитами. Swagger читає актуальну cookie автоматично.",
        auth=[],
        responses={200: CSRF_RESPONSE},
    )
    def get(self, request):
        return Response({"csrfToken": get_token(request)})


@method_decorator(csrf_protect, name="dispatch")
@method_decorator(never_cache, name="dispatch")
class AuthAPIView(APIView):
    """Protect anonymous auth requests as well as authenticated session requests."""


class RegisterView(AuthAPIView):
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="auth_register",
        tags=["Автентифікація"],
        summary="Зареєструвати користувача",
        description=(
            "Потрібен CSRF. Email унікальний без урахування регістру. "
            "Пароль перевіряється Django. Реєстрація не виконує автоматичний вхід."
        ),
        auth=[],
        request=RegisterSerializer,
        responses={201: UserSerializer, 400: VALIDATION_ERROR, **CSRF_ERRORS},
        examples=[AUTH_EXAMPLE],
    )
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


class LoginView(AuthAPIView):
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="auth_login",
        tags=["Автентифікація"],
        summary="Увійти через Django Session/Cookies",
        description=(
            "Потрібен CSRF. Створює sessionid cookie та змінює CSRF-токен. "
            "Swagger використовує нову cookie в наступних запитах."
        ),
        auth=[],
        request=LoginSerializer,
        responses={200: UserSerializer, 400: VALIDATION_ERROR, **CSRF_ERRORS},
        examples=[AUTH_EXAMPLE],
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        login(request, user)
        return Response(UserSerializer(user).data)


class LogoutView(AuthAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="auth_logout",
        tags=["Автентифікація"],
        summary="Завершити поточну сесію",
        description="Потрібні активна Django-сесія та CSRF. Для гостя повертається 403.",
        request=None,
        responses={204: None, **CSRF_ERRORS},
    )
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordResetView(AuthAPIView):
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="auth_password_reset",
        tags=["Відновлення пароля"],
        summary="Запросити лист відновлення пароля",
        description=(
            "Потрібен CSRF. Наявна й невідома адреса, а також збій SMTP повертають "
            "однакову нейтральну відповідь. За console backend лист доступний у логах web."
        ),
        auth=[],
        request=PasswordResetSerializer,
        responses={200: MESSAGE_RESPONSE, 400: VALIDATION_ERROR, **CSRF_ERRORS},
        examples=[
            OpenApiExample(
                "Email користувача", value={"email": "swagger@example.com"}, request_only=True
            )
        ],
    )
    def post(self, request):
        serializer = PasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        send_password_reset_email(serializer.validated_data["email"])
        return Response(
            {"detail": "Якщо для цієї адреси доступне відновлення, перевірте пошту."}
        )


class PasswordResetConfirmView(AuthAPIView):
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="auth_password_reset_confirm",
        tags=["Відновлення пароля"],
        summary="Змінити пароль за токеном із листа",
        description=(
            "Потрібен CSRF. Скопіюйте uid і token із посилання в листі. "
            "Новий пароль перевіряється Django; після зміни токен стає недійсним. "
            "Недійсний або прострочений токен повертає 400."
        ),
        auth=[],
        request=PasswordResetConfirmSerializer,
        responses={200: MESSAGE_RESPONSE, 400: VALIDATION_ERROR, **CSRF_ERRORS},
    )
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"detail": "Пароль змінено."})
