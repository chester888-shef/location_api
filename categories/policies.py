from core.permissions import IsSuperuserOrReadOnly


class CategoryPermission(IsSuperuserOrReadOnly):
    def has_permission(self, request, view):
        if view.action == "create":
            return request.user.is_authenticated
        return super().has_permission(request, view)
