# Diagramas de Arquitectura (PlantUML): SharePoint Connector

**Versión:** 2.8.0
**Fecha:** 2026-09-03
**Autor:** Juan Camilo López Alzate — Latinia

> Versión en **PlantUML** de los diagramas de [`ARQUITECTURA.md`](ARQUITECTURA.md).
> Cada bloque puede renderizarse en [plantuml.com](https://www.plantuml.com/plantuml),
> con la extensión *PlantUML* de VS Code, o con el CLI `plantuml diagrama.puml`.

---

## 2. Arquitectura actual

```plantuml
@startuml arquitectura-actual
left to right direction
skinparam componentStyle rectangle

package "Caller (cualquier sistema)" {
  component "HTTP Client" as C
}

package "sharepoint-connector (Docker)" {
  component "Middleware\nRequest ID · X-App-ID" as MW
  component "API Layer\n/v1/graph/..." as API
  component "Dependencies\nlru_cache singletons" as DEP
  component "SharePointService\nGraph API client" as SRV
  component "TokenManager\nOAuth2 cache" as AUTH
  component "Settings\npydantic-settings" as CFG
}

package "Azure AD" {
  component "Token Endpoint\nclient_credentials" as TK
}

package "Microsoft Graph API v1.0" {
  component "/sites — Discovery" as GS
  component "/sites/{id}/lists — List Items" as GL
  component "/sites/{id}/drives — Files" as GF
}

package "SharePoint" as SP {
  database "Biblioteca\nDocumentos" as DL
  database "Lista" as LST
}

C --> MW : HTTP + X-App-ID
MW --> API
API --> DEP
DEP --> SRV
DEP --> AUTH
AUTH --> TK : client_credentials
TK --> AUTH : Bearer token
SRV --> GS
SRV --> GL
SRV --> GF
GS --> SP
GL --> LST
GF --> DL
CFG --> AUTH
CFG --> SRV
@enduml
```

---

## 3. Componentes internos

```plantuml
@startuml componentes-internos
skinparam componentStyle rectangle

package "app/api/v1/endpoints" {
  component "discovery.py\nGET /sites\nGET /sites/{id}/lists\nGET /sites/{id}/drives\nGET /sites/{id}/drives/{id}/items" as EP_DISC
  component "list_items.py\nGET  /sites/{id}/lists/{id}/items\nPOST /sites/{id}/lists/{id}/items" as EP_LIST
  component "files.py\nPOST /sites/{id}/drives/{id}/files\nGET  /sites/{id}/drives/{id}/items/{id}\nGET  /sites/{id}/drives/{id}/items/{id}/download" as EP_FILE
  component "sharepoint.py (by URL)\nPOST  /sharepoint/list/item\nPATCH /sharepoint/list/item\nPOST  /sharepoint/list/item:upsert\nPOST  /sharepoint/list/items:search\nPOST  /sharepoint/upload" as EP_SP
}

package "app/core" {
  component "auth.py\nTokenManager" as AUTH
  component "config.py\nSettings" as CFG
  component "context.py\nContextVars" as CTX
  component "dependencies.py\nget_sp() / get_authorizer()" as DEP
  component "exceptions.py\nGraphAPIError / APIKeyError\nPermissionDeniedError" as EXC
  component "logging.py\nJSONFormatter" as LOG
  component "security.py\nrequire_principal\nrequire_admin / require_*_permission" as SEC
  component "api_keys.py\nApiKeyStore" as KEYS
  component "permissions.py\nPermissionStore / Authorizer" as PERM
}

database "SQLite (volumen)\napi_keys\napi_key_permissions" as DB

package "app/schemas" {
  component "discovery.py" as SCH_D
  component "files.py" as SCH_F
  component "list_items.py" as SCH_L
  component "sharepoint.py" as SCH_SP
}

package "app/services" {
  component "sharepoint.py\nSharePointService" as SRV
  component "resolver.py\nSharePointResolver" as RES
}

EP_DISC --> DEP
EP_LIST --> DEP
EP_FILE --> DEP
EP_SP --> DEP
EP_DISC --> SCH_D
EP_FILE --> SCH_F
EP_LIST --> SCH_L
EP_SP --> SCH_SP
EP_SP --> RES
EP_DISC --> SEC
EP_LIST --> SEC
EP_FILE --> SEC
EP_SP --> SEC
SEC --> KEYS
SEC --> PERM
KEYS --> DB
PERM --> DB
PERM --> EXC
RES --> SRV
DEP --> SRV
DEP --> AUTH
SRV --> CTX
SRV --> EXC
AUTH --> CFG
SRV --> CFG
@enduml
```

---

## 4. Capa de resolución de URL (`SharePointResolver`)

```plantuml
@startuml resolucion-url
left to right direction
skinparam componentStyle rectangle

component "URL SharePoint\n(navegador)" as URL
component "SharePointResolver" as R
component "site_id" as SID
component "list_id" as LID
component "drive_id + carpeta" as DID
component "SharePointService\ncreate_list_item / update_list_item /\nsearch_list_items (+ get_list_columns,\noperadores eq|in|ge|gt|le|lt) / upload_file" as SRV

URL --> R
R --> SID : GET /sites/host:/path\nrecorte hacia raíz
R --> LID : match webUrl / nombre
R --> DID : match prefijo webUrl drive
SID --> SRV
LID --> SRV
DID --> SRV
@enduml
```

---

## 4.b Validación de un filtro de `items:search` (v2.6.0)

> Los diagramas anteriores no descienden al detalle de un filtro, así que ninguno
> quedó obsoleto al añadir los operadores. Este se añade porque la validación sí
> gana un paso propio —«¿admite esta columna este operador?»— y porque la
> frontera `422`/`400` es el núcleo de este endpoint: `422` es lo que puede
> juzgarse con el body a solas, `400` lo que exige conocer la lista. Ninguna
> condición se delega a Graph.

```plantuml
@startuml validacion-filtro-search
skinparam defaultTextAlignment center

start
:Body del request;

partition "422 — schema (Pydantic), sin resolver la lista" {
  if (¿campo desconocido en el body?) then (sí)
    :422 — extra="forbid";
    stop
  else (no)
  endif
  if (¿`field` fuera de [A-Za-z0-9_]+?) then (sí)
    :422;
    stop
  else (no)
  endif
  if (¿`op` fuera de {eq, in, ge, gt, le, lt}?) then (sí)
    :422 — enum cerrado;
    stop
  else (no)
  endif
  if (¿la forma de `value` discrepa de `op`?) then (sí)
    :422 — `in` exige array,\nel resto un escalar;
    stop
  else (no)
  endif
  if (¿array de `in` vacío, >20\no de tipos mezclados?) then (sí)
    :422;
    stop
  else (no)
  endif
  if (¿>100 cláusulas tras expandir los `in`?) then (sí)
    :422 — indica el número generado\ny el límite;
    stop
  else (no)
  endif
}

:Resolver la URL → site_id + list_id;
:get_list_columns(site_id, list_id)\ncaché por (site_id, list_id), TTL 300 s\n(la validación de operadores no añade llamadas);

partition "400 — servicio, contra el esquema real de columnas" {
  :Revalidar `field` y `op`\n(defensa en profundidad);
  if (¿existe la columna?) then (no)
    :400 — nombra el campo;
    stop
  else (sí)
  endif
  if (¿lookup por su nombre base?) then (sí)
    :400 — debe usarse {Columna}LookupId;
    stop
  else (no)
  endif
  if (¿el tipo de la columna admite el `op`?) then (no)
    :400 — nombra columna, tipo y\noperadores válidos;
    stop
  else (sí)
  endif
  if (¿el tipo JSON corresponde al de la columna,\nen CADA elemento de un `in`?) then (no)
    :400 — un elemento malo\nrechaza el filtro entero;
    stop
  else (sí)
  endif
  if (¿columna de fecha con valor\nno parseable como ISO-8601?) then (sí)
    :400 — nombra la columna\ny el formato esperado;
    stop
  else (no)
  endif
}

:Construir la cláusula\neq/ge/gt/le/lt → fields/X op literal\nin → (fields/X eq a or fields/X eq b);
:Unir las cláusulas con " and " y percent-encodear\nla expresión completa como una sola unidad;
:GET .../items?$filter=...\nPrefer: HonorNonIndexedQueriesWarningMayFailRandomly;
stop
@enduml
```

---

## 5. Autenticación y seguridad

```plantuml
@startuml autenticacion
participant Caller
participant Connector
participant "Azure AD" as AAD
participant "Microsoft Graph" as Graph

Caller -> Connector : HTTP request + X-API-Key (+ X-App-ID)
Connector -> Connector : Asigna request_id (UUID)
Connector -> Connector : Valida X-API-Key (HMAC en SQLite) → principal
alt clave ausente / inválida / revocada
    Connector --> Caller : 401 (sin llamar a Graph) + auditoría
end
opt endpoint /v1/sharepoint (por URL)
    Connector -> Graph : resuelve URL → site_id / list_id / drive_id
end
Connector -> Connector : ¿permiso del principal sobre la lista? (SPEC-006)
alt sin permiso (o discovery sin is_admin)
    Connector --> Caller : 403 permissionDenied (sin operar en Graph) + auditoría
end
Connector -> Connector : ¿token en caché válido?
alt token expirado o no existe
    Connector -> AAD : POST /oauth2/v2.0/token\ngrant_type=client_credentials
    AAD --> Connector : access_token (1h TTL)
    Connector -> Connector : guarda token en memoria
end
Connector -> Graph : Llamada con Bearer token
Graph --> Connector : 200 / error
Connector --> Caller : respuesta + X-Request-ID
@enduml
```

---

## 6. Modelo de datos de claves y permisos (SPEC-005 / SPEC-006)

```plantuml
@startuml modelo-claves-permisos
hide circle
skinparam linetype ortho

entity "api_keys" as K {
  * principal : TEXT <<PK>>
  --
  * key_hash : TEXT <<UNIQUE>> (HMAC-SHA256)
  * active : INTEGER (1 = habilitada)
  * created_at : TEXT
  last_used : TEXT
  * is_admin : INTEGER (discovery)
}

entity "api_key_permissions" as P {
  * principal : TEXT <<PK, FK>>
  * site_id : TEXT <<PK>>
  * list_id : TEXT <<PK>>
  --
  drive_id : TEXT (solo bibliotecas)
  * list_url : TEXT (lectura humana)
  * can_read : INTEGER
  * can_create : INTEGER
  * can_update : INTEGER
  * can_delete : INTEGER
  * granted_at : TEXT
}

K ||--o{ P : "ON DELETE CASCADE"
@enduml
```
