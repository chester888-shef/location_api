from rest_framework.test import APIClient


def get_schema():
    response = APIClient().get("/api/schema/", HTTP_ACCEPT="application/json")
    assert response.status_code == 200
    return response.json()


def schema_component(schema, reference):
    return schema["components"]["schemas"][reference["$ref"].rsplit("/", 1)[1]]


def test_public_swagger_and_root_redirect():
    client = APIClient(enforce_csrf_checks=True)
    response = client.get("/")
    assert response.status_code == 302
    assert response["Location"] == "/api/docs/"

    response = client.get("/api/docs/")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    assert b"SwaggerUIBundle" in response.content
    assert b"/api/schema/" in response.content
    assert client.cookies["csrftoken"].value


def test_schema_contains_implemented_endpoints_and_session_security():
    schema = get_schema()
    assert schema["openapi"].startswith("3.")
    assert set(schema["paths"]) == {
        "/api/auth/csrf/",
        "/api/auth/register/",
        "/api/auth/login/",
        "/api/auth/logout/",
        "/api/auth/password-reset/",
        "/api/auth/password-reset/confirm/",
        "/api/categories/",
        "/api/categories/{id}/",
        "/api/locations/",
        "/api/locations/{id}/",
        "/api/locations/{location_id}/reviews/",
        "/api/reviews/{id}/",
        "/api/reviews/{id}/vote/",
    }
    logout = schema["paths"]["/api/auth/logout/"]["post"]
    assert logout["security"] == [{"cookieAuth": []}]
    assert "204" in logout["responses"]
    assert "content" not in logout["responses"]["204"]
    assert schema["components"]["securitySchemes"]["cookieAuth"] == {
        "type": "apiKey", "in": "cookie", "name": "sessionid"
    }

    for path, operations in schema["paths"].items():
        if path.startswith("/api/auth/") and path != "/api/auth/logout/":
            operation = operations["get" if path.endswith("csrf/") else "post"]
            assert operation.get("security", []) == []

    for resource in ["categories", "locations"]:
        for path in [f"/api/{resource}/", f"/api/{resource}/{{id}}/"]:
            assert schema["paths"][path]["get"].get("security", []) == []

    reviews = schema["paths"]["/api/locations/{location_id}/reviews/"]
    assert reviews["get"].get("security", []) == []
    assert "409" in reviews["post"]["responses"]
    assert set(schema["paths"]["/api/reviews/{id}/"]) == {"patch", "delete"}
    assert set(schema["paths"]["/api/reviews/{id}/vote/"]) == {"put", "delete"}


def test_schema_describes_auth_input_output_and_csrf_errors():
    schema = get_schema()
    for endpoint, success in [("register", "201"), ("login", "200")]:
        operation = schema["paths"][f"/api/auth/{endpoint}/"]["post"]
        request = schema_component(
            schema, operation["requestBody"]["content"]["application/json"]["schema"]
        )
        assert set(request["properties"]) == {"email", "password"}
        assert set(request["required"]) == {"email", "password"}
        assert request["properties"]["password"]["writeOnly"]
        response = schema_component(
            schema, operation["responses"][success]["content"]["application/json"]["schema"]
        )
        assert set(response["properties"]) == {"id", "email"}
        assert "text/html" in operation["responses"]["403"]["content"]
        assert "400" in operation["responses"]

    confirm = schema["paths"]["/api/auth/password-reset/confirm/"]["post"]
    request = schema_component(
        schema, confirm["requestBody"]["content"]["application/json"]["schema"]
    )
    assert set(request["required"]) == {"uid", "token", "new_password"}
    assert request["properties"]["new_password"]["writeOnly"]


def test_location_schema_requires_category_on_input_and_allows_null_on_read():
    schema = get_schema()
    create = schema["paths"]["/api/locations/"]["post"]
    request = schema_component(
        schema, create["requestBody"]["content"]["application/json"]["schema"]
    )
    assert set(request["required"]) == {
        "title", "description", "category", "address", "latitude", "longitude"
    }
    assert "author" not in request["properties"]
    assert not request["properties"]["category"].get("nullable", False)

    detail = schema["paths"]["/api/locations/{id}/"]["get"]
    response = schema_component(
        schema, detail["responses"]["200"]["content"]["application/json"]["schema"]
    )
    assert response["properties"]["category"]["nullable"]


def test_review_and_vote_schemas_keep_server_fields_out_of_input():
    schema = get_schema()
    operation = schema["paths"]["/api/locations/{location_id}/reviews/"]["post"]
    request = schema_component(
        schema, operation["requestBody"]["content"]["application/json"]["schema"]
    )
    assert set(request["properties"]) == {"rating", "comment"}
    assert set(request["required"]) == {"rating", "comment"}
    vote = schema["paths"]["/api/reviews/{id}/vote/"]["put"]
    request = schema_component(
        schema, vote["requestBody"]["content"]["application/json"]["schema"]
    )
    assert set(request["properties"]) == {"value"}
