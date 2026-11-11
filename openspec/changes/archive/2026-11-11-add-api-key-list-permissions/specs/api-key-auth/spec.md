## MODIFIED Requirements

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

## ADDED Requirements

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
