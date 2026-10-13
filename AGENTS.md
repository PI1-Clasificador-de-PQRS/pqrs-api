# AGENTS.md — pqrs-api

Guía para quien trabaje en este repositorio (personas o agentes). Idioma: documentación y mensajes
de la API en **español**; identificadores, commits y PR en **inglés**.

## Contexto del sistema

`pqrs-api` es la API REST del MVP del Clasificador automático de PQRS (Proyecto Integrador I, UdeA).
Hay tres repositorios: el clasificador (`pqrs-classifier`, paquete `pqrs_classifier`), esta API y un
dashboard en Next.js.

- **Monolito modular:** FastAPI + SQLAlchemy 2 (síncrono, psycopg 3) + Alembic sobre PostgreSQL 16.
- **Dos tipos de credencial:**
  - **Personas:** usan el dashboard, que es un proxy BFF. El navegador solo habla con Next.js y nunca
    maneja credenciales; Next.js reenvía la cookie de sesión a esta API.
  - **Sistemas externos:** llaman directo con `X-API-Key`. Una API key **solo puede clasificar**;
    historial, métricas y `/me` requieren sesión.
- **Un único rol:** todo usuario activo de una organización tiene los mismos permisos.
- **Administración solo por CLI** (`pqrs-admin`): no hay pantallas de usuarios ni claves.
- **Taxonomía (orden canónico):** `Petición`, `Reclamo`, `Denuncia`, `Solicitud de información`.
  **Motores:** `llm` y `classifier`. **Origen:** `ui`, `ui_batch`, `api`.
- **Sin CORS:** el navegador nunca llama directo a la API.
- Prefijo `/api/v1`. `GET /health` y `GET /ready` están en la raíz y son públicos.
- **CSRF (defensa en profundidad):** las peticiones con cookie que modifican datos exigen
  `X-PQRS-CSRF: 1` (la añade el proxy del dashboard). El login y las peticiones con API key no.

## Capas

```
domain  <-  application  <-  infrastructure | api | cli
```

| Capa | Contenido | Puede importar |
|---|---|---|
| `app/domain` | Entidades (dataclasses), `Principal`, taxonomía, códigos de error, generación y hash de tokens | Solo la biblioteca estándar |
| `app/application` | Casos de uso (`AuthService`, `AdminService`) y puertos (`Protocol`s de repos, `Clock`, hasher, limitador) | `domain` |
| `app/infrastructure` | Settings, SQLAlchemy (modelos, sesión), repositorios, argon2, limitador en memoria, logging | `domain`, `application` |
| `app/api` | Routers, esquemas Pydantic, dependencias (`require_session`, `require_session_or_key`), errores, middleware | todo lo anterior |
| `app/cli` | `pqrs-admin` (Typer) | todo lo anterior |

`tests/test_architecture.py` hace cumplir estas reglas. Los repositorios devuelven entidades de
dominio, no objetos ORM. Los casos de uso hacen `commit` **explícito** (no se confía en un commit
al salir de una dependencia con `yield`, que FastAPI ejecuta tras enviar la respuesta).

## Regla del `org_id` (aislamiento, RNF-05)

- Toda credencial se resuelve en un `Principal` (`org_id`, tipo de credencial, `user_id` o
  `api_key_id`). **El `org_id` sale siempre del `Principal`, nunca de la petición.**
- **Todo método de un repositorio de negocio recibe `org_id` como argumento obligatorio** y filtra
  por él en cada consulta.
- Excepciones, solo para resolver una credencial antes de conocer la organización:
  `UserRepository.get_for_login`, `ApiKeyRepository.get_by_hash` y
  `SessionRepository.get_by_token_hash`. `tests/test_repository_guard.py` falla si aparece otro
  método sin `org_id`.
- Un recurso de otra organización responde **404** (`not_found`): el repositorio devuelve `None` y
  la API lo convierte con `or_404()`. Nunca 403, para no revelar que existe.

## Comandos

```bash
uv sync                                         # instala dependencias (.venv); necesita acceso a pqrs-classifier
uv sync --group ml                              # además torch/SBERT (motor local real; pruebas `slow`)
docker compose up -d db                         # PostgreSQL 16 en localhost:5432
uv run alembic upgrade head                     # migraciones
uv run pytest                                   # pruebas contra PostgreSQL real
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run uvicorn app.main:get_app --factory --reload --no-access-log

uv run pqrs-admin create-org "Nombre"
uv run pqrs-admin list-orgs
uv run pqrs-admin create-user --org-id <uuid> --email <correo> --name "<nombre>"
uv run pqrs-admin reset-password --org-id <uuid> --email <correo>
uv run pqrs-admin deactivate-user --org-id <uuid> --email <correo>
uv run pqrs-admin create-key --org-id <uuid> --name "<nombre>"
uv run pqrs-admin list-keys --org-id <uuid>
uv run pqrs-admin revoke-key --org-id <uuid> <key-id>

uv run python scripts/export_openapi.py         # regenera openapi/openapi.json
uv run python scripts/export_openapi.py --check # lo que ejecuta la CI
```

Cada cambio de rutas o esquemas exige regenerar y subir `openapi/openapi.json`. Cambios en las
tablas exigen una migración nueva en `migrations/versions/` (`alembic check` debe quedar limpio).

### Pruebas

- Usan PostgreSQL real (`TEST_DATABASE_URL`, por defecto la base `pqrs_test` en localhost). Cada
  prueba corre dentro de una transacción externa con `join_transaction_mode="create_savepoint"`, que
  se revierte al terminar; los `commit()` del código solo liberan savepoints.
- El tiempo se controla con `FakeClock` (`tests/fakes.py`): toda marca de tiempo la pone Python con
  el `Clock` inyectado, nunca `now()` de Postgres (queda congelado dentro de la transacción).
- Los datos se siembran con los repositorios (`tests/factories.py`).
- `test_migrations.py` es la excepción: usa una base temporal vacía.

## Privacidad

- **El texto original de una PQRS nunca se guarda ni se registra.** Solo se persiste el texto
  anonimizado (`classifications.text_anonymized`).
- **Nunca registres** cuerpos de petición o respuesta, cookies, `X-API-Key` ni contraseñas. El
  formateador JSON (`app/infrastructure/logging.py`) solo emite una lista cerrada de campos
  (`method`, `path`, `status`, `latency_ms`, `request_id`, `exception_type`); `tests/test_logging.py`
  lo verifica con `caplog`.
- Los errores de validación (422) nombran los campos con error pero **nunca repiten el valor
  recibido**.
- Los tokens de sesión y las API keys solo se guardan como SHA-256; las contraseñas con argon2id.
- Las contraseñas jamás se aceptan como argumento de la CLI (`getpass` o `--password-stdin`).
- Todo fallo de login responde igual (401 `invalid_credentials`, mismo mensaje y tiempo parecido:
  se verifica contra un hash ficticio si el usuario no existe).

## Límites de red

- **IP del cliente (`TRUSTED_PROXY_HOPS`, por defecto `0`):** `X-Forwarded-For` lo escribe quien
  llama, así que con `0` se ignora y se usa `request.client.host`. Con `N > 0` se toma la entrada N
  contando **desde la derecha** (la que agregó el proxy de confianza más externo); con menos de N
  entradas, o si la entrada no es una IP, se usa la IP de la conexión. La lógica está en
  `app/api/client_ip.py` y nunca debe volver a leer la primera entrada. Esta API la llaman también
  sistemas externos directamente: N solo es válido si todo el tráfico pasa por los N proxies.
  - Cómo elegir N: número de proxies de confianza que agregan una entrada entre el cliente y la API.
    Un N mayor que el real permite falsificar la IP; uno menor agrupa a los usuarios en un cubo.
  - Azure Container Apps: **`TRUSTED_PROXY_HOPS=1`** (solo la ingress, que agrega la IP de quien la
    llama). El proxy del dashboard **no** se cuenta: envía la IP del navegador en `X-PQRS-Client-IP`,
    que solo se acepta con un `X-PQRS-Proxy-Secret` válido (ver el punto siguiente). **Nunca uses `2`:**
    como la API es pública, un atacante que la llame directamente podría escribir la entrada que se
    tomaría como su IP y saltarse el límite de intentos de login. Debe comprobarse en el despliegue.

- **Proxy de confianza del dashboard:** la API es pública, así que la IP del navegador solo se acepta
  de un proxy autenticado. `PROXY_SHARED_SECRET` (cabecera `X-PQRS-Proxy-Secret`, comparación con
  `hmac.compare_digest`) es obligatorio salvo con `ENVIRONMENT=development` (si falta, `get_app()` no
  arranca: `Settings.require_runtime_config`). Lo exigen el login, el logout y toda petición
  autenticada con cookie (403 `proxy_required`, **antes** de resolver la sesión); las peticiones con
  `X-API-Key` no. Con secreto válido y `X-PQRS-Client-IP` que sea una IP se usa esa (`app/api/deps.py:
  client_ip`); si no, `TRUSTED_PROXY_HOPS`. En Azure el valor recomendado pasa a ser `1` (solo la
  ingress). El proxy **no** debe reenviar `X-API-Key` desde el navegador (gana sobre la cookie).
  En `development` sin secreto el chequeo se desactiva (con secreto se exige siempre).
- **Límite de intentos de login** (`app/infrastructure/security/rate_limit.py`, en memoria, una sola
  réplica): tres contadores por fallo en la misma ventana (`LOGIN_WINDOW_MINUTES`): el par **(email
  normalizado, IP)** con `LOGIN_MAX_ATTEMPTS` (5), la **IP sola** con `LOGIN_MAX_ATTEMPTS_PER_IP`
  (20) y el **email solo** con `LOGIN_MAX_ATTEMPTS_PER_EMAIL` (50), un respaldo frente a ataques
  repartidos entre muchas IP. Costo aceptado del último: quien acumule 50 fallos contra un correo
  desde cualquier IP bloquea los logins de ese correo hasta que venza la ventana. Un login correcto
  limpia el contador del par, no los de la IP ni el email. Al superarlo, 429 `too_many_attempts`
  (con `Retry-After`).
  - La memoria está acotada por `LOGIN_LIMITER_MAX_KEYS` (10.000, mínimo 3; cada fallo usa tres
    claves). Las claves se ordenan por su último fallo: las vencidas se purgan al insertar y, si
    aún se supera el tope, se desalojan primero las de actividad más antigua. Costo aceptado: quien
    genere más de ese número de claves distintas dentro de la ventana puede desalojar contadores
    ajenos.

- **Vida de la sesión:** caduca por inactividad (`SESSION_IDLE_HOURS`, 8, medida con `last_seen_at`)
  **y** por vida máxima absoluta desde el login (`SESSION_MAX_HOURS`, 24, medida con `created_at`):
  pasado ese tiempo la sesión es inválida aunque tenga actividad reciente (401 `session_expired`,
  se borra la fila y la cookie).

## Motores de clasificación (parte 2, PR a)

- **Paquete privado `pqrs-classifier` (tag `v0.1.0`)**, dependencia por git (`[tool.uv.sources]`). La CI y
  Docker necesitan el token de solo lectura (README, "Token del repositorio privado"). El dominio solo puede
  importar `pqrs_classifier.categories` (stdlib); `test_architecture.py` lo verifica y
  `tests/test_categories_sync.py` comprueba que el `CHECK` de la base coincide con el paquete.
- **Grupo `ml`** (torch de CPU, sentence-transformers, `scikit-learn==1.9.0`): fuera de `uv sync` por defecto.
  Las pruebas usan `FakeEngine`/`ScriptedEngine` (`tests/fakes.py`); las de motor real son `@pytest.mark.slow`.
- **`EngineRegistry`** (`app/infrastructure/engines/registry.py`) implementa `EngineGateway`: el motor local se
  carga una vez en el `lifespan` desde `MODEL_DIR` (si falla, la API arranca con `not_loaded` y `/ready`
  responde 503); el LLM sale de `LLMEngine.from_env(Settings.llm_env())`. **Sin respaldo automático entre
  motores**: `select()` decide antes de clasificar y un fallo posterior es 503.
- **Circuito del LLM** (`app/application/circuit_breaker.py`, en memoria, **una sola réplica**): se abre tras 3
  fallos consecutivos o con un `EngineUnavailableError(rate_limited)`, al menos 60 s o `retry_after`; luego
  deja pasar un único sondeo (semiabierto).
- **NER obligatorio:** el extra `ner` no instala `es_core_news_sm`, así que el modelo es dependencia directa.
  `check_ner_available()` corre al arrancar; fuera de `ENVIRONMENT=development` la API no arranca sin él
  (sin NER los nombres propios se guardarían sin anonimizar).
- El LLM solo ve los primeros `MAX_CARACTERES_TEXTO` (1200) caracteres de cada texto; el local ve el texto
  completo.

## Clasificación (parte 2, PR b)

- `ClassificationService` (`app/application/classification.py`): validar → `EngineGateway.select` → `anonymize`
  → **una** llamada al motor → guardar → `commit`. `source`: sesión `ui` (individual) / `ui_batch`, API key `api`.
- Límites: 1–5.000 caracteres por texto (tras `strip`), 50 elementos por lote (413 `batch_too_large`),
  `external_ref` ≤ 100. En el lote la validación es por elemento, en código (`invalid_text`,
  `invalid_external_ref`); el individual la hace el esquema (422).
- Lote: si el motor falla, 503 para todo el bloque y **nada** de ese bloque se guarda (los bloques previos con el
  mismo `batch_id` sí). `latency_ms` = latencia del bloque (anonimización + motor) repartida entre los
  elementos válidos; `created_at` suma 1 µs por posición para conservar el orden en el historial.
- El texto original solo existe en memoria durante la petición. Los logs de motores registran únicamente
  `engine`, `exception_type` y `circuit_state`.

## Historial y métricas (parte 2, PR c)

- `GET /classifications` (+ `/{id}`) y `GET /metrics/summary` son solo de sesión. Historial y métricas usan los
  **mismos filtros** (`ClassificationFilters`, una sola función `_filtered` en el repositorio), así que
  `total` coincide con el de `/classifications` para las mismas fechas (criterio E4).
- Fechas `YYYY-MM-DD` en UTC, inclusivas: `[date_from 00:00, date_to + 1 día 00:00)`. `date_from > date_to` → 422.
- Métricas: agregaciones en SQL (`GROUP BY`, `date_trunc` sobre `timezone('UTC', created_at)`), mapas con todas
  las claves (también las de valor 0) y serie sin huecos (semanas ISO desde el lunes). Sin fechas, la serie va de
  la primera a la última clasificación; más de 400 periodos → 422. `evaluation.f1_macro` sale de los motores.
- Todo método nuevo del repositorio recibe `org_id` obligatorio (`test_repository_guard.py`).

## Límites de solicitudes y de cuerpo (parte 2, PR d)

- `/models`, `/classify` y `/classify/batch` usan `require_session_or_key_limited` (`app/api/deps.py`): límite
  por minuto por API key (`RATE_LIMIT_KEY_PER_MINUTE`, 60) o por usuario (`RATE_LIMIT_USER_PER_MINUTE`, 120),
  **nunca por IP**; ventana deslizante en memoria (`InMemoryRequestRateLimiter`, una sola réplica). 429
  `rate_limited` con `Retry-After`. Cuenta toda solicitud autenticada, aunque luego falle.
- `BodyLimitMiddleware` (`app/api/middleware.py`): `MAX_BODY_BYTES` (1 MiB); mira `Content-Length` y cuenta los
  bytes del stream. FastAPI convierte un fallo al leer el cuerpo en un 400, así que el middleware sustituye
  esa respuesta por el 413 `payload_too_large`. Está por dentro del middleware de log para que lo registre.

## Imagen Docker (parte 2, PR e)

- `Dockerfile` multi-etapa: `builder` (git, curl, `uv sync --frozen --no-dev --group ml`, pesos de SBERT en
  `HF_HOME=/opt/hf`, `model.joblib` y `metadata.json` del release con sha256 fijados como `ARG`) y etapa final
  sin git ni token, usuario no root, `HF_HUB_OFFLINE=1`, `MODEL_DIR=/opt/model`, `TQDM_DISABLE=1`. El build
  falla si el motor local no carga sin red. El token (`classifier_token`) solo vive en cada
  `RUN --mount=type=secret`, como variables `GIT_CONFIG_*` de git o en la cabecera de `curl`.
- Al actualizar `pqrs-classifier` (nuevo tag) hay que cambiar a la vez: el tag en `pyproject.toml`
  (`uv lock`), `CLASSIFIER_RELEASE`, `MODEL_SHA256` y `METADATA_SHA256` del `Dockerfile`, y la versión de
  scikit-learn del grupo `ml` si cambia `metadata.json`.
- `docker-compose.yml`: `api` construye la imagen (`pqrs-api:local`), `migrate` la reutiliza; `api` tiene
  healthcheck sobre `/ready`. Usa `ENVIRONMENT=development` con un `PROXY_SHARED_SECRET` de desarrollo.

## Qué queda para la parte 2

Nada pendiente de la parte 2 en esta rama; el despliegue en Azure es la etapa E5.
