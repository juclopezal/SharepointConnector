# SPEC-001: Arquitectura v2 — API Dinámica Multi-Site

## 1. Contexto (El Problema)

La versión 1.0.0 del SharePoint Connector presentaba varias limitaciones estructurales:

- **Site único fijo:** El conector estaba acoplado a un único site de SharePoint configurado mediante la variable de entorno `SITE_URL`. Cualquier operación sobre un site diferente requería una instancia separada del servicio.
- **Endpoints planos sin versionado:** `POST /upload` y `POST /list` no tenían prefijo de versión, imposibilitando evolucionar la API sin romper callers existentes.
- **Subida de archivos ineficiente:** El archivo viajaba codificado en Base64 dentro de un JSON, incrementando el payload ~33% y requiriendo codificación/decodificación en ambos extremos.
- **Sin capacidad de discovery:** El caller debía conocer de antemano el nombre de la lista y la biblioteca de documentos. No había forma de explorar qué sites, listas o drives eran accesibles.
- **Sin endpoints de descarga ni metadatos:** No era posible descargar archivos ni consultar sus metadatos a través del conector.
- **Logging básico:** Sin estructura JSON, sin `request_id`, sin propagación de contexto a la capa de servicio. Los errores de autenticación se propagaban como `500 Internal Server Error`.
- **Configuración muerta:** Variables de entorno (`DEFAULT_LIST_NAME`, `DEFAULT_DRIVE_NAME`) declaradas pero no utilizadas en la lógica de routing.

## 2. Propuesta (La Solución)

Rediseño completo hacia un servicio genérico y dinámico donde el site, la lista y el drive se identifican por ID en cada llamada, eliminando cualquier acoplamiento a recursos SharePoint específicos.

La API se versiona bajo `/v1/graph/...` siguiendo la estructura de rutas de Microsoft Graph API, agrupada en tres responsabilidades:

- **Discovery:** endpoints para explorar qué sites, listas, drives y carpetas son accesibles por la aplicación.
- **List Items:** lectura e inserción de ítems en cualquier lista de cualquier site.
- **Files:** subida (`multipart/form-data`), consulta de metadatos y descarga de archivos de cualquier biblioteca de documentos.

El logging pasa a JSON estructurado con `request_id` único por petición y `client_app_id` propagados desde el middleware hasta la capa de servicio. Los errores de autenticación con Azure AD se convierten en `GraphAPIError` tipado (401/502) en lugar de propagarse como 500.

La versión de la aplicación se lee de un archivo `VERSION` en la raíz del proyecto como fuente única de verdad.

## 3. Criterios de Aceptación

- [x] Todos los endpoints están bajo el prefijo `/v1/graph`
- [x] `GET /v1/graph/sites` lista los sites accesibles con parámetro `search`
- [x] `GET /v1/graph/sites/{site_id}/lists` lista las listas de un site
- [x] `GET /v1/graph/sites/{site_id}/drives` lista las bibliotecas de documentos
- [x] `GET /v1/graph/sites/{site_id}/drives/{drive_id}/items` navega carpetas
- [x] `GET /v1/graph/sites/{site_id}/lists/{list_id}/items` lee ítems con paginación básica (`top`)
- [x] `POST /v1/graph/sites/{site_id}/lists/{list_id}/items` inserta un ítem
- [x] `POST /v1/graph/sites/{site_id}/drives/{drive_id}/files` sube archivo vía `multipart/form-data`
- [x] `GET /v1/graph/sites/{site_id}/drives/{drive_id}/items/{item_id}` devuelve metadatos con `download_url`
- [x] `GET /v1/graph/sites/{site_id}/drives/{drive_id}/items/{item_id}/download` descarga el binario
- [x] Cada respuesta de error incluye `X-Request-ID` en header y `request_id` en body
- [x] Cada respuesta exitosa incluye `X-Request-ID` en header
- [x] Fallo de autenticación con Azure AD → `GraphAPIError` 401, no 500
- [x] Fallo de red contra token endpoint → `GraphAPIError` 502, no 500
- [x] Logs en JSON estructurado con `request_id`, `client_app_id`, `duration_ms`
- [x] `app_version` se lee del archivo `VERSION` en la raíz del proyecto
- [x] Suite de tests unitarios con cobertura de los fixes críticos

## 4. Historial de Implementación

### v2.0.0 — 2026-06-09 — Claude Code (claude-sonnet-4-6 / claude-opus-4-8)

**Decisiones de diseño:**

- Las rutas siguen la estructura de Microsoft Graph API (`/sites/{id}/lists/{id}/items`) de forma intencional: el caller que ya conoce Graph API reconoce inmediatamente la semántica de cada endpoint sin consultar documentación adicional.
- `folder` en el endpoint de upload se declaró como `Query` de FastAPI (en lugar de leerse de `request.query_params`) para que aparezca en el schema OpenAPI y sea validado por el framework consistentemente con el resto de la API.
- `TokenManager` captura por separado `HTTPStatusError` y `RequestError` de httpx para distinguir un rechazo de Azure AD (401) de un fallo de red (502). Sin esta distinción, ambos casos producían 500.
- El middleware `request_logging_middleware` envuelve `await call_next(request)` en try/except para garantizar que incluso las excepciones no controladas queden registradas con `request_id` y `client_app_id` antes de propagarse.
- `raise_from_httpx` se anotó `-> NoReturn` para que el type checker entienda que los métodos `_get`/`_post`/`_put_bytes` nunca retornan `None` implícitamente tras el `except`.
- La clave `file_name` (en lugar de `filename`) en los logs de descarga evita colisión con el atributo interno `LogRecord.filename` que hubiera producido `KeyError` en el formatter.
- Se eliminaron las variables de configuración `default_site_url`, `default_list_name` y `default_drive_name` porque ningún endpoint las consumía. Mantenerlas generaba expectativas falsas en operadores nuevos.

**Archivos nuevos:**
- `app/core/auth.py`, `app/core/config.py`, `app/core/context.py`, `app/core/dependencies.py`
- `app/core/exceptions.py`, `app/core/logging.py`
- `app/api/__init__.py`, `app/api/v1/__init__.py`, `app/api/v1/router.py`
- `app/api/v1/endpoints/__init__.py`, `discovery.py`, `list_items.py`, `files.py`
- `app/schemas/__init__.py`, `discovery.py`, `files.py`, `list_items.py`
- `VERSION`
- `requirements-dev.txt`, `pytest.ini`
- `tests/conftest.py`, `test_health.py`, `test_auth.py`, `test_download.py`, `test_files_endpoint.py`, `test_error_handling.py`

**Archivos modificados:**
- `app/main.py` — middleware con trazabilidad de 500; exception handlers
- `app/services/sharepoint.py` — discovery, metadata, download; logging completo con `file_name`
- `devops/docker-compose.yml`, `devops/.env.example`, `devops/deploy.sh`
- `ARQUITECTURA.md`, `README.md`, `doc/CHANGELOG.md`

**Sin desviaciones del diseño propuesto.**
