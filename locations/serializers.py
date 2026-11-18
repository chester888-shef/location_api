import math

from django.db import transaction
from rest_framework import serializers

from categories.models import Category
from core.cache import bump_location_revision
from locations.models import Location


class LocationSerializer(serializers.ModelSerializer):
    # NULL is permitted in storage only when a category is deleted.
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(), required=True, allow_null=False
    )

    class Meta:
        model = Location
        fields = [
            "id", "title", "description", "category", "address", "latitude", "longitude",
            "author", "created_at", "updated_at", "is_deleted",
        ]
        read_only_fields = ["id", "author", "created_at", "updated_at", "is_deleted"]

    def validate(self, attrs):
        errors = {}
        for field in ("latitude", "longitude"):
            if field in attrs and not math.isfinite(attrs[field]):
                errors[field] = "Координата має бути скінченним числом."
        if (
            self.instance is not None
            and self.instance.category_id is None
            and "category" not in attrs
        ):
            errors["category"] = "Після видалення категорії потрібно призначити нову."
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        instance = super().create(validated_data)
        bump_location_revision()
        return instance

    @transaction.atomic
    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)
        # Do not overwrite server fields, including a concurrent Soft Delete.
        instance.save(update_fields=[*validated_data, "updated_at"])
        bump_location_revision()
        return instance


class LocationReadSerializer(LocationSerializer):
    category = serializers.PrimaryKeyRelatedField(read_only=True, allow_null=True)

    class Meta(LocationSerializer.Meta):
        read_only_fields = LocationSerializer.Meta.fields
