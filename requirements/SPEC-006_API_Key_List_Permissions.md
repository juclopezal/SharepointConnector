# SPEC-006: Permisos por clave de API a nivel de lista

> **Estado:** ✅ Implementada (v2.8.0). Change de OpenSpec:
> `openspec/changes/archive/2026-11-11-add-api-key-list-permissions/`. Pendiente solo la verificación
> real de subida a una biblioteca (ver Bitácora).

## 1. Contexto (El Problema)

SPEC-005 (v2.7.0) dio al conector **autenticación**: cada petición trae
`X-API-Key` y el servicio sabe **quién** llama (`principal`). Pero no limita
**qué** puede hacer: el App Registration opera app-only con `Sites.ReadWrite.All`
sobre todo el tenant, de modo que cualquier clave válida alcanza cualquier lista.
Los permisos de SharePoint no ayudan, porque Graph ve una única identidad de
aplicación y no al usuario que hay detrás de la clave.

Es la "Fase 2 — Autorización por usuario y lista" que SPEC-005 dejó fuera de
alcance.

## 2. Propuesta (La Solución)

### 2.1 Modelo
Tabla nueva `api_key_permissions` en el mismo SQLite de SPEC-005:

| columna      | descripción |
|--------------|-------------|
| `principal`  | clave a la que aplica (FK a `api_keys`, borrado en cascada) |
| `site_id`, `list_id` | IDs reales de la lista, resueltos al conceder |
| `drive_id`   | ID del drive si la lista es una biblioteca de documentos |
| `list_url`   | URL con la que se concedió (lectura humana) |
| `can_read`, `can_create`, `can_update`, `can_delete` | permisos booleanos |
| `granted_at` | fecha de concesión |

Columna nueva `api_keys.is_admin` (por defecto `0`). La migración es automática al
arrancar.

### 2.2 Reglas
- **Denegar por defecto:** una clave sin permisos sobre una lista no puede operar
  sobre ella → `403` con `detail.code = "permissionDenied"` y `detail.required`.
- **Declarar por URL, evaluar por IDs:** se concede con la URL de SharePoint; las
  comprobaciones comparan `site_id` + `list_id` (o `drive_id`), así que cubren
  `/v1/sharepoint` y `/v1/graph` y no se esquivan con variantes de la URL.
- **Bibliotecas = listas:** el permiso sobre una biblioteca cubre sus archivos.
- **Mapeo:** lectura/búsqueda/descarga → `read`; crear ítem → `create`; `PATCH`
  → `update`; `:upsert` → `create` y `update`; subida → `create`, y además
  `update` si el archivo ya existe (Graph lo sobrescribiría en silencio).
- **Discovery** (`/graph/sites`, `/sites/{id}/lists`, `/sites/{id}/drives`) solo
  para claves con `is_admin = 1`. Ser administrador no da acceso a datos.
- **Modo compatibilidad:** sin `API_KEY_HMAC_SECRET` no hay autenticación ni
  autorización (como en v2.7.0).

### 2.3 Administración
`app/admin/api_keys_admin.py`: `grant`, `revoke`, `permissions`, `rotate`
(cambia la clave conservando permisos); `is_admin` por SQL (`--help-sql`).

## 3. Criterios de Aceptación

1. Una clave sin permisos sobre una lista recibe `403` en cualquier endpoint que
   opere sobre ella, y la operación no llega a Graph.
2. Cada endpoint exige el permiso de la tabla del spec `list-permissions`; el
   upsert exige `create` y `update` antes de buscar `match`.
3. Una subida con `create` pero sin `update` sobre un archivo existente responde
   `403` y no sobrescribe el archivo.
4. La misma regla autoriza la lista vía `/v1/sharepoint` (URL) y `/v1/graph`
   (IDs); un `list_id` o `site_id` distinto deniega.
5. Discovery exige `is_admin = 1`; un administrador sin filas de lista no lee
   datos.
6. El `403` propio es distinguible del de Graph (`permissionDenied` vs
   `accessDenied`) y queda registrado con `principal`, `permission` e IDs.
7. `grant`/`revoke`/`permissions`/`rotate` funcionan según el spec; `rotate`
   invalida la clave anterior y conserva los permisos.
8. Un SQLite de v2.7.0 se migra al arrancar sin perder filas; la migración es
   idempotente.
9. Sin `API_KEY_HMAC_SECRET` el servicio se comporta como v2.7.0.
10. Tests `pytest` cubren 1–9 y la suite completa queda en verde.
11. Documentación viva, `CHANGELOG.md` (marcado como cambio incompatible) y
    `VERSION` (2.8.0) actualizados.

## 4. Bitácora de IA / Historial de Implementación

### 2026-11-11 — Agente LLM (implementación v2.8.0)

**Archivos nuevos**
- `app/core/permissions.py` — `Permission` (enum cerrado), DDL de
  `api_key_permissions`, `connect()` (activa `PRAGMA foreign_keys`),
  `PermissionStore` (lecturas del camino de petición + operaciones de
  administración), `Authorizer` (`require_admin`/`require_list`/`require_drive`/
  `require_upload`) y `AllowAllAuthorizer` (modo compatibilidad).
- `tests/test_permissions.py` (20), `tests/test_permissions_endpoints.py` (55),
  `tests/test_api_keys_admin.py` (17); +5 en `tests/test_sharepoint_service.py`.

**Archivos modificados**
- `app/core/api_keys.py` — `ensure_schema()` idempotente (añade `is_admin` si falta
  y crea la tabla de permisos), `db_path`.
- `app/core/dependencies.py` — `get_authorizer` (depende de `get_api_key_store`).
- `app/core/security.py` — dependencias `require_admin`,
  `require_list_permission`, `require_drive_permission` para `/v1/graph`.
- `app/core/exceptions.py` — `PermissionDeniedError` + handler (403
  `permissionDenied`, log de auditoría "Permission denied").
- `app/core/logging.py` — `permission` en `_EXTRA_FIELDS`.
- `app/main.py` — handler; instancia el almacén al arrancar (migración visible al
  inicio); aviso de modo compatibilidad ampliado.
- `app/services/sharepoint.py` — `drive_item_exists`, `get_drive_list_id`.
- Endpoints `discovery.py`, `list_items.py`, `files.py`, `sharepoint.py`.
- `app/admin/api_keys_admin.py` — `grant`, `revoke`, `permissions`, `rotate`;
  `issue` limpia permisos huérfanos; `--help-sql` ampliado.
- Docs: `README.md`, `ARQUITECTURA.md`, `arquitecturasUML.md`, `TECNOLOGIAS.md`,
  `openspec/config.yaml`, `doc/CHANGELOG.md`, `VERSION` (2.8.0).

**Decisiones clave**
- **Autorizador nulo en vez de `None`** (desviación menor del design.md, decisión 9):
  en modo compatibilidad `get_authorizer` devuelve `AllowAllAuthorizer`, así los
  endpoints llaman siempre al autorizador sin ramas condicionales.
- **`get_authorizer` depende de `get_api_key_store`**: comparte archivo e
  interruptor; sustituir el almacén en los tests arrastra la autorización real.
- **Auditoría en el handler del 403**: la excepción lleva los IDs y el handler
  registra `principal`, `path`, `permission` e IDs en una sola entrada; la
  respuesta no los devuelve.
- **`revoke` primero por la URL exacta guardada** (sin llamar a Graph; funciona
  aunque la lista ya no exista) y, si no hay coincidencia, resolviendo la URL.
- **`issue` borra permisos huérfanos** del principal: un borrado manual con el
  cliente `sqlite3` sin `PRAGMA foreign_keys` no aplica la cascada, y una clave
  nueva con el mismo nombre no debe heredar esos permisos.
- **Migración al arrancar** (no en la primera petición): `main.py` instancia el
  almacén si hay secreto.
- **Versión 2.8.0** (no 3.0.0) por decisión del usuario, por coherencia con v2.7.0;
  el CHANGELOG lo marca como "Requiere acción al desplegar".

**Verificación**
- Suite completa en verde: **354/354** `pytest` (257 previos + 97 nuevos).
- Contenedor local reconstruido (v2.8.0): migración de un SQLite real de v2.7.0
  (`jdiaz` conservado, `is_admin = 0`). Contra el tenant, con una clave temporal
  con solo `read` sobre la lista DSL: `items:search` → 200; `POST /list/item` y
  `POST /graph/.../items` → 403 `permissionDenied` y búsqueda posterior del título
  → `total = 0` (no se escribió nada); misma lista por IDs → 200, otro `list_id` →
  403; lista Incidencias → 403; discovery → 403; `grant` con otra vista y
  minúsculas → mismos IDs; `rotate` → clave vieja 401, nueva 200 con sus permisos;
  log "Permission denied" con `principal`, `permission`, `site_id`, `list_id`. La
  clave temporal se borró (cascada verificada: 0 filas restantes).
- **Pendiente:** verificación real de subida (8.3) y de `grant` con URL de
  biblioteca/subcarpeta (8.4) — requieren una biblioteca de pruebas y escribir
  archivos en el tenant; cubiertas con dobles en los tests.
