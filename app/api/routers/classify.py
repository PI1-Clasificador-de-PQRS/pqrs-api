from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_classification_service, require_session_or_key_limited
from app.api.errors import error_responses
from app.api.schemas.classify import (
    BatchItemError,
    BatchItemResult,
    BatchRequest,
    BatchResponse,
    ClassifyRequest,
    ClassifyResponse,
)
from app.application.classification import BatchItemInput, ClassificationService
from app.domain.categories import Category, Engine
from app.domain.errors import ErrorCode
from app.domain.principal import Principal

router = APIRouter(tags=["classify"])

_AUTH_ERRORS = (
    ErrorCode.MISSING_CREDENTIALS,
    ErrorCode.INVALID_CREDENTIALS,
    ErrorCode.SESSION_EXPIRED,
    ErrorCode.CSRF_REQUIRED,
    ErrorCode.PROXY_REQUIRED,
    ErrorCode.PAYLOAD_TOO_LARGE,
    ErrorCode.INVALID_REQUEST,
    ErrorCode.RATE_LIMITED,
    ErrorCode.LLM_UNAVAILABLE,
    ErrorCode.CLASSIFIER_UNAVAILABLE,
)

_ENGINE_SELECTION = """\

**Motor.** Si `model` viene, se usa ese motor; si no está disponible o falla, la respuesta es 503
(`llm_unavailable` o `classifier_unavailable`) y la API **nunca** cambia de motor por su cuenta.
Si se omite, se usa `llm` cuando está disponible y, si no, `classifier`; esa elección se hace antes
de clasificar y, si el motor elegido falla después, también es un 503. La respuesta indica el
motor usado. Un 503 por circuito abierto trae `Retry-After`.

**Privacidad.** El texto se anonimiza antes de enviarse al motor y de guardarse; el texto original
no se guarda ni se registra. El LLM solo ve los primeros 1.200 caracteres de cada texto.
"""


_CLASSIFY_DESCRIPTION = (
    """\
Clasifica un texto en una de las 4 categorías. No devuelve confianza.

Acepta sesión o API key. El origen que se guarda es `ui` con sesión y `api` con API key.
"""
    + _ENGINE_SELECTION
)

_BATCH_DESCRIPTION = (
    """\
Clasifica un lote de hasta 50 textos.

Límites:

* **Máximo 50 elementos** en `items`; más de 50 responde 413 `batch_too_large` y no se
  clasifica ninguno. Una lista vacía responde 422 `invalid_request`.
* **Cada texto: de 1 a 5.000 caracteres** (tras quitar espacios de los extremos). Un elemento
  que no cumple devuelve su propio `error` (`invalid_text`) en `results` sin detener el resto.
* **`external_ref`: máximo 100 caracteres**; si lo excede, el elemento devuelve
  `invalid_external_ref`.

Los elementos válidos se anonimizan y se clasifican en **una sola llamada** al motor. Si el
motor falla, la respuesta es 503 para todo el bloque y no se guarda nada de él. El dashboard
envía los lotes grandes en bloques de 50 con el mismo `batch_id` y se detiene ante un fallo:
los bloques anteriores ya guardados se conservan.

`batch_id` se toma de la petición si viene; si no, se genera. El origen que se guarda es
`ui_batch` con sesión y `api` con API key. La latencia que se guarda de cada elemento es la del
bloque repartida entre sus elementos válidos.
"""
    + _ENGINE_SELECTION
)


@router.post(
    "/classify",
    summary="Clasificar una PQRS",
    description=_CLASSIFY_DESCRIPTION,
    response_model=ClassifyResponse,
    responses=error_responses(*_AUTH_ERRORS),
)
def classify(
    body: ClassifyRequest,
    principal: Annotated[Principal, Depends(require_session_or_key_limited)],
    service: Annotated[ClassificationService, Depends(get_classification_service)],
) -> ClassifyResponse:
    """Clasifica un texto en una de las 4 categorías. No devuelve confianza.

    Acepta sesión o API key. El origen que se guarda es `ui` con sesión y `api` con API key.
    """
    saved = service.classify(
        principal,
        text=body.text,
        model=body.model.value if body.model else None,
        external_ref=body.external_ref,
    )
    return ClassifyResponse(
        id=saved.id,
        category=Category(saved.category),
        model=Engine(saved.engine),
        model_version=saved.model_version,
        timestamp=saved.created_at,
    )


@router.post(
    "/classify/batch",
    summary="Clasificar un lote de PQRS",
    description=_BATCH_DESCRIPTION,
    response_model=BatchResponse,
    responses=error_responses(*_AUTH_ERRORS, ErrorCode.BATCH_TOO_LARGE),
)
def classify_batch(
    body: BatchRequest,
    principal: Annotated[Principal, Depends(require_session_or_key_limited)],
    service: Annotated[ClassificationService, Depends(get_classification_service)],
) -> BatchResponse:
    """Clasifica un lote de hasta 50 textos.

    Límites:

    * **Máximo 50 elementos** en `items`; más de 50 responde 413 `batch_too_large` y no se
      clasifica ninguno. Una lista vacía responde 422 `invalid_request`.
    * **Cada texto: de 1 a 5.000 caracteres** (tras quitar espacios de los extremos). Un elemento
      que no cumple devuelve su propio `error` (`invalid_text`) en `results` sin detener el resto.
    * **`external_ref`: máximo 100 caracteres**; si lo excede, el elemento devuelve
      `invalid_external_ref`.

    Los elementos válidos se anonimizan y se clasifican en **una sola llamada** al motor. Si el
    motor falla, la respuesta es 503 para todo el bloque y no se guarda nada de él. El dashboard
    envía los lotes grandes en bloques de 50 con el mismo `batch_id` y se detiene ante un fallo:
    los bloques anteriores ya guardados se conservan.

    `batch_id` se toma de la petición si viene; si no, se genera. El origen que se guarda es
    `ui_batch` con sesión y `api` con API key. **`latency_ms`** (solo visible en el historial
    interno) es la latencia del bloque repartida entre sus elementos válidos.
    """
    outcome = service.classify_batch(
        principal,
        items=[BatchItemInput(i.text, i.external_ref) for i in body.items],
        model=body.model.value if body.model else None,
        batch_id=body.batch_id,
    )
    results = []
    for item in outcome.results:
        if item.classification is not None:
            saved = item.classification
            results.append(
                BatchItemResult(
                    index=item.index,
                    id=saved.id,
                    category=Category(saved.category),
                    model_version=saved.model_version,
                )
            )
        else:
            assert item.error is not None
            results.append(
                BatchItemResult(
                    index=item.index,
                    error=BatchItemError(code=item.error.code, message=item.error.message),
                )
            )
    return BatchResponse(batch_id=outcome.batch_id, model=Engine(outcome.engine), results=results)
