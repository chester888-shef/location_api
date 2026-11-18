from rest_framework.permissions import IsAuthenticated


def can_modify_review(user, review):
    return bool(
        user.is_authenticated and (user.is_superuser or review.author_id == user.pk)
    )


class ReviewPermission(IsAuthenticated):
    def has_object_permission(self, request, view, obj):
        return can_modify_review(request.user, obj)
