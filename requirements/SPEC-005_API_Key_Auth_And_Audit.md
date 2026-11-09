# SPEC-005: Autenticación por clave y registro de auditoría

> **Estado:** ✅ Implementada (v2.7.0). Criterios de código y tests cubiertos;
> la **Bitácora de IA** al final registra la implementación.

## 1. Contexto (El Problema)

El conector es un **puente**: recibe una petición HTTP, la traduce a Microsoft
Graph (SharePoint) y devuelve la respuesta. Hoy **cualquiera que alcance la red
del servicio puede llamarlo**: no hay autenticación de entrada. El único dato de
"quién llama" es la cabecera `X-App-ID`, que **el propio cliente declara y nadie
verifica**, así que no sirve como evidencia de auditoría.

El servicio ya registra, por cada petición, el `request_id`, el método, la ruta,
el **código de respuesta** y la duración (middleware de `app/main.py`,
`JSONFormatter` en `app/core/logging.py`). Lo que falta es:

1. **Identidad fiable:** una credencial por llamante que el servicio valide, no
   que el llamante se autoasigne.
2. **Trazabilidad por identidad:** que cada entrada del log diga **quién** hizo
   la llamada, no solo qué y con qué resultado.

El conector se invoca desde varias aplicaciones e incluso desde Postman o curl,
por lo que la credencial debe ser independiente del medio: la misma clave
funciona desde cualquier cliente y siempre identifica al mismo titular.

> **Alcance de esta fase.** La clave identifica al **llamante** (una
> aplicación/persona a la que se le entrega una clave), no verifica la identidad
> de un usuario final vía su sesión corporativa. La identidad verificada por
> Entra ID (OAuth2 / `Bearer`) queda como **fase posterior**, fuera de esta SPEC.

## 2. Propuesta (La Solución)

### 2.1 Clave en cabecera
Cada petición incluye la clave en la cabecera **`X-API-Key`** (no en el body: los
`GET` no tienen body y así la credencial no se mezcla con los datos que se
registran). Se declara con `APIKeyHeader` de FastAPI para que aparezca en la
documentación OpenAPI.

### 2.2 Almacén de claves habilitadas (SQLite)
Las claves habilitadas viven en una base **SQLite** (un único archivo, sin
servidor aparte), en un volumen persistente (no dentro del contenedor) y con
permisos restringidos. Tabla propuesta `api_keys`:

| columna      | tipo    | descripción                                       |
|--------------|---------|---------------------------------------------------|
| `principal`  | TEXT    | identidad que aparece en la auditoría (ej. `jdiaz`) |
| `key_hash`   | TEXT    | **HMAC-SHA256** de la clave (nunca la clave en claro) |
| `active`     | INTEGER | 1 = habilitada, 0 = revocada                      |
| `created_at` | TEXT    | fecha de alta                                     |
| `last_used`  | TEXT    | última vez que se usó (se actualiza al validar)   |

- **No se guarda la clave**, solo su **HMAC-SHA256** (SHA-256 con un secreto del
  servidor, `API_KEY_HMAC_SECRET`). A diferencia de un SHA-256 a secas, el HMAC
  no es buscable en tablas públicas de hashes.
- **Alta/baja sin redesplegar:** añadir, revocar (`active=0`) o reactivar una
  clave es una fila en SQLite; no exige recompilar ni reiniciar.
- **Gestión de claves:** la emisión de claves (generar una clave aleatoria,
  calcular su HMAC e insertar la fila) se hace con un pequeño script/utilidad de
  administración fuera del camino de las peticiones. La clave en claro se entrega
  al titular por un canal seguro y **no vuelve a existir** en el servidor.

### 2.3 Validación como dependencia
Una dependencia de FastAPI (`Depends`/`Security`) común a los endpoints
protegidos:
1. Lee `X-API-Key`. Ausente → **`401`**.
2. Calcula su HMAC y busca una fila `active=1` con ese `key_hash`, comparando en
   tiempo constante (`secrets.compare_digest`). No encontrada → **`401`**.
3. Encontrada → expone el `principal` al resto de la petición y actualiza
   `last_used`.

Se inyecta siguiendo el patrón de DI del proyecto (`app/core/dependencies.py`),
de modo que en tests se pueda sustituir por un doble vía
`app.dependency_overrides`.

### 2.4 Registro de auditoría
Se añade el campo **`principal`** a la whitelist `_EXTRA_FIELDS`
(`app/core/logging.py`) y se propaga por `ContextVar` (`app/core/context.py`),
igual que `request_id` y `client_app_id`. El log de "petición completada" pasa a
incluir **quién** (`principal`), **qué** (método + ruta) y **con qué código**
(`status_code`) — el seguimiento de auditoría pedido.

- Los **intentos rechazados** (`401`) también se registran, con el resultado de
  la validación, para detectar uso indebido. Nunca se registra la clave.
- El `request_id` ya devuelto en `X-Request-ID` permite correlacionar una entrada
  de auditoría con su petición.

> **Decisión abierta (menor):** si el log de auditoría debe ir a un **archivo o
> destino separado** del log técnico y con su propia retención, o basta con el
> campo `principal` dentro del log JSON actual. Por defecto: lo segundo.

## 3. Criterios de Aceptación

1. Una petición **sin** `X-API-Key` a un endpoint protegido responde **`401`** y
   no llega a Graph.
2. Una petición con una clave **no habilitada o revocada** (`active=0`) responde
   **`401`**.
3. Una petición con una clave **habilitada** se procesa con normalidad y su log
   de "completada" incluye el campo **`principal`** correcto junto al
   `status_code`.
4. El almacén SQLite guarda las claves **hasheadas con HMAC-SHA256**; en ningún
   punto (BD, logs, respuestas) aparece la clave en claro.
5. Añadir o revocar una clave **no requiere redespliegue** del servicio.
6. Existe una utilidad de administración para emitir una clave (aleatoria) y dar
   de alta su titular.
7. El secreto HMAC y la ruta del archivo SQLite se configuran vía `Settings`
   (`app/core/config.py`) y están documentados en `devops/.env.example`; el
   archivo SQLite reside en un volumen persistente que el `Dockerfile`/compose
   contempla.
8. Tests `pytest` que cubren los criterios 1–4 usando `dependency_overrides`.
9. Documentación viva (`README.md`, `ARQUITECTURA.md`, `TECNOLOGIAS.md`) y
   `CHANGELOG.md` + `VERSION` actualizados al cerrar.

## 4. Fases futuras (fuera del alcance de esta SPEC)

### Fase 2 — Autorización por usuario y lista (RBAC)
Esta SPEC cubre **autenticación** (quién llama) y **auditoría**. Un paso
posterior, en su propia change/SPEC, añadiría **autorización** (qué puede hacer
cada `principal`), aplicando mínimo privilegio: restringir qué usuarios pueden
**leer/escribir** y sobre qué **listas**.

- **Por qué va aparte:** esta fase ya es BREAKING (obliga a `X-API-Key`); añadir
  permisos la agranda y arriesga el despliegue. Además, el registro de auditoría
  de esta fase es justo lo que se necesita **antes** de diseñar permisos: primero
  se observa quién llama y a qué listas, y con esos datos se definen reglas que no
  rompan a los integradores actuales.
- **Modelo tentativo (más tablas):**
  - `principals` — ya existe como `api_keys`.
  - `permissions` — concesiones: `principal` + ámbito (`site` / `list`) +
    operación (`read` / `write`). Relación muchos-a-muchos (un usuario sobre
    varias listas; una lista para varios usuarios).
- **Validación:** tras autenticar y **antes** de llamar a Graph, comprobar si el
  `principal` tiene permiso sobre la lista (del request) y la operación; si no →
  **`403`**.
- **Decisión pendiente para esa fase:** el conector es genérico (la lista viaja
  en cada petición), así que hay que decidir el comportamiento de un `principal`
  **sin reglas**: negar todo (lista blanca, lo seguro) o permitir todo (solo
  auditar). Recomendación provisional: lista blanca.

## 5. Bitácora de IA / Historial de Implementación

### 2026-11-09 — Agente LLM (implementación v2.7.0)

**Archivos nuevos**
- `app/core/api_keys.py` — `ApiKeyStore` (SQLite): crea la tabla `api_keys`,
  resuelve el `principal` de una clave `active=1` comparando HMAC en tiempo
  constante y actualiza `last_used`. Helpers `compute_hash` (HMAC-SHA256) y
  `generate_api_key` (`secrets.token_urlsafe(32)`).
- `app/core/security.py` — dependencia `require_principal`: lee `X-API-Key`
  (`APIKeyHeader`, `auto_error=False`), valida y fija el `principal` en
  `principal_var` y `request.state`. Lanza `APIKeyError` (401).
- `app/admin/api_keys_admin.py` (+ `app/admin/__init__.py`) — CLI de alta
  (`issue <principal>`) y ayuda SQL (`--help-sql`) para el resto de operaciones.
- `tests/test_api_key_auth.py` — 12 tests (integración 401/200, auditoría del
  intento rechazado, la clave nunca aparece en logs, + unidad de almacén/HMAC).

**Archivos modificados**
- `app/core/config.py` — `api_key_hmac_secret`, `api_key_db_path`.
- `app/core/context.py` — `principal_var`.
- `app/core/dependencies.py` — `get_api_key_store()` singleton (`None` si auth
  deshabilitada).
- `app/core/exceptions.py` — `APIKeyError` + `api_key_exception_handler`
  (mismo envoltorio `error`/`request_id`/`path`).
- `app/core/logging.py` — `principal` añadido a `_EXTRA_FIELDS`.
- `app/api/v1/router.py` — `dependencies=[Depends(require_principal)]` en `/v1`.
- `app/main.py` — registro del handler, `principal` en el log de "petición
  completada", aviso de arranque si la auth está deshabilitada.
- `devops/.env.example`, `devops/docker-compose.yml` (volumen
  `sp-connector-data` → `/app/data`), `.gitignore`.
- Docs: `README.md`, `ARQUITECTURA.md`, `TECNOLOGIAS.md`, `VERSION` (2.7.0),
  `doc/CHANGELOG.md`.

**Decisiones clave**
- **Protección a nivel de router `/v1`** (no endpoint por endpoint): un solo
  punto, `/health` queda público por estar fuera de `/v1`.
- **`principal` por `request.state`** además del `ContextVar`: el middleware
  (BaseHTTPMiddleware) lo lee de `request.state` tras `call_next` de forma
  fiable (el `scope` se comparte), evitando el problema de propagación de
  `ContextVar` hacia el middleware.
- **Modo compatibilidad** (desviación respecto al diseño original, que asumía
  auth siempre obligatoria): si `API_KEY_HMAC_SECRET` está vacío, la auth se
  deshabilita y se avisa al arrancar. Permite el despliegue escalonado que el
  propietario pidió (desplegar → repartir claves → activar). El comportamiento
  por defecto queda retrocompatible; al fijar el secreto, `X-API-Key` pasa a ser
  obligatoria.

**Verificación**
- Suite completa en verde: **257/257** `pytest`.
- Smoke manual: `issue jdiaz` genera clave + fila HMAC; vía TestClient,
  sin clave / clave inválida / revocada → `401`, clave válida → `200` con
  `principal=jdiaz` en el log de auditoría; `/health` → `200`.

**Fuera de alcance (ver sección 4):** autorización por usuario/lista (`403`) e
identidad vía Entra ID.
