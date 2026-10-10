"""RNF-05: ningún repositorio devuelve datos de otra organización."""

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.application.ports import ClassificationFilters
from app.infrastructure.repositories.uow import SqlUnitOfWork
from tests.factories import NOW, SeededOrg, seed_org


@pytest.fixture
def org_a(db_session: Session) -> SeededOrg:
    return seed_org(db_session, "A", email="a@example.com")


@pytest.fixture
def org_b(db_session: Session) -> SeededOrg:
    return seed_org(db_session, "B", email="b@example.com")


@pytest.fixture
def uow(db_session: Session) -> SqlUnitOfWork:
    return SqlUnitOfWork(db_session)


def test_users_never_leak_across_orgs(uow: SqlUnitOfWork, org_a: SeededOrg, org_b: SeededOrg):
    b = org_b.org.id
    assert uow.users.get(b, org_a.user.id) is None
    assert uow.users.get_by_email(b, "a@example.com") is None
    assert [u.id for u in uow.users.list(b)] == [org_b.user.id]
    # Con el org_id correcto sí aparece.
    assert uow.users.get(org_a.org.id, org_a.user.id) == org_a.user


def test_user_mutations_are_scoped_by_org(uow: SqlUnitOfWork, org_a: SeededOrg, org_b: SeededOrg):
    b = org_b.org.id
    assert uow.users.set_password_hash(b, org_a.user.id, "otro-hash") is False
    assert uow.users.deactivate(b, org_a.user.id) is False
    unchanged = uow.users.get(org_a.org.id, org_a.user.id)
    assert unchanged is not None
    assert unchanged.password_hash == org_a.user.password_hash
    assert unchanged.active is True


def test_api_keys_never_leak_across_orgs(uow: SqlUnitOfWork, org_a: SeededOrg, org_b: SeededOrg):
    b = org_b.org.id
    assert uow.api_keys.get(b, org_a.api_key.id) is None
    assert [k.id for k in uow.api_keys.list(b)] == [org_b.api_key.id]


def test_api_key_mutations_are_scoped_by_org(
    uow: SqlUnitOfWork, org_a: SeededOrg, org_b: SeededOrg
):
    b = org_b.org.id
    assert uow.api_keys.revoke(b, org_a.api_key.id, NOW) is False
    uow.api_keys.touch_last_used(b, org_a.api_key.id, NOW)
    key = uow.api_keys.get(org_a.org.id, org_a.api_key.id)
    assert key is not None
    assert key.active is True
    assert key.revoked_at is None
    assert key.last_used_at is None


def test_classifications_never_leak_across_orgs(
    uow: SqlUnitOfWork, org_a: SeededOrg, org_b: SeededOrg
):
    b = org_b.org.id
    assert uow.classifications.get(b, org_a.classification.id) is None
    assert [
        c.id for c in uow.classifications.search(b, ClassificationFilters(), limit=50, offset=0)
    ] == [org_b.classification.id]
    assert (
        uow.classifications.search(b, ClassificationFilters(batch_id=uuid4()), limit=50, offset=0)
        == []
    )


def test_classifications_batch_filter_is_scoped_by_org(db_session: Session):
    batch_id = uuid4()
    org_a = seed_org(db_session, "A", batch_id=batch_id)
    org_b = seed_org(db_session, "B", batch_id=batch_id)  # mismo batch_id en otra org
    uow = SqlUnitOfWork(db_session)
    assert [
        c.id
        for c in uow.classifications.search(
            org_b.org.id, ClassificationFilters(batch_id=batch_id), limit=50, offset=0
        )
    ] == [org_b.classification.id]
    assert [
        c.id
        for c in uow.classifications.search(
            org_a.org.id, ClassificationFilters(batch_id=batch_id), limit=50, offset=0
        )
    ] == [org_a.classification.id]


def test_session_mutations_are_scoped_by_org(
    uow: SqlUnitOfWork, org_a: SeededOrg, org_b: SeededOrg
):
    b = org_b.org.id
    assert uow.sessions.delete(b, org_a.session.id) is False
    assert uow.sessions.delete_for_user(b, org_a.user.id) == 0
    uow.sessions.touch(b, org_a.session.id, NOW.replace(year=2030))
    found = uow.sessions.get_by_token_hash(org_a.session.token_hash)
    assert found is not None
    assert found.last_seen_at == org_a.session.last_seen_at


def test_a_resource_of_a_is_none_for_b(uow: SqlUnitOfWork, org_a: SeededOrg, org_b: SeededOrg):
    """El ``None`` que la capa API convierte en 404 (ver test_api_not_found_for_other_org)."""
    assert uow.classifications.get(org_b.org.id, org_a.classification.id) is None


def test_api_not_found_for_other_org(app, client, db_session: Session, hasher) -> None:
    """La capa API convierte el ``None`` del repositorio en 404 (ruta solo de pruebas)."""
    from typing import Annotated
    from uuid import UUID

    from fastapi import Depends

    from app.api.deps import get_uow, require_session
    from app.api.errors import or_404
    from app.application.ports import UnitOfWork
    from app.domain.principal import Principal
    from tests.factories import seed_login_user

    @app.get("/_test/classifications/{classification_id}")
    def read_classification(
        classification_id: UUID,
        principal: Annotated[Principal, Depends(require_session)],
        uow: Annotated[UnitOfWork, Depends(get_uow)],
    ) -> dict[str, str]:
        found = or_404(uow.classifications.get(principal.org_id, classification_id))
        return {"id": str(found.id)}

    other = seed_org(db_session, "A")  # clasificación de la organización A
    seed_login_user(db_session, hasher, email="b@example.com", org_name="B")
    login = client.post(
        "/api/v1/auth/login", json={"email": "b@example.com", "password": "contraseña-segura-1"}
    )
    assert login.status_code == 200

    response = client.get(f"/_test/classifications/{other.classification.id}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"
    # Un id inexistente se comporta exactamente igual que uno de otra organización.
    missing = client.get(f"/_test/classifications/{uuid4()}")
    assert missing.status_code == 404
    assert missing.json() == response.json()


def test_aggregates_never_count_other_orgs(db_session: Session):
    org_a = seed_org(db_session, "A")
    org_b = seed_org(db_session, "B")
    repo = SqlUnitOfWork(db_session).classifications
    everything = ClassificationFilters()
    assert repo.count(org_a.org.id, everything) == 1
    assert repo.counts(org_a.org.id, everything).total == 1
    assert repo.counts(org_a.org.id, everything).by_category == {"Reclamo": 1}
    assert len(repo.timeseries(org_b.org.id, everything, granularity="day")) == 1
    assert repo.bounds(org_b.org.id, everything) == (NOW, NOW)
