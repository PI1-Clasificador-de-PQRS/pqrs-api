"""GET /classifications y /classifications/{id}: filtros, paginación y aislamiento."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.infrastructure.security.passwords import Argon2PasswordHasher
from tests.factories import seed_row
from tests.helpers import Tenant, log_in, make_tenant

URL = "/api/v1/classifications"
BATCH = uuid4()


def at(day: int, hour: int = 12, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 1, day, hour, minute, second, tzinfo=UTC)


@pytest.fixture
def tenant(db_session: Session, hasher: Argon2PasswordHasher) -> Tenant:
    return make_tenant(db_session, hasher)


@pytest.fixture
def seeded(db_session: Session, tenant: Tenant) -> dict[str, object]:
    org = tenant.org.id
    rows = {
        "a": seed_row(
            db_session, org, created_at=at(10), category="Petición", engine="llm", source="ui"
        ),
        "b": seed_row(
            db_session,
            org,
            created_at=at(11),
            category="Reclamo",
            engine="classifier",
            source="api",
        ),
        "c": seed_row(
            db_session,
            org,
            created_at=at(12, 23, 59, 59),
            category="Denuncia",
            engine="llm",
            source="ui_batch",
            batch_id=BATCH,
        ),
        "d": seed_row(
            db_session,
            org,
            created_at=at(13, 0, 0, 0),
            category="Solicitud de información",
            engine="classifier",
            source="ui_batch",
            batch_id=BATCH,
        ),
        "e": seed_row(
            db_session, org, created_at=at(14), category="Reclamo", engine="llm", source="ui"
        ),
    }
    return {"rows": rows}


def ids(response) -> list[str]:
    return [item["id"] for item in response.json()["items"]]


def expected(seeded, *names: str) -> list[str]:
    return [str(seeded["rows"][n].id) for n in names]


def test_requires_a_session(client: TestClient, tenant: Tenant) -> None:
    assert client.get(URL).status_code == 401
    response = client.get(URL, headers=tenant.key_headers)
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "session_required"
    detail = client.get(f"{URL}/{uuid4()}", headers=tenant.key_headers)
    assert detail.json()["detail"]["code"] == "session_required"


def test_lists_newest_first_with_defaults(client: TestClient, tenant: Tenant, seeded) -> None:
    log_in(client, tenant)
    response = client.get(URL)
    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["page"], body["page_size"]) == (5, 1, 20)
    assert ids(response) == expected(seeded, "e", "d", "c", "b", "a")
    assert set(body["items"][0]) == {
        "id", "created_at", "text_anonymized", "category", "model", "model_version",
        "source", "batch_id", "external_ref",
    }  # fmt: skip


@pytest.mark.parametrize(
    ("query", "names"),
    [
        ("date_from=2026-01-12", ["e", "d", "c"]),
        ("date_to=2026-01-12", ["c", "b", "a"]),  # inclusiva: c es del 12 a las 23:59:59
        ("date_to=2026-01-13", ["e", "d", "c", "b", "a"][1:]),
        ("date_from=2026-01-11&date_to=2026-01-13", ["d", "c", "b"]),
        ("date_from=2026-01-13&date_to=2026-01-13", ["d"]),
        ("category=Reclamo", ["e", "b"]),
        ("category=Solicitud de información", ["d"]),
        ("model=llm", ["e", "c", "a"]),
        ("model=classifier", ["d", "b"]),
        ("source=ui", ["e", "a"]),
        ("source=ui_batch", ["d", "c"]),
        ("source=api", ["b"]),
        (f"batch_id={BATCH}", ["d", "c"]),
        ("category=Reclamo&model=llm&source=ui", ["e"]),
        ("category=Denuncia&source=api", []),
    ],
)
def test_filters(client: TestClient, tenant: Tenant, seeded, query: str, names: list[str]) -> None:
    log_in(client, tenant)
    response = client.get(f"{URL}?{query}")
    assert response.status_code == 200
    assert ids(response) == expected(seeded, *names)
    assert response.json()["total"] == len(names)


def test_pagination(client: TestClient, tenant: Tenant, seeded) -> None:
    log_in(client, tenant)
    first = client.get(f"{URL}?page_size=2").json()
    second = client.get(f"{URL}?page_size=2&page=2").json()
    third = client.get(f"{URL}?page_size=2&page=3").json()
    beyond = client.get(f"{URL}?page_size=2&page=4").json()

    assert [i["id"] for i in first["items"]] == expected(seeded, "e", "d")
    assert [i["id"] for i in second["items"]] == expected(seeded, "c", "b")
    assert [i["id"] for i in third["items"]] == expected(seeded, "a")
    assert beyond["items"] == []
    assert {first["total"], second["total"], third["total"], beyond["total"]} == {5}
    assert (second["page"], second["page_size"]) == (2, 2)


def test_pagination_applies_after_filters(client: TestClient, tenant: Tenant, seeded) -> None:
    log_in(client, tenant)
    body = client.get(f"{URL}?model=llm&page_size=2&page=2").json()
    assert [i["id"] for i in body["items"]] == expected(seeded, "a")
    assert body["total"] == 3


@pytest.mark.parametrize(
    "query",
    [
        "page_size=101",
        "page_size=0",
        "page=0",
        "category=Otra",
        "model=gpt",
        "source=web",
        "batch_id=no-es-uuid",
        "date_from=ayer",
        "date_from=2026-01-14&date_to=2026-01-13",
    ],
)
def test_invalid_query_is_422(client: TestClient, tenant: Tenant, query: str) -> None:
    log_in(client, tenant)
    response = client.get(f"{URL}?{query}")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"
    assert "ayer" not in response.text and "no-es-uuid" not in response.text


def test_detail_and_other_organization_is_404(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher, tenant: Tenant, seeded
) -> None:
    other = make_tenant(db_session, hasher, name="Org B", email="bea@example.com")
    foreign = seed_row(db_session, other.org.id, text="de la otra organización")
    log_in(client, tenant)

    mine = seeded["rows"]["a"]
    response = client.get(f"{URL}/{mine.id}")
    assert response.status_code == 200
    assert response.json()["id"] == str(mine.id)
    assert response.json()["category"] == "Petición"

    hidden = client.get(f"{URL}/{foreign.id}")
    assert hidden.status_code == 404
    assert hidden.json()["detail"]["code"] == "not_found"
    assert client.get(f"{URL}/{uuid4()}").status_code == 404
    assert client.get(f"{URL}/no-es-uuid").status_code == 422


def test_list_never_includes_other_organizations(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher, tenant: Tenant, seeded
) -> None:
    other = make_tenant(db_session, hasher, name="Org B", email="bea@example.com")
    seed_row(db_session, other.org.id, batch_id=BATCH, text="ajena")
    log_in(client, tenant)
    body = client.get(f"{URL}?batch_id={BATCH}").json()
    assert body["total"] == 2
    assert all(i["text_anonymized"] != "ajena" for i in body["items"])
