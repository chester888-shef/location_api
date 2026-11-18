from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsAuthorOrSuperuser(BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return bool(
            request.user.is_authenticated
            and (request.user.is_superuser or obj.author_id == request.user.pk)
        )


class IsSuperuserOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        return request.method in SAFE_METHODS or bool(
            request.user.is_authenticated and request.user.is_superuser
        )
