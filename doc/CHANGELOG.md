## v2.0.0 — 2026-06-09

### Refactor: Arquitectura dinámica multi-site con API versionada (SPEC-001)

**Contexto:** La v1.0.0 estaba acoplada a un site SharePoint fijo configurado mediante `SITE_URL`. Los endpoints eran planos (`POST /upload`, `POST /list`) sin versionado y la subida de archivos usaba Base64 en JSON. No había endpoints de discovery, ni descarga, ni metadatos. El logging era básico y los errores de autenticación se propagaban como 500 genérico.

**Solución:** Rediseño completo de la arquitectura hacia un servicio genérico y dinámico. El site, la lista y el drive se identifican por ID en cada llamada. Se introduce una API REST versionada bajo `/v1/graph/...` con 9 endpoints organizados en tres grupos (Discovery, List Items, Files). El logging pasa a JSON estructurado con `request_id` y `client_app_id` propagados a todo el stack.

**Archivos nuevos:**
- `app/core/auth.py` — `TokenManager` con manejo tipado de errores y logging de adquisición de token
- `app/core/config.py` — `Settings` con pydantic-settings, lee versión desde `VERSION`
- `app/core/context.py` — `ContextVar`s para `request_id` y `client_app_id`
- `app/core/dependencies.py` — singletons inyectables vía `lru_cache`
- `app/core/exceptions.py` — `GraphAPIError` tipado con handlers para FastAPI; `-> NoReturn`
- `app/core/logging.py` — `JSONFormatter` con whitelist de campos estructurados
- `app/api/v1/router.py` — router versionado bajo `/v1`
- `app/api/v1/endpoints/discovery.py` — 4 endpoints de exploración de sites/listas/drives/carpetas
- `app/api/v1/endpoints/list_items.py` — GET y POST de ítems de lista
- `app/api/v1/endpoints/files.py` — upload (`multipart/form-data`), metadata y download
- `app/schemas/discovery.py`, `files.py`, `list_items.py` — modelos Pydantic por dominio
- `VERSION` — fuente única de verdad para el número de versión
- `requirements-dev.txt`, `pytest.ini`, `tests/` — suite de tests unitarios (12 tests)

**Archivos eliminados (v1):**
- `app/auth.py`, `app/config.py`, `app/dependencies.py`, `app/models.py`
- `app/routers/upload.py`, `app/routers/list_item.py`

**Archivos modificados:**
- `app/main.py` — middleware con try/except para trazabilidad en 500; exception handlers registrados
- `app/services/sharepoint.py` — métodos de discovery, metadata, download; logging completo
- `devops/docker-compose.yml`, `devops/.env.example`, `devops/deploy.sh` — limpieza de config muerta
- `ARQUITECTURA.md`, `README.md` — documentación actualizada a v2.0.0

---

## v1.0.0 — 2026-05-19

### Feature: Microservicio inicial de integración con SharePoint

**Contexto:** La integración entre Jirito Newsletter y SharePoint se realizaba a través de Power Automate, con errores 408/429/5xx no visibles en logs y lógica distribuida entre código y flujos visuales externos.

**Solución:** Microservicio FastAPI con dos endpoints planos que reemplazaban los webhooks de Power Automate: `POST /upload` (archivo en Base64 + JSON) y `POST /list` (crear ítem en lista). Site único configurado en `SITE_URL`. `TokenManager` con caché de token OAuth2. Logging básico.

**Archivos:**
- `app/main.py`, `app/auth.py`, `app/config.py`, `app/dependencies.py`, `app/models.py`
- `app/routers/upload.py`, `app/routers/list_item.py`
- `app/services/sharepoint.py`
- `devops/Dockerfile`, `devops/docker-compose.yml`, `devops/.env.example`, `devops/deploy.sh`
