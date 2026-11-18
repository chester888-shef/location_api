from django.db import transaction

from core.cache import bump_location_revision


@transaction.atomic
def delete_category(category):
    category.delete()
    bump_location_revision()
