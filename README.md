# pqrs-api

API REST del MVP del **Clasificador automático de PQRS** (Proyecto Integrador I, Universidad de
Antioquia). Clasifica Peticiones, Reclamos, Denuncias y Solicitudes de información. El sistema tiene
tres repositorios: el clasificador (`pqrs-classifier`), esta API y el dashboard en Next.js.

Estado: **E2 parte 2, en curso** — la parte 1 (autenticación, aislamiento por organización, CLI y
contrato OpenAPI v0) está en `main`; la parte 2 (clasificación, historial, métricas y endurecimiento)
se entrega en varios PR.

## Requisitos

- [uv](https://docs.astral.sh/uv/) (instala Python 3.11 por su cuenta)
- Docker con el plugin `docker compose`
- Acceso de lectura al repositorio **privado** `PI1-Clasificador-de-PQRS/pqrs-classifier` (ver
  [siguiente sección](#token-del-repositorio-privado-del-clasificador))

## Token del repositorio privado del clasificador

La API instala el paquete `pqrs-classifier` (tag `v0.1.0`) desde un repositorio privado de GitHub,
y la imagen Docker descarga además `model.joblib` y `metadata.json` de su release. Hace falta un
token de **solo lectura**:

1. GitHub → *Settings* → *Developer settings* → *Personal access tokens* → *Fine-grained tokens* →
   *Generate new token*.
2. *Resource owner*: la organización `PI1-Clasificador-de-PQRS`; *Repository access*: *Only select
   repositories* → `pqrs-classifier`; permisos de repositorio: **Contents: Read-only** (el permiso
   *Metadata: Read-only* se añade solo). Ningún otro. Pon una caducidad corta.
3. **CI**: en el repositorio `pqrs-api` → *Settings* → *Secrets and variables* → *Actions* → *New
   repository secret* con el nombre `CLASSIFIER_REPO_TOKEN`. La CI lo usa para que `git` pueda leer
   ese repositorio y para descargar el modelo en el job `slow`. Los PR desde forks no reciben el
   secreto y no podrán instalar las dependencias.
4. **Local**: `uv sync` necesita que `git` pueda leer el repositorio. Si ya tienes `gh auth login`
   (o llaves SSH con acceso) no hace falta nada más. Si no, exporta el token solo para ese comando:
   ```bash
   export GIT_CONFIG_COUNT=1
   export GIT_CONFIG_KEY_0="url.https://x-access-token:${CLASSIFIER_REPO_TOKEN}@github.com/PI1-Clasificador-de-PQRS/pqrs-classifier.insteadOf"
   export GIT_CONFIG_VALUE_0="https://github.com/PI1-Clasificador-de-PQRS/pqrs-classifier"
   uv sync
   ```
5. **Docker**: el token se pasa como *build secret* (nunca como `ARG` ni `ENV`, así no queda en una
   capa de la imagen): `export CLASSIFIER_REPO_TOKEN=<token>` antes de `docker compose up --build`,
   o `docker build --secret id=classifier_token,env=CLASSIFIER_REPO_TOKEN .`.

Nunca subas el token al repositorio ni lo pegues en un issue o PR.

### Dependencias de aprendizaje automático (grupo `ml`)

Por defecto `uv sync` **no** instala torch ni sentence-transformers: las pruebas usan motores falsos
(`pqrs_classifier.testing.FakeEngine`). El motor local real necesita el grupo `ml`
(`uv sync --group ml`, torch de CPU en Linux) y los artefactos del release `v0.1.0` en `MODEL_DIR`.
Las pruebas con el motor real están marcadas `slow` y corren en un workflow aparte
(`.github/workflows/slow.yml`), en los PR que tocan dependencias, `Dockerfile`, el arranque o los
motores, y a mano: `SLOW_MODEL_DIR=<dir> uv run --group ml pytest -m slow`.

## Levantar el entorno local de cero

### Opción A: todo en contenedores

```bash
export CLASSIFIER_REPO_TOKEN=<token>   # ver la sección anterior
docker compose up -d --build   # PostgreSQL 16, migraciones (servicio `migrate`) y API en :8000
curl localhost:8000/ready      # {"status":"ready"} cuando el modelo local ya cargó
```

La CLI corre dentro del contenedor: `docker compose exec api pqrs-admin --help`. La primera
construcción tarda varios minutos (torch y los pesos de SBERT); las siguientes usan la caché de
capas. Para probar el flujo completo a mano (login con las cabeceras del proxy, clasificar,
historial, métricas) sigue [Iniciar sesión con curl](#iniciar-sesión-con-curl); en `docker-compose.yml`
el secreto del proxy es `dev-proxy-secret-solo-para-local`. Sin `LLM_API_KEY` y `LLM_MODEL`, `/models`
muestra el LLM como `not_configured` y solo funciona `"model":"classifier"` (o omitir `model`).

### La imagen Docker

Imagen multi-etapa (`Dockerfile`): la etapa `builder` instala las dependencias con el grupo `ml`
(torch **de CPU**), descarga los pesos de SBERT (`paraphrase-multilingual-mpnet-base-v2`, en la
revisión que fija `metadata.json`) y `model.joblib` + `metadata.json` del GitHub Release `v0.1.0` del
clasificador (con verificación sha256); la etapa final solo copia el resultado. Corre como usuario no
root (uid 10001), con `HF_HUB_OFFLINE=1` (sin red hacia Hugging Face en ejecución) y `MODEL_DIR=/opt/model`.
El token entra como *build secret* y no queda en ninguna capa (se comprobó buscándolo en el sistema de
archivos y en `docker history`).

```bash
docker build --secret id=classifier_token,env=CLASSIFIER_REPO_TOKEN -t pqrs-api .
```

| | Medido (v0.1.0, `linux/amd64`) |
|---|---|
| Tamaño de la imagen | ~4,6 GB en disco según `docker images` (~1,6 GB comprimida): 1,7 GB de entorno Python (torch de CPU ≈ 0,8 GB), 1,1 GB de pesos de SBERT y el resto, la base |
| Memoria en ejecución | ~0,85 GB en el cgroup con el modelo cargado y unas pocas peticiones; pico de RSS ≈ 1,35 GB |
| Memoria a reservar | **≥ 2 GB** por réplica (la estimación del documento de diseño), por los lotes del motor local (`CLASSIFIER_MAX_CONCURRENCY`, 2 por defecto, acota el pico) |

Una sola réplica y un solo proceso `uvicorn`: el límite de solicitudes, el de intentos de login y el
circuito del LLM viven en memoria.

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
pqrs-admin list-orgs                      # id, estado, fecha de creación y nombre de cada una

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

El dashboard es un proxy BFF: el navegador nunca ve credenciales. Para probar a mano, haz lo que
haría el proxy (ver [Contrato con el proxy del dashboard](#contrato-con-el-proxy-del-dashboard)):
envía `X-PQRS-Proxy-Secret`, guarda la cookie con `-c` y reenvíala con `-b`. Las peticiones que
modifican datos con cookie exigen `X-PQRS-CSRF: 1`.

```bash
P="X-PQRS-Proxy-Secret: $PROXY_SHARED_SECRET"   # el valor de PROXY_SHARED_SECRET de la API

# Iniciar sesión (fija la cookie de sesión)
curl -c jar.txt -H "$P" -H 'X-PQRS-Client-IP: 203.0.113.7' -H 'Content-Type: application/json' \
  -d '{"email":"ana@example.com","password":"<tu contraseña>"}' \
  localhost:8000/api/v1/auth/login

# Datos de la sesión
curl -b jar.txt -H "$P" localhost:8000/api/v1/me

# Clasificar, ver el historial y las métricas
curl -b jar.txt -H "$P" -H 'X-PQRS-CSRF: 1' -H 'Content-Type: application/json' \
  -d '{"text":"Quiero saber el estado de mi trámite","model":"classifier"}' \
  localhost:8000/api/v1/classify
curl -b jar.txt -H "$P" 'localhost:8000/api/v1/classifications?page_size=5'
curl -b jar.txt -H "$P" 'localhost:8000/api/v1/metrics/summary?granularity=day'

# Cerrar sesión (POST con cookie → cabecera CSRF)
curl -b jar.txt -c jar.txt -H "$P" -X POST -H 'X-PQRS-CSRF: 1' localhost:8000/api/v1/auth/logout -i
```

Con `ENVIRONMENT=development` y **sin** `PROXY_SHARED_SECRET` el chequeo del proxy queda desactivado
(la API avisa al arrancar) y puedes omitir esas cabeceras.

Una API key se envía con `X-API-Key` y **solo permite clasificar**; historial, métricas y `/me`
exigen sesión:

```bash
curl -H "X-API-Key: $KEY" localhost:8000/api/v1/me   # 401 session_required
curl -X POST -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"text":"Solicito copia de mi expediente","model":"classifier"}' localhost:8000/api/v1/classify
```

## Contrato con el proxy del dashboard

La API es pública (también la llaman sistemas externos con `X-API-Key`), así que no puede fiarse de
una IP que escriba el cliente. El dashboard (Next.js) es el único que maneja la cookie de sesión y
debe comportarse así al reenviar cada petición del navegador a la API:

| Cabecera | Quién la pone | Para qué |
|---|---|---|
| `X-PQRS-Proxy-Secret: <PROXY_SHARED_SECRET>` | el proxy, siempre | Prueba de que la petición pasó por el proxy. Sin un secreto válido, el login, el logout y toda petición autenticada con la cookie responden **403 `proxy_required`**. Se compara en tiempo constante. |
| `X-PQRS-Client-IP: <IP del navegador>` | el proxy | IP real del navegador, para el límite de intentos de login. Solo se usa con un secreto válido y si es una IP; en cualquier otro caso se usa la lógica de `TRUSTED_PROXY_HOPS`. |
| `X-PQRS-CSRF: 1` | el proxy | En POST, PUT, PATCH y DELETE con cookie (no en el login). |
| `Cookie: pqrs_session=...` | el proxy | Se reenvía tal cual; el navegador nunca la maneja desde JavaScript. |

Reglas para el proxy:

- **No reenviar `X-API-Key` desde el navegador.** Si llegan cookie y `X-API-Key`, gana la API key (y
  esa rama no pasa por el proxy). El navegador no debe poder inyectarla; elimina esa cabecera y
  cualquier `X-PQRS-*` que traiga la petición del navegador antes de poner las suyas.
- Guardar `PROXY_SHARED_SECRET` solo en el servidor del dashboard (variable de entorno, nunca
  `NEXT_PUBLIC_*`), y generarlo largo y aleatorio (`openssl rand -hex 32`).
- Las peticiones con `X-API-Key` van directo a la API y no necesitan nada de lo anterior.
- `GET /health` y `GET /ready` son públicos.

`PROXY_SHARED_SECRET` es **obligatorio** salvo con `ENVIRONMENT=development`: si falta en otro
entorno, la API no arranca.

## IP del cliente y proxies (`TRUSTED_PROXY_HOPS`)

El límite de intentos de login usa la IP del cliente. Si el proxy envía un secreto válido y
`X-PQRS-Client-IP`, se usa esa IP. En cualquier otro caso `X-Forwarded-For` (que puede escribir quien
llama) **se ignora por defecto** (`TRUSTED_PROXY_HOPS=0`) y se usa la IP de la conexión.

Si la API está detrás de proxies de confianza, `TRUSTED_PROXY_HOPS=N` toma la entrada N contando
**desde la derecha** de `X-Forwarded-For`: la que agregó el proxy de confianza más externo. Lo que
haya a su izquierda lo controla quien llama y se ignora. Si la cabecera tiene menos de N entradas o
la entrada no es una IP, se usa la IP de la conexión.

Cómo elegir N: cuenta los proxies de confianza entre el cliente y la API que agregan una entrada a la
cabecera. Un N mayor que el real permite falsificar la IP; uno menor da la IP de un proxy y agrupa a
todos los usuarios en un mismo cubo. Con `X-PQRS-Client-IP` ya no hace falta contar al proxy del
dashboard: en **Azure Container Apps el valor recomendado es `1`** (solo la ingress, que agrega la IP
de quien la llama), y sirve para el tráfico directo de sistemas externos. Compruébalo en el
despliegue. En local déjalo en `0`.

## Límites

- **Cuerpo de la petición:** `MAX_BODY_BYTES` (1 MiB). Se verifica `Content-Length` y también se
  cuentan los bytes de cuerpos sin esa cabecera; al superarlo, 413 `payload_too_large`.
- **Solicitudes:** `/models`, `/classify` y `/classify/batch` admiten `RATE_LIMIT_KEY_PER_MINUTE` (60)
  por API key y `RATE_LIMIT_USER_PER_MINUTE` (120) por usuario, nunca por IP. Un lote cuenta como una
  solicitud. Al superarlo, 429 `rate_limited` con `Retry-After`.
- **Login:** `LOGIN_MAX_ATTEMPTS` (5) por (correo, IP), `LOGIN_MAX_ATTEMPTS_PER_IP` (20) por IP y
  `LOGIN_MAX_ATTEMPTS_PER_EMAIL` (50) por correo, todos en la ventana `LOGIN_WINDOW_MINUTES`. El
  límite por correo es un respaldo frente a ataques repartidos entre muchas IP; su costo es que quien
  acumule 50 fallos contra un correo bloquea sus logins hasta que venza la ventana.
- Todos los límites son **en memoria y asumen una sola réplica** de la API.

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
