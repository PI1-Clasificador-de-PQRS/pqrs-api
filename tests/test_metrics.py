"""GET /metrics/summary: agregaciones en SQL, claves en 0, serie sin huecos y aislamiento."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.categories import CATEGORIES, ENGINES, SOURCES
from app.infrastructure.security.passwords import Argon2PasswordHasher
from tests.factories import seed_row
from tests.helpers import Tenant, log_in, make_tenant

URL = "/api/v1/metrics/summary"
HISTORY = "/api/v1/classifications"


def at(day: int, hour: int = 12, month: int = 1) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


@pytest.fixture
def tenant(db_session: Session, hasher: Argon2PasswordHasher) -> Tenant:
    return make_tenant(db_session, hasher)


def test_requires_a_session(client: TestClient, tenant: Tenant) -> None:
    assert client.get(URL).status_code == 401
    response = client.get(URL, headers=tenant.key_headers)
    assert response.json()["detail"]["code"] == "session_required"


def test_empty_organization_has_all_keys_with_zero(client: TestClient, tenant: Tenant) -> None:
    log_in(client, tenant)
    body = client.get(URL).json()
    assert body["total"] == 0
    assert body["by_category"] == dict.fromkeys(CATEGORIES, 0)
    assert body["by_model"] == dict.fromkeys(ENGINES, 0)
    assert body["by_source"] == dict.fromkeys(SOURCES, 0)
    assert body["timeseries"] == []


def test_counts_include_every_key_even_with_zero(
    client: TestClient, db_session: Session, tenant: Tenant
) -> None:
    seed_row(db_session, tenant.org.id, category="Reclamo", engine="llm", source="ui")
    seed_row(db_session, tenant.org.id, category="Reclamo", engine="llm", source="ui")
    seed_row(db_session, tenant.org.id, category="Denuncia", engine="llm", source="ui_batch")
    log_in(client, tenant)
    body = client.get(URL).json()
    assert body["total"] == 3
    assert body["by_category"] == {
        "Petición": 0, "Reclamo": 2, "Denuncia": 1, "Solicitud de información": 0,
    }  # fmt: skip
    assert body["by_model"] == {"llm": 3, "classifier": 0}
    assert body["by_source"] == {"ui": 2, "ui_batch": 1, "api": 0}


def test_evaluation_comes_from_the_engines(client: TestClient, tenant: Tenant) -> None:
    log_in(client, tenant)
    evaluation = client.get(URL).json()["evaluation"]
    assert evaluation == {"llm": {"f1_macro": 0.8329}, "classifier": {"f1_macro": 0.7776}}


@pytest.fixture
def seeded(db_session: Session, tenant: Tenant) -> None:
    org = tenant.org.id
    data = [
        (at(5), "Petición", "llm", "ui"),
        (at(6), "Reclamo", "classifier", "api"),
        (at(6, 23), "Reclamo", "llm", "api"),
        (at(8), "Denuncia", "llm", "ui_batch"),
        (at(8, 1), "Solicitud de información", "classifier", "ui_batch"),
        (at(12), "Petición", "classifier", "ui"),
        (at(12, 20), "Petición", "llm", "api"),
        (at(15), "Reclamo", "classifier", "ui"),
    ]
    for created_at, category, engine, source in data:
        seed_row(
            db_session, org, created_at=created_at, category=category, engine=engine, source=source
        )


@pytest.mark.parametrize(
    "query",
    [
        "",
        "date_from=2026-01-06",
        "date_to=2026-01-08",
        "date_from=2026-01-06&date_to=2026-01-12",
        "date_from=2026-01-08&date_to=2026-01-08",
        "date_from=2026-02-01",
    ],
)
def test_totals_match_the_history_with_the_same_filters(
    client: TestClient, tenant: Tenant, seeded, query: str
) -> None:
    """Criterio de la etapa E4: el dashboard muestra métricas e historial coherentes."""
    log_in(client, tenant)
    metrics = client.get(f"{URL}?{query}").json()
    history = client.get(f"{HISTORY}?{query}&page_size=100").json()

    assert metrics["total"] == history["total"] == len(history["items"])
    assert sum(metrics["by_category"].values()) == metrics["total"]
    assert sum(metrics["by_model"].values()) == metrics["total"]
    assert sum(metrics["by_source"].values()) == metrics["total"]
    assert sum(p["count"] for p in metrics["timeseries"]) == metrics["total"]
    for category, n in metrics["by_category"].items():
        assert client.get(f"{HISTORY}?{query}&category={category}").json()["total"] == n
    for model, n in metrics["by_model"].items():
        assert client.get(f"{HISTORY}?{query}&model={model}").json()["total"] == n
    for source, n in metrics["by_source"].items():
        assert client.get(f"{HISTORY}?{query}&source={source}").json()["total"] == n


def test_daily_series_has_no_gaps(client: TestClient, tenant: Tenant, seeded) -> None:
    log_in(client, tenant)
    series = client.get(f"{URL}?date_from=2026-01-04&date_to=2026-01-16").json()["timeseries"]
    assert [p["period"] for p in series] == [f"2026-01-{d:02d}" for d in range(4, 17)]
    counts = {p["period"]: p["count"] for p in series}
    assert counts["2026-01-04"] == 0 and counts["2026-01-07"] == 0
    assert counts["2026-01-05"] == 1 and counts["2026-01-06"] == 2 and counts["2026-01-08"] == 2
    assert counts["2026-01-12"] == 2 and counts["2026-01-15"] == 1 and counts["2026-01-16"] == 0


def test_weekly_series_starts_on_monday_and_has_no_gaps(
    client: TestClient, tenant: Tenant, seeded
) -> None:
    log_in(client, tenant)
    # 2026-01-05 es lunes; 2026-01-15 es jueves de la semana que empieza el 12.
    series = client.get(f"{URL}?granularity=week&date_from=2026-01-01&date_to=2026-01-25").json()[
        "timeseries"
    ]
    assert series == [
        {"period": "2025-12-29", "count": 0},
        {"period": "2026-01-05", "count": 5},
        {"period": "2026-01-12", "count": 3},
        {"period": "2026-01-19", "count": 0},
    ]


def test_series_without_dates_spans_first_to_last_classification(
    client: TestClient, tenant: Tenant, seeded
) -> None:
    log_in(client, tenant)
    series = client.get(URL).json()["timeseries"]
    assert series[0] == {"period": "2026-01-05", "count": 1}
    assert series[-1] == {"period": "2026-01-15", "count": 1}
    assert len(series) == 11
    only_to = client.get(f"{URL}?date_to=2026-01-09").json()["timeseries"]
    assert (only_to[0]["period"], only_to[-1]["period"]) == ("2026-01-05", "2026-01-09")


def test_days_are_utc(client: TestClient, db_session: Session, tenant: Tenant) -> None:
    bogota = timezone(timedelta(hours=-5))
    # 23:30 del 10 en Bogotá = 04:30 UTC del día 11.
    seed_row(db_session, tenant.org.id, created_at=datetime(2026, 1, 10, 23, 30, tzinfo=bogota))
    log_in(client, tenant)
    series = client.get(f"{URL}?date_from=2026-01-10&date_to=2026-01-11").json()["timeseries"]
    assert series == [{"period": "2026-01-10", "count": 0}, {"period": "2026-01-11", "count": 1}]
    assert client.get(f"{HISTORY}?date_to=2026-01-10").json()["total"] == 0


def test_range_with_too_many_periods_is_422(client: TestClient, tenant: Tenant) -> None:
    log_in(client, tenant)
    too_long = client.get(f"{URL}?date_from=2024-01-01&date_to=2026-01-01")
    assert too_long.status_code == 422
    assert too_long.json()["detail"]["code"] == "invalid_request"
    assert (
        client.get(f"{URL}?date_from=2024-01-01&date_to=2026-01-01&granularity=week").status_code
        == 200
    )
    assert client.get(f"{URL}?date_from=0001-01-01").status_code == 200  # sin datos: serie vacía


@pytest.mark.parametrize(
    "query", ["granularity=month", "date_from=ayer", "date_from=2026-01-09&date_to=2026-01-08"]
)
def test_invalid_query_is_422(client: TestClient, tenant: Tenant, query: str) -> None:
    log_in(client, tenant)
    assert client.get(f"{URL}?{query}").status_code == 422


def test_metrics_are_isolated_between_organizations(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher, tenant: Tenant, seeded
) -> None:
    other = make_tenant(db_session, hasher, name="Org B", email="bea@example.com")
    for _ in range(3):
        seed_row(db_session, other.org.id, created_at=at(20), category="Denuncia", source="ui")

    log_in(client, tenant)
    mine = client.get(URL).json()
    assert mine["total"] == 8
    assert mine["by_category"]["Denuncia"] == 1
    assert mine["timeseries"][-1]["period"] == "2026-01-15"  # sin el día 20 de la otra org

    client.cookies.clear()
    log_in(client, other)
    theirs = client.get(URL).json()
    assert theirs["total"] == 3
    assert theirs["by_category"]["Denuncia"] == 3
    assert theirs["by_source"] == {"ui": 3, "ui_batch": 0, "api": 0}
    assert [p for p in theirs["timeseries"] if p["count"]] == [{"period": "2026-01-20", "count": 3}]
