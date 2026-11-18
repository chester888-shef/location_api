from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsSuperuserOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        return request.method in SAFE_METHODS or bool(
            request.user.is_authenticated and request.user.is_superuser
        )
