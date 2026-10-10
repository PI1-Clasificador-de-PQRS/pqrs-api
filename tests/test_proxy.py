"""Proxy de confianza del dashboard: secreto compartido e IP del navegador."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.config.settings import Settings, get_settings
from app.infrastructure.security.passwords import Argon2PasswordHasher
from app.main import create_app, get_app
from tests.conftest import PROXY_SECRET
from tests.factories import seed_login_user
from tests.helpers import make_tenant

LOGIN = "/api/v1/auth/login"
PASSWORD = "contraseña-segura-1"
EMAIL = "ana@example.com"
PROXY_IP = "X-PQRS-Client-IP"
SECRET = "X-PQRS-Proxy-Secret"


def credentials(password: str = PASSWORD, email: str = EMAIL) -> dict:
    return {"email": email, "password": password}


def login(client: TestClient, ip: str | None = None, password: str = PASSWORD, email: str = EMAIL,
          secret: str | None = PROXY_SECRET, forwarded: str | None = None):  # fmt: skip
    headers = {}
    if secret is not None:
        headers[SECRET] = secret
    if ip is not None:
        headers[PROXY_IP] = ip
    if forwarded is not None:
        headers["X-Forwarded-For"] = forwarded
    return client.post(LOGIN, json=credentials(password, email), headers=headers)


@pytest.fixture
def user(db_session: Session, hasher: Argon2PasswordHasher) -> None:
    seed_login_user(db_session, hasher)


def test_login_without_the_secret_is_403(direct_client: TestClient, user) -> None:
    response = login(direct_client, secret=None)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "proxy_required"
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("secret", ["incorrecto", "", PROXY_SECRET + "x", PROXY_SECRET[:-1]])
def test_login_with_a_wrong_secret_is_403(direct_client: TestClient, user, secret: str) -> None:
    response = login(direct_client, secret=secret)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "proxy_required"


def test_proxy_check_runs_before_credentials_and_validation(
    direct_client: TestClient, user
) -> None:
    assert login(direct_client, secret=None, password="incorrecta").status_code == 403
    response = direct_client.post(LOGIN, json={"email": 1})
    assert response.status_code == 403


def test_login_with_the_secret_works(direct_client: TestClient, user) -> None:
    assert login(direct_client).status_code == 200


def test_logout_requires_the_proxy(client: TestClient, direct_client: TestClient, user) -> None:
    assert login(client).status_code == 200
    # Con la cookie pero sin pasar por el proxy.
    direct_client.cookies.set("pqrs_session", client.cookies["pqrs_session"])
    response = direct_client.post("/api/v1/auth/logout", headers={"X-PQRS-CSRF": "1"})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "proxy_required"
    assert client.post("/api/v1/auth/logout", headers={"X-PQRS-CSRF": "1"}).status_code == 204


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/me"),
        ("GET", "/api/v1/classifications"),
        ("GET", "/api/v1/metrics/summary"),
        ("GET", "/api/v1/models"),
        ("POST", "/api/v1/classify"),
    ],
)
def test_authenticated_cookie_requests_require_the_proxy(
    client: TestClient, direct_client: TestClient, user, method: str, path: str
) -> None:
    assert login(client).status_code == 200
    direct_client.cookies.set("pqrs_session", client.cookies["pqrs_session"])
    response = direct_client.request(
        method, path, json={"text": "hola"} if method == "POST" else None,
        headers={"X-PQRS-CSRF": "1"},
    )  # fmt: skip
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "proxy_required"
    # Con el secreto, la misma cookie sirve.
    ok = client.request(method, path, json={"text": "hola"} if method == "POST" else None,
                        headers={"X-PQRS-CSRF": "1"})  # fmt: skip
    assert ok.status_code == 200


def test_no_cookie_is_still_401_not_403(direct_client: TestClient) -> None:
    response = direct_client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "missing_credentials"


def test_api_key_requests_do_not_need_the_proxy(
    direct_client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    tenant = make_tenant(db_session, hasher)
    response = direct_client.post(
        "/api/v1/classify", json={"text": "hola"}, headers=tenant.key_headers
    )
    assert response.status_code == 200
    assert direct_client.get("/api/v1/models", headers=tenant.key_headers).status_code == 200


def test_health_and_ready_are_public(direct_client: TestClient) -> None:
    assert direct_client.get("/health").status_code == 200
    assert direct_client.get("/ready").status_code == 200


# --- IP del cliente ----------------------------------------------------------------------------


def test_client_ip_header_is_used_with_a_valid_secret(
    session_factory, clock, hasher, settings, registry, user
) -> None:
    """Dos navegadores detrás del mismo proxy se cuentan por separado."""
    app = create_app(settings, session_factory, clock=clock, hasher=hasher, registry=registry)
    client = TestClient(app, base_url="https://testserver")
    for _ in range(5):
        assert login(client, ip="9.9.9.9", password="incorrecta").status_code == 401
    assert login(client, ip="9.9.9.9", password="incorrecta").status_code == 429
    assert login(client, ip="8.8.8.8").status_code == 200  # otro navegador, no bloqueado


def test_an_attacker_without_the_secret_cannot_choose_their_ip(
    session_factory, clock, hasher, settings, registry, user
) -> None:
    """Sin secreto no hay login (403), así que X-PQRS-Client-IP no puede evadir el límite."""
    app = create_app(settings, session_factory, clock=clock, hasher=hasher, registry=registry)
    attacker = TestClient(app, base_url="https://testserver")
    for i in range(10):
        response = login(attacker, ip=f"10.0.0.{i}", secret=None, password="incorrecta")
        assert response.status_code == 403
    # Nada de eso contó contra la IP real ni contra nadie.
    legit = TestClient(app, base_url="https://testserver")
    assert login(legit, ip="9.9.9.9").status_code == 200


def test_client_ip_header_with_a_wrong_secret_is_rejected_before_it_is_used(
    session_factory, clock, hasher, settings, registry, user
) -> None:
    app = create_app(settings, session_factory, clock=clock, hasher=hasher, registry=registry)
    attacker = TestClient(app, base_url="https://testserver")
    assert login(attacker, ip="1.2.3.4", secret="adivinado").status_code == 403


def test_client_ip_helper_ignores_the_header_without_a_valid_secret(
    settings: Settings,
) -> None:
    from starlette.requests import Request

    from app.api.deps import client_ip

    def request(headers: list[tuple[bytes, bytes]]) -> Request:
        return Request(
            {
                "type": "http",
                "headers": headers,
                "client": ("7.7.7.7", 1234),
                "method": "GET",
                "path": "/",
            }  # fmt: skip
        )

    secret = (b"x-pqrs-proxy-secret", PROXY_SECRET.encode())
    spoof = (b"x-pqrs-client-ip", b"1.2.3.4")
    assert client_ip(request([secret, spoof]), settings) == "1.2.3.4"
    assert client_ip(request([spoof]), settings) == "7.7.7.7"  # sin secreto
    assert client_ip(request([(b"x-pqrs-proxy-secret", b"otro"), spoof]), settings) == "7.7.7.7"
    assert client_ip(request([secret, (b"x-pqrs-client-ip", b"no-es-ip")]), settings) == "7.7.7.7"
    assert client_ip(request([secret]), settings) == "7.7.7.7"
    hops = settings.model_copy(update={"trusted_proxy_hops": 1})
    forwarded = (b"x-forwarded-for", b"6.6.6.6, 5.5.5.5")
    assert client_ip(request([forwarded]), hops) == "5.5.5.5"  # sin cabecera del proxy: saltos
    assert client_ip(request([secret, spoof, forwarded]), hops) == "1.2.3.4"
    assert client_ip(request([(b"x-pqrs-client-ip", b"::1"), secret]), hops) == "::1"


def test_email_limit_acts_as_a_backstop_against_distributed_attacks(
    session_factory, clock, hasher, settings, registry, user
) -> None:
    configured = settings.model_copy(
        update={
            "login_max_attempts": 100,
            "login_max_attempts_per_ip": 100,
            "login_max_attempts_per_email": 6,
        }
    )
    app = create_app(configured, session_factory, clock=clock, hasher=hasher, registry=registry)
    client = TestClient(app, base_url="https://testserver")
    for i in range(6):  # el mismo correo desde 6 IP distintas: ni el par ni la IP se llenan
        assert login(client, ip=f"20.0.0.{i}", password="incorrecta").status_code == 401
    blocked = login(client, ip="20.0.1.1")
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "too_many_attempts"
    assert login(client, ip="20.0.1.1", email="otra@example.com").status_code == 401


# --- configuración obligatoria ----------------------------------------------------------------


def test_app_does_not_start_without_the_secret_outside_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROXY_SHARED_SECRET", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="PROXY_SHARED_SECRET"):
            get_app()
    finally:
        get_settings.cache_clear()


def test_empty_secret_does_not_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROXY_SHARED_SECRET", "")
    monkeypatch.setenv("ENVIRONMENT", "staging")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError):
            get_app()
    finally:
        get_settings.cache_clear()


def test_app_starts_with_the_secret_or_in_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PROXY_SHARED_SECRET", "un-secreto")
    get_settings.cache_clear()
    try:
        assert get_app() is not None
        monkeypatch.delenv("PROXY_SHARED_SECRET")
        monkeypatch.setenv("ENVIRONMENT", "development")
        get_settings.cache_clear()
        assert get_app() is not None
    finally:
        get_settings.cache_clear()


def test_development_without_secret_skips_the_proxy_check(
    settings: Settings, session_factory: sessionmaker[Session], clock, hasher, registry, user
) -> None:
    dev = settings.model_copy(update={"environment": "development", "proxy_shared_secret": None})
    client = TestClient(
        create_app(dev, session_factory, clock=clock, hasher=hasher, registry=registry),
        base_url="https://testserver",
    )
    assert login(client, secret=None).status_code == 200


def test_without_a_secret_outside_development_everything_cookie_based_is_denied(
    settings: Settings, session_factory: sessionmaker[Session], clock, hasher, registry, user
) -> None:
    prod = settings.model_copy(update={"proxy_shared_secret": None})
    client = TestClient(
        create_app(prod, session_factory, clock=clock, hasher=hasher, registry=registry),
        base_url="https://testserver",
    )
    assert login(client, secret="cualquiera").status_code == 403
    assert login(client, secret="").status_code == 403


def test_development_with_a_secret_still_enforces_it(
    settings: Settings, session_factory: sessionmaker[Session], clock, hasher, registry, user
) -> None:
    dev = settings.model_copy(update={"environment": "development"})
    client = TestClient(
        create_app(dev, session_factory, clock=clock, hasher=hasher, registry=registry),
        base_url="https://testserver",
    )
    assert login(client, secret=None).status_code == 403
    assert login(client).status_code == 200
