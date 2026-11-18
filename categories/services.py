from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError

from categories.models import Category
from core.cache import bump_location_revision


def create_category(*, data):
    try:
        with transaction.atomic():
            return Category.objects.create(**data)
    except IntegrityError as error:
        diagnostic = getattr(error.__cause__, "diag", None)
        if getattr(diagnostic, "constraint_name", None) == "category_name_ci_unique":
            raise ValidationError({"name": ["Категорія з такою назвою вже існує."]}) from error
        raise


def update_category(category, *, data):
    try:
        with transaction.atomic():
            category = get_object_or_404(Category.objects.select_for_update(), pk=category.pk)
            for field, value in data.items():
                setattr(category, field, value)
            category.save(update_fields=list(data))
            bump_location_revision()
            return category
    except IntegrityError as error:
        diagnostic = getattr(error.__cause__, "diag", None)
        if getattr(diagnostic, "constraint_name", None) == "category_name_ci_unique":
            raise ValidationError({"name": ["Категорія з такою назвою вже існує."]}) from error
        raise


@transaction.atomic
def delete_category(category):
    category = get_object_or_404(Category.objects.select_for_update(), pk=category.pk)
    category.delete()
    bump_location_revision()
