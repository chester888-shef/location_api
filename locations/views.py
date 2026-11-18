from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.cache import cache_location_list, get_location_list_cache, location_list_cache_key
from core.renderers import CSVRenderer
from locations.filters import LOCATION_ORDERINGS
from locations.models import Location
from locations.policies import LocationPermission
from locations.serializers import LocationExportSerializer, LocationReadSerializer, LocationSerializer
from locations.selectors import build_location_queryset, validate_location_params
from locations.services import count_location_view, export_locations_csv, soft_delete_location

LIST_PARAMETERS = [
    OpenApiParameter("search", str, description="Пошук у назві та описі."),
    OpenApiParameter("category", int, description="ID категорії."),
    OpenApiParameter("author", int, description="ID автора."),
    OpenApiParameter(
        "rating_min", {"type": "number", "minimum": 1, "maximum": 5},
        description=(
            "Нижня межа точного середнього рейтингу без округлення. "
            "Середнє 4.666… відображається як 4.67, але не проходить rating_min=4.67."
        ),
    ),
    OpenApiParameter(
        "rating_max", {"type": "number", "minimum": 1, "maximum": 5},
        description="Верхня межа точного середнього рейтингу без округлення.",
    ),
    OpenApiParameter("ordering", str, enum=LOCATION_ORDERINGS),
]


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
    list=extend_schema(auth=[], summary="Список локацій", parameters=LIST_PARAMETERS),
    retrieve=extend_schema(
        auth=[], summary="Переглянути локацію",
        description=(
            "GET активної локації зараховує не більше одного перегляду на visitor за 3600 секунд. "
            "Для гостя visitor визначається сесією. HEAD, OPTIONS і видалені локації не зараховуються."
        ),
    ),
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
    filter_backends = []

    def get_serializer_class(self):
        if self.action in {"list", "retrieve"}:
            return LocationReadSerializer
        return super().get_serializer_class()

    def get_queryset(self):
        if self.action in {"list", "export_json", "export_csv"}:
            params = validate_location_params(self.request.query_params)
            return build_location_queryset(self.request.user, params)
        if self.action == "retrieve":
            return build_location_queryset(self.request.user, {})
        return super().get_queryset().filter(is_deleted=False)

    def list(self, request, *args, **kwargs):
        params = validate_location_params(request.query_params)
        page_number = self.paginator.get_page_number(request, None)
        key = None
        if not request.user.is_superuser:
            key = location_list_cache_key(params, page_number)
            cached = get_location_list_cache(key)
            if cached is not None:
                return self.paginator.get_cached_response(cached, request, page_number)
        queryset = build_location_queryset(request.user, params)
        page = self.paginate_queryset(queryset)
        response = self.get_paginated_response(self.get_serializer(page, many=True).data)
        if key is not None:
            cache_location_list(key, response.data)
        return response

    @extend_schema(
        auth=[], summary="Експорт локацій у JSON без пагінації",
        parameters=LIST_PARAMETERS, responses=LocationExportSerializer(many=True),
    )
    @action(detail=False, methods=["get"], url_path="export/json", pagination_class=None,
            serializer_class=LocationExportSerializer)
    def export_json(self, request):
        return Response(self.get_serializer(self.get_queryset(), many=True).data)

    @extend_schema(
        auth=[], summary="Експорт локацій у CSV без пагінації",
        parameters=LIST_PARAMETERS, responses={(200, "text/csv"): OpenApiTypes.BINARY},
    )
    @action(detail=False, methods=["get"], url_path="export/csv", pagination_class=None,
            serializer_class=LocationExportSerializer, renderer_classes=[CSVRenderer])
    def export_csv(self, request):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response(
            export_locations_csv(serializer.data, columns=serializer.child.Meta.fields),
            headers={"Content-Disposition": 'attachment; filename="locations.csv"'},
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        if request.method == "GET" and not instance.is_deleted:
            count_location_view(instance, request)
            instance = get_object_or_404(self.get_queryset(), pk=instance.pk)
        return Response(self.get_serializer(instance).data)

    def perform_create(self, serializer):
        serializer.save(author=self.request.user)

    def perform_destroy(self, instance):
        soft_delete_location(instance, actor=self.request.user)
