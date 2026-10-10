import importlib.util
import json
from pathlib import Path

from app.domain.errors import ErrorCode

ROOT = Path(__file__).resolve().parents[1]

# Códigos estables que el contrato debe mostrar a quien genera tipos y mocks (prompt, sección 7).
CONTRACT_CODES = {
    "missing_credentials",
    "invalid_credentials",
    "session_expired",
    "session_required",
    "csrf_required",
    "not_found",
    "batch_too_large",
    "invalid_request",
    "too_many_attempts",
    "rate_limited",
    "not_implemented",
    "llm_unavailable",
    "classifier_unavailable",
}


def load_export_module():
    spec = importlib.util.spec_from_file_location(
        "export_openapi", ROOT / "scripts/export_openapi.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def spec() -> dict:
    return json.loads((ROOT / "openapi/openapi.json").read_text(encoding="utf-8"))


def test_openapi_file_is_up_to_date() -> None:
    module = load_export_module()
    current = (ROOT / "openapi/openapi.json").read_text(encoding="utf-8")
    assert current == module.render(), (
        "openapi/openapi.json está desactualizado: ejecuta "
        "`uv run python scripts/export_openapi.py`"
    )


def test_export_is_deterministic() -> None:
    module = load_export_module()
    assert module.render() == module.render()


def test_all_mvp_endpoints_are_in_the_contract() -> None:
    paths = spec()["paths"]
    expected = {
        ("post", "/api/v1/auth/login"),
        ("post", "/api/v1/auth/logout"),
        ("get", "/api/v1/me"),
        ("get", "/api/v1/models"),
        ("post", "/api/v1/classify"),
        ("post", "/api/v1/classify/batch"),
        ("get", "/api/v1/classifications"),
        ("get", "/api/v1/classifications/{classification_id}"),
        ("get", "/api/v1/metrics/summary"),
        ("get", "/health"),
        ("get", "/ready"),
    }
    assert {(m, p) for p, ops in paths.items() for m in ops} == expected


def test_every_stable_error_code_is_documented() -> None:
    document = spec()
    assert {c.value for c in ErrorCode} >= CONTRACT_CODES
    assert set(document["components"]["schemas"]["ErrorCode"]["enum"]) >= CONTRACT_CODES
    examples = json.dumps(document["paths"])
    for code in CONTRACT_CODES:
        assert f'"code": "{code}"' in examples, f"{code} no aparece en ningún ejemplo de ruta"


def test_validation_errors_use_the_unified_format() -> None:
    document = spec()
    assert "HTTPValidationError" not in document["components"]["schemas"]
    assert "HTTPValidationError" not in json.dumps(document["paths"])


def test_classify_contract_has_no_confidence_and_enforces_limits() -> None:
    document = spec()
    assert "confidence" not in json.dumps(document).lower()
    request = document["components"]["schemas"]["ClassifyRequest"]["properties"]
    assert (request["text"]["minLength"], request["text"]["maxLength"]) == (1, 5000)
    assert request["external_ref"]["anyOf"][0]["maxLength"] == 100
    response = document["components"]["schemas"]["ClassifyResponse"]
    assert set(response["properties"]) == {"id", "category", "model", "model_version", "timestamp"}
    assert document["components"]["schemas"]["Category"]["enum"] == [
        "Petición",
        "Reclamo",
        "Denuncia",
        "Solicitud de información",
    ]


def test_history_pagination_limits() -> None:
    params = {p["name"]: p for p in spec()["paths"]["/api/v1/classifications"]["get"]["parameters"]}
    assert params["page_size"]["schema"]["maximum"] == 100
    assert {"date_from", "date_to", "category", "model", "source", "batch_id", "page"} <= set(
        params
    )


def test_csrf_header_and_security_schemes_are_documented() -> None:
    document = spec()
    post = document["paths"]["/api/v1/classify"]["post"]
    csrf = next(p for p in post["parameters"] if p["name"] == "X-PQRS-CSRF")
    assert csrf["in"] == "header" and "CSRF" in csrf["description"]
    assert post["security"] == [{"SessionCookie": []}, {"ApiKeyAuth": []}]
    assert document["paths"]["/api/v1/me"]["get"]["security"] == [{"SessionCookie": []}]
    assert "security" not in document["paths"]["/api/v1/auth/login"]["post"]
    assert set(document["components"]["securitySchemes"]) == {"SessionCookie", "ApiKeyAuth"}
