from django.db import IntegrityError, transaction
from rest_framework import serializers

from categories.models import Category
from core.cache import bump_location_revision


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name"]
        read_only_fields = ["id"]

    def validate_name(self, value):
        duplicates = Category.objects.filter(name__iexact=value)
        if self.instance is not None:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError("Категорія з такою назвою вже існує.")
        return value

    def save(self, **kwargs):
        is_update = self.instance is not None
        try:
            with transaction.atomic():
                instance = super().save(**kwargs)
                if is_update:
                    bump_location_revision()
                return instance
        except IntegrityError as error:
            # Preserve other database failures; only a duplicate name is a 400.
            diagnostic = getattr(error.__cause__, "diag", None)
            if getattr(diagnostic, "constraint_name", None) == "category_name_ci_unique":
                raise serializers.ValidationError(
                    {"name": ["Категорія з такою назвою вже існує."]}
                ) from error
            raise
