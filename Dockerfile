FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencias primero para aprovechar la caché de capas.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

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
