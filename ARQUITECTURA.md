# Arquitectura: SharePoint Connector

**Versión:** 2.6.1  
**Fecha:** 2026-09-03  
**Autor:** Juan Camilo López Alzate — Latinia  

---

## 1. Contexto y motivación

La integración original entre **Jirito Newsletter** y **SharePoint** se realizaba a través de **Power Automate**, con dos limitaciones principales:

- Errores transitorios (408, 429, 5xx) no visibles en los logs, sin trazabilidad de fallos.
- Lógica de negocio fragmentada entre código y flujos visuales en plataforma externa.

**SharePoint Connector** elimina esta dependencia implementando la integración directamente sobre la **Microsoft Graph API**. La versión 2.0.0 amplía el alcance del servicio: en lugar de estar acoplado a un site concreto, expone una API genérica que opera sobre cualquier site, lista y biblioteca de documentos identificados dinámicamente en cada llamada.

---

## 2. Arquitectura actual

```mermaid
graph LR
    subgraph Caller["Caller (cualquier sistema)"]
        C[HTTP Client]
    end

    subgraph SPC["sharepoint-connector (Docker)"]
        MW[Middleware\nRequest ID · X-App-ID]
        API["API Layer\n/v1/graph/..."]
        DEP[Dependencies\nlru_cache singletons]
        SRV[SharePointService\nGraph API client]
        AUTH[TokenManager\nOAuth2 cache]
        CFG[Settings\npydantic-settings]
    end

    subgraph AAD["Azure AD"]
        TK[Token Endpoint\nclient_credentials]
    end

    subgraph GRAPH["Microsoft Graph API v1.0"]
        GS["/sites — Discovery"]
        GL["/sites/{id}/lists — List Items"]
        GF["/sites/{id}/drives — Files"]
    end

    subgraph SP["SharePoint"]
        DL[(Biblioteca\nDocumentos)]
        LST[(Lista)]
    end

    C -->|HTTP + X-App-ID| MW
    MW --> API
    API --> DEP
    DEP --> SRV
    DEP --> AUTH
    AUTH -->|client_credentials| TK
    TK -->|Bearer token| AUTH
    SRV --> GS
    SRV --> GL
    SRV --> GF
    GS --> SP
    GL --> LST
    GF --> DL
    CFG --> AUTH
    CFG --> SRV
```

---

## 3. Componentes internos

```mermaid
graph TD
    subgraph "app/api/v1/endpoints"
        EP_DISC["discovery.py\nGET /sites\nGET /sites/{id}/lists\nGET /sites/{id}/drives\nGET /sites/{id}/drives/{id}/items"]
        EP_LIST["list_items.py\nGET  /sites/{id}/lists/{id}/items\nPOST /sites/{id}/lists/{id}/items"]
        EP_FILE["files.py\nPOST /sites/{id}/drives/{id}/files\nGET  /sites/{id}/drives/{id}/items/{id}\nGET  /sites/{id}/drives/{id}/items/{id}/download"]
        EP_SP["sharepoint.py (by URL)\nPOST  /sharepoint/list/item\nPATCH /sharepoint/list/item\nPOST  /sharepoint/list/item:upsert\nPOST  /sharepoint/list/items:search\nPOST  /sharepoint/upload"]
    end

    subgraph "app/core"
        AUTH["auth.py\nTokenManager"]
        CFG["config.py\nSettings"]
        CTX["context.py\nContextVars"]
        DEP["dependencies.py\nget_sp()"]
        EXC["exceptions.py\nGraphAPIError"]
        LOG["logging.py\nJSONFormatter"]
    end

    subgraph "app/schemas"
        SCH_D["discovery.py"]
        SCH_F["files.py"]
        SCH_L["list_items.py"]
        SCH_SP["sharepoint.py"]
    end

    subgraph "app/services"
        SRV["sharepoint.py\nSharePointService"]
        RES["resolver.py\nSharePointResolver"]
    end

    EP_DISC & EP_LIST & EP_FILE & EP_SP --> DEP
    EP_DISC --> SCH_D
    EP_FILE --> SCH_F
    EP_LIST --> SCH_L
    EP_SP --> SCH_SP
    EP_SP --> RES
    RES --> SRV
    DEP --> SRV
    DEP --> AUTH
    SRV --> CTX
    SRV --> EXC
    AUTH --> CFG
    SRV --> CFG
```

### Descripción de módulos

| Módulo | Archivo | Responsabilidad |
|---|---|---|
| **FastAPI app** | `app/main.py` | Punto de entrada, middleware de logging, exception handlers |
| **Config** | `app/core/config.py` | Variables de entorno validadas con pydantic-settings |
| **TokenManager** | `app/core/auth.py` | Obtención y caché del token OAuth2 (client_credentials) |
| **Dependencies** | `app/core/dependencies.py` | Singletons inyectables via `lru_cache` (FastAPI DI) |
| **Exceptions** | `app/core/exceptions.py` | `GraphAPIError` y handlers de error para FastAPI |
| **Logging** | `app/core/logging.py` | `JSONFormatter` para logs estructurados en JSON |
| **Context** | `app/core/context.py` | `ContextVar`s para `request_id` y `client_app_id` |
| **Router v1** | `app/api/v1/router.py` | Agrupador de endpoints bajo prefijo `/v1` |
| **Discovery** | `app/api/v1/endpoints/discovery.py` | Endpoints de exploración (sites, listas, drives, carpetas) |
| **List Items** | `app/api/v1/endpoints/list_items.py` | Lectura y creación de ítems en listas |
| **Files** | `app/api/v1/endpoints/files.py` | Subida, metadata y descarga de archivos |
| **SharePoint (by URL)** | `app/api/v1/endpoints/sharepoint.py` | Endpoints orientados a usuario: crear / actualizar / *upsert* / búsqueda multi-campo de ítems y subir archivo a partir de una URL |
| **Schemas** | `app/schemas/` | Modelos Pydantic de request/response por dominio |
| **SharePointService** | `app/services/sharepoint.py` | Cliente HTTP de Graph API (GET, POST, PATCH, PUT, descarga, búsqueda y actualización de ítems, resolución de site, esquema de columnas con caché TTL) |
| **Period** | `app/services/period.py` | Traducción del `period` del upsert (atajo con nombre o rango explícito) a un intervalo UTC, calculado en la zona horaria del tenant |
| **SharePointResolver** | `app/services/resolver.py` | Traducción de URLs de SharePoint a `site_id`/`list_id`/`drive_id`+carpeta |

---

## 4. API

El conector expone dos niveles de API bajo `/v1`:

- **`/v1/sharepoint`** — orientada a usuario. El caller pasa la **URL de SharePoint** y el conector resuelve internamente los identificadores de Graph mediante `SharePointResolver`.
- **`/v1/graph`** — de bajo nivel. Opera con `site_id`/`list_id`/`drive_id` explícitos (obtenidos vía discovery).

### Capa de resolución de URL (`SharePointResolver`)

`SharePointResolver` traduce una URL "humana" de SharePoint a los identificadores que Graph necesita:

1. **Site:** se intenta `GET /sites/{host}:/{path}` con la ruta de la URL y, ante un `404`, se recorta el último segmento y se reintenta hacia la raíz. El primer path que resuelve es el *web* más profundo que contiene el recurso. Cubre de forma uniforme la raíz, managed paths (`/Oper`), `/sites/{x}`, `/teams/{x}` y subsites anidados. Los IDs de site resueltos se cachean (son estables).
2. **Lista** (`POST`, `PATCH`, `POST .../list/item:upsert` y `POST .../list/items:search`): se localiza el segmento tras `/Lists/` y se empareja contra las listas del site por `webUrl` (coincidencia exacta) con fallback a `displayName`/`name`. La misma resolución sirve para crear (`POST`), actualizar (`PATCH`), el upsert y la búsqueda.
3. **Biblioteca y carpeta** (`POST /v1/sharepoint/upload`): la carpeta destino se toma del parámetro `?id=` (ruta servidor) o de la propia ruta sin la página de formulario. La biblioteca es el drive cuyo `webUrl` es el prefijo más largo de esa ruta; el resto es la carpeta destino (que se crea automáticamente al subir).

```mermaid
graph LR
    URL["URL SharePoint<br/>(navegador)"] --> R[SharePointResolver]
    R -->|GET /sites/host:/path<br/>recorte hacia raíz| SID[site_id]
    R -->|match webUrl / nombre| LID[list_id]
    R -->|match prefijo webUrl drive| DID[drive_id + carpeta]
    SID & LID & DID --> SRV[SharePointService<br/>create_list_item / update_list_item / upload_file]
```

### SharePoint (por URL)

#### `POST /v1/sharepoint/list/item`

Inserta un ítem en la lista identificada por su URL.

**Request:**
```json
{
  "sharepoint_url": "https://host.sharepoint.com/Oper/Lists/Incidencias/View.aspx",
  "data": { "Title": "LATSUP-6585", "Prioridad": "Alta" }
}
```

> Las claves de `data` son los **nombres internos** de las columnas (igual que `fields` en `/v1/graph`).

**Response 201:**
```json
{ "status": "created", "id": "42", "webUrl": "https://...", "site_id": "...", "list_id": "..." }
```

#### `PATCH /v1/sharepoint/list/item`

Actualiza un ítem existente de la lista identificada por su URL. El registro a modificar se localiza con `filter_by` (`field` + `value`), que debe identificar un **único** ítem: el conector lo busca vía Graph para obtener su `id` interno y después aplica `data`.

**Request:**
```json
{
  "sharepoint_url": "https://host.sharepoint.com/Oper/Lists/Incidencias/View.aspx",
  "filter_by": { "field": "_x006c_dq4", "value": "LATSUP-0000" },
  "data": { "Title": "Actualización", "y4ap": "Baja", "Atendida": false }
}
```

> Tanto `filter_by.field` como las claves de `data` son los **nombres internos** de las columnas. `filter_by.value` se trata como texto.

La búsqueda usa `GET .../items?$expand=fields&$filter=fields/{field} eq '{value}'` con la cabecera `Prefer: HonorNonIndexedQueriesWarningMayFailRandomly` (las columnas custom no suelen estar indexadas). La actualización es `PATCH .../items/{id}/fields` con el conjunto de campos sin envolver.

| Resultado de `filter_by` | Respuesta |
|---|---|
| 1 coincidencia | `200` — ítem actualizado |
| 0 coincidencias | `404` — ningún registro coincide |
| > 1 coincidencia | `409` — el filtro no es único; no se modifica nada |

**Localizar por `ID` (v2.6.1).** Graph no reconoce `fields/ID` en un `$filter` (`400 "A provided field name is not recognized"` con cualquier valor): `ID` es el identificador del propio ítem, no una entrada filtrable de `fields`. Por eso, cuando `filter_by.field` es `ID` (sin distinguir mayúsculas), el endpoint no busca: valida que `filter_by.value` sean 1–10 dígitos ASCII (si no, `400` antes de llamar a Graph, porque el valor se interpola en la ruta), hace `GET .../items/{id}` (que aporta el `webUrl` de la respuesta y un `404` limpio antes de escribir nada) y luego el mismo `PATCH .../items/{id}/fields`. Un `404` de Graph se traduce al mismo mensaje de "ningún registro coincide"; el `409` no aplica, porque el `ID` es único. En `items:search`, un filtro sobre `ID` se rechaza con `400` explicativo en vez de reenviar el error de Graph.

**Response 200:**
```json
{ "status": "updated", "id": "7", "webUrl": "https://...", "site_id": "...", "list_id": "..." }
```

#### `POST /v1/sharepoint/list/item:upsert`

*Upsert* genérico (verificar-y-decidir): inserta o actualiza según una coincidencia por **clave** y, opcionalmente, **periodo**. No hay nada de dominio quemado — la columna clave, la de fecha y el esquema de `data` viajan en el payload, de modo que el mismo endpoint sirve a **cualquier lista**.

**Request:**
```json
{
  "sharepoint_url": "https://host.sharepoint.com/Oper/Lists/Incidencias/View.aspx",
  "match": {
    "key_field": "TicketId",
    "key_value": "INC-12345",
    "date_field": "Created",
    "period": "current_month"
  },
  "data": { "Title": "Incidencia", "Estado": "Resuelto" }
}
```

El filtro combina con `and`: igualdad exacta sobre `key_field` y, si se indica `date_field` + `period`, un rango `fields/{date_field} ge '{inicio}' and ... lt '{fin}'`. `period` admite un **atajo con nombre** (`current_day`, `current_week`, `current_month`, `current_year`) o un **rango explícito** `{ "from": "YYYY-MM-DD", "to": "YYYY-MM-DD" }` (con `to` inclusive). Los límites se calculan en la **zona horaria del tenant** (`TENANT_TIMEZONE`) y se convierten a UTC para el `$filter` (`app/services/period.py`). Sin `period`/`date_field`, la coincidencia es solo por clave; indicar `period` sin `date_field` se rechaza con `422`.

| Coincidencias | Desenlace |
|---|---|
| 0 | `create_list_item` → `result="created"` |
| 1 | `update_list_item` sobre ese ítem → `result="updated"` |
| > 1 | se actualiza el **primero**, `result="updated"` y `warning` en logs (no `409`) |

**Response 200:**
```json
{ "result": "updated", "id": "7", "webUrl": "https://...", "site_id": "...", "list_id": "...", "matched": 1 }
```

> A diferencia del `PATCH`, varias coincidencias **no** son error: la decisión de negocio es actualizar la primera y emitir un `warning` para detectar duplicados preexistentes.

#### `POST /v1/sharepoint/list/items:search`

Búsqueda de ítems con hasta **15 condiciones combinadas con AND**, validadas contra el esquema real de la lista. Generaliza el filtro clave+fecha del upsert a N campos con valores **tipados** y, desde la v2.6.0, con **operadores** (`eq`, `in`, `ge`, `gt`, `le`, `lt`) en lugar de solo igualdad.

**Request:**
```json
{
  "sharepoint_url": "https://host.sharepoint.com/Oper/Lists/DSL/Allitemsg.aspx",
  "filters": [
    { "field": "Entorno", "value": "L02" },
    { "field": "_x00da_ltima", "value": true },
    { "field": "Estado", "op": "in", "value": ["Iniciado", "Pausado"] },
    { "field": "FechaFinalizado", "op": "ge", "value": "2026-08-18T00:00:00Z" }
  ],
  "order_by": { "field": "Created", "direction": "desc" },
  "top": 100
}
```

`filters`, `order_by` y `top` son opcionales; sin `filters` (u `[]`) se devuelven los primeros `top` ítems sin filtrar. El request usa `extra="forbid"`: cualquier campo desconocido (p. ej. un `filter_by` de otros endpoints) → `422`, nunca ignorado en silencio.

**Traducción de valores a literal OData** (interna, invisible para el caller):

| Tipo JSON de `value` | Literal OData | Nota |
|---|---|---|
| string | `'texto'` (comillas internas duplicadas: `'O''Hara'`) | igual que el resto de endpoints |
| boolean | **`1` / `0`** — nunca `true`/`false` | Graph **ignora en silencio** `eq true\|false` (y `eq 'true'`) sobre columnas Sí/No: devuelve todas las filas sin error. `eq 1`/`eq 0` sí filtra. Verificado empíricamente contra la lista DSL (2026-07-22). |
| entero / decimal | literal sin comillas | |
| string contra columna dateTime, con un operador nuevo de la v2.6.0 | instante canónico **entrecomillado** en UTC (`'2026-08-18T00:00:00Z'`) | mismo literal que produciría `eq`, pero validado como ISO-8601 y normalizado a UTC. El `dateTimeOffset` desnudo de OData 4.0 da `400` en Graph: ver más abajo |

**Validación contra el esquema de columnas** (anti-"ignorado en silencio"): antes de construir el `$filter`, el conector consulta `GET /sites/{id}/lists/{id}/columns` — cacheado por `(site_id, list_id)` con **TTL de 300 s** (a diferencia del caché de sites, sin caducidad, porque las columnas pueden cambiar) — y valida cada condición:

| Condición | Respuesta |
|---|---|
| Campo inexistente en la lista | `400` nombrando el campo |
| Tipo JSON ≠ tipo de columna (string contra Sí/No, texto contra numérica, …) | `400` explicando el tipo esperado (sin coerción) |
| Columna lookup referenciada por su nombre base | `400` — debe usarse `{Columna}LookupId` con el ID numérico |
| `{Columna}LookupId` de una columna lookup | aceptado, tipo esperado: entero |
| Valor bien tipado que no coincide con ninguna fila | `200` con `total: 0` (no es error) |

La expresión completa se percent-encodea y se envía con `Prefer: HonorNonIndexedQueriesWarningMayFailRandomly`; los nombres de campo se validan contra `[A-Za-z0-9_]+` en doble capa (schema `422` + servicio `400`, anti-inyección OData).

**Operadores (`op`, v2.6.0, opcional, `eq` por defecto)** → enum **cerrado** `eq | in | ge | gt | le | lt`. Es la segunda mitad de la garantía anti-inyección: con `field` acotado a `[A-Za-z0-9_]+` y `op` a seis tokens, todo lo que se interpola en una cláusula salvo el literal proviene de un alfabeto finito. Un token fuera del conjunto → `422` en el schema, y el operador se **revalida en el servicio** antes de interpolarse (misma defensa en profundidad que el nombre de campo). El OR existe **solo dentro** de un `in`; entre condiciones el enlace sigue siendo AND, invariable.

OData generado por cada operador (`Estado` = choice, `hohl` = number, `FechaFinalizado` = dateTime):

| Filtro | `$filter` generado (antes de percent-encodear) |
|---|---|
| `{"field":"Entorno","value":"L02"}` | `fields/Entorno eq 'L02'` |
| `{"field":"Estado","op":"in","value":["Iniciado","Pausado"]}` | `(fields/Estado eq 'Iniciado' or fields/Estado eq 'Pausado')` |
| `{"field":"Estado","op":"in","value":["Pausado"]}` | `(fields/Estado eq 'Pausado')` — un elemento también va entre paréntesis |
| `{"field":"hohl","op":"ge","value":10}` | `fields/hohl ge 10` |
| `{"field":"FechaFinalizado","op":"ge","value":"2026-08-18T00:00:00Z"}` | `fields/FechaFinalizado ge '2026-08-18T00:00:00Z'` (entrecomillado: es la forma que Graph evalúa) |
| los dos primeros juntos | `(fields/Estado eq 'Iniciado' or fields/Estado eq 'Pausado') and fields/Entorno eq 'L02'` |

**`in` no usa el operador nativo de OData.** OData 4.0 lo define, pero su comportamiento contra `fields/{columna}` en listas de SharePoint no está verificado por el proyecto, y el modo de fallo si no estuviera soportado es justo el peligroso: cláusula descartada y `200` con todas las filas. Expandirlo a un grupo OR usa el único operador probado sobre estas columnas. **Los paréntesis son obligatorios, no cosméticos**: sin ellos la precedencia entre `or` y el `and` que une los filtros cambiaría el significado de toda la expresión.

**Literal de las columnas de fecha/hora: entrecomillado, y esto se midió.** OData 4.0 especifica un `dateTimeOffset` **desnudo**, y al diseñar el cambio se dio por hecho que era lo que Graph esperaba. Es falso para columnas de listas de SharePoint. Medido el 2026-09-03 contra la lista DSL real (columna `Modified`, 9.320 filas):

| Cláusula enviada | Resultado de Graph |
|---|---|
| `fields/Modified ge 2018-01-01T00:00:00Z` (desnudo, según OData 4.0) | **`400 invalidRequest`** |
| …con milisegundos, con `+00:00`, solo fecha, o `datetime'…'` (OData v3) | **`400`** en todas las variantes |
| `fields/Modified ge '2018-01-01T00:00:00Z'` (entrecomillado) | **`200`, 9.127 filas**, mínimo devuelto `2018-02-15T14:27:11Z` |
| `fields/Modified lt '2018-01-01T00:00:00Z'` (entrecomillado) | **`200`, 193 filas**, máximo devuelto `2017-07-28T08:23:28Z` |

9.127 + 193 = 9.320, el total exacto de la lista: la partición perfecta es lo que prueba que la cláusula **se evalúa**, no solo que no da error. Así que aquí Graph se comporta **al revés que con los booleanos**, donde el literal desnudo `1`/`0` es el que filtra y el entrecomillado el que se ignora en silencio. La lección transferible, y la razón de ser de la fase empírica de este proyecto: sobre columnas de listas de SharePoint la forma del literal se decide midiendo, nunca leyendo la especificación.

Lo que el camino consciente del tipo de columna aporta entonces sobre el entrecomillado genérico son dos cosas: **valida** el valor como instante ISO-8601 — un `"18/08/2026"` es un `400` explícito en vez de una cláusula incapaz de coincidir con nada — y lo **canonicaliza a UTC**, reemitiéndolo a partir del valor parseado en vez de copiarlo, así que lo que se interpola nunca es texto del caller.

*Alcance:* esa validación se aplica a los operadores **nuevos** de la v2.6.0 (`in`, `ge`, `gt`, `le`, `lt`); `eq` conserva la traducción dirigida por el valor de la v2.5.0, sin validar. Como ambos caminos entrecomillan, un instante bien formado genera la misma cláusula por los dos. Y la pregunta que quedaba abierta está respondida: **`eq` sobre columna de fecha ya filtraba bien antes de este cambio** — `fields/Modified eq '2026-09-02T14:15:45Z'` devolvió exactamente la única fila con ese instante, de 9.320. No había defecto preexistente que arrastrar.

**Texto: Graph compara sin distinguir mayúsculas.** `fields/Entorno eq 'L02'` también trae las filas almacenadas como `l02` (medido el 2026-09-03: `eq 'L02'` → 2.290 filas, `eq 'l01-2016'` → 55, el `in` de ambos → 2.345 = la suma exacta, y entre los valores devueltos aparecen las dos cajas). Aplica igual a `eq` y a `in`, y conviene documentarlo porque nada en la API lo insinúa: un `in` no puede usarse para distinguir valores que solo difieren en la caja.

**Semántica temporal: UTC tal como viene.** Un valor de comparación se interpreta exactamente en el instante dado, en UTC; un valor sin zona se asume UTC y uno con offset se normaliza a UTC. El conector **no** le aplica `TENANT_TIMEZONE`. Es una divergencia consciente respecto al `period` del upsert, que sí resuelve los periodos con nombre en la zona del tenant:

| | Quién fija el instante | Zona aplicada |
|---|---|---|
| `period` de `item:upsert` | el conector calcula los límites del periodo | `TENANT_TIMEZONE` — necesita la zona para saber dónde empieza «este mes» |
| `op: ge/gt/le/lt` de `items:search` | el caller da el instante explícito | ninguna: UTC tal como viene |

El motivo: desplazar en silencio un instante explícito haría que el mismo string señalara filas distintas en cada despliegue. Son dos semánticas de fecha en la misma API y la inconsistencia es real; se documenta en vez de disimularla.

**Matriz operador × tipo de columna** (validada contra el esquema real, `400` antes de llamar a Graph):

| Tipo de columna (facet de Graph) | `eq` | `in` | `ge` `gt` `le` `lt` |
|---|---|---|---|
| text, note, choice y cualquier otro no reconocido | sí | sí | **no** |
| number, currency | sí | sí | sí |
| dateTime | sí | sí | sí |
| boolean (Sí/No) | sí | **no** | **no** |
| lookup vía `{Base}LookupId` | sí | sí | sí |
| lookup por su nombre base | **no** (regla existente, sin cambios) | | |

Los dos rechazos, razonados: **ordenar texto** compararía lexicográficamente, que casi nunca es lo que pide quien escribe `ge` sobre una columna *choice*, y el resultado parecería lo bastante plausible para pasar desapercibido; **`in` sobre Sí/No** tiene un dominio de dos valores, así que es o un `eq` redundante o una tautología que devuelve todas las filas. Ambos son errores del caller que vale la pena hacer visibles. El `400` nombra la columna, su tipo y los operadores que ese tipo sí admite.

**La validación de operadores no cuesta ninguna llamada extra a Graph:** lee las mismas definiciones de columna cacheadas que ya leen las comprobaciones de campo y de tipo.

**Frontera `422`/`400` con operadores** — la línea no se mueve, solo crecen las preguntas. `422` es lo que se juzga con el body a solas; `400` lo que exige conocer la lista:

| Caso | Respuesta | Capa |
|---|---|---|
| `op` fuera del enum (`contains`, `ne`, `eq 1 or 1`) | `422` | schema |
| `op: "in"` con `value` escalar, u otro operador con `value` array | `422` | schema |
| Array de `in` vacío o de más de 20 elementos | `422` | schema |
| Array de `in` con tipos JSON mezclados (`[true, 1]` incluido: `bool` no se colapsa en `int`) | `422` | schema |
| Más de 100 cláusulas generadas tras expandir los `in` | `422` indicando el número generado y el límite | schema |
| Operador que el tipo de la columna no admite | `400` nombrando columna, tipo y operadores válidos | servicio |
| Un solo elemento mal tipado en un array de `in` | `400` — rechaza el filtro entero, nada parcial llega a Graph | servicio |
| Valor de fecha no parseable como ISO-8601 | `400` nombrando la columna y el formato | servicio |

**Presupuesto de cláusulas — y el techo real, ya medido.** `filters` sigue topado en 15 y un `in` en 20 valores, lo que permitiría 300 cláusulas. El tope efectivo es de **100 cláusulas generadas** tras la expansión, comprobado sobre el body.

Ese 100 era un margen a ojo. Medido por bisección el 2026-09-03, el techo de Graph **no se cuenta en cláusulas sino en longitud de URL**, y es abrupto y reproducible (`404 UnknownError` con mensaje vacío al pasarse):

| Forma de la cláusula (longitud) | Última aceptada | Primera rechazada |
|---|---|---|
| `fields/Entorno eq 'v0000'` (29 ch) | **115** cláusulas · `$filter` 3.333 ch · URL 5.187 ch | 116 · URL 5.230 ch |
| `fields/Cliente_x002d_LIBSALookupId eq 35` (44 ch) | **88** cláusulas · `$filter` 3.861 ch · URL 4.985 ch | 89 · URL 5.039 ch |
| `fields/Modified eq '2026-08-01T00:00:00Z'` (45 ch) | **73** cláusulas · `$filter` 3.283 ch · URL 4.841 ch | 74 · URL 4.904 ch |

La URL se agota alrededor de **4,9–5,2 KB** (la horquilla la produce el percent-encoding, que triplica espacios y comillas). Es decir: **el presupuesto de 100 cláusulas admite peticiones que Graph va a rechazar** — 100 cláusulas de fecha se pasan del techo. El modo de fallo es al menos el honesto (un `404` propagado, no una cláusula descartada en silencio), pero es una petición que el conector aceptó y no debería. Lo que la evidencia pide es presupuestar por **longitud de la expresión generada** (comprobación en el servicio sobre el `$filter` construido, ~3 KB sin encodear, `400` antes de la llamada) y dejar el número de cláusulas solo como guarda barata en el schema. Queda como seguimiento, no plegado en este cambio.

**Umbral de vista, sin cambios.** Estos operadores **no** lo levantan; al contrario, un grupo OR o una comparación sobre una columna no indexada es candidato preferente a que Graph responda `notSupported` en listas grandes (propagado con su detalle, como con `order_by`). `ge`/`le` sí reducen cuántas veces se topa uno con el corte de `top`, porque el servidor descarta las filas fuera de rango en vez de gastar el presupuesto en ellas — pero `has_more` sigue sin cursor.

**`order_by`** → `$orderby=fields/{field} asc|desc`. **Limitación conocida:** en listas que superan el umbral de vista de SharePoint (~5000 filas), Graph responde `notSupported` al ordenar por columnas no indexadas; el error se propaga con su detalle. Recomendación: ordenar por columnas indexadas (`Created`, `Modified`, `ID`).

**`top`** (1–5000, default 100): Graph trata `$top` como tamaño de página, no como límite total, así que el conector sigue `@odata.nextLink` solo hasta reunir `top` ítems y trunca el excedente. `has_more: true` indica que el corte dejó fuera filas coincidentes. (Paginación por cursor: evaluada y pospuesta conscientemente; añadirla después es retrocompatible.)

**`select`** (v2.5.0, opcional, hasta 50 nombres) → `$expand=fields($select=Col1,Col2,...)`: proyecta el `fields` de cada ítem a solo esas columnas. Cada nombre se valida en doble capa (`[A-Za-z0-9_]+` → `422`; contra el esquema real de columnas → `400` si no existe, porque Graph ignora en silencio los nombres desconocidos en `$select`). **Asimetría deliberada con los filtros:** en `select` las columnas lookup admiten ambas formas — el nombre base proyecta el valor visible y `{Columna}LookupId` el ID numérico — mientras que en `filters` el nombre base se rechaza (filtrar por él es poco fiable en Graph). Omitido o `[]` → todos los campos. Graph puede añadir claves de sistema (p. ej. `@odata.etag`) dentro de `fields` aun con proyección.

**Response 200:**
```json
{ "total": 2, "items": [ { "id": "7", "webUrl": "https://...", "fields": { "Entorno": "L02", "_x00da_ltima": true } } ],
  "has_more": false, "site_id": "...", "list_id": "..." }
```

#### `POST /v1/sharepoint/upload`

Sube un archivo a la biblioteca/carpeta identificada por su URL. Cuerpo `multipart/form-data` con `sharepoint_url` y `file`.

**Response 201:**
```json
{ "status": "uploaded", "id": "01ABC...", "name": "TAM.txt", "size": 2048,
  "webUrl": "https://...", "site_id": "...", "drive_id": "...", "folder": "Areas/Advisors/OnlyTest" }
```

---

### Discovery

> Los endpoints de discovery, list items y files siguientes están bajo `/v1/graph` y operan con identificadores explícitos.

#### `GET /v1/graph/sites`

Lista todos los sites de SharePoint accesibles por la aplicación.

| Query param | Tipo | Default | Descripción |
|---|---|---|---|
| `search` | string | `*` | Palabra clave para filtrar por nombre o URL |

**Response 200:**
```json
{
  "sites": [
    { "id": "hostname,site-col-id,site-id", "name": "soporte", "displayName": "Soporte", "webUrl": "https://..." }
  ],
  "total": 1
}
```

---

#### `GET /v1/graph/sites/{site_id}/lists`

Lista todas las listas del site (incluidas listas de sistema).

**Response 200:**
```json
{
  "site_id": "...",
  "lists": [
    { "id": "uuid", "name": "Análisis Soporte", "displayName": "Análisis Soporte", "webUrl": "https://..." }
  ],
  "total": 1
}
```

---

#### `GET /v1/graph/sites/{site_id}/drives`

Lista las bibliotecas de documentos (drives) del site.

**Response 200:**
```json
{
  "site_id": "...",
  "drives": [
    { "id": "b!...", "name": "Documents", "driveType": "documentLibrary", "webUrl": "https://..." }
  ],
  "total": 1
}
```

---

#### `GET /v1/graph/sites/{site_id}/drives/{drive_id}/items`

Navega el árbol de carpetas y archivos de un drive.

| Query param | Tipo | Default | Descripción |
|---|---|---|---|
| `item_id` | string | null | ID de carpeta a listar. Omitir para listar la raíz |

**Response 200:**
```json
{
  "drive_id": "b!...",
  "parent_id": null,
  "items": [
    { "id": "...", "name": "DailyDelivery", "is_folder": true, "size": null, "webUrl": "..." }
  ],
  "total": 1
}
```

---

### List Items

#### `GET /v1/graph/sites/{site_id}/lists/{list_id}/items`

Lee ítems de una lista junto con sus valores de campo.

| Query param | Tipo | Default | Descripción |
|---|---|---|---|
| `top` | int | `20` | Número máximo de ítems a devolver (1–5000) |

**Response 200:**
```json
{
  "site_id": "...",
  "list_id": "...",
  "items": [
    { "id": "1", "fields": { "Title": "LATSUP-001", "organization": "Acme" }, "webUrl": "..." }
  ],
  "total": 1
}
```

---

#### `POST /v1/graph/sites/{site_id}/lists/{list_id}/items`

Inserta un nuevo ítem en la lista.

**Request:**
```json
{
  "fields": {
    "Title": "LATSUP-6585",
    "organization": "Acme Corp",
    "score": 9.5,
    "resolved": true
  }
}
```

> Los nombres de campo deben ser los **nombres internos** (internal name) de las columnas en SharePoint, no el nombre de visualización.

**Response 201:**
```json
{ "status": "created", "id": "42", "webUrl": "https://..." }
```

---

### Files

#### `POST /v1/graph/sites/{site_id}/drives/{drive_id}/files`

Sube un archivo a una biblioteca de documentos. El cuerpo es `multipart/form-data`.

| Query param | Tipo | Default | Descripción |
|---|---|---|---|
| `folder` | string | `""` | Subcarpeta destino (p.ej. `Areas/testing_empty/OnlyTest`) |

La carpeta se crea automáticamente si no existe.

**Response 201:**
```json
{
  "status": "uploaded",
  "id": "01ABC...",
  "name": "informe.json",
  "size": 2048,
  "webUrl": "https://...",
  "drive_path": "/drive/root:/DailyDelivery"
}
```

---

#### `GET /v1/graph/sites/{site_id}/drives/{drive_id}/items/{item_id}`

Devuelve metadatos de un archivo o carpeta.

**Response 200:**
```json
{
  "id": "01ABC...",
  "name": "informe.json",
  "size": 2048,
  "webUrl": "https://...",
  "mime_type": "application/json",
  "created_at": "2026-06-01T10:00:00Z",
  "modified_at": "2026-06-09T08:30:00Z",
  "download_url": "https://...pre-authenticated-url..."
}
```

El campo `download_url` es una URL pre-autenticada válida ~1 hora (sin token Bearer).

---

#### `GET /v1/graph/sites/{site_id}/drives/{drive_id}/items/{item_id}/download`

Descarga el contenido binario del archivo.

**Response 200:** bytes del archivo con `Content-Type` del MIME real y `Content-Disposition: attachment; filename="..."`.

---

### Health

#### `GET /health`

```json
{ "status": "ok", "service": "SharePoint Connector", "version": "2.3.0" }
```

---

### Cabeceras HTTP

| Cabecera | Dirección | Descripción |
|---|---|---|
| `X-App-ID` | Request | Identificador del caller. Se registra en todos los logs |
| `X-Request-ID` | Response | UUID generado por el middleware para trazabilidad |

### Códigos de error

| Código | Causa |
|---|---|
| `400` | Parámetro inválido o faltante |
| `401` | Fallo de autenticación con Microsoft Graph |
| `403` | Permisos insuficientes (site sin grant, permisos Graph incorrectos) |
| `404` | Recurso no encontrado en SharePoint (incluye `filter_by` sin coincidencias en `PATCH /v1/sharepoint/list/item`) |
| `409` | Conflicto: `filter_by` coincide con más de un registro en `PATCH /v1/sharepoint/list/item` |
| `429` | Rate limit de Microsoft Graph — reintentar más tarde |
| `500` | Error interno no controlado |
| `502` | Error inesperado devuelto por Microsoft Graph |

---

## 5. Autenticación y seguridad

```mermaid
sequenceDiagram
    participant Caller
    participant Connector
    participant AAD as Azure AD
    participant Graph as Microsoft Graph

    Caller->>Connector: HTTP request + X-App-ID
    Connector->>Connector: Asigna request_id (UUID)
    Connector->>Connector: ¿token en caché válido?
    alt token expirado o no existe
        Connector->>AAD: POST /oauth2/v2.0/token<br/>grant_type=client_credentials
        AAD-->>Connector: access_token (1h TTL)
        Connector->>Connector: guarda token en memoria
    end
    Connector->>Graph: Llamada con Bearer token
    Graph-->>Connector: 200 / error
    Connector-->>Caller: respuesta + X-Request-ID
```

### Modelo de permisos: `Sites.Read.All` / `Sites.ReadWrite.All`

El App Registration en Azure AD requiere los permisos de aplicación:

- **`Sites.Read.All`** — para discovery y lectura de listas/archivos.
- **`Sites.ReadWrite.All`** — para escritura (crear ítems, subir archivos).

Como alternativa más restrictiva se puede usar **`Sites.Selected`**, que limita el acceso a sites específicos. En ese caso, un administrador debe conceder acceso explícitamente:

```powershell
Connect-PnPOnline -Url "https://latinia.sharepoint.com" -Interactive

Grant-PnPAzureADAppSitePermission `
  -AppId "<CLIENT_ID>" `
  -DisplayName "SharePoint Connector" `
  -Site "https://latinia.sharepoint.com/sites/yoursite" `
  -Permissions Write
```

Si el grant no está concedido, Graph devuelve `403 Forbidden` — visible en los logs del conector con `request_id`.

---

## 6. Logging estructurado

Todos los logs se emiten en JSON por `stdout`, compatibles con cualquier stack de observabilidad (ELK, Loki, CloudWatch, etc.).

```json
{
  "timestamp": "2026-06-09T10:00:00.000Z",
  "level": "INFO",
  "logger": "app.services.sharepoint",
  "message": "Uploaded file 'informe.json' (2048 bytes) → drive b!... / site hostname,...",
  "module": "sharepoint",
  "function": "upload_file",
  "line": 167,
  "request_id": "a1b2c3d4-...",
  "client_app_id": "jirito-newsletter",
  "site_id": "hostname,...",
  "drive_id": "b!...",
  "file_name": "informe.json"
}
```

Campos estructurados disponibles: `request_id`, `client_app_id`, `method`, `path`, `status_code`, `duration_ms`, `site_id`, `list_id`, `drive_id`, `item_id`, `file_name`, `graph_url`, `graph_status`.

---

## 7. Despliegue

### Variables de entorno

| Variable | Requerida | Descripción | Ejemplo |
|---|---|---|---|
| `TENANT_ID` | Sí | ID del tenant Azure AD | `xxxxxxxx-...` |
| `CLIENT_ID` | Sí | ID del App Registration | `xxxxxxxx-...` |
| `CLIENT_SECRET` | Sí | Secreto del App Registration | `abc123~...` |
| `LOG_LEVEL` | No | Nivel de log (`DEBUG`/`INFO`/`WARNING`/`ERROR`) | `INFO` |
| `TENANT_TIMEZONE` | No | Zona horaria IANA del tenant para acotar periodos del upsert | `UTC` / `Europe/Madrid` |
| `SP_PORT` | No | Puerto expuesto en el host | `8003` |

> La API opera siempre con `site_id`, `list_id` y `drive_id` pasados como path params en cada llamada. Obtener estos IDs es el primer paso mediante los endpoints de discovery.

### Arranque con Docker Compose

```bash
cp devops/.env.example devops/.env
# Editar devops/.env con los valores reales
docker compose -f devops/docker-compose.yml up -d --build
```

### Integración en una red Docker existente

```yaml
# docker-compose.yml del caller (p.ej. Jirito Newsletter)
services:
  app:
    networks:
      - sp-net

  sharepoint-connector:
    image: sharepoint-connector:latest
    env_file: .env.sp-connector
    networks:
      - sp-net

networks:
  sp-net:
    driver: bridge
```

El caller apunta a `http://sharepoint-connector:8003/v1/graph/...`.

---

## 8. Flujo típico de uso

```
1. GET /v1/graph/sites?search=soporte
   → obtener site_id

2. GET /v1/graph/sites/{site_id}/lists
   → obtener list_id

3. GET /v1/graph/sites/{site_id}/drives
   → obtener drive_id

4. POST /v1/graph/sites/{site_id}/lists/{list_id}/items
   → crear ítem en lista

5. POST /v1/graph/sites/{site_id}/drives/{drive_id}/files?folder=DailyDelivery
   → subir archivo (multipart/form-data)

6. GET /v1/graph/sites/{site_id}/drives/{drive_id}/items/{item_id}/download
   → descargar archivo
```

Los IDs de site, lista y drive son estables; solo es necesario hacer discovery una vez y cachear los resultados en el caller.

---

## 9. Comparativa con versión anterior (v1)

| Aspecto | v1.0.0 | v2.0.0 |
|---|---|---|
| **Endpoints** | `POST /upload`, `POST /list` | API REST versionada `/v1/graph/...` con 9 endpoints |
| **Scope** | Site único fijo (env var `SITE_URL`) | Multi-site dinámico — site/lista/drive en cada llamada |
| **Discovery** | No disponible | Endpoints para explorar sites, listas, drives y carpetas |
| **Subida de archivos** | JSON con `data` en Base64 | `multipart/form-data` — más eficiente y estándar |
| **Descarga de archivos** | No disponible | `GET .../download` — bytes directos con Content-Type |
| **Metadatos de archivo** | No disponible | `GET .../items/{item_id}` con `download_url` pre-autenticada |
| **Logging** | Básico | JSON estructurado con `request_id`, `client_app_id`, duración |
| **Trazabilidad** | Parcial | `X-Request-ID` en respuesta + contexto propagado a services |
| **Manejo de errores** | Genérico | `GraphAPIError` tipado con códigos 400/401/403/404/429/502 |
| **Estructura de módulos** | Plana (`app/*.py`) | Capas separadas: `core/`, `api/v1/`, `schemas/`, `services/` |

---

## 10. Limitaciones conocidas (v2)

| Limitación | Condición | Solución futura |
|---|---|---|
| Tamaño máximo de archivo | 4 MB (límite de `PUT .../content` en Graph) | Implementar upload sessions para archivos mayores |
| Sin paginación en discovery | Resultados truncados si hay >1000 sites/listas/drives | Implementar `@odata.nextLink` |
| Sin cola de reintentos | Fallo en Graph → error inmediato al caller | Añadir cola interna con reintentos exponenciales |
| Sin autenticación entre caller y conector | El servicio confía en cualquier caller en la red | Añadir API key o JWT en cabecera `X-Api-Key` |
| Token en memoria (no distribuido) | Una sola instancia — token no compartido entre réplicas | Externalizar caché de token (Redis) para alta disponibilidad |
| Sin OR entre campos distintos | `items:search` compone OR **solo dentro** de un `op: "in"`; «(A o B) y (C o D)» sobre columnas distintas no se puede expresar en una llamada | Un `POST .../list/items:query` con un árbol booleano en el body — descartado por ahora: exige diseñar un lenguaje de consulta y complica las garantías anti-inyección |
| `has_more` sin cursor de paginación | Un resultado truncado por `top` es un callejón sin salida: no hay forma de pedir la página siguiente | Cursor de paginación (evaluado y pospuesto; añadirlo es retrocompatible). `ge`/`le` reducen la frecuencia del problema, no lo resuelven |
| Presupuesto de cláusulas en la unidad equivocada | El tope son 100 cláusulas, pero el techo de Graph es la longitud de URL (~5 KB): caben 115 cláusulas cortas y solo 73 de fecha, así que 100 cláusulas de fecha se pasan y Graph responde `404` | Presupuestar por longitud del `$filter` generado (~3 KB sin encodear) con `400` antes de la llamada, y dejar el conteo de cláusulas como guarda del schema. Umbral ya medido (2026-09-03) |
| Comparación de texto sin distinguir mayúsculas | `eq 'L02'` también devuelve las filas guardadas como `l02`; un `in` no puede distinguir valores que solo difieren en la caja | Ninguna prevista: es comportamiento de Graph/SharePoint, no del conector. Documentado para que no sorprenda |
