from rest_framework.exceptions import APIException


class Conflict(APIException):
    status_code = 409
    default_detail = "Конфлікт із наявними даними."
    default_code = "conflict"
