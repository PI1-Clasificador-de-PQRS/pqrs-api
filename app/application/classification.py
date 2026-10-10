"""Casos de uso de clasificación: un texto o un lote.

Orden de cada operación: validar → resolver el motor → anonimizar → una llamada al motor →
guardar → ``commit`` explícito. El texto original nunca se guarda ni se registra: solo el
anonimizado. No hay respaldo entre motores: si el elegido falla, el error es un 503.
"""

import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from pqrs_classifier.engines.base import Prediction

from app.application.ports import Clock, EngineGateway, UnitOfWork
from app.domain.categories import Source
from app.domain.entities import Classification
from app.domain.errors import DomainError, ErrorCode, ServiceUnavailable
from app.domain.principal import CredentialType, Principal

MAX_TEXT_CHARS = 5000
MAX_BATCH_ITEMS = 50
MAX_EXTERNAL_REF_CHARS = 100

INVALID_TEXT_MESSAGE = f"El texto debe tener entre 1 y {MAX_TEXT_CHARS} caracteres."
INVALID_EXTERNAL_REF_MESSAGE = (
    f"La referencia externa no puede superar {MAX_EXTERNAL_REF_CHARS} caracteres."
)


@dataclass(frozen=True, slots=True)
class BatchItemInput:
    text: str
    external_ref: str | None = None


@dataclass(frozen=True, slots=True)
class BatchItemOutcome:
    """Resultado de un elemento: la clasificación guardada o el error de ese elemento."""

    index: int
    classification: Classification | None = None
    error: DomainError | None = None


@dataclass(frozen=True, slots=True)
class BatchOutcome:
    batch_id: uuid.UUID
    engine: str
    results: list[BatchItemOutcome]


def validate_text(text: str) -> str:
    """Quita los espacios de los extremos y exige de 1 a 5.000 caracteres (sobre ese texto)."""
    stripped = text.strip()
    if not 1 <= len(stripped) <= MAX_TEXT_CHARS:
        raise DomainError(ErrorCode.INVALID_TEXT, INVALID_TEXT_MESSAGE)
    return stripped


def validate_external_ref(external_ref: str | None) -> str | None:
    if external_ref is not None and len(external_ref) > MAX_EXTERNAL_REF_CHARS:
        raise DomainError(ErrorCode.INVALID_EXTERNAL_REF, INVALID_EXTERNAL_REF_MESSAGE)
    return external_ref


class ClassificationService:
    def __init__(
        self,
        uow: UnitOfWork,
        gateway: EngineGateway,
        clock: Clock,
        anonymize: Callable[[str], str],
        timer: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._uow = uow
        self._gateway = gateway
        self._clock = clock
        self._anonymize = anonymize
        self._timer = timer

    @staticmethod
    def _source(principal: Principal, *, batch: bool) -> Source:
        if principal.credential_type is CredentialType.API_KEY:
            return Source.API
        return Source.UI_BATCH if batch else Source.UI

    def classify(
        self,
        principal: Principal,
        *,
        text: str,
        model: str | None = None,
        external_ref: str | None = None,
    ) -> Classification:
        text = validate_text(text)
        external_ref = validate_external_ref(external_ref)
        engine = self._gateway.select(model)  # antes de llamar: sin respaldo si luego falla

        started = self._timer()
        anonymized = self._anonymize(text)
        prediction = self._gateway.classify(engine, anonymized)
        latency_ms = round((self._timer() - started) * 1000)

        saved = self._save(
            principal,
            anonymized=anonymized,
            prediction=prediction,
            engine=engine,
            source=self._source(principal, batch=False),
            batch_id=None,
            external_ref=external_ref,
            latency_ms=latency_ms,
            created_at=self._clock.now(),
        )
        self._uow.commit()
        return saved

    def classify_batch(
        self,
        principal: Principal,
        *,
        items: Sequence[BatchItemInput],
        model: str | None = None,
        batch_id: uuid.UUID | None = None,
    ) -> BatchOutcome:
        if len(items) > MAX_BATCH_ITEMS:
            raise DomainError(
                ErrorCode.BATCH_TOO_LARGE,
                f"El lote supera el máximo de {MAX_BATCH_ITEMS} elementos.",
            )
        batch_id = batch_id or uuid.uuid4()
        engine = self._gateway.select(model)

        results: list[BatchItemOutcome | None] = [None] * len(items)
        valid: list[tuple[int, str, str | None]] = []
        for index, item in enumerate(items):
            try:
                text = validate_text(item.text)
                external_ref = validate_external_ref(item.external_ref)
            except DomainError as exc:
                results[index] = BatchItemOutcome(index, error=exc)
            else:
                valid.append((index, text, external_ref))

        if valid:
            started = self._timer()
            anonymized = [self._anonymize(text) for _, text, _ in valid]
            predictions = self._gateway.classify_batch(engine, anonymized)  # una sola llamada
            if len(predictions) != len(valid):
                raise ServiceUnavailable(
                    ErrorCode.LLM_UNAVAILABLE
                    if engine == "llm"
                    else ErrorCode.CLASSIFIER_UNAVAILABLE,
                    "El motor devolvió un número de resultados distinto al de textos.",
                )
            # La latencia del bloque se reparte entre sus elementos.
            share = round((self._timer() - started) * 1000 / len(valid))
            now = self._clock.now()
            source = self._source(principal, batch=True)
            for position, ((index, _, external_ref), text, prediction) in enumerate(
                zip(valid, anonymized, predictions, strict=True)
            ):
                saved = self._save(
                    principal,
                    anonymized=text,
                    prediction=prediction,
                    engine=engine,
                    source=source,
                    batch_id=batch_id,
                    external_ref=external_ref,
                    latency_ms=share,
                    # Microsegundos de diferencia para que el historial conserve el orden del lote.
                    created_at=now + timedelta(microseconds=position),
                )
                results[index] = BatchItemOutcome(index, classification=saved)
            self._uow.commit()  # todo el bloque o nada

        return BatchOutcome(batch_id, engine, [r for r in results if r is not None])

    def _save(
        self,
        principal: Principal,
        *,
        anonymized: str,
        prediction: Prediction,
        engine: str,
        source: Source,
        batch_id: uuid.UUID | None,
        external_ref: str | None,
        latency_ms: int,
        created_at: datetime,
    ) -> Classification:
        return self._uow.classifications.add(
            principal.org_id,  # siempre del Principal, nunca de la petición
            text_anonymized=anonymized,
            category=prediction.category.value,
            engine=engine,
            model_version=prediction.model_version,
            prompt_version=prediction.prompt_version,
            source=source.value,
            batch_id=batch_id,
            external_ref=external_ref,
            latency_ms=latency_ms,
            user_id=principal.user_id,
            api_key_id=principal.api_key_id,
            created_at=created_at,
        )
