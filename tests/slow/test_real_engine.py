"""Motor local real (torch + SBERT). Corre con ``pytest -m slow`` y el grupo ``ml``.

``SLOW_MODEL_DIR`` apunta a un directorio con ``model.joblib`` y ``metadata.json`` del release
del clasificador. Sin él, o sin torch, las pruebas se omiten.
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pqrs_classifier.testing import FakeEngine
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.config.settings import Settings
from app.infrastructure.engines.registry import EngineRegistry
from app.main import create_app
from tests.factories import seed_api_key, seed_login_user
from tests.fakes import FakeClock

pytestmark = pytest.mark.slow

MODEL_DIR = os.environ.get("SLOW_MODEL_DIR")


@pytest.fixture(scope="module")
def model_dir() -> Path:
    pytest.importorskip("sentence_transformers")
    if not MODEL_DIR or not (Path(MODEL_DIR) / "model.joblib").is_file():
        pytest.skip("SLOW_MODEL_DIR no apunta a un modelo local")
    return Path(MODEL_DIR)


def test_starts_with_the_real_local_engine_and_classifies(
    model_dir: Path,
    settings: Settings,
    session_factory: sessionmaker[Session],
    db_session: Session,
    clock: FakeClock,
    hasher,
) -> None:
    real = settings.model_copy(update={"model_dir": model_dir})
    registry = EngineRegistry(real, clock)
    app = create_app(real, session_factory, clock=clock, registry=registry)
    org, _ = seed_login_user(db_session, hasher)
    raw, _ = seed_api_key(db_session, org.id)

    with TestClient(app) as client:  # el lifespan carga el motor local una vez
        assert client.get("/ready").status_code == 200
        models = {
            m["id"]: m for m in client.get("/api/v1/models", headers={"X-API-Key": raw}).json()
        }
    assert models["classifier"]["available"] is True
    assert 0 < models["classifier"]["evaluation"]["f1_macro"] <= 1

    prediction = registry.classify(
        "classifier", "Solicito información sobre el estado de mi trámite"
    )
    assert prediction.engine == "classifier"
    assert prediction.category.value in {
        "Petición",
        "Reclamo",
        "Denuncia",
        "Solicitud de información",
    }


def test_real_registry_without_artifacts_is_not_loaded(
    settings: Settings, clock: FakeClock, tmp_path: Path
) -> None:
    registry = EngineRegistry(settings.model_copy(update={"model_dir": tmp_path}), clock)
    registry.load()
    assert registry.classifier_loaded is False
    assert FakeEngine  # el paquete de pruebas sigue disponible junto al real
