"""Garantía estructural: todo método de un repo de negocio exige ``org_id``."""

import inspect

import pytest

from app.infrastructure.repositories.api_keys import SqlApiKeyRepository
from app.infrastructure.repositories.classifications import SqlClassificationRepository
from app.infrastructure.repositories.sessions import SqlSessionRepository
from app.infrastructure.repositories.users import SqlUserRepository

BUSINESS_REPOS = [
    SqlUserRepository,
    SqlSessionRepository,
    SqlApiKeyRepository,
    SqlClassificationRepository,
]

# Resolución de credenciales: la organización todavía no se conoce.
ALLOWED_WITHOUT_ORG_ID = {
    (SqlUserRepository, "get_for_login"),
    (SqlApiKeyRepository, "get_by_hash"),
    (SqlSessionRepository, "get_by_token_hash"),
}


def _public_methods(cls: type) -> list[str]:
    return [
        name
        for name, member in inspect.getmembers(cls, inspect.isfunction)
        if not name.startswith("_")
    ]


@pytest.mark.parametrize("repo", BUSINESS_REPOS, ids=lambda r: r.__name__)
def test_every_public_method_requires_org_id(repo: type) -> None:
    for name in _public_methods(repo):
        params = inspect.signature(getattr(repo, name)).parameters
        if (repo, name) in ALLOWED_WITHOUT_ORG_ID:
            assert "org_id" not in params
            continue
        assert "org_id" in params, f"{repo.__name__}.{name} no recibe org_id"
        assert params["org_id"].default is inspect.Parameter.empty, (
            f"{repo.__name__}.{name}: org_id debe ser obligatorio"
        )


def test_allowlist_has_no_stale_entries() -> None:
    for repo, name in ALLOWED_WITHOUT_ORG_ID:
        assert name in _public_methods(repo)
