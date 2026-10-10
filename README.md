# pqrs-api

API REST del MVP del **Clasificador automático de PQRS** (Proyecto Integrador I, Universidad de
Antioquia). Clasifica Peticiones, Reclamos, Denuncias y Solicitudes de información. El sistema tiene
tres repositorios: el clasificador (`pqrs-classifier`), esta API y el dashboard en Next.js.

Estado: **E2 parte 1** — base del proyecto, autenticación, aislamiento por organización, CLI de
administración y contrato OpenAPI v0. La clasificación real llega en la parte 2: hoy esos endpoints
responden `501 not_implemented`.

## Requisitos

- [uv](https://docs.astral.sh/uv/) (instala Python 3.11 por su cuenta)
- Docker con el plugin `docker compose`

## Levantar el entorno local de cero

### Opción A: todo en contenedores

```bash
docker compose up -d --build   # PostgreSQL 16, migraciones (servicio `migrate`) y API en :8000
curl localhost:8000/ready      # {"status":"ready"}
```

La CLI corre dentro del contenedor: `docker compose exec api pqrs-admin --help`.

### Opción B: API local con `uv` y solo la base de datos en Docker

```bash
cp .env.example .env            # revisa los valores; SESSION_COOKIE_SECURE=false es solo para local
uv sync                         # crea .venv e instala dependencias
docker compose up -d db         # PostgreSQL 16 en localhost:5432
uv run alembic upgrade head     # aplica las migraciones
uv run uvicorn app.main:get_app --factory --reload --no-access-log
```

## Crear una organización, un usuario y una clave

Toda la administración se hace por línea de comandos (no hay pantallas de gestión). Con la opción B
usa `uv run pqrs-admin ...`; con la opción A, `docker compose exec api pqrs-admin ...`.

```bash
# 1. Organización: imprime su id (el resto de comandos lo piden con --org-id)
pqrs-admin create-org "Alcaldía de Ejemplo"
ORG=<id impreso arriba>

# 2. Usuario: la contraseña (mínimo 12 caracteres) se pide por teclado, nunca como argumento
pqrs-admin create-user --org-id $ORG --email ana@example.com --name "Ana Pérez"
#    para scripts: echo "$PASSWORD" | pqrs-admin create-user ... --password-stdin

# 3. API key: la clave completa se muestra UNA sola vez; guárdala
pqrs-admin create-key --org-id $ORG --name "Sistema de radicación"
pqrs-admin list-keys --org-id $ORG        # después solo se ve el prefijo
```

Otros comandos: `reset-password` (cierra las sesiones del usuario), `deactivate-user` (borra todas
sus sesiones) y `revoke-key`.

## Iniciar sesión con curl

El dashboard es un proxy BFF: el navegador nunca ve credenciales. Para probar a mano, guarda la
cookie con `-c` y reenvíala con `-b`. Las peticiones que modifican datos con cookie exigen
`X-PQRS-CSRF: 1`.

```bash
# Iniciar sesión (fija la cookie de sesión)
curl -c jar.txt -H 'Content-Type: application/json' \
  -d '{"email":"ana@example.com","password":"<tu contraseña>"}' \
  localhost:8000/api/v1/auth/login

# Datos de la sesión
curl -b jar.txt localhost:8000/api/v1/me

# Cerrar sesión (POST con cookie → cabecera CSRF)
curl -b jar.txt -c jar.txt -X POST -H 'X-PQRS-CSRF: 1' localhost:8000/api/v1/auth/logout -i
```

Una API key se envía con `X-API-Key` y **solo permite clasificar**; historial, métricas y `/me`
exigen sesión:

```bash
curl -H "X-API-Key: $KEY" localhost:8000/api/v1/me   # 401 session_required
curl -X POST -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"text":"Solicito copia de mi expediente"}' localhost:8000/api/v1/classify   # 501 hasta la parte 2
```

## IP del cliente y proxies (`TRUSTED_PROXY_HOPS`)

El límite de intentos de login usa la IP del cliente. `X-Forwarded-For` la puede escribir quien
llama, así que **por defecto se ignora** (`TRUSTED_PROXY_HOPS=0`) y se usa la IP de la conexión.

Si la API está detrás de proxies de confianza, `TRUSTED_PROXY_HOPS=N` toma la entrada N contando
**desde la derecha** de `X-Forwarded-For`: la que agregó el proxy de confianza más externo. Lo que
haya a su izquierda lo controla quien llama y se ignora. Si la cabecera tiene menos de N entradas o
la entrada no es una IP, se usa la IP de la conexión.

Cómo elegir N: cuenta los proxies de confianza entre el cliente y la API que agregan una entrada a la
cabecera. Un N mayor que el real permite falsificar la IP; uno menor da la IP de un proxy y agrupa a
todos los usuarios en un mismo cubo. En Azure Container Apps la ingress agrega una entrada y el proxy
del dashboard debe agregar la IP real del navegador, así que para el tráfico del dashboard hacia la API
por su ingress el valor esperado es `2` (`1` si solo hay ingress). Compruébalo en el despliegue. En
local déjalo en `0`.

## Desarrollo

```bash
uv run pytest                                  # necesita PostgreSQL (docker compose up -d db)
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run python scripts/export_openapi.py        # regenera openapi/openapi.json
uv run python scripts/export_openapi.py --check
```

Las pruebas usan PostgreSQL real: crean la base `pqrs_test` si no existe (`TEST_DATABASE_URL`) y
aíslan cada prueba en una transacción que se revierte.

## Contrato OpenAPI

[`openapi/openapi.json`](openapi/openapi.json) está versionado y la CI falla si no está al día.
El dashboard puede generar de él sus tipos y mocks. Con la API en marcha, la documentación
interactiva está en `/docs`. Todos los errores usan el formato
`{"detail": {"code": "<código_estable>", "message": "<texto en español>"}}`.

Más detalle de arquitectura y reglas del repositorio en [`AGENTS.md`](AGENTS.md).
