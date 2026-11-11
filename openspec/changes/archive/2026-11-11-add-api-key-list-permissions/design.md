## Context

SPEC-005 (v2.7.0) introdujo la autenticación por `X-API-Key`: `require_principal`
(`app/core/security.py`) se aplica a todo el router `/v1` y resuelve el `principal`
contra la tabla `api_keys` de un SQLite persistido en volumen (`ApiKeyStore`,
`app/core/api_keys.py`). No existe autorización: el App Registration opera app-only con
`Sites.ReadWrite.All`, así que toda clave válida alcanza todo el tenant. Los permisos de
SharePoint no ayudan porque Graph ve una sola identidad de aplicación.

La superficie tiene dos familias de endpoints:

- `/v1/sharepoint/...` — el caller envía `sharepoint_url`; el `SharePointResolver` la
  traduce a `site_id` + `list_id` (`resolve_list`, URLs con `/Lists/`) o a `site_id` +
  `drive_id` + carpeta (`resolve_upload_target`, URLs de biblioteca).
- `/v1/graph/...` — el caller envía IDs en la ruta (`site_id`, `list_id`, `drive_id`).
  Incluye discovery (`/sites`, `/sites/{id}/lists`, `/sites/{id}/drives`), que no apunta
  a ninguna lista concreta.

El servicio es un proceso único; los servicios son singletons vía `@lru_cache` en
`app/core/dependencies.py`.

## Goals / Non-Goals

**Goals:**
- Restringir cada clave a listas/bibliotecas concretas con cuatro permisos booleanos.
- Una sola regla que cubra las dos familias de endpoints, imposible de esquivar con
  variantes de URL.
- Denegar por defecto; revocación en caliente; auditoría de denegaciones.
- Administración por línea de comandos (`grant`, `revoke`, `permissions`, `rotate`).

**Non-Goals:**
- Permisos por carpeta, por ítem o por columna (la granularidad mínima es la lista).
- Interfaz gráfica de administración (proyecto aparte, posterior).
- Endpoint de borrado: `can_delete` se almacena pero ningún endpoint lo exige aún.
- Exponer la administración de permisos por la API HTTP.
- Cambiar rutas o contratos de los endpoints existentes.

## Decisions

### 1. Tabla `api_key_permissions` en el mismo SQLite

```
api_key_permissions(
  principal   TEXT NOT NULL REFERENCES api_keys(principal) ON DELETE CASCADE,
  site_id     TEXT NOT NULL,
  list_id     TEXT NOT NULL,
  drive_id    TEXT NULL,          -- solo bibliotecas de documentos
  list_url    TEXT NOT NULL,      -- URL con la que se concedió (lectura humana)
  can_read    INTEGER NOT NULL DEFAULT 0,
  can_create  INTEGER NOT NULL DEFAULT 0,
  can_update  INTEGER NOT NULL DEFAULT 0,
  can_delete  INTEGER NOT NULL DEFAULT 0,
  granted_at  TEXT NOT NULL,
  PRIMARY KEY (principal, site_id, list_id)
)
índice: (principal, site_id, drive_id)
```

`drive_id` se añade a lo acordado (principal, IDs, URL, 4 booleanos) para que los
endpoints `/v1/graph/.../drives/{drive_id}/...` se autoricen sin una llamada extra a
Graph por petición (drive → lista). `ON DELETE CASCADE` exige `PRAGMA foreign_keys = ON`
en cada conexión: SQLite lo trae desactivado por defecto.

*Alternativas:* archivo/BD separado (descartado: duplicaría volumen, ruta y migración);
permisos como JSON en `api_keys` (descartado: no indexable ni consultable por SQL).

### 2. Migración automática e idempotente al arrancar

`ApiKeyStore._ensure_schema` crea la tabla nueva con `CREATE TABLE IF NOT EXISTS` y añade
`api_keys.is_admin INTEGER NOT NULL DEFAULT 0` solo si `PRAGMA table_info(api_keys)` no
la tiene (SQLite no soporta `ADD COLUMN IF NOT EXISTS`). Sin script de migración manual.

### 3. Declarar por URL, evaluar por IDs

`grant` resuelve la URL **una vez**, al conceder, con el resolver existente:

- URL con `/Lists/` → `resolve_list` → `site_id`, `list_id`; `drive_id = NULL`.
- URL de biblioteca → `resolve_upload_target` → `site_id`, `drive_id`; `list_id` con
  `GET /sites/{site_id}/drives/{drive_id}/list`. Si la URL apunta a una subcarpeta, el
  permiso se concede sobre la biblioteca completa (la utilidad lo avisa).

En cada petición la comprobación compara IDs: nunca el texto de la URL. Así una misma fila
autoriza `/v1/sharepoint` (tras resolver la URL de la petición) y `/v1/graph` (IDs de la
ruta), y las variantes de URL (vista, mayúsculas, codificación) no producen decisiones
distintas. Se compara siempre el par completo (`site_id` + `list_id`, o `site_id` +
`drive_id`), nunca solo el ID de lista.

*Alternativa:* comparar la URL normalizada (descartado: frágil y no cubre `/v1/graph`).

### 4. Componente `PermissionStore` + `Authorizer`

- `app/core/permissions.py`: `PermissionStore` (lecturas SQLite, una conexión por
  operación como `ApiKeyStore`) y la lógica pura de decisión
  `check(principal, site_id, list_id|drive_id, required: set[Permission]) → missing`.
- Singleton en `app/core/dependencies.py` (`get_authorizer`), `None` en modo
  compatibilidad (decisión 9), sustituible en tests con `dependency_overrides`.
- `Permission` es un enum cerrado: `read`, `create`, `update`, `delete` (+ `admin` para
  discovery).

### 5. Punto de aplicación por familia de endpoints

- **`/v1/graph/...`**: dependencias de FastAPI declaradas por endpoint
  (`require_list_permission(Permission.READ)`, `require_drive_permission(...)`,
  `require_admin()`), que leen los path params y deniegan **antes** de cualquier llamada
  a Graph.
- **`/v1/sharepoint/...`**: el endpoint resuelve la URL (como hoy) y llama a
  `authorizer.require(...)` inmediatamente después, antes de validar columnas o escribir.
  No puede ser una dependencia porque los IDs solo existen tras la resolución.

El endpoint sigue sin lógica de negocio: delega la decisión en el `Authorizer` inyectado.

*Alternativa:* un middleware global que mapee ruta → permiso (descartado: tendría que
duplicar la resolución de URL y parsear bodies).

### 6. Mapeo operación → permiso

Fijado en la tabla del spec `list-permissions`. Casos con lógica:

- **Upsert** exige `create` **y** `update` antes de buscar `match`: el resultado
  (crear/actualizar) depende del estado de la lista, y una autorización que dependiera
  de él haría que la misma petición unas veces pasara y otras no.
- **Subida de archivos**: `PUT .../content` de Graph **reemplaza en silencio** un archivo
  existente (responde `200` en vez de `201`, sin error). Para no convertir un permiso de
  "crear" en uno de "sobrescribir", si la clave tiene `create` pero no `update`, el
  conector consulta antes si el archivo existe
  (`GET /sites/{site_id}/drives/{drive_id}/root:/{carpeta}/{nombre}`) y deniega con `403`
  si existe. Si la clave tiene ambos permisos no hay consulta previa (sin coste extra).
  Ruta y nombre ya pasan por el saneo existente contra `..`, separadores y caracteres de
  control antes de construir la URL.

### 7. Discovery solo para `is_admin = 1`

`require_admin()` en los tres endpoints de discovery. `is_admin` no otorga acceso a datos:
separa "ver qué existe" de "operar sobre ello". Se cambia por SQL
(`UPDATE api_keys SET is_admin = 1 WHERE principal = '...'`), documentado en
`--help-sql`.

### 8. Respuesta y auditoría

- Nueva `PermissionDeniedError` en `app/core/exceptions.py` + handler en `app/main.py`:
  `403`, `{"error": "Insufficient permissions for this API key", "detail": {"code":
  "permissionDenied", "required": ["update"]}, "request_id", "path"}`. No se incluyen
  los permisos que la clave sí tiene ni información de otras listas.
- Distinguible del `403` de Graph, que conserva su `detail.code` (`accessDenied`).
- Log `WARNING` "Permission denied" con `principal`, `permission`, `site_id`, `list_id`
  / `drive_id`. Se añade `permission` a `_EXTRA_FIELDS` (`app/core/logging.py`).

### 9. Modo compatibilidad

Si `API_KEY_HMAC_SECRET` está vacío, `get_authorizer` devuelve `None` y todas las
comprobaciones pasan (comportamiento v2.7.0).

> **Enmienda de implementación (2026-11-11):** en lugar de `None`, `get_authorizer`
> devuelve un autorizador nulo (`AllowAllAuthorizer`) que no deniega nada, para que
> los endpoints no necesiten ramas condicionales. Mismo comportamiento observable. Mantiene un único interruptor de seguridad
y no introduce variables de entorno nuevas.

### 10. Sin caché de permisos

Cada comprobación lee SQLite (consulta por clave primaria/índice, sub-milisegundo). Da
revocación en caliente igual que `api_keys`. No hay estado nuevo en memoria ni impacto de
concurrencia sobre los singletons.

### 11. Comandos de administración (`app/admin/api_keys_admin.py`)

- `grant <principal> <url> [--read] [--create] [--update] [--delete]`: exige al menos un
  flag y que el `principal` exista; resuelve la URL con Graph (usa `SharePointService` y
  `SharePointResolver` con las credenciales del `.env`, por eso solo funciona dentro del
  contenedor/venv con configuración completa); `INSERT ... ON CONFLICT DO UPDATE` que fija
  exactamente los flags indicados.
- `revoke <principal> <url>`: resuelve la URL y borra la fila.
- `permissions <principal>`: lista `list_url`, IDs y flags.
- `rotate <principal>`: genera clave nueva y hace `UPDATE api_keys SET key_hash = ?`
  sobre la fila existente (una transacción); falla si el `principal` no existe. Conserva
  `created_at`, `is_admin` y permisos.

### 12. Validaciones: 422 / 400 / 403

No cambia ningún schema Pydantic: el `422` sigue igual. El `400` del conector (URL no
reconocible, columna inexistente) mantiene su significado; el `403` se intercala entre la
resolución de URL y la validación contra el esquema de la lista (orden fijado en el
spec). No se construye ninguna expresión OData nueva: no aplican nuevas reglas
anti-inyección.

### 13. Compatibilidad y versionado

Rutas y cuerpos no cambian, pero denegar por defecto rompe a todo consumidor cuya clave
no tenga permisos → **cambio incompatible**. Versión **2.8.0** (decisión del usuario, 2026-11-11): se sigue el precedente de v2.7.0, que también introdujo un requisito incompatible (clave obligatoria) como versión menor. El CHANGELOG lo marca explícitamente como BREAKING.

### 14. Limitaciones conocidas

Sin cambios: siguen sin cursor de paginación, sin reintentos ante `429` y con el umbral
de vista de ~5000 filas.

## Risks / Trade-offs

- **[Corte al desplegar]** Tras desplegar, toda clave sin permisos recibe `403` → la
  utilidad `grant` solo existe en la imagen nueva: desplegar en ventana tranquila y
  ejecutar los `grant` inmediatamente (lista preparada de antemano).
- **[Fuga de existencia]** En `/v1/sharepoint`, la URL se resuelve antes de autorizar, así
  que una clave sin permisos puede distinguir "lista inexistente" (`404`) de "sin
  permiso" (`403`) → aceptado: no expone datos y el auditor registra el intento.
- **[Carrera en subida]** Entre la comprobación de existencia y el `PUT` otro proceso
  podría crear el archivo → ventana de milisegundos; aceptado.
- **[IDs obsoletos]** Si una lista se borra y se recrea con el mismo nombre, cambia su
  `list_id` y el permiso deja de aplicar (`403`) → falla del lado seguro; se re-concede.
- **[Coste Graph en `grant`]** La resolución consume llamadas a Graph → solo al
  administrar, nunca por petición.
- **[Granularidad]** Un permiso sobre una subcarpeta se convierte en permiso sobre la
  biblioteca entera → avisado por la utilidad; permisos por carpeta son Non-Goal.

## Migration Plan

1. Preparar la lista de consumidores y sus listas (clave → URL → permisos).
2. Desplegar 2.8.0 (local, luego docker-ag con `deploy.sh update --pull`). La migración
   del esquema es automática al arrancar.
3. Ejecutar inmediatamente `grant` por cada consumidor y marcar `is_admin` donde haga
   falta discovery.
4. Verificar con cada clave una petición permitida (`200`) y una no permitida (`403`).

**Rollback:** volver a la imagen 2.7.0. Las columnas/tablas nuevas son aditivas y v2.7.0
las ignora, así que el SQLite sigue siendo compatible.

## Open Questions

Ninguna pendiente.

- ~~¿Despliegue con corte breve o un interruptor temporal de "solo auditar, no
  bloquear"?~~ **Resuelta (2026-11-11, usuario):** corte breve. Se despliega en ventana
  tranquila y se ejecutan los `grant` inmediatamente (Migration Plan). No se añade modo
  "solo auditar" ni variable de entorno nueva.
