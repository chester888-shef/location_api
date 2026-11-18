from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.utils.urls import remove_query_param, replace_query_param


class StandardPagination(PageNumberPagination):
    page_size = 10
    last_page_strings = ()

    def get_page_number(self, request, paginator):
        value = request.query_params.get(self.page_query_param, "1")
        if not value.isascii() or not value.isdecimal() or len(value) > 18 or int(value) < 1:
            raise ValidationError({"page": "Вкажіть додатний цілий номер сторінки."})
        return int(value)

    def paginate_queryset(self, queryset, request, view=None):
        try:
            return super().paginate_queryset(queryset, request, view=view)
        except NotFound as error:
            raise ValidationError({"page": error.detail}) from error

    def get_cached_response(self, data, request, page_number):
        # Links belong to this request, even when equivalent parameters share a key.
        url = request.build_absolute_uri()
        next_link = replace_query_param(url, self.page_query_param, page_number + 1) if data["next"] else None
        previous = None
        if data["previous"]:
            previous = (
                remove_query_param(url, self.page_query_param) if page_number == 2
                else replace_query_param(url, self.page_query_param, page_number - 1)
            )
        return Response({**data, "next": next_link, "previous": previous})
