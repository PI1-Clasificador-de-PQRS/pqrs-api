"""Escribe ``openapi/openapi.json`` (formateado y estable) a partir de la aplicación.

Uso:
    uv run python scripts/export_openapi.py          # regenera el archivo
    uv run python scripts/export_openapi.py --check  # falla si está desactualizado (CI)
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = ROOT / "openapi" / "openapi.json"

# El contrato debe ser el mismo en cualquier máquina: se ignora el entorno local (.env incluido).
os.environ["SESSION_COOKIE_NAME"] = "pqrs_session"


def render() -> str:
    from app.main import create_app

    schema = create_app().openapi()
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="solo comprueba, no escribe")
    args = parser.parse_args()

    expected = render()
    if args.check:
        current = OPENAPI_PATH.read_text(encoding="utf-8") if OPENAPI_PATH.exists() else None
        if current != expected:
            print(
                "openapi/openapi.json está desactualizado. "
                "Ejecuta `uv run python scripts/export_openapi.py` y súbelo al commit.",
                file=sys.stderr,
            )
            return 1
        print("openapi/openapi.json está al día.")
        return 0

    OPENAPI_PATH.parent.mkdir(parents=True, exist_ok=True)
    OPENAPI_PATH.write_text(expected, encoding="utf-8")
    print(f"Escrito {OPENAPI_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
