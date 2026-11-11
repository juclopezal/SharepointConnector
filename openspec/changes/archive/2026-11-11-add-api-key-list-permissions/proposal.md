## Why

Desde SPEC-005 (v2.7.0) el conector sabe **quién** llama (`X-API-Key` → `principal`),
pero no limita **qué** puede hacer: cualquier clave válida alcanza todo lo que alcanza
el App Registration, que opera app-only con `Sites.ReadWrite.All` sobre todo el tenant.
Los permisos propios de SharePoint no aplican porque el servicio entra con una única
identidad de aplicación. Hace falta una capa de autorización propia que restrinja cada
clave a listas concretas y a operaciones concretas (SPEC-006).

## What Changes

- **Autorización por clave y lista.** Cada clave tiene, por lista (o biblioteca de
  documentos), cuatro permisos booleanos: leer, crear, actualizar y borrar.
- **Denegar por defecto. BREAKING:** una clave sin permisos asignados no accede a nada.
  Las claves existentes reciben `403` en todo `/v1/...` hasta que se les concedan
  permisos.
- **Permisos declarados por URL, evaluados por ID.** El administrador concede el permiso
  con la URL de SharePoint; el conector la resuelve a los IDs reales en ese momento y
  todas las comprobaciones se hacen contra esos IDs. Una misma regla cubre `/v1/sharepoint`
  (por URL) y `/v1/graph` (por IDs), y no se puede esquivar con variantes de la URL.
- **Bibliotecas de documentos = listas.** El permiso sobre una biblioteca cubre sus
  archivos (`upload`, `files`, `download`, navegación de carpetas).
- **Mapeo operación → permiso.** Lectura y búsqueda → leer; `POST` de ítem → crear;
  `PATCH` → actualizar; `:upsert` → crear **y** actualizar; subida de archivo → crear,
  y además actualizar si sobrescribe uno existente. `can_delete` se almacena aunque hoy
  no hay endpoint de borrado.
- **Discovery solo para administradores.** `GET /v1/graph/sites`, `/sites/{id}/lists` y
  `/sites/{id}/drives` exigen una clave marcada como administradora.
- **Respuesta `403` propia y distinguible** del `403` que propaga Graph: nombra el
  permiso que falta.
- **Auditoría de las denegaciones**: cada `403` por permisos queda registrado con el
  `principal`, la lista y el permiso requerido.
- **Herramienta de administración ampliada** (`app/admin/api_keys_admin.py`):
  `grant`, `revoke`, `permissions` y `rotate` (cambia la clave conservando permisos e
  historial).
- Sin cambios de rutas: no se renombra ningún endpoint.

## Capabilities

### New Capabilities
- `list-permissions`: autorización por clave a nivel de lista/biblioteca — modelo de
  permisos (leer/crear/actualizar/borrar), denegar por defecto, mapeo de cada endpoint a
  su permiso, restricción de discovery a administradores, respuesta `403` y
  administración de permisos (`grant`, `revoke`, `permissions`).

### Modified Capabilities
- `api-key-auth`: la rotación pasa a ser una operación atómica (`rotate`) que conserva
  permisos e historial; las claves adquieren el atributo de administrador.
- `request-audit-log`: además de los `401`, se registran las denegaciones `403` por
  permisos con el permiso requerido y la lista afectada.

## Impact

- **Código**: nuevo `app/core/permissions.py` (almacén y comprobación), dependencia de
  autorización en `app/core/security.py`/`dependencies.py`, comprobaciones en los
  endpoints de `app/api/v1/endpoints/` (`discovery`, `list_items`, `files`,
  `sharepoint`), nueva excepción en `app/core/exceptions.py`, campo nuevo en la whitelist
  de `app/core/logging.py`, comandos nuevos en `app/admin/api_keys_admin.py`.
- **Datos**: mismo archivo SQLite de SPEC-005; tabla nueva `api_key_permissions` y
  columna nueva `is_admin` en `api_keys` (migración automática e idempotente al arrancar).
- **API**: nuevo `403` en todos los endpoints `/v1/...`; sin cambios de rutas ni de
  cuerpos.
- **Despliegue**: cambio incompatible para los consumidores actuales — hay que conceder
  permisos a cada clave inmediatamente después de desplegar (local y docker-ag).
  Versión **2.8.0** (mismo criterio que v2.7.0; el CHANGELOG lo marca como BREAKING).
- **Docs**: `README.md`, `ARQUITECTURA.md`, `arquitecturasUML.md`, `doc/CHANGELOG.md`,
  `VERSION`, `requirements/SPEC-006_*.md`, y el contexto de `openspec/config.yaml` (aún
  dice que no existe autenticación de clientes).
