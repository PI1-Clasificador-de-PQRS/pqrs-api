FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

# git: `pqrs-classifier` se instala desde un repositorio privado de GitHub.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# El token (build secret `classifier_token`) solo existe dentro de este RUN, como variables de
# entorno de git: no se escribe en ningún archivo ni queda en una capa.
# Dependencias primero para aprovechar la caché de capas.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=secret,id=classifier_token \
    GIT_CONFIG_COUNT=1 \
    GIT_CONFIG_KEY_0="url.https://x-access-token:$(cat /run/secrets/classifier_token)@github.com/PI1-Clasificador-de-PQRS/pqrs-classifier.insteadOf" \
    GIT_CONFIG_VALUE_0="https://github.com/PI1-Clasificador-de-PQRS/pqrs-classifier" \
    uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
RUN uv sync --frozen --no-dev

# `pqrs-admin`, `alembic` y `uvicorn` quedan en el PATH (también con `docker compose exec`).
ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --system --uid 10001 --no-create-home pqrs
USER pqrs

EXPOSE 8000

# Sin log de acceso de uvicorn: lo registra la propia aplicación (JSON, sin query string).
CMD ["uvicorn", "app.main:get_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
