import ipaddress
from typing import Annotated

import pytest
from fastapi import Depends, Request
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.client_ip import resolve_client_ip
from app.api.deps import client_ip, get_settings_dep
from app.infrastructure.config.settings import Settings
from app.main import create_app
from tests.conftest import PROXY_HEADERS

PEER = "10.0.0.9"


def test_zero_hops_ignores_the_header() -> None:
    assert resolve_client_ip("1.1.1.1, 2.2.2.2", PEER, 0) == PEER
    assert resolve_client_ip("6.6.6.6", PEER, 0) == PEER


def test_one_hop_takes_the_rightmost_entry() -> None:
    assert resolve_client_ip("1.1.1.1, 2.2.2.2", PEER, 1) == "2.2.2.2"


def test_two_hops_take_the_second_from_the_right() -> None:
    assert resolve_client_ip("1.1.1.1, 2.2.2.2", PEER, 2) == "1.1.1.1"
    assert resolve_client_ip("9.9.9.9, 1.1.1.1, 2.2.2.2", PEER, 2) == "1.1.1.1"


@pytest.mark.parametrize("hops", [1, 2])
def test_forged_entries_on_the_left_do_not_change_the_result(hops: int) -> None:
    genuine = "1.1.1.1, 2.2.2.2"
    forged = "6.6.6.6, 7.7.7.7, " + genuine
    assert resolve_client_ip(forged, PEER, hops) == resolve_client_ip(genuine, PEER, hops)


def test_header_shorter_than_hops_falls_back_to_the_peer() -> None:
    assert resolve_client_ip("1.1.1.1", PEER, 2) == PEER
    assert resolve_client_ip("1.1.1.1, 2.2.2.2", PEER, 3) == PEER


@pytest.mark.parametrize(
    "header", [None, "", " ", ",", ",,", "basura", "1.1.1.1,", "1.1.1.1, no-es-ip", "999.1.1.1"]
)
@pytest.mark.parametrize("hops", [1, 2, 5])
def test_empty_or_malformed_header_never_raises(header: str | None, hops: int) -> None:
    result = resolve_client_ip(header, PEER, hops)
    assert result == PEER or ipaddress.ip_address(result)  # el peer o una IP válida


@pytest.mark.parametrize("header", [None, "", ",", "basura", "1.1.1.1,", "1.1.1.1, no-es-ip"])
def test_malformed_rightmost_entry_is_never_used(header: str | None) -> None:
    assert resolve_client_ip(header, PEER, 1) == PEER


def test_ipv6_is_normalized() -> None:
    assert resolve_client_ip("0:0:0:0:0:0:0:1", PEER, 1) == "::1"
    assert resolve_client_ip("2001:DB8::1", PEER, 1) == "2001:db8::1"


def test_missing_peer_stays_none() -> None:
    assert resolve_client_ip(None, None, 1) is None
    assert resolve_client_ip("garbage", None, 1) is None


def build_client(settings: Settings, session_factory: sessionmaker[Session], hops: int):
    app = create_app(settings.model_copy(update={"trusted_proxy_hops": hops}), session_factory)

    @app.get("/_test/ip")
    def show_ip(
        request: Request, settings: Annotated[Settings, Depends(get_settings_dep)]
    ) -> dict[str, str | None]:
        return {"ip": client_ip(request, settings)}

    return TestClient(app, base_url="https://testserver", headers=PROXY_HEADERS)


def test_request_uses_the_connection_ip_by_default(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    client = build_client(settings, session_factory, hops=0)
    response = client.get("/_test/ip", headers={"X-Forwarded-For": "1.1.1.1"})
    assert response.json() == {"ip": "testclient"}  # peer del TestClient


def test_request_uses_the_forwarded_ip_when_hops_are_set(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    client = build_client(settings, session_factory, hops=1)
    response = client.get("/_test/ip", headers={"X-Forwarded-For": "6.6.6.6, 1.1.1.1, 2.2.2.2"})
    assert response.json() == {"ip": "2.2.2.2"}


def test_multiple_header_lines_are_combined(
    settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    client = build_client(settings, session_factory, hops=2)
    response = client.get(
        "/_test/ip", headers=[("X-Forwarded-For", "1.1.1.1"), ("X-Forwarded-For", "2.2.2.2")]
    )
    assert response.json() == {"ip": "1.1.1.1"}


@pytest.mark.parametrize("header", ["", ",", "basura", "1.1.1.1,"])
def test_malformed_header_does_not_break_the_request(
    settings: Settings, session_factory: sessionmaker[Session], header: str
) -> None:
    client = build_client(settings, session_factory, hops=1)
    response = client.get("/_test/ip", headers={"X-Forwarded-For": header})
    assert response.status_code == 200
    assert response.json() == {"ip": "testclient"}

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "nadie@example.com", "password": "x"},
        headers={"X-Forwarded-For": header},
    )
    assert login.status_code == 401
