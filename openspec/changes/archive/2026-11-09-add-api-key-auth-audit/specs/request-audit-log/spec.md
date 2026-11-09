## ADDED Requirements

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
