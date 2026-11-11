# request-audit-log Specification

## Purpose
Registro de auditoría por petición que liga `principal`, método, ruta y `status_code`, incluidos los intentos rechazados, sin exponer nunca la clave (SPEC-005, v2.7.0).
## Requirements
### Requirement: Identidad en el registro de cada petición

El registro estructurado de cada petición completada SHALL incluir el campo
`principal` resuelto por la autenticación, junto a los campos ya existentes
(`request_id`, `method`, `path`, `status_code`, `duration_ms`). El campo
`principal` SHALL añadirse a la whitelist de campos estructurados permitidos y
propagarse por el contexto de la petición.

#### Scenario: Petición autenticada

- **WHEN** una petición con clave válida se completa
- **THEN** la entrada de log de "petición completada" incluye `principal`,
  `method`, `path` y `status_code`

### Requirement: Registro de los intentos rechazados

El servicio SHALL registrar también las peticiones rechazadas por autenticación
(`401`), de modo que un uso indebido quede trazado. La entrada SHALL indicar la
ruta y el resultado de la validación.

#### Scenario: Intento con clave inválida

- **WHEN** una petición se rechaza con `401` por clave ausente, desconocida o
  revocada
- **THEN** se registra una entrada de log con `path` y `status_code` 401
- **AND** la entrada no contiene la clave recibida

### Requirement: La clave nunca se registra

Ninguna entrada de log SHALL contener la clave de API en claro ni su valor
recibido, en ninguna circunstancia (petición aceptada o rechazada).

#### Scenario: Revisión de los logs

- **WHEN** se revisan los logs de auditoría
- **THEN** no aparece ninguna clave de API en claro
- **AND** cada entrada puede correlacionarse con su petición mediante el
  `request_id` (devuelto también en la cabecera `X-Request-ID`)

### Requirement: Registro de las denegaciones por permisos

El servicio SHALL registrar cada petición denegada por autorización (`403`
`permissionDenied`) con el `principal`, `path`, `status_code` 403, los permisos
requeridos que faltaban (`permission`) y, cuando se conozcan, `site_id`, `list_id` o
`drive_id` de la lista afectada. El campo `permission` SHALL añadirse a la whitelist de
campos estructurados permitidos. La entrada NO SHALL contener la clave de API.

#### Scenario: Denegación por falta de permiso

- **WHEN** la clave `jdiaz` intenta un `PATCH /v1/sharepoint/list/item` sobre una lista
  sin `can_update`
- **THEN** se registra una entrada con `principal = "jdiaz"`, `status_code = 403`,
  `permission = "update"` y el `site_id`/`list_id` de la lista
- **AND** la entrada no contiene la clave recibida

#### Scenario: Denegación de discovery

- **WHEN** una clave no administradora llama a `GET /v1/graph/sites`
- **THEN** se registra una entrada con su `principal`, `status_code = 403` y
  `permission = "admin"`

