"""Reglas de capas: domain <- application <- (infrastructure, api, cli)."""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

FRAMEWORKS = ("sqlalchemy", "fastapi", "starlette", "pydantic", "typer", "alembic")
FORBIDDEN = {
    "domain": (*FRAMEWORKS, "app.application", "app.infrastructure", "app.api", "app.cli"),
    "application": (
        *(f for f in FRAMEWORKS if f != "pydantic"),
        "app.infrastructure",
        "app.api",
        "app.cli",
    ),
}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_layer_does_not_import_outer_layers_or_frameworks(layer: str) -> None:
    files = list((APP / layer).rglob("*.py"))
    assert files
    for path in files:
        for module in imported_modules(path):
            for forbidden in FORBIDDEN[layer]:
                assert module != forbidden and not module.startswith(forbidden + "."), (
                    f"{path.relative_to(APP.parent)} importa {module}, prohibido en {layer}"
                )
