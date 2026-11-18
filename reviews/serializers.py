from rest_framework import serializers

from reviews.models import Review, ReviewVote
from reviews.services import create_review, update_review


class ReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ["id", "location", "author", "rating", "comment", "created_at", "updated_at"]
        read_only_fields = ["id", "location", "author", "created_at", "updated_at"]
        # The database constraint is converted to a 409 by create_review.
        validators = []

    def create(self, validated_data):
        return create_review(
            location_id=validated_data["location_id"],
            actor=validated_data["author"],
            rating=validated_data["rating"],
            comment=validated_data["comment"],
        )

    def update(self, instance, validated_data):
        return update_review(
            instance, actor=self.context["request"].user, data=validated_data
        )


class ReviewVoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewVote
        fields = ["id", "review", "user", "value"]
        read_only_fields = ["id", "review", "user"]
        validators = []
