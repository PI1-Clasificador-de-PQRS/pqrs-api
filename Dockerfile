# syntax=docker/dockerfile:1
#
# Imagen de la API con el motor local incluido (SBERT + regresión logística). Dos etapas:
#   builder  instala las dependencias (torch de CPU), descarga los pesos de SBERT y los artefactos
#            del modelo del release del clasificador. Necesita git, curl y el build secret.
#   final    solo copia lo necesario; sin git, sin token, sin cachés de build, usuario no root y
#            sin red hacia Hugging Face en ejecución (HF_HUB_OFFLINE=1).
#
# Build (el token es de solo lectura sobre el repositorio privado `pqrs-classifier`; ver README):
#   docker build --secret id=classifier_token,env=CLASSIFIER_REPO_TOKEN -t pqrs-api .
#
# El token solo existe dentro de cada `RUN --mount=type=secret`; nunca como ARG, ENV ni archivo.

ARG PYTHON_IMAGE=python:3.11-slim

# ------------------------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# git: `pqrs-classifier` se instala desde un repositorio privado de GitHub. curl: artefactos del release.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencias primero para aprovechar la caché de capas. `--group ml` agrega torch (índice de CPU
# fijado en uv.lock), sentence-transformers y scikit-learn 1.9.0.
# El token entra como variables de entorno de git solo durante este RUN.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=secret,id=classifier_token \
    GIT_CONFIG_COUNT=1 \
    GIT_CONFIG_KEY_0="url.https://x-access-token:$(cat /run/secrets/classifier_token)@github.com/PI1-Clasificador-de-PQRS/pqrs-classifier.insteadOf" \
    GIT_CONFIG_VALUE_0="https://github.com/PI1-Clasificador-de-PQRS/pqrs-classifier" \
    uv sync --frozen --no-dev --group ml --no-install-project

COPY app ./app
RUN uv sync --frozen --no-dev --group ml

# model.joblib y metadata.json del GitHub Release del clasificador. Las sumas sha256 son las que
# publica el release v0.1.0: si alguien reemplaza un archivo del release, el build falla.
ARG CLASSIFIER_RELEASE=v0.1.0
ARG MODEL_SHA256=bf6b1032bfaebc9fedb507972a69dd0b799f6ee6b13c2ce7a56ffbeadc8addbe
ARG METADATA_SHA256=cd8a287d31818ddd783f4c4768df08634f867d4520c592856799342cad9bc25b
RUN --mount=type=secret,id=classifier_token \
    set -eu; \
    token="$(cat /run/secrets/classifier_token)"; \
    api=https://api.github.com/repos/PI1-Clasificador-de-PQRS/pqrs-classifier; \
    mkdir -p /opt/model; \
    release="$(curl -fsSL -H "Authorization: Bearer $token" -H "Accept: application/vnd.github+json" \
        "$api/releases/tags/$CLASSIFIER_RELEASE")"; \
    for name in model.joblib metadata.json; do \
        id="$(printf '%s' "$release" | python -c "import json,sys; print(next(a['id'] for a in json.load(sys.stdin)['assets'] if a['name'] == '$name'))")"; \
        curl -fsSL -H "Authorization: Bearer $token" -H "Accept: application/octet-stream" \
            -o "/opt/model/$name" "$api/releases/assets/$id"; \
    done; \
    echo "$MODEL_SHA256  /opt/model/model.joblib" | sha256sum -c -; \
    echo "$METADATA_SHA256  /opt/model/metadata.json" | sha256sum -c -

# Pesos de SBERT, en la revisión que fija metadata.json, descargados ahora para no necesitar red en
# ejecución. Después se comprueba que el motor local carga del todo sin conexión.
ENV HF_HOME=/opt/hf
RUN python - <<'PY'
import json
from sentence_transformers import SentenceTransformer

emb = json.load(open("/opt/model/metadata.json"))["embedding_model"]
SentenceTransformer(emb["name"], revision=emb["revision"])
PY
RUN HF_HUB_OFFLINE=1 python - <<'PY'
from pqrs_classifier.engines import ClassifierEngine

engine = ClassifierEngine("/opt/model")
assert engine.is_available()[0]
print(engine.classify("Solicito información sobre mi trámite"))
PY

# ------------------------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE}

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /opt/hf /opt/hf
COPY --from=builder /opt/model /opt/model
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

# `pqrs-admin`, `alembic` y `uvicorn` quedan en el PATH (también con `docker compose exec`).
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    MODEL_DIR=/opt/model \
    HF_HOME=/opt/hf \
    HF_HUB_OFFLINE=1 \
    TQDM_DISABLE=1

RUN useradd --system --uid 10001 --no-create-home pqrs
USER pqrs

EXPOSE 8000

# Sin log de acceso de uvicorn: lo registra la propia aplicación (JSON, sin query string).
# Una sola réplica y un solo proceso: los límites y el circuito del LLM viven en memoria.
CMD ["uvicorn", "app.main:get_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
