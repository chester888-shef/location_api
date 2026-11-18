import math

from rest_framework import serializers

from categories.models import Category
from locations.models import Location
from locations.services import create_location, update_location


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
    rating = serializers.FloatField(
        read_only=True, allow_null=True,
        help_text="Середній рейтинг, округлений до 2 знаків лише для відображення.",
    )
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

    def create(self, validated_data):
        return create_location(data=validated_data)

    def update(self, instance, validated_data):
        return update_location(instance, data=validated_data)


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
