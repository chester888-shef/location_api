from rest_framework import serializers

from categories.models import Category
from categories.services import create_category, update_category


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

    def create(self, validated_data):
        return create_category(data=validated_data)

    def update(self, instance, validated_data):
        return update_category(instance, data=validated_data)
