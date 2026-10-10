from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.infrastructure.db.models import SessionModel
from app.infrastructure.security.passwords import Argon2PasswordHasher
from tests.factories import seed_login_user
from tests.fakes import FakeClock

CSRF = {"X-PQRS-CSRF": "1"}
PASSWORD = "contraseña-segura-1"


def logged_in(client: TestClient, db: Session, hasher: Argon2PasswordHasher, **kwargs):
    org, user = seed_login_user(db, hasher, **kwargs)
    response = client.post("/api/v1/auth/login", json={"email": user.email, "password": PASSWORD})
    assert response.status_code == 200
    return org, user


def last_seen(db: Session):
    db.expire_all()
    return db.scalars(select(SessionModel.last_seen_at)).one()


def test_me_requires_credentials(client: TestClient) -> None:
    response = client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "missing_credentials"


def test_me_returns_user_and_organization(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    org, user = logged_in(client, db_session, hasher)
    response = client.get("/api/v1/me")
    assert response.status_code == 200
    assert response.json()["user"]["id"] == str(user.id)
    assert response.json()["organization"]["id"] == str(org.id)


def test_logout_invalidates_the_session(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    logged_in(client, db_session, hasher)
    token = client.cookies.get("pqrs_session")
    assert token

    response = client.post("/api/v1/auth/logout", headers=CSRF)
    assert response.status_code == 204
    assert response.content == b""
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert db_session.scalar(text("SELECT count(*) FROM sessions")) == 0

    # Aunque el navegador conserve la cookie, la sesión ya no existe.
    client.cookies.set("pqrs_session", token)
    stale = client.get("/api/v1/me")
    assert stale.status_code == 401
    assert stale.json()["detail"]["code"] == "session_expired"


def test_session_expires_after_idle_hours(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher, clock: FakeClock
) -> None:
    logged_in(client, db_session, hasher)
    clock.advance(hours=8, seconds=1)

    response = client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "session_expired"
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert db_session.scalar(text("SELECT count(*) FROM sessions")) == 0


def test_activity_keeps_the_session_alive(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher, clock: FakeClock
) -> None:
    logged_in(client, db_session, hasher)
    clock.advance(hours=7)
    assert client.get("/api/v1/me").status_code == 200
    clock.advance(hours=7)  # 14 h desde el login, pero solo 7 h desde la última actividad
    assert client.get("/api/v1/me").status_code == 200


def test_last_seen_is_updated_at_most_once_per_minute(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher, clock: FakeClock
) -> None:
    logged_in(client, db_session, hasher)
    first = last_seen(db_session)

    clock.advance(seconds=30)
    assert client.get("/api/v1/me").status_code == 200
    assert last_seen(db_session) == first

    clock.advance(seconds=31)
    assert client.get("/api/v1/me").status_code == 200
    assert last_seen(db_session) == first + timedelta(seconds=61)


def test_session_of_inactive_user_is_rejected(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    org, user = logged_in(client, db_session, hasher)
    db_session.execute(text("UPDATE users SET active = false"))
    db_session.commit()

    response = client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_credentials"


def test_session_of_inactive_org_is_rejected(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    logged_in(client, db_session, hasher)
    db_session.execute(text("UPDATE organizations SET active = false"))
    db_session.commit()

    response = client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_credentials"


def test_session_has_an_absolute_maximum_lifetime_despite_recent_activity(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher, clock: FakeClock
) -> None:
    logged_in(client, db_session, hasher)

    for _ in range(3):  # actividad cada 7 h: nunca supera las 8 h de inactividad
        clock.advance(hours=7)
        assert client.get("/api/v1/me").status_code == 200
    clock.advance(hours=2, minutes=59)  # 23 h 59 min desde el login
    assert client.get("/api/v1/me").status_code == 200

    clock.advance(minutes=1, seconds=1)  # 24 h 0 min 1 s; la última actividad fue hace 1 min 1 s
    response = client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "session_expired"
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert db_session.scalar(text("SELECT count(*) FROM sessions")) == 0


def test_absolute_lifetime_is_configurable(
    db_session: Session, session_factory, clock: FakeClock, hasher: Argon2PasswordHasher, settings
) -> None:
    from app.main import create_app

    short = settings.model_copy(update={"session_max_hours": 2})
    client = TestClient(
        create_app(short, session_factory, clock=clock, hasher=hasher),
        base_url="https://testserver",
    )
    logged_in(client, db_session, hasher)
    clock.advance(hours=1, minutes=59)
    assert client.get("/api/v1/me").status_code == 200
    clock.advance(minutes=2)
    assert client.get("/api/v1/me").json()["detail"]["code"] == "session_expired"
