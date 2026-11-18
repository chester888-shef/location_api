from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Coalesce, Ln
from django.utils import timezone


class LocationQuerySet(models.QuerySet):
    def with_statistics(self, *, at=None):
        at = at or timezone.now()
        views = LocationView.objects.filter(location_id=models.OuterRef("pk")).order_by()
        totals = views.values("location_id").annotate(total=models.Count("pk")).values("total")
        recent = views.filter(viewed_at__gte=at - timedelta(days=7))
        recent = recent.values("location_id").annotate(total=models.Count("pk")).values("total")
        queryset = self.annotate(
            rating=models.Avg("reviews__rating"),
            reviews_count=models.Count("reviews"),
            views_count=Coalesce(models.Subquery(totals), 0),
            views_7d=Coalesce(models.Subquery(recent), 0),
        )
        return queryset.annotate(
            popularity=models.ExpressionWrapper(
                0.5 * Coalesce(models.F("rating"), 0.0)
                + 0.3 * Ln(1.0 + models.F("reviews_count"))
                + 0.2 * Ln(1.0 + models.F("views_7d")),
                output_field=models.FloatField(),
            )
        )


class Location(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField()
    category = models.ForeignKey(
        "categories.Category", on_delete=models.SET_NULL, null=True, related_name="locations"
    )
    address = models.TextField()
    latitude = models.FloatField(validators=[MinValueValidator(-90), MaxValueValidator(90)])
    longitude = models.FloatField(validators=[MinValueValidator(-180), MaxValueValidator(180)])
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="locations"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    objects = LocationQuerySet.as_manager()

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(latitude__gte=-90, latitude__lte=90),
                name="location_latitude_range",
            ),
            models.CheckConstraint(
                condition=models.Q(longitude__gte=-180, longitude__lte=180),
                name="location_longitude_range",
            ),
        ]

    def __str__(self):
        return self.title


class LocationView(models.Model):
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name="views")
    viewed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=["location", "viewed_at"], name="location_view_time_idx")]


class LocationViewGate(models.Model):
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name="view_gates")
    visitor_hash = models.CharField(max_length=64)
    last_counted_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["location", "visitor_hash"], name="location_visitor_gate_unique"
            ),
        ]
