import getpass
import re
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from app.cli.main import app as cli
from app.domain.credentials import hash_token
from app.infrastructure.repositories.uow import SqlUnitOfWork
from tests.conftest import PROXY_HEADERS

PASSWORD = "contraseña-larga-123"
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@pytest.fixture(autouse=True)
def cli_uses_test_database(
    monkeypatch: pytest.MonkeyPatch, session_factory: sessionmaker[Session]
) -> None:
    """La CLI abre sus sesiones sobre la misma transacción aislada de la prueba."""
    monkeypatch.setattr("app.infrastructure.db.session.get_sessionmaker", lambda: session_factory)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def create_org(runner: CliRunner, name: str = "Alcaldía") -> UUID:
    result = runner.invoke(cli, ["create-org", name])
    assert result.exit_code == 0, result.output
    return UUID(result.stdout.strip())


def create_user(runner: CliRunner, org_id: UUID, email: str = "ana@example.com", password=PASSWORD):
    return runner.invoke(
        cli,
        [
            "create-user",
            "--org-id",
            str(org_id),
            "--email",
            email,
            "--name",
            "Ana",
            "--password-stdin",
        ],
        input=password + "\n",
    )


def test_create_org_prints_its_id(runner: CliRunner, db_session: Session) -> None:
    result = runner.invoke(cli, ["create-org", "Gobernación"])
    assert result.exit_code == 0
    org_id = UUID(result.stdout.strip())
    name = db_session.scalar(text("SELECT name FROM organizations WHERE id = :i"), {"i": org_id})
    assert name == "Gobernación"


def test_list_orgs_shows_every_org_sorted_by_name(runner: CliRunner) -> None:
    empty = runner.invoke(cli, ["list-orgs"])
    assert empty.exit_code == 0
    assert "No hay organizaciones" in empty.stdout

    org_b, org_a = create_org(runner, "Gobernación"), create_org(runner, "Alcaldía")
    result = runner.invoke(cli, ["list-orgs"])
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith(str(org_a)) and lines[0].endswith("Alcaldía")
    assert lines[1].startswith(str(org_b)) and lines[1].endswith("Gobernación")
    assert all("activa" in line for line in lines)


def test_create_user_with_password_stdin_can_log_in(runner: CliRunner, client: TestClient) -> None:
    org_id = create_org(runner)
    result = create_user(runner, org_id)
    assert result.exit_code == 0, result.output
    assert UUID(result.stdout.strip())

    login = client.post(
        "/api/v1/auth/login", json={"email": "ana@example.com", "password": PASSWORD}
    )
    assert login.status_code == 200
    assert login.json()["organization"]["id"] == str(org_id)


def test_create_user_prompts_with_getpass_and_confirms(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    org_id = create_org(runner)
    answers = iter([PASSWORD, PASSWORD])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    result = runner.invoke(
        cli, ["create-user", "--org-id", str(org_id), "--email", "b@example.com", "--name", "B"]
    )
    assert result.exit_code == 0, result.output
    assert db_session.scalar(text("SELECT count(*) FROM users")) == 1


def test_create_user_getpass_mismatch_fails(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    org_id = create_org(runner)
    answers = iter([PASSWORD, PASSWORD + "x"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    result = runner.invoke(
        cli, ["create-user", "--org-id", str(org_id), "--email", "b@example.com", "--name", "B"]
    )
    assert result.exit_code == 1
    assert "no coinciden" in result.stderr
    assert db_session.scalar(text("SELECT count(*) FROM users")) == 0


def test_password_is_never_accepted_as_an_argument(runner: CliRunner) -> None:
    org_id = create_org(runner)
    result = runner.invoke(
        cli,
        [
            "create-user",
            "--org-id",
            str(org_id),
            "--email",
            "a@x.com",
            "--name",
            "A",
            "--password",
            PASSWORD,
        ],
    )
    assert result.exit_code == 2  # opción desconocida


def test_create_user_rejects_short_password_unknown_org_and_duplicates(
    runner: CliRunner,
) -> None:
    org_id = create_org(runner)

    short = create_user(runner, org_id, password="corta")
    assert short.exit_code == 1
    assert "12 caracteres" in short.stderr

    unknown_org = create_user(runner, uuid4())
    assert unknown_org.exit_code == 1
    assert "Organización no encontrada" in unknown_org.stderr

    assert create_user(runner, org_id).exit_code == 0
    duplicate = create_user(runner, org_id, email="ANA@example.com")  # sin distinguir mayúsculas
    assert duplicate.exit_code == 1
    assert "Ya existe un usuario" in duplicate.stderr
    # El error no deja la transacción rota: se puede seguir operando.
    assert create_user(runner, org_id, email="otra@example.com").exit_code == 0


def test_reset_password_changes_it_and_closes_sessions(
    runner: CliRunner, client: TestClient, db_session: Session
) -> None:
    org_id = create_org(runner)
    create_user(runner, org_id)
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "ana@example.com", "password": PASSWORD}
        ).status_code
        == 200
    )

    new_password = "otra-contraseña-456"
    result = runner.invoke(
        cli,
        [
            "reset-password",
            "--org-id",
            str(org_id),
            "--email",
            "ana@example.com",
            "--password-stdin",
        ],
        input=new_password + "\n",
    )
    assert result.exit_code == 0, result.output
    assert "Sesiones cerradas: 1" in result.stdout
    assert db_session.scalar(text("SELECT count(*) FROM sessions")) == 0

    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "ana@example.com", "password": PASSWORD}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "ana@example.com", "password": new_password}
        ).status_code
        == 200
    )


def test_reset_password_enforces_policy_and_scopes_by_org(runner: CliRunner) -> None:
    org_a, org_b = create_org(runner, "A"), create_org(runner, "B")
    create_user(runner, org_a)

    short = runner.invoke(
        cli,
        [
            "reset-password",
            "--org-id",
            str(org_a),
            "--email",
            "ana@example.com",
            "--password-stdin",
        ],
        input="corta\n",
    )
    assert short.exit_code == 1

    other_org = runner.invoke(
        cli,
        [
            "reset-password",
            "--org-id",
            str(org_b),
            "--email",
            "ana@example.com",
            "--password-stdin",
        ],
        input=PASSWORD + "\n",
    )
    assert other_org.exit_code == 1
    assert "Usuario no encontrado" in other_org.stderr


def test_deactivate_user_closes_all_sessions(
    runner: CliRunner, client: TestClient, app, db_session: Session
) -> None:
    org_id = create_org(runner)
    create_user(runner, org_id)
    # Dos sesiones del mismo usuario (dos navegadores).
    second = TestClient(app, base_url="https://testserver", headers=PROXY_HEADERS)
    for c in (client, second):
        assert (
            c.post(
                "/api/v1/auth/login", json={"email": "ana@example.com", "password": PASSWORD}
            ).status_code
            == 200
        )
    assert db_session.scalar(text("SELECT count(*) FROM sessions")) == 2

    result = runner.invoke(
        cli, ["deactivate-user", "--org-id", str(org_id), "--email", "ana@example.com"]
    )
    assert result.exit_code == 0, result.output
    assert "Sesiones cerradas: 2" in result.stdout
    assert db_session.scalar(text("SELECT count(*) FROM sessions")) == 0

    for c in (client, second):
        assert c.get("/api/v1/me").status_code == 401
    login = client.post(
        "/api/v1/auth/login", json={"email": "ana@example.com", "password": PASSWORD}
    )
    assert login.status_code == 401
    assert login.json()["detail"]["code"] == "invalid_credentials"


def test_create_key_shows_the_full_key_exactly_once(runner: CliRunner, db_session: Session) -> None:
    org_id = create_org(runner)
    result = runner.invoke(cli, ["create-key", "--org-id", str(org_id), "--name", "Integración"])
    assert result.exit_code == 0, result.output

    raw = result.stdout.strip()
    assert raw.startswith("pqrs_")
    assert result.stdout.count(raw) == 1
    assert "no se podrá volver a ver" in result.stderr
    assert raw not in result.stderr

    # En la base solo está el hash.
    uow = SqlUnitOfWork(db_session)
    key = uow.api_keys.get_by_hash(hash_token(raw))
    assert key is not None and key.org_id == org_id and key.prefix == raw[:12]
    dump = " ".join(db_session.scalars(text("SELECT row_to_json(t)::text FROM api_keys t")))
    assert raw not in dump

    # Después solo se ve el prefijo.
    listing = runner.invoke(cli, ["list-keys", "--org-id", str(org_id)])
    assert listing.exit_code == 0
    assert raw[:12] in listing.stdout
    assert raw not in listing.stdout + listing.stderr
    assert "activa" in listing.stdout


def test_revoke_key_and_list_keys(runner: CliRunner, db_session: Session) -> None:
    org_id = create_org(runner)
    raw = runner.invoke(cli, ["create-key", "--org-id", str(org_id), "--name", "K"]).stdout.strip()
    key_id = SqlUnitOfWork(db_session).api_keys.get_by_hash(hash_token(raw)).id  # type: ignore[union-attr]

    revoked = runner.invoke(cli, ["revoke-key", "--org-id", str(org_id), str(key_id)])
    assert revoked.exit_code == 0, revoked.output
    assert "revocada" in runner.invoke(cli, ["list-keys", "--org-id", str(org_id)]).stdout

    again = runner.invoke(cli, ["revoke-key", "--org-id", str(org_id), str(key_id)])
    assert again.exit_code == 1
    assert "ya estaba revocada" in again.stderr


def test_revoke_key_is_scoped_by_org(runner: CliRunner, db_session: Session) -> None:
    org_a, org_b = create_org(runner, "A"), create_org(runner, "B")
    raw = runner.invoke(cli, ["create-key", "--org-id", str(org_a), "--name", "K"]).stdout.strip()
    key_id = SqlUnitOfWork(db_session).api_keys.get_by_hash(hash_token(raw)).id  # type: ignore[union-attr]

    result = runner.invoke(cli, ["revoke-key", "--org-id", str(org_b), str(key_id)])
    assert result.exit_code == 1
    assert "Clave no encontrada" in result.stderr
    assert "activa" in runner.invoke(cli, ["list-keys", "--org-id", str(org_a)]).stdout
    assert "no tiene claves" in runner.invoke(cli, ["list-keys", "--org-id", str(org_b)]).stdout


def test_create_key_requires_an_existing_org(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["create-key", "--org-id", str(uuid4()), "--name", "K"])
    assert result.exit_code == 1
    assert "Organización no encontrada" in result.stderr


def test_help_lists_every_command(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])
    for command in (
        "create-org",
        "list-orgs",
        "create-user",
        "reset-password",
        "deactivate-user",
        "create-key",
        "revoke-key",
        "list-keys",
    ):
        assert command in result.stdout
