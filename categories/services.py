from django.db import transaction
from django.shortcuts import get_object_or_404

from categories.models import Category
from core.cache import bump_location_revision


@transaction.atomic
def delete_category(category):
    category = get_object_or_404(Category.objects.select_for_update(), pk=category.pk)
    category.delete()
    bump_location_revision()
