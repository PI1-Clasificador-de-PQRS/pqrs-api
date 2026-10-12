"""CLI de administración: ``pqrs-admin``. No hay otra vía para gestionar organizaciones,
usuarios ni claves."""

import getpass
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

import typer

from app.application.admin import AdminService
from app.domain.errors import DomainError
from app.infrastructure.db.session import session_scope
from app.infrastructure.repositories.uow import SqlUnitOfWork
from app.infrastructure.security.clock import SystemClock
from app.infrastructure.security.passwords import Argon2PasswordHasher

app = typer.Typer(
    help="Administración de pqrs-api: organizaciones, usuarios y claves de API.",
    no_args_is_help=True,
    add_completion=False,
)

OrgIdOption = Annotated[UUID, typer.Option("--org-id", help="UUID de la organización.")]
EmailOption = Annotated[str, typer.Option("--email", help="Correo del usuario.")]
PasswordStdinOption = Annotated[
    bool,
    typer.Option(
        "--password-stdin",
        help="Lee la contraseña de la entrada estándar (una línea) en lugar de pedirla.",
    ),
]


def _info(message: str) -> None:
    typer.echo(message, err=True)


@contextmanager
def _admin() -> Iterator[AdminService]:
    """Abre una sesión de base de datos y traduce los errores de negocio a un código de salida."""
    try:
        with session_scope() as db:
            yield AdminService(
                SqlUnitOfWork(db), hasher=Argon2PasswordHasher(), clock=SystemClock()
            )
    except DomainError as exc:
        typer.echo(f"Error: {exc.message}", err=True)
        raise typer.Exit(code=1) from exc


def _read_password(password_stdin: bool) -> str:
    """La contraseña nunca se recibe como argumento: se pide con getpass o llega por stdin."""
    if password_stdin:
        line = sys.stdin.readline()
        if not line:
            typer.echo("Error: no se recibió ninguna contraseña por la entrada estándar.", err=True)
            raise typer.Exit(code=1)
        return line.rstrip("\r\n")
    password = getpass.getpass("Contraseña: ")
    if getpass.getpass("Repite la contraseña: ") != password:
        typer.echo("Error: las contraseñas no coinciden.", err=True)
        raise typer.Exit(code=1)
    return password


@app.command("create-org")
def create_org(name: Annotated[str, typer.Argument(help="Nombre de la organización.")]) -> None:
    """Crea una organización e imprime su id."""
    with _admin() as admin:
        org = admin.create_org(name)
    _info(f"Organización creada: {org.name}")
    typer.echo(str(org.id))


@app.command("list-orgs")
def list_orgs() -> None:
    """Lista las organizaciones registradas (id, estado, fecha de creación y nombre)."""
    with _admin() as admin:
        orgs = admin.list_orgs()
    if not orgs:
        typer.echo("No hay organizaciones registradas.")
        return
    for org in orgs:
        status = "activa" if org.active else "inactiva"
        created = org.created_at.isoformat(timespec="seconds")
        typer.echo(f"{org.id}  {status:<8}  creada: {created}  {org.name}")


@app.command("create-user")
def create_user(
    org_id: OrgIdOption,
    email: EmailOption,
    name: Annotated[str, typer.Option("--name", help="Nombre completo del usuario.")],
    password_stdin: PasswordStdinOption = False,
) -> None:
    """Crea un usuario activo en la organización (contraseña de al menos 12 caracteres)."""
    password = _read_password(password_stdin)
    with _admin() as admin:
        user = admin.create_user(org_id, email=email, name=name, password=password)
    _info(f"Usuario creado: {user.email}")
    typer.echo(str(user.id))


@app.command("reset-password")
def reset_password(
    org_id: OrgIdOption, email: EmailOption, password_stdin: PasswordStdinOption = False
) -> None:
    """Restablece la contraseña de un usuario y cierra sus sesiones abiertas."""
    password = _read_password(password_stdin)
    with _admin() as admin:
        closed = admin.reset_password(org_id, email=email, password=password)
    typer.echo(f"Contraseña restablecida. Sesiones cerradas: {closed}.")


@app.command("deactivate-user")
def deactivate_user(org_id: OrgIdOption, email: EmailOption) -> None:
    """Desactiva a un usuario y borra todas sus sesiones."""
    with _admin() as admin:
        closed = admin.deactivate_user(org_id, email=email)
    typer.echo(f"Usuario desactivado. Sesiones cerradas: {closed}.")


@app.command("create-key")
def create_key(
    org_id: OrgIdOption,
    name: Annotated[str, typer.Option("--name", help="Nombre descriptivo de la clave.")],
) -> None:
    """Crea una API key. La clave completa se muestra UNA sola vez."""
    with _admin() as admin:
        raw, key = admin.create_key(org_id, name=name)
    _info(f"Clave creada (id: {key.id}, prefijo: {key.prefix}).")
    _info("Guárdala ahora: no se podrá volver a ver; después solo se muestra el prefijo.")
    typer.echo(raw)


@app.command("revoke-key")
def revoke_key(
    org_id: OrgIdOption,
    key_id: Annotated[UUID, typer.Argument(help="Id de la clave (ver list-keys).")],
) -> None:
    """Revoca una API key; deja de funcionar de inmediato."""
    with _admin() as admin:
        admin.revoke_key(org_id, key_id)
    typer.echo("Clave revocada.")


@app.command("list-keys")
def list_keys(org_id: OrgIdOption) -> None:
    """Lista las claves de la organización (solo el prefijo, nunca la clave completa)."""
    with _admin() as admin:
        keys = admin.list_keys(org_id)
    if not keys:
        typer.echo("La organización no tiene claves.")
        return
    for key in keys:
        status = "revocada" if key.revoked_at else "activa"
        used = key.last_used_at.isoformat(timespec="seconds") if key.last_used_at else "nunca"
        typer.echo(f"{key.id}  {key.prefix}…  {status:<8}  último uso: {used}  {key.name}")
