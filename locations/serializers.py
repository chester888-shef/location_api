import math

from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import NotFound

from categories.models import Category
from core.cache import bump_location_revision
from locations.models import Location


class CoordinateField(serializers.FloatField):
    def to_internal_value(self, data):
        if isinstance(data, bool):
            self.fail("invalid")
        return super().to_internal_value(data)


class LocationSerializer(serializers.ModelSerializer):
    # NULL is permitted in storage only when a category is deleted.
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(), required=True, allow_null=False,
        pk_field=serializers.IntegerField(min_value=1),
    )
    latitude = CoordinateField(min_value=-90, max_value=90)
    longitude = CoordinateField(min_value=-180, max_value=180)
    rating = serializers.FloatField(read_only=True, allow_null=True)
    popularity = serializers.FloatField(read_only=True)
    reviews_count = serializers.IntegerField(read_only=True)
    views_count = serializers.IntegerField(read_only=True)
    views_7d = serializers.IntegerField(read_only=True)

    class Meta:
        model = Location
        fields = [
            "id", "title", "description", "category", "address", "latitude", "longitude",
            "author", "created_at", "updated_at", "is_deleted",
            "rating", "popularity", "reviews_count", "views_count", "views_7d",
        ]
        read_only_fields = [
            "id", "author", "created_at", "updated_at", "is_deleted",
            "rating", "popularity", "reviews_count", "views_count", "views_7d",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get("rating") is not None:
            data["rating"] = round(data["rating"], 2)
        return data

    def validate(self, attrs):
        errors = {}
        for field in ("latitude", "longitude"):
            if field in attrs and not math.isfinite(attrs[field]):
                errors[field] = "Координата має бути скінченним числом."
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def _lock_category(self, validated_data):
        if "category" in validated_data:
            try:
                validated_data["category"] = Category.objects.select_for_update().get(
                    pk=validated_data["category"].pk
                )
            except Category.DoesNotExist as error:
                raise serializers.ValidationError(
                    {"category": "Категорію видалено. Виберіть наявну категорію."}
                ) from error

    @transaction.atomic
    def create(self, validated_data):
        # Category -> Location -> CacheRevision, matching category deletion.
        self._lock_category(validated_data)
        instance = super().create(validated_data)
        bump_location_revision()
        return Location.objects.with_statistics().get(pk=instance.pk)

    @transaction.atomic
    def update(self, instance, validated_data):
        self._lock_category(validated_data)
        try:
            instance = Location.objects.select_for_update().get(pk=instance.pk, is_deleted=False)
        except Location.DoesNotExist as error:
            raise NotFound("Локацію не знайдено.") from error
        if instance.category_id is None and "category" not in validated_data:
            raise serializers.ValidationError(
                {"category": "Після видалення категорії потрібно призначити нову."}
            )
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save(update_fields=[*validated_data, "updated_at"])
        bump_location_revision()
        return Location.objects.with_statistics().get(pk=instance.pk)


class LocationReadSerializer(LocationSerializer):
    category = serializers.PrimaryKeyRelatedField(read_only=True, allow_null=True)
    latitude = CoordinateField(read_only=True)
    longitude = CoordinateField(read_only=True)

    class Meta(LocationSerializer.Meta):
        read_only_fields = LocationSerializer.Meta.fields


class LocationExportSerializer(LocationReadSerializer):
    class Meta(LocationReadSerializer.Meta):
        fields = [field for field in LocationReadSerializer.Meta.fields if field != "is_deleted"]
        read_only_fields = fields
