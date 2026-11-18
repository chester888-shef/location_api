from drf_spectacular.utils import OpenApiExample, extend_schema, extend_schema_view
from rest_framework.viewsets import ModelViewSet

from categories.models import Category
from categories.policies import CategoryPermission
from categories.serializers import CategorySerializer
from categories.services import delete_category


@extend_schema(
    tags=["Категорії"],
    examples=[OpenApiExample("Категорія", value={"name": "Парки"}, request_only=True)],
)
@extend_schema_view(
    list=extend_schema(auth=[], summary="Список категорій"),
    retrieve=extend_schema(auth=[], summary="Переглянути категорію"),
    create=extend_schema(summary="Створити категорію — авторизований користувач"),
    update=extend_schema(summary="Замінити категорію — superuser"),
    partial_update=extend_schema(summary="Змінити категорію — superuser"),
    destroy=extend_schema(
        summary="Видалити категорію — superuser",
        description="Фізичне видалення. Пов’язані локації залишаються з category=null.",
    ),
)
class CategoryViewSet(ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [CategoryPermission]

    def perform_destroy(self, instance):
        delete_category(instance)
