from django.urls import path

from reviews.views import LocationReviewListCreateView, ReviewDetailView, ReviewVoteView

app_name = "reviews"

urlpatterns = [
    path("locations/<int:location_id>/reviews/", LocationReviewListCreateView.as_view(), name="location-reviews"),
    path("reviews/<int:pk>/", ReviewDetailView.as_view(), name="detail"),
    path("reviews/<int:pk>/vote/", ReviewVoteView.as_view(), name="vote"),
]
