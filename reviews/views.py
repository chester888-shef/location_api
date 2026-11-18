from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.response import Response

from locations.models import Location
from reviews.models import Review
from reviews.policies import ReviewPermission
from reviews.serializers import ReviewSerializer, ReviewVoteSerializer
from reviews.services import delete_review, delete_vote, put_vote


@extend_schema_view(
    get=extend_schema(auth=[], summary="Відгуки локації"),
    post=extend_schema(
        summary="Залишити один відгук до активної локації",
        responses={
            201: ReviewSerializer,
            409: OpenApiResponse(response=OpenApiTypes.OBJECT, description="Відгук уже існує."),
        },
        examples=[OpenApiExample("Відгук", value={"rating": 5, "comment": "Гарне місце."}, request_only=True)],
    ),
)
@extend_schema(tags=["Відгуки"])
class LocationReviewListCreateView(generics.ListCreateAPIView):
    queryset = Review.objects.all()
    serializer_class = ReviewSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        locations = Location.objects.all()
        if not self.request.user.is_superuser:
            locations = locations.filter(is_deleted=False)
        location = get_object_or_404(locations, pk=self.kwargs["location_id"])
        return super().get_queryset().filter(location=location)

    def perform_create(self, serializer):
        serializer.save(author=self.request.user, location_id=self.kwargs["location_id"])


@extend_schema_view(patch=extend_schema(summary="Змінити відгук — автор або superuser"))
@extend_schema(tags=["Відгуки"])
class ReviewDetailView(generics.UpdateAPIView):
    queryset = Review.objects.filter(location__is_deleted=False)
    serializer_class = ReviewSerializer
    permission_classes = [ReviewPermission]
    http_method_names = ["patch", "delete", "options"]

    @extend_schema(summary="Видалити відгук і його голоси", request=None, responses={204: None})
    def delete(self, request, *args, **kwargs):
        delete_review(self.get_object(), actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Голоси"])
class ReviewVoteView(generics.GenericAPIView):
    queryset = Review.objects.filter(location__is_deleted=False)
    serializer_class = ReviewVoteSerializer
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Поставити або змінити Like/Dislike",
        description="Один голос користувача. За власний відгук голосувати не можна, навіть superuser.",
        responses={200: ReviewVoteSerializer},
        examples=[OpenApiExample("Like", value={"value": "like"}, request_only=True)],
    )
    def put(self, request, *args, **kwargs):
        review = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        vote = put_vote(review, actor=request.user, value=serializer.validated_data["value"])
        return Response(self.get_serializer(vote).data)

    @extend_schema(summary="Скасувати власний голос", request=None, responses={204: None})
    def delete(self, request, *args, **kwargs):
        delete_vote(self.get_object(), actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
