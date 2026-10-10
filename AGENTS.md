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
uv sync                                         # instala dependencias (.venv)
docker compose up -d db                         # PostgreSQL 16 en localhost:5432
uv run alembic upgrade head                     # migraciones
uv run pytest                                   # pruebas contra PostgreSQL real
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run uvicorn app.main:get_app --factory --reload --no-access-log

uv run pqrs-admin create-org "Nombre"
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
  - Azure Container Apps: la ingress agrega una entrada y el proxy del dashboard debe agregar la IP
    real del navegador, así que para el tráfico del dashboard el valor esperado es `2` (`1` si solo
    hay ingress). Debe comprobarse en el despliegue.

- **Límite de intentos de login** (`app/infrastructure/security/rate_limit.py`, en memoria, una sola
  réplica): dos contadores por fallo en la misma ventana (`LOGIN_WINDOW_MINUTES`): el par **(email
  normalizado, IP)** con `LOGIN_MAX_ATTEMPTS` (5) y la **IP sola** con `LOGIN_MAX_ATTEMPTS_PER_IP`
  (20). **No hay límite por email solo**: permitiría que un tercero bloquee a un usuario real desde
  otra IP. Un login correcto limpia el contador del par, no el de la IP. Al superarlo, 429
  `too_many_attempts` (con `Retry-After`).
  - La memoria está acotada por `LOGIN_LIMITER_MAX_KEYS` (10.000, mínimo 2; cada fallo usa dos
    claves). Las claves se ordenan por su último fallo: las vencidas se purgan al insertar y, si
    aún se supera el tope, se desalojan primero las de actividad más antigua. Costo aceptado: quien
    genere más de ese número de claves distintas dentro de la ventana puede desalojar contadores
    ajenos.

- **Vida de la sesión:** caduca por inactividad (`SESSION_IDLE_HOURS`, 8, medida con `last_seen_at`)
  **y** por vida máxima absoluta desde el login (`SESSION_MAX_HOURS`, 24, medida con `created_at`):
  pasado ese tiempo la sesión es inválida aunque tenga actividad reciente (401 `session_expired`,
  se borra la fila y la cookie).

## Qué queda para la parte 2

Hoy estas rutas están definidas con sus esquemas y responden `501 not_implemented` (tras
autenticar):

- `GET /models`, `POST /classify`, `POST /classify/batch` (sesión o API key).
- `GET /classifications`, `GET /classifications/{id}`, `GET /metrics/summary` (solo sesión).

Pendiente:

- Importar las categorías de `pqrs_classifier.categories` (hoy duplicadas en
  `app/domain/categories.py`, con un comentario que exige mantenerlas iguales) e instalar el paquete.
- Motores `llm` y `classifier`, circuito de protección del LLM (`llm_unavailable`,
  `classifier_unavailable`) y anonimización antes de persistir.
- Límite de solicitudes por API key o usuario en clasificación (`rate_limited`).
- Historial y métricas: filtros, paginación y agregaciones en `ClassificationRepository` (hoy solo
  `add`, `get` y un `list` mínimo), validación por elemento y 413 `batch_too_large` en el lote.
- `/ready` debe exigir además que el modelo local esté cargado.
- Cualquier cambio de contrato debe regenerar `openapi/openapi.json`.
