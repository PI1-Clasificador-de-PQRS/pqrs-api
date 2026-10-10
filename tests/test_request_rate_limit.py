"""Límite de solicitudes por API key y por usuario (nunca por IP)."""

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.infrastructure.config.settings import Settings
from app.infrastructure.security.passwords import Argon2PasswordHasher
from app.infrastructure.security.request_rate_limit import InMemoryRequestRateLimiter
from app.main import create_app
from tests.conftest import PROXY_HEADERS
from tests.fakes import FakeClock
from tests.helpers import CSRF, Tenant, log_in, make_tenant

CLASSIFY = "/api/v1/classify"


@pytest.fixture
def tenant(db_session: Session, hasher: Argon2PasswordHasher) -> Tenant:
    return make_tenant(db_session, hasher)


def small_limits(settings, session_factory, clock, hasher, registry, key=3, user=5) -> TestClient:
    configured = settings.model_copy(
        update={"rate_limit_key_per_minute": key, "rate_limit_user_per_minute": user}
    )
    app: FastAPI = create_app(
        configured, session_factory, clock=clock, hasher=hasher, registry=registry
    )
    return TestClient(app, base_url="https://testserver", headers=PROXY_HEADERS)


def test_api_key_limit_returns_429_with_retry_after(
    settings: Settings, session_factory, clock: FakeClock, hasher, registry, tenant: Tenant
) -> None:
    client = small_limits(settings, session_factory, clock, hasher, registry)
    for _ in range(3):
        assert (
            client.post(CLASSIFY, json={"text": "hola"}, headers=tenant.key_headers).status_code
            == 200
        )
    clock.advance(seconds=20)
    blocked = client.post(CLASSIFY, json={"text": "hola"}, headers=tenant.key_headers)
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "rate_limited"
    assert blocked.headers["retry-after"] == "40"  # la ventana de 60 s se libera a los 40 s

    clock.advance(seconds=41)
    assert (
        client.post(CLASSIFY, json={"text": "hola"}, headers=tenant.key_headers).status_code == 200
    )


def test_all_three_routes_share_the_same_counter(
    settings: Settings, session_factory, clock: FakeClock, hasher, registry, tenant: Tenant
) -> None:
    client = small_limits(settings, session_factory, clock, hasher, registry)
    h = tenant.key_headers
    assert client.get("/api/v1/models", headers=h).status_code == 200
    assert client.post(CLASSIFY, json={"text": "a"}, headers=h).status_code == 200
    assert (
        client.post(f"{CLASSIFY}/batch", json={"items": [{"text": "a"}]}, headers=h).status_code
        == 200
    )
    for response in (
        client.get("/api/v1/models", headers=h),
        client.post(CLASSIFY, json={"text": "a"}, headers=h),
        client.post(f"{CLASSIFY}/batch", json={"items": [{"text": "a"}]}, headers=h),
    ):
        assert response.status_code == 429


def test_user_limit_with_a_session(
    settings: Settings, session_factory, clock: FakeClock, hasher, registry, tenant: Tenant
) -> None:
    client = small_limits(settings, session_factory, clock, hasher, registry)
    log_in(client, tenant)
    for _ in range(5):
        assert client.post(CLASSIFY, json={"text": "hola"}, headers=CSRF).status_code == 200
    blocked = client.post(CLASSIFY, json={"text": "hola"}, headers=CSRF)
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) >= 1


def test_limits_are_per_key_and_per_user_not_shared(
    settings: Settings,
    session_factory,
    clock: FakeClock,
    hasher,
    registry,
    db_session: Session,
    tenant: Tenant,
) -> None:
    from tests.factories import seed_api_key

    client = small_limits(settings, session_factory, clock, hasher, registry)
    other_key, _ = seed_api_key(db_session, tenant.org.id, name="Otra")
    for _ in range(3):
        client.post(CLASSIFY, json={"text": "a"}, headers=tenant.key_headers)
    assert client.post(CLASSIFY, json={"text": "a"}, headers=tenant.key_headers).status_code == 429
    assert (
        client.post(CLASSIFY, json={"text": "a"}, headers={"X-API-Key": other_key}).status_code
        == 200
    )
    log_in(client, tenant)  # el usuario tiene su propio contador
    assert client.post(CLASSIFY, json={"text": "a"}, headers=CSRF).status_code == 200


def test_the_limit_never_depends_on_the_ip(
    settings: Settings, session_factory, clock: FakeClock, hasher, registry, tenant: Tenant
) -> None:
    client = small_limits(settings, session_factory, clock, hasher, registry)
    for i in range(3):
        headers = {
            **tenant.key_headers,
            "X-Forwarded-For": f"1.1.1.{i}",
            "X-PQRS-Client-IP": f"2.2.2.{i}",
        }
        assert client.post(CLASSIFY, json={"text": "a"}, headers=headers).status_code == 200
    headers = {**tenant.key_headers, "X-Forwarded-For": "9.9.9.9"}  # otra IP, misma key
    assert client.post(CLASSIFY, json={"text": "a"}, headers=headers).status_code == 429


def test_unauthenticated_requests_are_not_counted(
    settings: Settings, session_factory, clock: FakeClock, hasher, registry, tenant: Tenant
) -> None:
    client = small_limits(settings, session_factory, clock, hasher, registry)
    for _ in range(10):
        assert client.post(CLASSIFY, json={"text": "a"}).status_code == 401
    assert client.post(CLASSIFY, json={"text": "a"}, headers=tenant.key_headers).status_code == 200


def test_history_and_metrics_are_not_rate_limited(
    settings: Settings, session_factory, clock: FakeClock, hasher, registry, tenant: Tenant
) -> None:
    client = small_limits(settings, session_factory, clock, hasher, registry, user=1)
    log_in(client, tenant)
    for _ in range(5):
        assert client.get("/api/v1/classifications").status_code == 200
        assert client.get("/api/v1/metrics/summary").status_code == 200


# --- limitador ----------------------------------------------------------------------------------


def make(clock: FakeClock, max_keys: int = 100) -> InMemoryRequestRateLimiter:
    return InMemoryRequestRateLimiter(window=timedelta(minutes=1), max_keys=max_keys, clock=clock)


def test_window_slides(clock: FakeClock) -> None:
    limiter = make(clock)
    assert limiter.hit("k", 2) is None
    clock.advance(seconds=30)
    assert limiter.hit("k", 2) is None
    assert limiter.hit("k", 2) == 30  # la primera sale de la ventana en 30 s
    clock.advance(seconds=30)
    assert limiter.hit("k", 2) is None


def test_rejected_requests_do_not_extend_the_block(clock: FakeClock) -> None:
    limiter = make(clock)
    limiter.hit("k", 1)
    for _ in range(10):
        clock.advance(seconds=5)
        assert limiter.hit("k", 1) is not None
    clock.advance(seconds=10)  # 60 s desde la única solicitud aceptada
    assert limiter.hit("k", 1) is None


def test_memory_is_capped(clock: FakeClock) -> None:
    limiter = make(clock, max_keys=5)
    for i in range(50):
        clock.advance(seconds=1)
        limiter.hit(f"k{i}", 10)
        assert limiter.size <= 5
