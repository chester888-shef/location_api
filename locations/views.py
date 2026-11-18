from drf_spectacular.utils import OpenApiExample, extend_schema, extend_schema_view
from rest_framework.viewsets import ModelViewSet

from locations.models import Location
from locations.policies import LocationPermission
from locations.serializers import LocationReadSerializer, LocationSerializer
from locations.services import soft_delete_location


@extend_schema(
    tags=["Локації"],
    examples=[
        OpenApiExample(
            "Локація — замініть category на ID створеної категорії",
            value={
                "title": "Парк Шевченка",
                "description": "Парк для прогулянок у центрі міста.",
                "category": 1,
                "address": "Київ, бульвар Тараса Шевченка",
                "latitude": 50.4416,
                "longitude": 30.5116,
            },
            request_only=True,
        )
    ],
)
@extend_schema_view(
    list=extend_schema(auth=[], summary="Список локацій"),
    retrieve=extend_schema(auth=[], summary="Переглянути локацію"),
    create=extend_schema(
        summary="Створити локацію",
        description="Усі шість користувацьких полів обов’язкові. Автор — поточний користувач.",
    ),
    update=extend_schema(summary="Замінити всі користувацькі поля — автор або superuser"),
    partial_update=extend_schema(
        summary="Змінити окремі поля — автор або superuser",
        description="Якщо категорію було видалено, потрібно передати ID нової категорії.",
    ),
    destroy=extend_schema(
        summary="Soft Delete — автор або superuser",
        description="Рядок зберігається. Видалену локацію може лише переглядати superuser.",
    ),
)
class LocationViewSet(ModelViewSet):
    queryset = Location.objects.all()
    serializer_class = LocationSerializer
    permission_classes = [LocationPermission]

    def get_serializer_class(self):
        if self.action in {"list", "retrieve"}:
            return LocationReadSerializer
        return super().get_serializer_class()

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action in {"list", "retrieve"} and self.request.user.is_superuser:
            return queryset
        return queryset.filter(is_deleted=False)

    def perform_create(self, serializer):
        serializer.save(author=self.request.user)

    def perform_destroy(self, instance):
        soft_delete_location(instance, actor=self.request.user)
