"""Parámetro ``tz`` de GET /classifications y GET /metrics/summary: días y periodos locales."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.db.timezones import PgTimezoneCatalog
from app.infrastructure.security.passwords import Argon2PasswordHasher
from tests.factories import seed_row
from tests.helpers import Tenant, log_in, make_tenant

URL = "/api/v1/metrics/summary"
HISTORY = "/api/v1/classifications"
BOGOTA = "tz=America/Bogota"
NEW_YORK = "tz=America/New_York"


def utc(month: int, day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, second, tzinfo=UTC)


@pytest.fixture
def tenant(db_session: Session, hasher: Argon2PasswordHasher) -> Tenant:
    return make_tenant(db_session, hasher)


@pytest.fixture
def evening_in_bogota(db_session: Session, tenant: Tenant) -> None:
    # 01:00 UTC del 13 de octubre = 20:00 del 12 en Bogotá (UTC−5).
    seed_row(db_session, tenant.org.id, created_at=utc(10, 13, 1))


def history_total(client: TestClient, query: str) -> int:
    response = client.get(f"{HISTORY}?{query}")
    assert response.status_code == 200, response.text
    total: int = response.json()["total"]
    return total


def test_history_days_follow_the_timezone(
    client: TestClient, tenant: Tenant, evening_in_bogota: None
) -> None:
    log_in(client, tenant)
    assert history_total(client, f"date_from=2026-10-12&date_to=2026-10-12&{BOGOTA}") == 1
    assert history_total(client, f"date_from=2026-10-13&date_to=2026-10-13&{BOGOTA}") == 0
    assert history_total(client, "date_from=2026-10-12&date_to=2026-10-12") == 0
    assert history_total(client, "date_from=2026-10-13&date_to=2026-10-13") == 1


def test_daily_series_follows_the_timezone(
    client: TestClient, tenant: Tenant, evening_in_bogota: None
) -> None:
    log_in(client, tenant)
    query = "date_from=2026-10-12&date_to=2026-10-13"
    local = client.get(f"{URL}?{query}&{BOGOTA}").json()["timeseries"]
    assert local == [{"period": "2026-10-12", "count": 1}, {"period": "2026-10-13", "count": 0}]
    in_utc = client.get(f"{URL}?{query}").json()["timeseries"]
    assert in_utc == [{"period": "2026-10-12", "count": 0}, {"period": "2026-10-13", "count": 1}]


def test_series_without_dates_spans_local_days(
    client: TestClient, db_session: Session, tenant: Tenant, evening_in_bogota: None
) -> None:
    seed_row(db_session, tenant.org.id, created_at=utc(10, 15, 4, 59))  # 23:59 del 14 en Bogotá
    log_in(client, tenant)
    series = client.get(f"{URL}?{BOGOTA}").json()["timeseries"]
    assert series == [
        {"period": "2026-10-12", "count": 1},
        {"period": "2026-10-13", "count": 0},
        {"period": "2026-10-14", "count": 1},
    ]
    assert [p["period"] for p in client.get(URL).json()["timeseries"]] == [
        "2026-10-13",
        "2026-10-14",
        "2026-10-15",
    ]


def test_week_starts_on_local_monday(
    client: TestClient, db_session: Session, tenant: Tenant
) -> None:
    # Domingo 11 de octubre a las 20:00 en Bogotá = lunes 12 a las 01:00 UTC.
    seed_row(db_session, tenant.org.id, created_at=utc(10, 12, 1))
    log_in(client, tenant)
    query = "granularity=week&date_from=2026-10-05&date_to=2026-10-18"
    local = client.get(f"{URL}?{query}&{BOGOTA}").json()["timeseries"]
    assert local == [{"period": "2026-10-05", "count": 1}, {"period": "2026-10-12", "count": 0}]
    in_utc = client.get(f"{URL}?{query}").json()["timeseries"]
    assert in_utc == [{"period": "2026-10-05", "count": 0}, {"period": "2026-10-12", "count": 1}]


# Nueva York: el 8 de marzo de 2026 dura 23 horas ([05:00 UTC, 04:00 UTC del 9)) y el 1 de
# noviembre, 25 ([04:00 UTC, 05:00 UTC del 2), con la 01:xx local repetida).
DST_DAYS = [
    pytest.param(
        "2026-03-08",
        [utc(3, 8, 5), utc(3, 9, 3, 59, 59)],
        [utc(3, 8, 4, 59, 59), utc(3, 9, 4)],
        id="spring-forward-23h",
    ),
    pytest.param(
        "2026-11-01",
        [utc(11, 1, 4), utc(11, 1, 5, 30), utc(11, 1, 6, 30), utc(11, 2, 4, 59, 59)],
        [utc(11, 1, 3, 59, 59), utc(11, 2, 5)],
        id="fall-back-25h",
    ),
]


@pytest.mark.parametrize(("day", "inside", "outside"), DST_DAYS)
def test_dst_transition_day_contains_exactly_its_local_classifications(
    client: TestClient,
    db_session: Session,
    tenant: Tenant,
    day: str,
    inside: list[datetime],
    outside: list[datetime],
) -> None:
    for created_at in inside + outside:
        seed_row(db_session, tenant.org.id, created_at=created_at)
    log_in(client, tenant)
    query = f"date_from={day}&date_to={day}&{NEW_YORK}"

    history = client.get(f"{HISTORY}?{query}&page_size=100").json()
    assert sorted(datetime.fromisoformat(i["created_at"]) for i in history["items"]) == inside
    metrics = client.get(f"{URL}?{query}").json()
    assert metrics["total"] == len(inside)
    assert metrics["timeseries"] == [{"period": day, "count": len(inside)}]
    # La serie de PostgreSQL reparte las de fuera en los días vecinos, igual que el filtro.
    series = client.get(f"{URL}?{NEW_YORK}").json()["timeseries"]
    assert {p["period"]: p["count"] for p in series}[day] == len(inside)
    assert sum(p["count"] for p in series) == len(inside) + len(outside)


@pytest.mark.parametrize("tz", ["UTC", "America/Bogota", "America/New_York", "Asia/Tokyo"])
@pytest.mark.parametrize(
    "dates",
    ["", "date_from=2026-10-12", "date_to=2026-10-12", "date_from=2026-10-12&date_to=2026-10-13"],
)
def test_totals_match_the_history_with_the_same_timezone(
    client: TestClient, db_session: Session, tenant: Tenant, tz: str, dates: str
) -> None:
    # Una cada 5 horas del 11 al 13 de octubre UTC: caen a ambos lados de cada medianoche local.
    for hour in range(0, 72, 5):
        seed_row(db_session, tenant.org.id, created_at=utc(10, 11, 0) + timedelta(hours=hour))
    log_in(client, tenant)
    query = f"{dates}&tz={tz}"
    metrics = client.get(f"{URL}?{query}").json()
    assert metrics["total"] == history_total(client, query)
    assert sum(p["count"] for p in metrics["timeseries"]) == metrics["total"]
    assert metrics["timezone"] == tz


def test_explicit_utc_matches_the_default(
    client: TestClient, tenant: Tenant, evening_in_bogota: None
) -> None:
    log_in(client, tenant)
    for granularity in ("day", "week"):
        default = client.get(f"{URL}?granularity={granularity}").json()
        assert client.get(f"{URL}?granularity={granularity}&tz=UTC").json() == default
        assert default["timezone"] == "UTC"
    assert client.get(f"{HISTORY}?tz=UTC").json() == client.get(HISTORY).json()


def test_summary_echoes_the_applied_timezone(client: TestClient, tenant: Tenant) -> None:
    log_in(client, tenant)
    assert client.get(URL).json()["timezone"] == "UTC"
    assert client.get(f"{URL}?{BOGOTA}").json()["timezone"] == "America/Bogota"


INVALID_ZONES = [
    "Mars/Olympus",
    "",
    "UTC'; --",
    "america/bogota",  # PostgreSQL no distingue mayúsculas; se exige el nombre exacto
    "UTC+5",  # cadena POSIX: PostgreSQL la entiende como UTC−5
    "localtime",  # solo existe para PostgreSQL
    "../../etc/passwd",
]


@pytest.mark.parametrize("url", [URL, HISTORY])
@pytest.mark.parametrize("tz", INVALID_ZONES)
def test_invalid_timezone_is_422_without_echoing_it(
    client: TestClient, tenant: Tenant, url: str, tz: str
) -> None:
    log_in(client, tenant)
    response = client.get(url, params={"tz": tz})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"
    if tz:
        assert tz not in response.text


def test_zone_unknown_to_postgres_is_422(app: FastAPI, client: TestClient, tenant: Tenant) -> None:
    """Python la conoce, pero la base no: se rechaza para que filtro y serie no difieran."""

    class WithoutMadrid:
        def names(self) -> frozenset[str]:
            return frozenset({"UTC", "America/New_York"})

    app.state.timezones = WithoutMadrid()
    log_in(client, tenant)
    assert client.get(f"{URL}?{NEW_YORK}").status_code == 200
    response = client.get(f"{URL}?tz=Europe/Madrid")
    assert response.status_code == 422
    assert "Europe/Madrid" not in response.text


def test_catalog_reads_postgres_once(session_factory: sessionmaker[Session]) -> None:
    catalog = PgTimezoneCatalog(session_factory)
    names = catalog.names()
    assert {"UTC", "America/Bogota", "America/New_York", "Europe/Madrid"} <= names
    assert catalog.names() is names


def test_isolation_is_unchanged_with_timezone(
    client: TestClient,
    db_session: Session,
    hasher: Argon2PasswordHasher,
    tenant: Tenant,
    evening_in_bogota: None,
) -> None:
    other = make_tenant(db_session, hasher, name="Org B", email="bea@example.com")
    for _ in range(3):
        seed_row(db_session, other.org.id, created_at=utc(10, 13, 2))
    query = f"date_from=2026-10-12&date_to=2026-10-12&{BOGOTA}"

    log_in(client, tenant)
    assert client.get(f"{URL}?{query}").json()["total"] == 1
    assert history_total(client, query) == 1

    client.cookies.clear()
    log_in(client, other)
    theirs = client.get(f"{URL}?{query}").json()
    assert theirs["total"] == 3
    assert theirs["timeseries"] == [{"period": "2026-10-12", "count": 3}]
    assert history_total(client, query) == 3
