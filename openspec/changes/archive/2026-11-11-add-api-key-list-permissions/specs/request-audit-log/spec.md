## ADDED Requirements

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
