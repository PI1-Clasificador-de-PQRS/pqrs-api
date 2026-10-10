from fastapi.testclient import TestClient


def assert_error_shape(body: dict, code: str) -> None:
    assert set(body) == {"detail"}
    assert set(body["detail"]) == {"code", "message"}
    assert body["detail"]["code"] == code
    assert isinstance(body["detail"]["message"], str) and body["detail"]["message"]


def test_unknown_route_uses_the_error_format(client: TestClient) -> None:
    response = client.get("/no-existe")
    assert response.status_code == 404
    assert_error_shape(response.json(), "not_found")


def test_wrong_method_uses_the_error_format(client: TestClient) -> None:
    response = client.put("/health")
    assert response.status_code == 405
    assert_error_shape(response.json(), "method_not_allowed")


def test_validation_error_names_fields_but_not_values(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"email": 123})
    assert response.status_code == 422
    assert_error_shape(response.json(), "invalid_request")
    message = response.json()["detail"]["message"]
    assert "email" in message and "password" in message
    assert "123" not in message


def test_malformed_json_is_invalid_request(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login", content=b"{no es json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert_error_shape(response.json(), "invalid_request")


def test_error_messages_are_in_spanish(client: TestClient) -> None:
    response = client.get("/api/v1/me")
    assert response.json()["detail"]["message"].startswith("Se requieren credenciales")
