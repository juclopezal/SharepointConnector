# SPEC-002: Endpoints orientados a usuario con resolución por URL

## 1. Contexto (El Problema)

La arquitectura v2.0.0 (SPEC-001) expone una API dinámica donde el site, la lista y el drive se identifican por su ID de Microsoft Graph en cada llamada (`/v1/graph/sites/{site_id}/lists/{list_id}/items`, etc.). Esos identificadores son estables y eficientes para integraciones máquina-a-máquina, pero **inviables de obtener para un usuario humano**:

- El `site_id` tiene el formato `hostname,site-collection-guid,site-guid`.
- El `drive_id` es un identificador opaco (`b!...`).
- El `list_id` es un GUID.

Un usuario solo dispone de lo que ve en el navegador: la **URL** de la lista o de la biblioteca de documentos. Forzarle a ejecutar primero los endpoints de discovery para traducir esa URL a IDs es una barrera de uso real.

## 2. Propuesta (La Solución)

Añadir una **capa de resolución** (`SharePointResolver`) y dos endpoints orientados a usuario bajo `/v1/sharepoint` que aceptan la URL de SharePoint y derivan internamente los identificadores de Graph, delegando luego en los primitivos ya existentes del `SharePointService` (`create_list_item`, `upload_file`).

- `POST /v1/sharepoint/list/item` — JSON con `sharepoint_url` + `data`.
- `POST /v1/sharepoint/upload` — `multipart/form-data` con `sharepoint_url` + `file`.

Los endpoints `/v1/graph/...` de la v2.0.0 se conservan intactos como API de bajo nivel y discovery; ambas capas coexisten.

**Estrategia de resolución:**

- **Site:** `GET /sites/{host}:/{path}` con la ruta de la URL, recortando el último segmento hacia la raíz ante un `404`. El primer path que resuelve es el *web* más profundo que contiene el recurso. Cubre raíz, managed paths (`/Oper`), `/sites/{x}`, `/teams/{x}` y subsites, sin codificar cada patrón por separado. Los IDs de site se cachean (son estables durante la vida del proceso).
- **Lista:** segmento tras `/Lists/` emparejado contra las listas del site por `webUrl` (exacto) con fallback a `displayName`/`name`.
- **Biblioteca + carpeta:** la carpeta destino se toma del parámetro `?id=` (ruta servidor) o de la ruta de la URL sin la página de formulario. La biblioteca es el drive cuyo `webUrl` es el prefijo más largo de esa ruta; el remanente es la carpeta destino.

Las claves de `data` siguen siendo los **nombres internos** de columna (sin resolución display→internal), consistente con `fields` en `/v1/graph`.

## 3. Criterios de Aceptación

- [x] `POST /v1/sharepoint/list/item` crea un ítem a partir de la URL de una lista
- [x] `POST /v1/sharepoint/upload` sube un archivo a partir de la URL de una biblioteca/carpeta (`multipart/form-data`)
- [x] El site se resuelve para raíz, managed paths, `/sites/{x}`, `/teams/{x}` y subsites (recorte hacia la raíz)
- [x] La lista se empareja por `webUrl` con fallback a `displayName`/`name`
- [x] La biblioteca se empareja por el prefijo más largo de `webUrl`; la carpeta del `?id=` se respeta y se crea si no existe
- [x] Los IDs de site resueltos se cachean entre llamadas
- [x] URL sin `/Lists/` → `400`; lista/biblioteca no encontrada → `404`
- [x] Los endpoints `/v1/graph/...` de v2.0.0 permanecen sin cambios de comportamiento
- [x] `data` se envía a Graph tal cual (nombres internos)
- [x] Suite de tests cubre resolución (listas, upload, recorte de site, caché) y cableado de endpoints

## 4. Historial de Implementación

### v2.1.0 — 2026-06-10 — Claude Code (claude-opus-4-8)

**Decisiones de diseño:**

- **Resolución de site por recorte hacia la raíz** en lugar de codificar cada patrón de managed path. `GET /sites/{host}:/{path}` solo resuelve si el path es exactamente un *web*; empezar por la ruta más profunda y recortar garantiza encontrar el web contenedor real, cubriendo subsites sin lógica especial. El coste (N llamadas) se amortiza con caché de los IDs de site, que son estables.
- **Emparejado de lista/biblioteca por `webUrl`** (la ruta real del recurso en SharePoint) como criterio primario, porque es robusto frente a renombrados de `displayName`. Fallback a `displayName`/`name` para listas cuyo `webUrl` no encaja por codificación.
- **El parámetro `?id=` es la fuente autoritativa de la carpeta destino** en upload: SharePoint lo rellena con la ruta servidor exacta de la carpeta navegada, lo que evita ambigüedad entre biblioteca y subcarpeta.
- **Se reutilizan `create_list_item` y `upload_file` existentes**: el resolutor solo produce identificadores; la escritura no se duplica. `upload_file` ya auto-crea carpetas intermedias vía `PUT root:/{path}:/content`, comportamiento que se preserva.
- **`data` con nombres internos** (no display names): se mantiene consistencia con `/v1/graph` y se evita una resolución de columnas frágil (los internal names de columnas con acentos/espacios se codifican como `_x00f3_`, `_x0020_`).
- **Sin autenticación de entrada** en esta iteración (el `Bearer` de los ejemplos de uso es ilustrativo); se aborda por separado. El conector sigue autenticándose contra Graph con `client_credentials` (app-only).
- **`SharePointResolver` se registra como singleton** (`lru_cache` en `dependencies.get_resolver`) para que su caché de sites persista entre peticiones.

**Archivos nuevos:**
- `app/services/resolver.py` — `SharePointResolver`, dataclasses `ResolvedList` / `ResolvedUploadTarget`
- `app/api/v1/endpoints/sharepoint.py` — endpoints `/v1/sharepoint/list/item` y `/v1/sharepoint/upload`
- `app/schemas/sharepoint.py` — `ListItemByUrlRequest`, `ListItemByUrlResponse`, `UploadByUrlResponse`
- `tests/test_resolver.py`, `tests/test_sharepoint_endpoints.py`
- `requirements/SPEC-002_User_Facing_URL_Resolution.md`

**Archivos modificados:**
- `app/services/sharepoint.py` — método `get_site_by_path(hostname, path)`
- `app/core/dependencies.py` — `get_resolver()`
- `app/api/v1/router.py` — registro del router `sharepoint`
- `VERSION`, `doc/CHANGELOG.md`, `README.md`, `ARQUITECTURA.md`

**Sin desviaciones del diseño propuesto.**
