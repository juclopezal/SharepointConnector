# api-key-auth Specification

## Purpose
Autenticación de los clientes del conector mediante clave en la cabecera `X-API-Key`, validada contra un almacén de claves habilitadas (SPEC-005, v2.7.0). Resuelve el `principal` que identifica al llamante para la auditoría.
## Requirements
### Requirement: Clave obligatoria en cabecera

Todo endpoint protegido SHALL exigir una clave de API en la cabecera HTTP
`X-API-Key`. La cabecera SHALL declararse mediante `APIKeyHeader` para que
aparezca en la documentación OpenAPI. La clave nunca SHALL viajar en el cuerpo
ni en la URL de la petición.

#### Scenario: Petición sin la cabecera

- **WHEN** llega una petición a un endpoint protegido sin la cabecera `X-API-Key`
- **THEN** el servicio responde `401` y la petición no llega a Microsoft Graph
- **AND** la respuesta incluye `request_id` y `path`

#### Scenario: Cabecera presente pero vacía

- **WHEN** llega una petición con `X-API-Key` vacía
- **THEN** el servicio responde `401` sin tocar Graph

### Requirement: Validación contra el almacén de claves habilitadas

El servicio SHALL validar la clave recibida contra un almacén de claves
habilitadas. La comparación SHALL hacerse sobre el **HMAC-SHA256** de la clave
(usando el secreto de servidor configurado) y en **tiempo constante**. Solo las
filas con `active = 1` SHALL considerarse válidas.

#### Scenario: Clave habilitada

- **WHEN** la clave recibida corresponde (por su HMAC) a una fila `active = 1`
- **THEN** la petición se procesa con normalidad
- **AND** el servicio resuelve el `principal` asociado y lo deja disponible para
  el resto de la petición
- **AND** actualiza el `last_used` de esa clave

#### Scenario: Clave desconocida

- **WHEN** la clave recibida no coincide con ninguna fila del almacén
- **THEN** el servicio responde `401` sin tocar Graph

#### Scenario: Clave revocada

- **WHEN** la clave recibida coincide con una fila cuyo `active = 0`
- **THEN** el servicio responde `401` sin tocar Graph

### Requirement: Confidencialidad de la clave

El servicio SHALL almacenar únicamente el HMAC-SHA256 de cada clave, nunca la
clave en claro. La clave en claro NO SHALL aparecer en la base de datos, en los
logs ni en ninguna respuesta de la API.

#### Scenario: Inspección del almacén

- **WHEN** se inspecciona la base de datos de claves
- **THEN** cada fila contiene el HMAC de la clave y su `principal`, pero no la
  clave en claro

### Requirement: Alta y baja de claves sin redespliegue

El sistema SHALL permitir dar de alta, revocar (`active = 0`), reactivar y **rotar**
claves modificando el almacén, sin necesidad de reconstruir ni reiniciar el servicio.
La emisión de una clave SHALL generar un valor aleatorio, calcular su HMAC e
insertar la fila con su `principal`; el valor en claro SHALL entregarse una sola
vez al titular y no persistirse en el servidor.

La rotación (`rotate <principal>`) SHALL sustituir en una única operación el HMAC de
la clave de un `principal` existente por el de una clave nueva, conservando su fila
(`principal`, `created_at`, `is_admin`) y todos sus permisos de lista. Tras la rotación
la clave anterior SHALL dejar de ser válida de inmediato y la nueva SHALL mostrarse una
sola vez.

#### Scenario: Revocar una clave en caliente

- **WHEN** un administrador marca `active = 0` una clave en el almacén
- **THEN** la siguiente petición con esa clave recibe `401` sin que haya hecho
  falta redesplegar el servicio

#### Scenario: Rotar una clave conserva sus permisos

- **WHEN** se ejecuta `rotate jdiaz` sobre una clave con permisos sobre `Clientes`
- **THEN** la utilidad muestra una sola vez la clave nueva
- **AND** una petición con la clave anterior recibe `401`
- **AND** una petición con la clave nueva sobre `Clientes` se autoriza con los mismos
  permisos que tenía antes

#### Scenario: Rotar un principal inexistente

- **WHEN** se ejecuta `rotate` para un `principal` que no existe en el almacén
- **THEN** la utilidad termina con error y no crea ninguna fila

### Requirement: Atributo de administrador en las claves

Cada clave SHALL tener un atributo `is_admin` (por defecto `0`). Las claves existentes
antes de este cambio SHALL quedar con `is_admin = 0` tras la migración automática del
almacén, sin intervención manual y sin perder ningún dato.

#### Scenario: Migración de un almacén de v2.7.0

- **WHEN** el servicio arranca sobre un archivo SQLite creado por v2.7.0
- **THEN** la tabla `api_keys` conserva todas sus filas, gana la columna `is_admin`
  con valor `0` y se crea la tabla de permisos vacía
- **AND** arrancar de nuevo no vuelve a modificar el esquema

#### Scenario: Borrar una clave borra sus permisos

- **WHEN** un administrador borra la fila de una clave en `api_keys`
- **THEN** sus permisos de lista se eliminan con ella

