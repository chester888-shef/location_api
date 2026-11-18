from rest_framework.exceptions import ValidationError

from locations.filters import LocationFilterSet
from locations.models import Location


def validate_location_params(params):
    filters = LocationFilterSet(data=params, queryset=Location.objects.none())
    if not filters.is_valid():
        raise ValidationError({field: list(errors) for field, errors in filters.errors.items()})
    return filters.form.cleaned_data


def build_location_queryset(user, validated_params):
    queryset = Location.objects.all()
    if not user.is_superuser:
        queryset = queryset.filter(is_deleted=False)
    queryset = queryset.with_statistics().order_by("id")
    filters = LocationFilterSet(queryset=queryset)
    for name, value in validated_params.items():
        queryset = filters.filters[name].filter(queryset, value)
    return queryset
