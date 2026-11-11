## 1. Spec numerada

- [x] 1.1 Crear `requirements/SPEC-006_API_Key_List_Permissions.md` (Contexto, Propuesta, Criterios de Aceptación, Bitácora de IA vacía) a partir de este change

## 2. Schemas y modelo de datos

- [x] 2.1 `app/core/permissions.py`: enum `Permission` (`read`, `create`, `update`, `delete`, `admin`) y DDL de `api_key_permissions` con `drive_id` e índice (design.md decisión 1)
- [x] 2.2 `app/core/api_keys.py`: `_ensure_schema` idempotente — crear tabla de permisos y añadir `is_admin` solo si falta (`PRAGMA table_info`); activar `PRAGMA foreign_keys = ON` en cada conexión (decisión 2)
- [x] 2.3 `app/core/exceptions.py`: `PermissionDeniedError` (403) con `required`; handler en `app/main.py` con el envoltorio estándar y `detail.code = "permissionDenied"` (decisión 8)
- [x] 2.4 `app/core/logging.py`: añadir `permission` a `_EXTRA_FIELDS`

## 3. Servicio de autorización

- [x] 3.1 `app/core/permissions.py`: `PermissionStore` (lectura por `(principal, site_id, list_id)` y `(principal, site_id, drive_id)`; `is_admin`) y `Authorizer.require(...)` que lanza `PermissionDeniedError` y registra la denegación con `principal`, `permission`, `site_id`, `list_id`/`drive_id` (decisiones 4, 8, 10)
- [x] 3.2 `app/core/dependencies.py`: singleton `get_authorizer` (`@lru_cache`), `None` en modo compatibilidad (decisión 9)
- [x] 3.3 `app/core/security.py`: dependencias `require_list_permission`, `require_drive_permission`, `require_admin` que leen los path params (decisión 5)
- [x] 3.4 `app/services/sharepoint.py`: método de comprobación de existencia de archivo por ruta (`GET .../root:/{ruta}`, 404 → no existe), reutilizando el saneo de ruta/nombre existente (decisión 6)

## 4. Endpoints

- [x] 4.1 `app/api/v1/endpoints/discovery.py`: `require_admin` en `/sites`, `/sites/{site_id}/lists`, `/sites/{site_id}/drives`; `require_drive_permission(READ)` en `/sites/{site_id}/drives/{drive_id}/items` (decisión 7)
- [x] 4.2 `app/api/v1/endpoints/list_items.py`: `READ` en `GET` y `CREATE` en `POST` de `/sites/{site_id}/lists/{list_id}/items`
- [x] 4.3 `app/api/v1/endpoints/files.py`: `READ` en metadatos y descarga; en subida `CREATE` y, si falta `UPDATE`, comprobación de existencia previa → 403 si existe
- [x] 4.4 `app/api/v1/endpoints/sharepoint.py`: `authorizer.require` tras resolver la URL y antes de validar columnas/escribir — `create` en `POST /list/item`, `update` en `PATCH /list/item`, `create`+`update` en `:upsert` (antes de buscar `match`), `read` en `items:search`, `create` (+existencia/`update`) en `/upload`

## 5. Administración

- [x] 5.1 `app/admin/api_keys_admin.py`: `grant` (≥1 flag, principal existente, resolución de URL lista/biblioteca, `list_id` de biblioteca vía `GET /drives/{drive_id}/list`, aviso si es subcarpeta, upsert que fija exactamente los flags) (decisión 11)
- [x] 5.2 `app/admin/api_keys_admin.py`: `revoke`, `permissions` y `rotate` (UPDATE del hash en una transacción, error si no existe el principal)
- [x] 5.3 `app/admin/api_keys_admin.py`: ampliar `--help-sql` con `is_admin` y consultas de permisos; actualizar docstrings de `app/core/api_keys.py` (la rotación ya no es "revocar + alta")

## 6. Tests

- [x] 6.1 `tests/test_permissions.py`: lógica de decisión — sin filas, otra lista, flag a false, par `site_id`/`list_id` cruzado, biblioteca por `drive_id`, admin sin filas de lista
- [x] 6.2 `tests/test_permissions.py`: migración desde un SQLite de v2.7.0 (filas conservadas, `is_admin = 0`, idempotente) y `ON DELETE CASCADE`
- [x] 6.3 `tests/test_permissions_endpoints.py`: 403 por cada endpoint de la tabla del spec con forma `permissionDenied` + `required`, y 200 con el permiso; Graph no se llama al denegar
- [x] 6.4 `tests/test_permissions_endpoints.py`: upsert con solo `create` → 403 sin buscar `match`; subida con solo `create` sobre archivo existente → 403 sin `PUT`, sobre archivo nuevo → 201
- [x] 6.5 `tests/test_permissions_endpoints.py`: orden 422 → 401 → 403 → 400 (columna inexistente sin permiso → 403); 403 de Graph se propaga con `accessDenied`
- [x] 6.6 `tests/test_permissions_endpoints.py`: discovery con y sin `is_admin`; modo compatibilidad (sin secreto) sin comprobación de permisos
- [x] 6.7 `tests/test_permissions_endpoints.py`: log de denegación con `principal`, `permission`, IDs y sin la clave
- [x] 6.8 `tests/test_api_keys_admin.py`: `grant` (sin flags, principal inexistente, URL no reconocible, re-grant con menos flags), `revoke`, `permissions`, `rotate` (clave vieja 401, nueva conserva permisos, principal inexistente)
- [x] 6.9 Ajustar los tests existentes (`tests/test_sharepoint_endpoints.py`, `tests/test_files_endpoint.py`, `tests/test_download.py`, `tests/conftest.py`) para que su doble de autorización conceda permisos
- [x] 6.10 Ejecutar `pytest` completo y confirmar que no hay regresiones

## 7. Documentación

- [x] 7.1 `README.md`: sección de permisos (modelo, `grant`/`revoke`/`permissions`/`rotate`, `is_admin`, 403) y tabla de endpoints con permiso requerido
- [x] 7.2 `ARQUITECTURA.md` y `arquitecturasUML.md`: flujo 401 → resolución → 403 y modelo de datos
- [x] 7.3 `openspec/config.yaml`: actualizar el contexto (ya existe autenticación y autorización de clientes)
- [x] 7.4 `VERSION` → `2.8.0` y entrada en `doc/CHANGELOG.md` marcando el cambio incompatible y el procedimiento de despliegue
- [x] 7.5 `requirements/SPEC-006_*.md`: estado `✅ Implementada` y Bitácora de IA

## 8. Verificación empírica (tenant real)

- [x] 8.1 Con una clave con `read` sobre una lista de `https://latinia2com-portal8.sharepoint.com/Oper/...`: `items:search` → 200; `POST /list/item` → 403. Bug bloqueante: cualquier escritura que llegue a la lista
- [x] 8.2 La misma clave vía `/v1/graph/sites/{site_id}/lists/{list_id}/items` con los IDs de esa lista → 200, con el `list_id` de otra lista → 403. Bug bloqueante: 200 sobre la lista no concedida
- [ ] 8.3 Biblioteca con solo `create`: subir archivo nuevo → 201; subirlo otra vez → 403 y el archivo en SharePoint conserva su versión anterior. Bug bloqueante: el archivo queda reemplazado — **PENDIENTE**: no ejecutada; escribe archivos en una biblioteca real del tenant y requiere autorización del usuario y una biblioteca de pruebas (cubierta con dobles en `tests/test_permissions_endpoints.py`)
- [ ] 8.4 `grant` con URL de vista distinta y con URL de subcarpeta resuelve a los mismos IDs que la URL de la lista/biblioteca. Bug bloqueante: IDs distintos para la misma lista — **PARCIAL** (2026-11-11): verificado con la lista DSL (`.../Lists/DSL/Allitemsg.aspx` y `.../oper/lists/dsl/AllItems.aspx` → mismos IDs); falta una URL de biblioteca/subcarpeta real
- [x] 8.5 `rotate` sobre una clave con permisos: la vieja → 401, la nueva conserva los permisos

## 9. Despliegue (seguimiento manual)

- [ ] 9.1 Preparar la lista de consumidores → listas → permisos antes de desplegar
- [ ] 9.2 Docker local: `docker compose -f devops/docker-compose.yml up -d --build`, ejecutar `grant` por consumidor y verificar `/health` = 2.8.0
- [ ] 9.3 docker-ag: push a `origin/main` (GitHub), `sh deploy.sh update --pull` desde `devops/`, ejecutar los `grant` inmediatamente y verificar 200/403 con cada clave
