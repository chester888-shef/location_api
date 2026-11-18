from rest_framework.permissions import SAFE_METHODS, IsAuthenticatedOrReadOnly


def can_modify_location(user, location):
    return bool(
        user.is_authenticated and (user.is_superuser or location.author_id == user.pk)
    )


class LocationPermission(IsAuthenticatedOrReadOnly):
    def has_object_permission(self, request, view, obj):
        return request.method in SAFE_METHODS or can_modify_location(request.user, obj)
