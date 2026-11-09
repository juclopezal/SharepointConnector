## v2.7.0 — 2026-11-09

### Feature: Autenticación por clave de API y auditoría por identidad (SPEC-005)

**Contexto:** el conector opera con permisos de aplicación amplios sobre todo el tenant (`Sites.ReadWrite.All`) pero no validaba quién le llamaba: la cabecera `X-App-ID` es informativa y ni siquiera obligatoria. No había forma fiable de saber —ni de auditar— qué llamante ejecutó cada operación.

**Solución:**
- **`X-API-Key` obligatoria en `/v1/...`:** cada petición debe traer la clave en la cabecera. Ausente, vacía, desconocida o revocada → `401` sin llegar a Graph. `/health` queda público. La dependencia se aplica a nivel del router `/v1` (`APIKeyHeader`, visible en OpenAPI).
- **Almacén SQLite de claves habilitadas:** tabla `api_keys(principal, key_hash, active, created_at, last_used)`. Las claves se guardan como **HMAC-SHA256** (secreto de servidor), nunca en claro; validación en tiempo constante (`hmac.compare_digest`) y solo filas `active=1`.
- **Auditoría por identidad:** nuevo campo `principal` en la whitelist `_EXTRA_FIELDS` y propagado por `ContextVar`. El log "HTTP request completed" registra ahora `principal` + `path` + `status_code`, incluidos los intentos rechazados (`401`). La clave nunca se registra.
- **Gestión de claves:** alta con `python -m app.admin.api_keys_admin issue <principal>` (genera clave aleatoria + HMAC, imprime la clave una sola vez). Revocar/reactivar/borrar/consultar = SQL directo (`--help-sql`). Rotar = revocar + nueva alta.
- **Modo compatibilidad:** si `API_KEY_HMAC_SECRET` está vacío, la auth queda deshabilitada (aviso al arrancar) para permitir un despliegue escalonado: desplegar, repartir claves, y luego fijar el secreto para forzar la validación.

**Retrocompatible por defecto:** sin `API_KEY_HMAC_SECRET` el comportamiento no cambia. Al fijarlo, los clientes **deben** enviar `X-API-Key` (cambio de contrato, ya anticipado a los usuarios).

**Fuera de alcance:** autorización por usuario/lista (RBAC, `403`) e identidad verificada vía Entra ID (OAuth2 `Bearer`) quedan como fases futuras en la SPEC-005.

**Archivos nuevos:**
- `app/core/api_keys.py` — almacén SQLite + HMAC
- `app/core/security.py` — dependencia `require_principal`
- `app/admin/api_keys_admin.py`, `app/admin/__init__.py` — utilidad de alta y ayuda SQL
- `tests/test_api_key_auth.py` — 12 tests (257/257 en verde)

**Archivos modificados:**
- `app/core/config.py` — `api_key_hmac_secret`, `api_key_db_path`
- `app/core/context.py` — `principal_var`
- `app/core/dependencies.py` — `get_api_key_store()` singleton
- `app/core/exceptions.py` — `APIKeyError` + handler
- `app/core/logging.py` — `principal` en `_EXTRA_FIELDS`
- `app/api/v1/router.py` — dependencia de auth en `/v1`
- `app/main.py` — handler, `principal` en el log de auditoría, aviso de auth deshabilitada
- `devops/.env.example`, `devops/docker-compose.yml` (volumen `sp-connector-data`), `.gitignore`
- `README.md`, `ARQUITECTURA.md`, `TECNOLOGIAS.md`, `VERSION`, `requirements/SPEC-005`
- OpenSpec: cambio `add-api-key-auth-audit` (capabilities `api-key-auth`, `request-audit-log`)

---

## v2.6.1 — 2026-10-09

### Fix: `PATCH /list/item` localizado por `ID` (SPEC-002, SPEC-004)

**Contexto:** `PATCH /v1/sharepoint/list/item` localizaba siempre el registro con `$filter=fields/{field} eq '{value}'`. Con `filter_by.field = "ID"`, Graph responde `400 "A provided field name is not recognized"` con cualquier valor: `ID` es el identificador del propio ítem, no una entrada filtrable de `fields`. Bloqueaba el paso 5 del flujo de cierre, que actualiza `Estimadas` en la lista `Trabajos` por `ID`. `Trabajos` no tiene otra columna única: `Cod_x002e_Propuesta` tiene 279 valores repetidos y `Ticket` tiene vacíos y duplicados. `items:search` con un filtro sobre `ID` fallaba igual.

**Solución:**
- **Actualizar por `ID`:** si `filter_by.field` es `ID` (sin distinguir mayúsculas), el conector no busca. Hace `GET .../items/{id}`, que da un `404` limpio antes de escribir y aporta el `webUrl` de la respuesta, y después el mismo `PATCH .../items/{id}/fields`. Contrato de request y response sin cambios.
- `filter_by.value` debe tener 1–10 dígitos ASCII, porque se interpola en la ruta de Graph. Si no, devuelve `400` antes de llamar a Graph.
- Un `ID` inexistente, incluido `"0"`, devuelve `404` con el mismo mensaje que "ningún registro coincide". El `409` no aplica, porque el `ID` es único.
- **Buscar con `ID`:** en `items:search`, un filtro sobre `ID` (cualquier operador y cualquier capitalización) se rechaza con un `400` que explica el motivo y remite a `PATCH /list/item`, en lugar de reenviar el error críptico de Graph. No se añade búsqueda por `ID`.

**Retrocompatible:** las peticiones sobre `ID` fallaban en la v2.6.0 en ambos endpoints. El resto de campos se comportan igual.

**Fuera de alcance:** el upsert con `key_field: "ID"` falla igual, pero no tiene una rama de creación con sentido, así que queda como seguimiento. `order_by` sobre `ID` no se toca, porque no se ha visto que falle.

**Archivos modificados:**
- `app/services/sharepoint.py` — `get_list_item()` y rechazo de `ID` en `search_list_items()`
- `app/api/v1/endpoints/sharepoint.py` — rama por `ID` en `update_list_item_by_url`, `_ITEM_ID_RE` y `_find_list_item_by_id()`, más la documentación en `/docs`
- `app/schemas/sharepoint.py` — descripciones de `FilterBy`
- `tests/test_sharepoint_service.py`, `tests/test_sharepoint_endpoints.py` — 35 tests nuevos (245/245 en verde)
- `VERSION`, `README.md`, `ARQUITECTURA.md`, `arquitecturasUML.md`, `requirements/SPEC-002`, `requirements/SPEC-004`
- OpenSpec: cambio `fix-filter-by-id` y capability nueva `list-item-update-by-url`

---

## v2.6.0 — 2026-09-03

### Feature: Operadores de filtro (`in`, `ge`, `gt`, `le`, `lt`) en items:search (SPEC-004)

**Contexto:** `POST /v1/sharepoint/list/items:search` solo sabía expresar igualdad, y solo unida con AND. Cualquier pregunta de la forma «dame las filas que están en uno de estos estados» o «dame las finalizadas en los últimos 15 días» no se podía formular: el caller tenía que emitir una petición HTTP por valor aceptado, unir las respuestas y volver a filtrarlas en cliente. El caso que lo motivó — los ítems de `HistorialConsumoBolsas` cuyo `Estado` es *Para Entregar*, *Iniciado* o *Pausado*, más los *Completado* de los últimos 15 días — costaba **4 llamadas al conector más un post-filtrado con `jq`**, y el corte por fecha obligaba a descargar el histórico completo de filas completadas para descartar casi todas, chocando además con el techo de `top`/`has_more`.

**Solución:** Campo opcional `op` en cada condición de `filters`, con `eq` por defecto. Enum **cerrado** de seis operadores: ningún token del caller puede llegar a la expresión OData generada.

| `op`                | `value`                     | Columnas admitidas |
|---------------------|-----------------------------|--------------------|
| `eq`                | escalar                     | todas |
| `in`                | array de 1–20, mismo tipo   | todas menos Sí/No |
| `ge` `gt` `le` `lt` | escalar                     | número, moneda, fecha/hora, `{Columna}LookupId` |

```json
{ "sharepoint_url": "https://.../Lists/HistorialConsumoBolsas/AllItems.aspx",
  "filters": [
    { "field": "Estado", "op": "in",
      "value": ["Para Entregar", "Iniciado", "Pausado"] },
    { "field": "Entorno", "value": "L02" }
  ] }
```

genera, antes de percent-encodear:

```
(fields/Estado eq 'Para Entregar' or fields/Estado eq 'Iniciado' or fields/Estado eq 'Pausado') and fields/Entorno eq 'L02'
```

- **`in` se expande a un grupo OR de igualdades entre paréntesis**, no al operador nativo `in` de OData: su comportamiento contra `fields/{columna}` en listas de SharePoint no está verificado, y si no estuviera soportado el fallo sería el peligroso — cláusula descartada y `200` con todas las filas. Los paréntesis son obligatorios: sin ellos la precedencia entre `or` y el `and` que une los filtros cambiaría el significado de toda la expresión. Un array de un solo elemento también va entre paréntesis, para que la forma generada no dependa de su longitud.
- **El OR existe solo dentro de un `in`.** Entre condiciones el enlace sigue siendo AND, sin cambios.
- **Los operadores se validan contra el esquema real de la lista** antes de llamar a Graph: `ge`/`gt`/`le`/`lt` sobre una columna de texto o *choice* → `400` (OData compararía lexicográficamente y el resultado parecería plausible); `in` sobre una columna Sí/No → `400` (dos valores posibles: o `eq` redundante o tautología que devuelve todo). El error nombra la columna, su tipo y los operadores que ese tipo sí admite. **No cuesta ninguna llamada extra a Graph:** lee las mismas definiciones de columna cacheadas que ya usan las comprobaciones de campo y de tipo.
- **Fechas: ISO-8601 en UTC tal como viene.** Un valor de comparación se interpreta exactamente en el instante dado (sin zona → UTC; con offset → normalizado a UTC) y el conector **no** le aplica `TENANT_TIMEZONE`. Divergencia deliberada con el `period` del upsert, donde el conector calcula los límites y por eso sí necesita la zona: desplazar en silencio un instante explícito haría que el mismo string señalara filas distintas en cada despliegue. Un valor no parseable → `400` con el nombre de la columna y el formato esperado.
- **Literal de fecha entrecomillado — y esto se midió, porque la suposición inicial era falsa.** OData 4.0 especifica un `dateTimeOffset` desnudo, pero sobre columnas de listas de SharePoint Graph lo rechaza con `400 invalidRequest` (igual con milisegundos, con offset `+00:00`, solo fecha, o `datetime'…'`), mientras que el entrecomillado sí filtra. Verificado el 2026-09-03 contra la lista DSL real (columna `Modified`, 9.320 filas): `ge '2018-01-01T00:00:00Z'` → 9.127 filas con mínimo `2018-02-15`, el `lt` complementario → 193 filas con máximo `2017-07-28`, y **9.127 + 193 = 9.320**, el total exacto de la lista. La partición perfecta es lo que prueba que la cláusula se evalúa. Graph se comporta aquí **al revés que con los booleanos**, donde el literal desnudo `1`/`0` es el que filtra: sobre estas columnas la forma del literal se decide midiendo, no leyendo la especificación.
- Lo que el camino consciente del tipo de columna aporta sobre el entrecomillado genérico es **validar** el valor como instante ISO-8601 (`"18/08/2026"` → `400` explícito en vez de una cláusula que no puede coincidir) y **canonicalizarlo a UTC**, reemitiéndolo desde el valor parseado en vez de copiarlo. Confirmado: `2026-06-01T00:00:00Z`, `2026-06-01T02:00:00+02:00`, `2026-06-01T00:00:00` y `2026-06-01` devuelven las mismas 590 filas. Se aplica a los operadores nuevos; `eq` conserva la traducción de la v2.5.0 sin validar, y **queda confirmado que `eq` sobre columna de fecha ya filtraba bien** (`eq '2026-09-02T14:15:45Z'` → exactamente 1 fila de 9.320): no había defecto preexistente.
- **Texto: Graph compara sin distinguir mayúsculas.** `eq 'L02'` también devuelve las filas guardadas como `l02` — aplica igual a `in`, y significa que un `in` no sirve para distinguir valores que solo difieren en la caja. Sin cambios en el conector; documentado porque nada en la API lo insinúa.
- **Presupuesto de cláusulas:** el tope de 15 filtros sigue igual y un `in` admite hasta 20 valores, pero el total de cláusulas generadas tras la expansión no puede pasar de **100** → `422` indicando el número generado y el límite. ⚠️ El techo real de Graph, medido por bisección el 2026-09-03, **no se cuenta en cláusulas sino en longitud de URL** (~4,9–5,2 KB, `404 UnknownError` al pasarse): caben 115 cláusulas cortas de texto, 88 de lookup y solo **73 de fecha**. Es decir, el presupuesto de 100 admite peticiones que Graph rechazará cuando las cláusulas son largas. El fallo es el honesto (un `404` propagado, no una cláusula descartada en silencio), pero queda como **seguimiento**: presupuestar por longitud del `$filter` generado (~3 KB sin encodear) en vez de por número de cláusulas.
- **Frontera `422`/`400` sin mover:** `422` es lo que se juzga con el body a solas (`op` fuera del enum; `in` con escalar u otro operador con array; array vacío, de más de 20 o de tipos mezclados —`[true, 1]` incluido, porque `bool` no se colapsa en `int`—; presupuesto excedido); `400` lo que exige conocer la lista (columna inexistente, operador no admitido por el tipo, tipo de valor equivocado en cualquier elemento de un `in`, fecha no parseable). Ninguna condición nueva se delega a Graph.
- **El umbral de vista de SharePoint (~5000 filas) no se levanta.** Al contrario: un grupo OR o una comparación sobre una columna no indexada es candidato preferente a un `notSupported` de Graph, que se propaga con su detalle. `ge`/`le` sí reducen cuántas veces se topa uno con el corte de `top`, pero `has_more` sigue sin cursor de paginación.

**Retrocompatible: `op` es opcional y `eq` por defecto**, así que todo body escrito para la v2.5.0 produce el mismo `$filter` y los mismos ítems. Ningún campo de la respuesta cambia de forma.

**Fuera de alcance, a propósito:** grupos OR anidados entre campos distintos — «(tres estados abiertos) O (Completado Y fecha reciente)» en una sola petición — que exigirían diseñar un lenguaje de consulta en el body; los operadores de este cambio ya llevan el caso motivador de 4 llamadas a 2. Tampoco se añade el cursor de paginación de `has_more`, ni los operadores `ne`, `contains`, `startswith` o las comprobaciones de nulo, cada uno con su propio perfil de fallo silencioso y su propia evidencia pendiente.

**Verificación empírica (2026-09-03, lista DSL real, Docker local, v2.6.0 en `/health`):** todos los criterios en verde tras corregir el literal de fecha; detalle fila a fila en `requirements/SPEC-004`.

**Archivos modificados:**
- `app/schemas/sharepoint.py` — `op` (`Literal` cerrado) y `value` ampliado a array en `SearchFilter`; validador de concordancia `op`↔forma de `value`, cotas y homogeneidad del array de `in`; validador del presupuesto de 100 cláusulas en `ListItemsSearchByUrlRequest`
- `app/services/sharepoint.py` — familia de tipo de columna en `_search_field_expected_type()`; `_check_search_operator()` con la matriz operador × tipo; `_to_odata_datetime_literal()`; `_build_search_clause()` (grupo OR entre paréntesis y cláusulas de comparación); comprobación de tipo por elemento y revalidación del operador en el servicio
- `app/api/v1/endpoints/sharepoint.py` — paso de `op` al servicio, `op` en el log y documentación del endpoint en `/docs` (tabla de operadores y ejemplos)
- `tests/test_sharepoint_service.py`, `tests/test_sharepoint_endpoints.py` — 82 tests nuevos (210/210 en verde)
- `VERSION`, `README.md`, `ARQUITECTURA.md`, `arquitecturasUML.md`, `requirements/SPEC-004` — documentación a v2.6.0

---

## v2.5.0 — 2026-07-22

### Feature: Proyección de campos con `select` en items:search (SPEC-004)

**Contexto:** Los consumidores de `POST /v1/sharepoint/list/items:search` (p. ej. sobre la lista DSL) extraen registros cuyas filas tienen muchas columnas, pero solo usan unas pocas — cada respuesta arrastraba el `fields` completo de cada ítem.

**Solución:** Campo opcional `select` en el body (hasta 50 nombres internos de columna): el `fields` de cada ítem devuelto contiene **solo esas columnas**, vía `$expand=fields($select=...)` de Graph. Omitido o `[]` → todos los campos (retrocompatible con v2.4.0).

```json
{ "sharepoint_url": "https://.../Lists/DSL/Allitemsg.aspx",
  "filters": [ { "field": "Entorno", "value": "L02" } ],
  "select": ["Title", "Entorno", "_x00da_ltima"] }
```

- Cada nombre se valida en doble capa: patrón `[A-Za-z0-9_]+` (`422`) y esquema real de columnas (`400` si no existe — Graph ignora en silencio los nombres desconocidos en `$select`, ocultando typos del caller).
- **Asimetría deliberada con `filters`:** en `select` las columnas lookup admiten ambas formas (nombre base → valor visible; `{Columna}LookupId` → ID numérico); en `filters` el nombre base sigue rechazándose con `400`.
- Graph puede incluir claves de sistema (p. ej. `@odata.etag`) dentro de `fields` aun con proyección.

**Archivos modificados:**
- `app/schemas/sharepoint.py` — campo `select` en `ListItemsSearchByUrlRequest` (max 50, patrón por entrada)
- `app/services/sharepoint.py` — `_validate_select_column()`, construcción de `$expand=fields($select=...)` en `search_list_items()`
- `app/api/v1/endpoints/sharepoint.py` — paso de `select` y documentación del endpoint
- `tests/test_sharepoint_service.py`, `tests/test_sharepoint_endpoints.py` — 14 tests nuevos (128/128 en verde)
- `VERSION`, `README.md`, `ARQUITECTURA.md`, `arquitecturasUML.md`, `requirements/SPEC-004` — documentación a v2.5.0

---

## v2.4.0 — 2026-07-22

### Feature: Búsqueda multi-campo de ítems de lista (SPEC-004)

**Contexto:** El conector permitía crear, actualizar (por un campo único) y hacer upsert (clave + periodo) de ítems por URL, pero no **leer/buscar** por varios campos a la vez sin construir a mano un `$filter` OData contra `/v1/graph/...`. Además, el primitivo más cercano (`find_list_items_by_field`) trata todo valor como string, lo que produce resultados **silenciosamente incorrectos** con columnas Sí/No: Graph ignora sin error las cláusulas `eq true|false` (y `eq 'true'`) sobre esas columnas y devuelve todas las filas como si el filtro no existiera.

**Solución:** Nuevo `POST /v1/sharepoint/list/items:search` — hasta 15 condiciones de igualdad combinadas con **AND**, valores **tipados** y validación previa contra el esquema real de la lista.

```json
{
  "sharepoint_url": "https://host.sharepoint.com/Oper/Lists/DSL/Allitemsg.aspx",
  "filters": [
    { "field": "Entorno", "value": "L02" },
    { "field": "_x00da_ltima", "value": true }
  ],
  "top": 100
}
```

- **Booleano → `1`/`0`, nunca `true`/`false`** (verificado empíricamente contra la lista DSL, 2026-07-22): el caller envía booleanos JSON puros y el conector los traduce internamente al único literal OData que filtra de verdad las columnas Sí/No. String → `'texto'` con comillas duplicadas; número → literal sin comillas.
- **Validación contra el esquema de columnas** (`GET .../columns`, caché por `(site_id, list_id)` con TTL 300 s): campo inexistente → `400` nombrándolo; tipo JSON ≠ tipo de columna → `400` con el tipo esperado (sin coerción); columna lookup por su nombre base → `400` (debe usarse `{Columna}LookupId`, tipo entero). Valor bien tipado sin coincidencias → `200` con `total: 0`.
- **`extra="forbid"`**: cualquier campo desconocido del body (p. ej. `filter_by`) → `422`, nunca ignorado.
- **`order_by`** (`$orderby`): en listas por encima del umbral de vista (~5000 filas) Graph responde `notSupported` al ordenar por columnas no indexadas; el error se propaga con detalle (usar `Created`, `Modified`, `ID`).
- **`top`** (1–5000, default 100): Graph trata `$top` como tamaño de página; el conector sigue `@odata.nextLink` hasta reunir `top` ítems, trunca el excedente y devuelve `has_more`. Paginación por cursor: pospuesta conscientemente (retrocompatible añadirla).
- Se mantienen las garantías existentes: validación de campo `[A-Za-z0-9_]+` en doble capa (`422` schema + `400` servicio), percent-encoding de la expresión completa, cabecera `Prefer: HonorNonIndexedQueriesWarningMayFailRandomly`.

**Archivos nuevos:**
- `requirements/SPEC-004_List_Items_Search_By_Url.md` — especificación de la feature

**Archivos modificados:**
- `app/services/sharepoint.py` — `_to_odata_literal()`, `_search_field_expected_type()`, `_check_search_value_type()`, `get_list_columns()` (caché TTL 300 s), `_get_bounded()` (paginación acotada), `search_list_items()`
- `app/schemas/sharepoint.py` — `SearchFilter`, `OrderBy`, `ListItemsSearchByUrlRequest` (extra="forbid"), `ListItemsSearchByUrlItem`, `ListItemsSearchByUrlResponse`
- `app/api/v1/endpoints/sharepoint.py` — endpoint `POST /v1/sharepoint/list/items:search`
- `tests/test_sharepoint_service.py`, `tests/test_sharepoint_endpoints.py` — 30 tests nuevos (literales OData, AND multi-campo, validación de esquema, lookup, caché, orderby, truncado/has_more, 422s)
- `VERSION`, `README.md`, `ARQUITECTURA.md`, `arquitecturasUML.md` — documentación a v2.4.0

---

## v2.3.0 — 2026-06-18

### Feature: Upsert genérico de ítems de lista (SPEC-003)

**Contexto:** Un servicio externo disparaba un flujo de Power Automate que, antes de insertar en una lista, **verificaba** si ya existía un registro equivalente (misma clave dentro del mes en curso) y actualizaba o creaba según el resultado. Se migra esa lógica "verificar-y-decidir" a este conector, manteniéndola **genérica**: ninguna lista, columna clave, campo de fecha ni esquema de datos queda quemado en el código — todo viaja en el payload, de modo que cualquier lista de SharePoint pueda reutilizar el mismo endpoint.

**Solución:** Se añade `POST /v1/sharepoint/list/item:upsert` (tercera vía: endpoint de *upsert* explícito, evitando el anti-patrón *flag-argument* sobre el `POST`/`PATCH` ya estables). Orquesta sobre primitivos existentes: resuelve la URL a `site_id`+`list_id` (resolver de SPEC-002), busca coincidencias y crea o actualiza.

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

- **Coincidencia** = igualdad exacta sobre `key_field` **y** (opcional) rango sobre `date_field`. `period` admite atajos con nombre (`current_day`, `current_week`, `current_month`, `current_year`) o rango explícito `{from,to}` (`to` inclusive). Sin `period`/`date_field`, solo por clave; `period` sin `date_field` se rechaza con `422`.
- **Periodo en la zona horaria del tenant:** los límites se calculan en `TENANT_TIMEZONE` (IANA, default `UTC`) y se convierten a UTC para el `$filter` de Graph (intervalo semiabierto `ge`/`lt`).
- **Desenlace:** `result="created"` si 0 coincidencias; `result="updated"` si ≥1 (se actualiza la **primera**). A diferencia del `PATCH`, varias coincidencias **no** son `409`: se emite un `warning` en logs y se devuelve `matched` (nº de coincidencias) para detectar duplicados preexistentes.

**Archivos nuevos:**
- `app/services/period.py` — `resolve_period()`: atajo con nombre / rango explícito → intervalo UTC en la zona del tenant
- `tests/test_period.py` — 10 tests (rango explícito, conversión TZ, atajos, validaciones)

**Archivos modificados:**
- `app/services/sharepoint.py` — nuevo método `find_list_items_for_upsert()` (filtro combinado clave + rango de fecha)
- `app/schemas/sharepoint.py` — modelos `ExplicitPeriod`, `UpsertMatch` (con validador period↔date_field), `UpsertListItemRequest`, `UpsertListItemResponse`
- `app/api/v1/endpoints/sharepoint.py` — endpoint `POST /v1/sharepoint/list/item:upsert`
- `app/core/config.py` — setting `tenant_timezone` (default `UTC`)
- `requirements.txt` — `tzdata` (zoneinfo en Windows)
- `tests/test_sharepoint_endpoints.py`, `tests/test_sharepoint_service.py` — 10 tests nuevos (created/updated/empate, validaciones, filtro)
- `VERSION`, `README.md`, `ARQUITECTURA.md`, `TECNOLOGIAS.md`, `devops/.env.example` — documentación y config a v2.3.0

---

## v2.2.1 — 2026-06-11

### Fix: Endurecimiento de seguridad y paginación de Graph

**Contexto:** Una revisión del código detectó tres grupos de problemas: (1) entradas del caller interpoladas sin validar en URLs de Graph (inyección OData vía `filter_by.field`, escape de carpeta vía `filename`/`folder` en subidas), (2) ausencia de límite de tamaño en uploads (el archivo se bufferizaba entero en memoria antes de fallar en Graph) y falta de seguimiento de la paginación de Graph (`@odata.nextLink`), que podía producir 404/409 falsos en sites con muchas listas/drives o listas grandes, y (3) higiene de configuración (ruta de logs personal hardcodeada como default, `VERSION` ausente de la imagen Docker → `/health` reportaba `0.0.0`).

**Cambios:**
- **Anti-inyección OData** — `filter_by.field` se valida contra `[A-Za-z0-9_]+` tanto en el schema (`422`) como en el servicio (`400`, defensa en profundidad); la expresión `$filter` completa se percent-encodea.
- **Saneo del path de subida** — `filename` y `folder` se rechazan con `400` si contienen `..`, separadores (`/` en filename, `\`) o caracteres de control; cada segmento se percent-encodea antes de construir la URL de Graph (`_encode_drive_path`).
- **Límite de subida** — los dos endpoints de upload leen como máximo 4 MB + 1 byte y responden `413` si se supera, sin bufferizar el archivo completo ni llamar a Graph (`app/core/uploads.py`).
- **Paginación** — nuevo helper `_get_all()` que sigue `@odata.nextLink`; aplicado a `list_sites`, `list_site_lists`, `list_site_drives`, `list_folder_children` y `find_list_items_by_field`. El término `search` de `/sites` también se percent-encodea.
- **Configuración** — `log_dir` ya no tiene una ruta personal hardcodeada como default (vacío = solo consola; configurable con `LOG_DIR`/`LOG_FILE`). El Dockerfile copia `VERSION`, de modo que `/health` reporta la versión real.

**Archivos nuevos:**
- `app/core/uploads.py` — `read_upload()` con `MAX_UPLOAD_BYTES`
- `tests/test_sharepoint_service.py` — 22 tests (saneo de paths, validación de campo, escape OData, paginación)

**Archivos modificados:**
- `app/services/sharepoint.py` — `_encode_drive_path`, `_get_all`, validación de `field`, encoding de `$filter` y `search`
- `app/schemas/sharepoint.py` — `FilterBy.field` con `pattern`
- `app/api/v1/endpoints/sharepoint.py`, `app/api/v1/endpoints/files.py` — uso de `read_upload()`
- `app/core/config.py` — default de `log_dir` vacío
- `devops/Dockerfile` — `COPY VERSION .`
- `devops/.env.example` — variables `LOG_DIR`/`LOG_FILE`
- `tests/test_sharepoint_endpoints.py`, `tests/test_files_endpoint.py` — tests de `422` y `413`
- `VERSION`, `README.md`, `ARQUITECTURA.md`, `TECNOLOGIAS.md` — documentación a v2.2.1

---

## v2.2.0 — 2026-06-10

### Feature: Actualización de ítems de lista por URL localizados por campo único

**Contexto:** Los endpoints by-URL de la v2.1.0 solo permitían **crear** ítems. Actualizar un registro existente exigía conocer su `item_id` interno de Graph, dato del que el caller no dispone: solo tiene la URL de la lista y algún valor de negocio que identifica la fila (p.ej. un código `LATSUP-0000`).

**Solución:** Se añade `PATCH /v1/sharepoint/list/item`, que localiza el registro mediante `filter_by` (`field` + `value`) y aplica `data`. El conector busca el ítem vía Graph (`$filter` sobre `fields/{field}`) para obtener su `id` interno y luego actualiza sus campos. El `filter_by` debe identificar un único registro.

```json
{
  "sharepoint_url": "https://host.sharepoint.com/Oper/Lists/Incidencias/View.aspx",
  "filter_by": { "field": "_x006c_dq4", "value": "LATSUP-0000" },
  "data": { "Title": "Actualización", "Atendida": false }
}
```

Comportamiento según las coincidencias de `filter_by`: `200` si hay exactamente una (ítem actualizado), `404` si ninguna, `409` si más de una (filtro no único — no se modifica nada). La búsqueda añade la cabecera `Prefer: HonorNonIndexedQueriesWarningMayFailRandomly` porque las columnas custom no suelen estar indexadas; el valor se trata como texto y se escapa según OData.

**Archivos modificados:**
- `app/services/sharepoint.py` — helper `_patch`, `_get` admite `extra_headers`, y nuevos métodos `find_list_items_by_field()` y `update_list_item()`
- `app/api/v1/endpoints/sharepoint.py` — endpoint `PATCH /v1/sharepoint/list/item`
- `app/schemas/sharepoint.py` — modelos `FilterBy`, `ListItemUpdateByUrlRequest`, `ListItemUpdateByUrlResponse`
- `tests/test_sharepoint_endpoints.py` — 3 tests nuevos (éxito, 404, 409)
- `VERSION`, `README.md`, `ARQUITECTURA.md`, `TECNOLOGIAS.md` — documentación a v2.2.0

---

## v2.1.0 — 2026-06-10

### Feature: Endpoints orientados a usuario con resolución por URL (SPEC-002)

**Contexto:** La v2.0.0 exige que el caller conozca `site_id`, `list_id` y `drive_id` de Graph. Obtener esos identificadores es inviable para un usuario humano, que solo dispone de la URL de SharePoint tal como aparece en el navegador.

**Solución:** Se añade una capa de resolución (`SharePointResolver`) y dos endpoints nuevos que aceptan una URL "humana" y derivan internamente los identificadores de Graph:
- `POST /v1/sharepoint/list/item` — inserta un ítem en una lista a partir de su URL.
- `POST /v1/sharepoint/upload` — sube un archivo a una biblioteca/carpeta a partir de su URL (`multipart/form-data`).

El site se resuelve con `GET /sites/{host}:/{path}` recortando segmentos hacia la raíz (cubre raíz, managed paths como `/Oper`, `/sites/X`, `/teams/X` y subsites). Las listas se emparejan por `webUrl`/nombre y las bibliotecas por prefijo del `webUrl` del drive, con la carpeta destino tomada del parámetro `?id=` cuando está presente. Los IDs de site resueltos se cachean (son estables). Las claves de `data` siguen siendo los nombres internos de columna.

Los endpoints `/v1/graph/...` de la v2.0.0 se mantienen intactos como API de bajo nivel/discovery.

**Archivos nuevos:**
- `app/services/resolver.py` — `SharePointResolver` (parseo de URL, resolución de site/lista/drive/carpeta)
- `app/api/v1/endpoints/sharepoint.py` — endpoints `POST /v1/sharepoint/list/item` y `POST /v1/sharepoint/upload`
- `app/schemas/sharepoint.py` — modelos de request/response by-URL
- `tests/test_resolver.py`, `tests/test_sharepoint_endpoints.py` — 13 tests nuevos

**Archivos modificados:**
- `app/services/sharepoint.py` — nuevo método `get_site_by_path(hostname, path)`
- `app/core/dependencies.py` — singleton `get_resolver()`
- `app/api/v1/router.py` — registro del router `sharepoint`
- `VERSION`, `README.md`, `ARQUITECTURA.md` — documentación a v2.1.0

---

## v2.0.0 — 2026-06-09

### Refactor: Arquitectura dinámica multi-site con API versionada (SPEC-001)

**Contexto:** La v1.0.0 estaba acoplada a un site SharePoint fijo configurado mediante `SITE_URL`. Los endpoints eran planos (`POST /upload`, `POST /list`) sin versionado y la subida de archivos usaba Base64 en JSON. No había endpoints de discovery, ni descarga, ni metadatos. El logging era básico y los errores de autenticación se propagaban como 500 genérico.

**Solución:** Rediseño completo de la arquitectura hacia un servicio genérico y dinámico. El site, la lista y el drive se identifican por ID en cada llamada. Se introduce una API REST versionada bajo `/v1/graph/...` con 9 endpoints organizados en tres grupos (Discovery, List Items, Files). El logging pasa a JSON estructurado con `request_id` y `client_app_id` propagados a todo el stack.

**Archivos nuevos:**
- `app/core/auth.py` — `TokenManager` con manejo tipado de errores y logging de adquisición de token
- `app/core/config.py` — `Settings` con pydantic-settings, lee versión desde `VERSION`
- `app/core/context.py` — `ContextVar`s para `request_id` y `client_app_id`
- `app/core/dependencies.py` — singletons inyectables vía `lru_cache`
- `app/core/exceptions.py` — `GraphAPIError` tipado con handlers para FastAPI; `-> NoReturn`
- `app/core/logging.py` — `JSONFormatter` con whitelist de campos estructurados
- `app/api/v1/router.py` — router versionado bajo `/v1`
- `app/api/v1/endpoints/discovery.py` — 4 endpoints de exploración de sites/listas/drives/carpetas
- `app/api/v1/endpoints/list_items.py` — GET y POST de ítems de lista
- `app/api/v1/endpoints/files.py` — upload (`multipart/form-data`), metadata y download
- `app/schemas/discovery.py`, `files.py`, `list_items.py` — modelos Pydantic por dominio
- `VERSION` — fuente única de verdad para el número de versión
- `requirements-dev.txt`, `pytest.ini`, `tests/` — suite de tests unitarios (12 tests)

**Archivos eliminados (v1):**
- `app/auth.py`, `app/config.py`, `app/dependencies.py`, `app/models.py`
- `app/routers/upload.py`, `app/routers/list_item.py`

**Archivos modificados:**
- `app/main.py` — middleware con try/except para trazabilidad en 500; exception handlers registrados
- `app/services/sharepoint.py` — métodos de discovery, metadata, download; logging completo
- `devops/docker-compose.yml`, `devops/.env.example`, `devops/deploy.sh` — limpieza de config muerta
- `ARQUITECTURA.md`, `README.md` — documentación actualizada a v2.0.0

---

## v1.0.0 — 2026-05-19

### Feature: Microservicio inicial de integración con SharePoint

**Contexto:** La integración entre Jirito Newsletter y SharePoint se realizaba a través de Power Automate, con errores 408/429/5xx no visibles en logs y lógica distribuida entre código y flujos visuales externos.

**Solución:** Microservicio FastAPI con dos endpoints planos que reemplazaban los webhooks de Power Automate: `POST /upload` (archivo en Base64 + JSON) y `POST /list` (crear ítem en lista). Site único configurado en `SITE_URL`. `TokenManager` con caché de token OAuth2. Logging básico.

**Archivos:**
- `app/main.py`, `app/auth.py`, `app/config.py`, `app/dependencies.py`, `app/models.py`
- `app/routers/upload.py`, `app/routers/list_item.py`
- `app/services/sharepoint.py`
- `devops/Dockerfile`, `devops/docker-compose.yml`, `devops/.env.example`, `devops/deploy.sh`
