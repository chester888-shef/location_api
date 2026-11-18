from django import forms
from django.db.models import F, Q
from django_filters import CharFilter, ChoiceFilter, Filter, FilterSet, NumberFilter

from locations.models import Location

LOCATION_ORDERINGS = (
    "created_at", "-created_at", "rating", "-rating", "popularity", "-popularity"
)


class IntegerFilter(Filter):
    field_class = forms.IntegerField


class LocationFilterForm(forms.Form):
    def clean(self):
        data = super().clean()
        lower, upper = data.get("rating_min"), data.get("rating_max")
        if lower is not None and upper is not None and lower > upper:
            self.add_error("rating_max", "Має бути не меншим за rating_min.")
        return data


class LocationFilterSet(FilterSet):
    search = CharFilter(method="filter_search")
    category = IntegerFilter(field_name="category_id", min_value=1)
    author = IntegerFilter(field_name="author_id", min_value=1)
    rating_min = NumberFilter(field_name="rating", lookup_expr="gte", min_value=1, max_value=5)
    rating_max = NumberFilter(field_name="rating", lookup_expr="lte", min_value=1, max_value=5)
    ordering = ChoiceFilter(
        choices=[(value, value) for value in LOCATION_ORDERINGS], method="filter_ordering"
    )

    class Meta:
        model = Location
        fields = []
        form = LocationFilterForm

    def filter_search(self, queryset, name, value):
        return queryset.filter(Q(title__icontains=value) | Q(description__icontains=value))

    def filter_ordering(self, queryset, name, value):
        if value.lstrip("-") == "rating":
            order = F("rating").desc(nulls_last=True) if value.startswith("-") else F("rating").asc(nulls_last=True)
            return queryset.order_by(order, "id")
        return queryset.order_by(value, "id")
