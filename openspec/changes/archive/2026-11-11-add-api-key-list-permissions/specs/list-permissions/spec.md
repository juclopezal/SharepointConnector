## ADDED Requirements

### Requirement: Modelo de permisos por clave y lista

El servicio SHALL autorizar cada petición a `/v1/...` según los permisos que la clave
del llamante (`principal`) tenga sobre la lista o biblioteca de documentos afectada.
Por cada par (clave, lista) existen cuatro permisos booleanos independientes:
`can_read`, `can_create`, `can_update` y `can_delete`. Una biblioteca de documentos
SHALL tratarse como una lista: el permiso concedido sobre ella cubre tanto sus ítems
como sus archivos.

#### Scenario: Clave con el permiso requerido

- **WHEN** la clave `jdiaz` tiene `can_read` sobre la lista `Clientes` y envía
  `POST /v1/sharepoint/list/items:search` con `sharepoint_url` de esa lista
- **THEN** la petición se procesa con normalidad y responde `200`

#### Scenario: Permiso sobre una biblioteca cubre sus archivos

- **WHEN** la clave tiene `can_read` sobre la biblioteca `Documentos` y envía
  `GET /v1/graph/sites/{site_id}/drives/{drive_id}/items/{item_id}/download` de un
  archivo de esa biblioteca
- **THEN** la descarga se procesa con normalidad

### Requirement: Denegar por defecto

Una clave SHALL poder operar únicamente sobre las listas para las que tenga permisos
concedidos explícitamente. Una clave sin ninguna fila de permisos, o sin fila para la
lista afectada, o con el permiso requerido a `false`, SHALL recibir `403` y la petición
NO SHALL llegar a ejecutar la operación en Microsoft Graph.

#### Scenario: Clave sin permisos asignados

- **WHEN** una clave válida y activa sin ninguna fila de permisos llama a cualquier
  endpoint `/v1/...`
- **THEN** el servicio responde `403`
- **AND** no se ejecuta ninguna lectura ni escritura de datos en Graph

#### Scenario: Clave con permisos sobre otra lista

- **WHEN** la clave tiene permisos sobre `Clientes` y envía
  `PATCH /v1/sharepoint/list/item` con `sharepoint_url` de la lista `Proveedores`
- **THEN** el servicio responde `403`

#### Scenario: Permiso presente pero a false

- **WHEN** la clave tiene sobre `Clientes` `can_read = true` y `can_create = false` y
  envía `POST /v1/sharepoint/list/item` a esa lista
- **THEN** el servicio responde `403` nombrando `create` como permiso requerido
- **AND** NO se crea el ítem

### Requirement: Permisos evaluados contra IDs, no contra el texto de la URL

La comprobación SHALL hacerse contra los identificadores reales (`site_id`, `list_id`
y, en bibliotecas, `drive_id`) resueltos al conceder el permiso. Cualquier forma de URL
que el conector resuelva a la misma lista (vista `AllItems.aspx`, otra vista, mayúsculas
distintas, codificación porcentual) SHALL producir la misma decisión, y los endpoints
`/v1/graph/...` que reciben IDs en la ruta SHALL quedar sujetos a las mismas reglas.

#### Scenario: Variante de URL de una lista permitida

- **WHEN** el permiso se concedió con `.../Lists/Clientes/AllItems.aspx` y la petición
  usa `.../lists/clientes/MiVista.aspx`, que el conector resuelve a la misma lista
- **THEN** la decisión de autorización es la misma que con la URL original

#### Scenario: Acceso por IDs a una lista no permitida

- **WHEN** la clave no tiene permisos sobre la lista con `list_id = L2` y envía
  `GET /v1/graph/sites/{site_id}/lists/L2/items`
- **THEN** el servicio responde `403` sin llamar a Graph

#### Scenario: site_id que no corresponde al permiso

- **WHEN** la clave tiene permiso sobre (`S1`, `L1`) y envía una petición con
  `site_id = S2` y `list_id = L1`
- **THEN** el servicio responde `403`

### Requirement: Mapeo de cada operación a su permiso

Cada endpoint SHALL exigir el permiso correspondiente a la operación que realiza:

| Endpoint | Permiso requerido |
|---|---|
| `GET /v1/graph/sites/{site_id}/lists/{list_id}/items` | `read` |
| `POST /v1/graph/sites/{site_id}/lists/{list_id}/items` | `create` |
| `GET /v1/graph/sites/{site_id}/drives/{drive_id}/items` | `read` |
| `GET /v1/graph/sites/{site_id}/drives/{drive_id}/items/{item_id}` | `read` |
| `GET /v1/graph/sites/{site_id}/drives/{drive_id}/items/{item_id}/download` | `read` |
| `POST /v1/graph/sites/{site_id}/drives/{drive_id}/files` | `create`; además `update` si el archivo ya existe |
| `POST /v1/sharepoint/list/item` | `create` |
| `PATCH /v1/sharepoint/list/item` | `update` |
| `POST /v1/sharepoint/list/item:upsert` | `create` **y** `update` |
| `POST /v1/sharepoint/list/items:search` | `read` |
| `POST /v1/sharepoint/upload` | `create`; además `update` si el archivo ya existe |

`can_delete` SHALL almacenarse y administrarse aunque hoy ningún endpoint lo exija.

#### Scenario: Upsert con solo permiso de crear

- **WHEN** la clave tiene `can_create = true` y `can_update = false` sobre la lista y
  envía `POST /v1/sharepoint/list/item:upsert`
- **THEN** el servicio responde `403` nombrando `update` como permiso requerido,
  independientemente de si el registro buscado por `match` existe o no
- **AND** no se ejecuta la búsqueda de `match` ni ninguna escritura

#### Scenario: Upsert con crear y actualizar

- **WHEN** la clave tiene `can_create` y `can_update` sobre la lista y envía un upsert
- **THEN** el servicio procesa el upsert con normalidad (`result` `created` o `updated`)

#### Scenario: Subida de un archivo nuevo con solo crear

- **WHEN** la clave tiene `can_create = true` y `can_update = false` sobre la biblioteca
  y sube `informe.pdf`, que no existe en la carpeta destino
- **THEN** el archivo se crea y la respuesta es `201`

#### Scenario: Subida que sobrescribiría sin permiso de actualizar

- **WHEN** la clave tiene `can_create = true` y `can_update = false` y sube
  `informe.pdf`, que ya existe en la carpeta destino
- **THEN** el servicio responde `403` nombrando `update` como permiso requerido
- **AND** NO sobrescribe en silencio el archivo existente (Graph lo reemplazaría sin
  error)

### Requirement: Discovery restringido a claves administradoras

Los endpoints de descubrimiento que no apuntan a una lista concreta
(`GET /v1/graph/sites`, `GET /v1/graph/sites/{site_id}/lists` y
`GET /v1/graph/sites/{site_id}/drives`) SHALL exigir una clave marcada como
administradora (`is_admin = 1`). Ser administrador NO SHALL conceder acceso a los datos
de ninguna lista: los permisos de lista se rigen solo por las filas concedidas.

#### Scenario: Discovery con clave no administradora

- **WHEN** una clave con `is_admin = 0` envía `GET /v1/graph/sites`
- **THEN** el servicio responde `403` sin llamar a Graph

#### Scenario: Discovery con clave administradora

- **WHEN** una clave con `is_admin = 1` envía `GET /v1/graph/sites/{site_id}/lists`
- **THEN** la petición se procesa con normalidad

#### Scenario: Administrador sin permisos de lista

- **WHEN** una clave con `is_admin = 1` y sin filas de permisos envía
  `POST /v1/sharepoint/list/items:search`
- **THEN** el servicio responde `403`

### Requirement: Respuesta 403 propia y distinguible de la de Graph

La denegación por permisos SHALL responder `403` con el envoltorio de error estándar
del servicio (`error`, `detail`, `request_id`, `path`). `detail.code` SHALL ser
`permissionDenied` y `detail` SHALL nombrar los permisos requeridos que faltan
(`required`). Un `403` devuelto por Microsoft Graph SHALL seguir propagándose con su
propio `detail` (p. ej. `code = accessDenied`), de modo que el llamante distinga ambos
casos. La respuesta NO SHALL revelar qué permisos tiene la clave sobre otras listas.

#### Scenario: Forma de la denegación

- **WHEN** una petición se deniega por falta de `update`
- **THEN** la respuesta es `403` con `detail.code = "permissionDenied"`,
  `detail.required = ["update"]`, `request_id` y `path`

#### Scenario: 403 de Graph con clave autorizada

- **WHEN** la clave tiene el permiso requerido pero Graph responde `403 accessDenied`
- **THEN** el servicio propaga `403` con el `detail` de Graph (`code = accessDenied`),
  no con `permissionDenied`

### Requirement: Orden de las comprobaciones

El servicio SHALL aplicar las comprobaciones en este orden: validación del body (`422`),
autenticación (`401`), resolución de la URL de SharePoint cuando aplique (`400`/`404`),
autorización (`403`), validación contra el esquema real de la lista (`400`) y, por
último, la operación en Graph. Ninguna escritura SHALL ejecutarse antes de superar la
autorización.

#### Scenario: Clave inválida y sin permisos

- **WHEN** la petición trae una clave revocada
- **THEN** el servicio responde `401`, no `403`

#### Scenario: Body inválido

- **WHEN** la petición trae un body que Pydantic rechaza
- **THEN** el servicio responde `422` antes de evaluar permisos

#### Scenario: Columna inexistente sin permiso

- **WHEN** la clave no tiene `can_update` y el `data` de un `PATCH` nombra una columna
  inexistente
- **THEN** el servicio responde `403`, no `400`

### Requirement: Revocación de permisos en caliente

Los cambios de permisos SHALL aplicar desde la siguiente petición, sin reiniciar ni
redesplegar el servicio.

#### Scenario: Retirar un permiso

- **WHEN** un administrador retira `can_read` a una clave sobre una lista
- **THEN** la siguiente petición de lectura de esa clave sobre esa lista recibe `403`

### Requirement: Autorización deshabilitada en modo compatibilidad

Cuando la autenticación está deshabilitada (sin `API_KEY_HMAC_SECRET`, modo
compatibilidad de SPEC-005), la autorización SHALL estar también deshabilitada y el
servicio SHALL comportarse como en v2.7.0.

#### Scenario: Sin secreto configurado

- **WHEN** `API_KEY_HMAC_SECRET` está vacío y llega cualquier petición a `/v1/...`
- **THEN** la petición se procesa sin comprobar permisos

### Requirement: Administración de permisos

La utilidad de administración SHALL ofrecer:

- `grant <principal> <sharepoint_url> [--read] [--create] [--update] [--delete]`:
  resuelve la URL a sus IDs en ese momento y fija exactamente los permisos indicados
  para ese par (clave, lista), creando la fila o sustituyendo la existente. La URL
  original SHALL guardarse para lectura humana.
- `revoke <principal> <sharepoint_url>`: elimina todos los permisos de esa clave sobre
  esa lista.
- `permissions <principal>`: muestra las listas y permisos de una clave.

El atributo de administrador SHALL poder cambiarse con una sentencia SQL documentada
(`UPDATE api_keys SET is_admin = ...`), como el resto de operaciones que no requieren
resolver URLs ni calcular HMAC.

#### Scenario: Conceder con URL no reconocible

- **WHEN** se ejecuta `grant` con una URL que el conector no sabe resolver
- **THEN** la utilidad termina con error y no inserta ninguna fila

#### Scenario: Conceder a una clave inexistente

- **WHEN** se ejecuta `grant` para un `principal` que no existe en `api_keys`
- **THEN** la utilidad termina con error y no inserta ninguna fila

#### Scenario: Conceder sin ningún permiso

- **WHEN** se ejecuta `grant` sin `--read`, `--create`, `--update` ni `--delete`
- **THEN** la utilidad termina con error indicando que debe darse al menos un permiso

#### Scenario: Re-conceder con menos permisos

- **WHEN** una clave tiene `read` y `update` sobre una lista y se ejecuta `grant` con
  solo `--read`
- **THEN** la clave queda solo con `read` sobre esa lista
