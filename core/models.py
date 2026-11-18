from django.db import models


class CacheRevision(models.Model):
    name = models.CharField(max_length=32, primary_key=True, default="locations")
    version = models.PositiveBigIntegerField(default=1)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(name="locations"), name="cache_revision_locations_only"
            ),
        ]
