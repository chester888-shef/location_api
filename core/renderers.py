from rest_framework.renderers import BaseRenderer, JSONRenderer


class CSVRenderer(BaseRenderer):
    media_type = "text/csv"
    format = "csv"
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if isinstance(data, bytes):
            return data
        # Validation and permission errors keep the standard JSON error format.
        renderer_context["response"]["Content-Type"] = "application/json"
        return JSONRenderer().render(data, renderer_context=renderer_context)
