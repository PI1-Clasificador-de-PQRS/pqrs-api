import warnings

import pytest
from fastapi.testclient import TestClient

from app.infrastructure import ner
from app.infrastructure.config.settings import Settings
from app.main import create_app


def test_ner_model_is_installed() -> None:
    assert ner.check_ner_available() is True


def _missing_ner(_text: str) -> str:
    warnings.warn("sin modelo", RuntimeWarning, stacklevel=2)
    return _text


def test_check_detects_the_fallback_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ner, "anonymize", _missing_ner)
    assert ner.check_ner_available() is False


def test_startup_fails_outside_development_without_ner(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr("app.main.check_ner_available", lambda: False)
    with pytest.raises(RuntimeError), TestClient(create_app(settings)):
        pass


def test_startup_only_warns_in_development_without_ner(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr("app.main.check_ner_available", lambda: False)
    dev = settings.model_copy(update={"environment": "development", "model_dir": "/no/existe"})
    with TestClient(create_app(dev)) as client:
        assert client.get("/health").status_code == 200
        assert (
            client.get("/ready").status_code == 503
        )  # el modelo local no cargó, la API sí arrancó
