# SharePoint Connector

Microservicio REST que reemplaza Power Automate para operaciones sobre SharePoint vía Microsoft Graph API. Expone una API genérica y versionada que opera sobre cualquier site, lista y biblioteca de documentos de forma dinámica.

**Versión actual:** 2.6.1

---

## Endpoints

El conector ofrece dos niveles de API:

- **`/v1/sharepoint`** — orientada a usuario: se pasa una **URL de SharePoint** (la de la barra de direcciones del navegador) y el conector resuelve por sí mismo los identificadores de Graph. Es la vía recomendada cuando quien llama es una persona.
- **`/v1/graph`** — de bajo nivel: opera con `site_id`/`list_id`/`drive_id` ya conocidos (obtenidos vía discovery). Útil para integraciones que cachean los IDs.

Ver [ARQUITECTURA.md](ARQUITECTURA.md) para la referencia completa.

### SharePoint (por URL)

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/v1/sharepoint/list/item` | Inserta un ítem en una lista identificada por su URL |
| `PATCH` | `/v1/sharepoint/list/item` | Actualiza un ítem localizándolo por un campo único (`filter_by`) |
| `POST` | `/v1/sharepoint/list/item:upsert` | Inserta o actualiza un ítem según coincidencia por clave (+ periodo opcional) |
| `POST` | `/v1/sharepoint/list/items:search` | Busca ítems con hasta 15 filtros multi-campo (AND) y operadores `eq`/`in`/`ge`/`gt`/`le`/`lt`, validados contra el esquema de la lista |
| `POST` | `/v1/sharepoint/upload` | Sube un archivo a una biblioteca/carpeta identificada por su URL (`multipart/form-data`) |

### Discovery

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/v1/graph/sites` | Lista los sites accesibles por la aplicación |
| `GET` | `/v1/graph/sites/{site_id}/lists` | Lista las listas de un site |
| `GET` | `/v1/graph/sites/{site_id}/drives` | Lista las bibliotecas de documentos de un site |
| `GET` | `/v1/graph/sites/{site_id}/drives/{drive_id}/items` | Navega carpetas y archivos de un drive |

### List Items

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/v1/graph/sites/{site_id}/lists/{list_id}/items` | Lee ítems de una lista |
| `POST` | `/v1/graph/sites/{site_id}/lists/{list_id}/items` | Inserta un ítem en una lista |

### Files

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/v1/graph/sites/{site_id}/drives/{drive_id}/files` | Sube un archivo (`multipart/form-data`) |
| `GET` | `/v1/graph/sites/{site_id}/drives/{drive_id}/items/{item_id}` | Metadatos de un archivo o carpeta |
| `GET` | `/v1/graph/sites/{site_id}/drives/{drive_id}/items/{item_id}/download` | Descarga el contenido de un archivo |

### Health

```
GET /health  →  { "status": "ok", "service": "SharePoint Connector", "version": "2.6.1" }
```

---

## Uso por URL (recomendado para usuarios)

No requiere conocer los IDs de Graph: basta con la URL de SharePoint.

### Crear un ítem en una lista

```bash
curl -X POST "http://localhost:8003/v1/sharepoint/list/item" \
  -H "Content-Type: application/json" \
  -H "X-App-ID: mi-app" \
  -d '{
    "sharepoint_url": "https://latinia2com-portal8.sharepoint.com/Oper/Lists/Registro%20incidencias%2024x7/View_RegistroInci.aspx",
    "data": {
      "Title": "Incidencia en servidor de producción",
      "Prioridad": "Alta",
      "Responsable": "jlopeza@latinia.com"
    }
  }'
```

Las claves de `data` deben ser los **nombres internos** de las columnas.

### Actualizar un ítem de una lista

Se localiza el registro con `filter_by` (un campo único y su valor) y se aplican los cambios de `data`:

```bash
curl -X PATCH "http://localhost:8003/v1/sharepoint/list/item" \
  -H "Content-Type: application/json" \
  -H "X-App-ID: mi-app" \
  -d '{
    "sharepoint_url": "https://latinia2com-portal8.sharepoint.com/Oper/Lists/Registro%20incidencias%2024x7/View_RegistroInci.aspx",
    "filter_by": { "field": "_x006c_dq4", "value": "LATSUP-0000" },
    "data": {
      "Title": "Prueba de inyección - actualización",
      "y4ap": "Baja",
      "Atendida": false
    }
  }'
```

`filter_by` debe identificar un **único** registro: si ninguno coincide se devuelve `404`, y si coincide más de uno, `409` (sin modificar nada).

**Por `ID` (v2.6.1):** con `"filter_by": { "field": "ID", "value": "1087" }` el conector no busca, sino que actualiza directamente el ítem con ese identificador (Graph no admite filtrar por `ID`). El valor debe ser numérico (si no, `400`); un `ID` inexistente devuelve `404`.

### Insertar o actualizar un ítem (upsert)

Migra a código la lógica "verificar-y-decidir" del flujo de Power Automate: el
conector busca un registro que coincida con `match` y, según el resultado, **crea**
o **actualiza**. Es genérico (sirve a cualquier lista): la columna clave, la de
fecha y el esquema de `data` viajan en el payload.

```bash
curl -X POST "http://localhost:8003/v1/sharepoint/list/item:upsert" \
  -H "Content-Type: application/json" \
  -H "X-App-ID: mi-app" \
  -d '{
    "sharepoint_url": "https://latinia2com-portal8.sharepoint.com/Oper/Lists/Registro%20incidencias%2024x7/View_RegistroInci.aspx",
    "match": {
      "key_field": "TicketId",
      "key_value": "INC-12345",
      "date_field": "Created",
      "period": "current_month"
    },
    "data": { "Title": "Incidencia", "Estado": "Resuelto" }
  }'
```

- `match.key_field` + `key_value`: igualdad exacta sobre la columna clave.
- `match.date_field` + `period` (opcional): acota la coincidencia a un periodo.
  `period` admite un atajo con nombre (`current_day`, `current_week`,
  `current_month`, `current_year`) o un rango explícito `{ "from": "...", "to": "..." }`.
  Los límites se calculan en la zona horaria del tenant (`TENANT_TIMEZONE`).
  Sin `period`/`date_field`, la coincidencia es solo por clave.

La respuesta indica `result: "created"` o `"updated"`, con `id`, `site_id`,
`list_id` y `matched` (nº de coincidencias). Ante varias coincidencias se actualiza
la **primera** y se registra un `warning` (no es error).

### Buscar ítems de una lista

Búsqueda con hasta 15 condiciones combinadas con **AND**. Cada valor viaja en su
tipo JSON natural (string, boolean sin comillas, número) y se valida contra el
esquema real de la lista antes de consultar Graph:

```bash
curl -X POST "http://localhost:8003/v1/sharepoint/list/items:search" \
  -H "Content-Type: application/json" \
  -H "X-App-ID: mi-app" \
  -d '{
    "sharepoint_url": "https://latinia2com-portal8.sharepoint.com/Oper/Lists/DSL/Allitemsg.aspx",
    "filters": [
      { "field": "Entorno", "value": "L02" },
      { "field": "_x00da_ltima", "value": true }
    ],
    "top": 100
  }'
```

- `filters` (opcional, hasta 15): condiciones `{field, op, value}` con nombres
  **internos** de columna. Omitido o `[]` → se devuelven los primeros `top` ítems
  sin filtrar. Campo inexistente, operador que la columna no admite o tipo que no
  corresponde a la columna → `400` explícito (nunca una cláusula ignorada en
  silencio). Cualquier campo desconocido en el body → `422`.
- **Booleanos**: se envían como `true`/`false` JSON (sin comillas). Internamente el
  conector los traduce al literal OData `1`/`0` — Graph **ignora en silencio**
  `eq true|false` sobre columnas Sí/No (devuelve todas las filas sin error); con
  `1`/`0` sí filtra (verificado empíricamente contra la lista DSL).
- **Columnas lookup**: se filtran por su campo `{Columna}LookupId` con el ID
  numérico (p. ej. `Cliente_x002d_LIBSALookupId`); filtrar por la columna base da `400`.

#### Operadores (`op`)

`op` es opcional y vale `eq` por defecto, así que **cualquier body escrito para la
v2.5.0 sigue funcionando sin cambios**. Los operadores son un conjunto cerrado:
cualquier otro token → `422`.

| `op`                | `value`                     | Columnas admitidas |
| ------------------- | --------------------------- | ------------------ |
| `eq`                | escalar                     | todas |
| `in`                | array de 1–20, mismo tipo   | todas menos Sí/No |
| `ge` `gt` `le` `lt` | escalar                     | número, moneda, fecha/hora, `{Columna}LookupId` |

- **`in`** se traduce a un grupo OR de igualdades **entre paréntesis** —
  `(fields/Estado eq 'Iniciado' or fields/Estado eq 'Pausado')` — no al operador
  nativo `in` de OData, cuyo soporte sobre columnas de listas de SharePoint no
  está verificado (y si no estuviera soportado, el fallo sería el peligroso:
  cláusula descartada y `200` con todas las filas). El OR existe **solo dentro**
  de un `in`; entre condiciones el enlace es siempre AND.
- **`ge`/`gt`/`le`/`lt`** solo se admiten donde el orden significa algo. Sobre una
  columna de texto o *choice* → `400` con el nombre y el tipo de la columna: OData
  compararía lexicográficamente y el resultado parecería lo bastante plausible
  para pasar desapercibido.
- **`in` sobre una columna Sí/No** → `400`: con dos valores posibles, `in` es o un
  `eq` redundante o una tautología que devuelve todas las filas.
- **Fechas**: el valor es un instante **ISO-8601 y se interpreta en UTC tal como
  viene** — el conector *no* le aplica la zona del tenant (a diferencia del
  `period` del upsert, donde el conector calcula los límites y por eso necesita la
  zona). Un valor con offset se normaliza a UTC y uno sin zona se toma como UTC:
  `2026-06-01T00:00:00Z`, `2026-06-01T02:00:00+02:00`, `2026-06-01T00:00:00` y
  `2026-06-01` seleccionan exactamente las mismas filas. Un valor no parseable →
  `400` con el nombre de la columna y el formato esperado.
- **Texto: Graph compara sin distinguir mayúsculas.** `{"field":"Entorno","value":"L02"}`
  también devuelve las filas guardadas como `l02`. Vale igual para `eq` y para `in`, y
  significa que un `in` no sirve para distinguir valores que solo difieren en la caja.
- **Presupuesto de cláusulas**: tras expandir todos los `in`, el total de cláusulas
  generadas no puede pasar de **100** → `422` indicando el número generado y el
  límite. ⚠️ **El techo real de Graph es la longitud de la URL** (~5 KB), medido el
  2026-09-03: caben 115 cláusulas cortas de texto, pero solo **73** de fecha y **88**
  de lookup, y al pasarse Graph responde `404`. Con arrays `in` grandes de valores
  largos conviene quedarse bastante por debajo de las 100.

**Ejemplo — el caso que motivó los operadores.** Los ítems de
`HistorialConsumoBolsas` en cualquiera de los tres estados abiertos, más los
completados de los últimos 15 días. Antes: **4 llamadas + un filtrado `jq` en
cliente**. Ahora, **2 llamadas y ningún post-filtrado**:

```json
{ "sharepoint_url": "...", "filters": [
  { "field": "Estado", "op": "in",
    "value": ["Para Entregar", "Iniciado", "Pausado"] }
]}
```

```json
{ "sharepoint_url": "...", "filters": [
  { "field": "Estado", "value": "Completado" },
  { "field": "FechaFinalizado", "op": "ge", "value": "2026-08-18T00:00:00Z" }
]}
```

> **Los operadores no levantan el umbral de vista de SharePoint (~5000 filas).**
> Al contrario: un grupo OR o una comparación sobre una columna **no indexada** es
> justo el tipo de consulta que Graph no puede servir desde un índice, así que en
> listas grandes es más probable ver un `notSupported` (propagado con su detalle)
> que con un filtro de igualdad simple. Si la lista es grande, filtra por columnas
> indexadas. `ge`/`le` sí reducen cuántas veces se topa uno con el corte de `top`,
> porque el servidor descarta las filas fuera de rango en vez de gastar el
> presupuesto en ellas — pero `has_more` sigue sin cursor de paginación.

- `order_by` (opcional): `{ "field": "...", "direction": "asc"|"desc" }`. En listas
  que superan el **umbral de vista** de SharePoint (~5000 filas), Graph rechaza
  ordenar por columnas no indexadas (`notSupported`, error propagado con detalle);
  usa columnas indexadas como `Created`, `Modified` o `ID`.
- `select` (opcional, hasta 50 nombres): proyecta el `fields` de cada ítem a **solo
  esas columnas** — útil cuando la lista tiene muchos campos y solo se usan unos pocos:

  ```json
  "select": ["Title", "Entorno", "_x00da_ltima"]
  ```

  Columna inexistente en `select` → `400` (Graph la ignoraría en silencio; el conector
  no). Las columnas lookup admiten ambas formas: el nombre base (valor visible) y
  `{Columna}LookupId` (ID numérico). Omitido o `[]` → todos los campos. Graph puede
  incluir además claves de sistema (p. ej. `@odata.etag`) dentro de `fields`.
- `top` (1–5000, default 100): acota el resultado siguiendo la paginación de Graph;
  la respuesta incluye `has_more` si quedaron coincidencias fuera del corte.

Respuesta: `{ total, items[{id, webUrl, fields}], has_more, site_id, list_id }`.
0 coincidencias → `200` con `total: 0`.

### Subir un archivo

```bash
curl -X POST "http://localhost:8003/v1/sharepoint/upload" \
  -H "X-App-ID: mi-app" \
  -F "sharepoint_url=https://latinia2com.sharepoint.com/sites/IADocs/Documentos%20compartidos/Forms/AllItems.aspx?id=%2Fsites%2FIADocs%2FDocumentos%20compartidos%2FAreas%2FAdvisors%2FOnlyTest" \
  -F "file=@./TAM.txt"
```

El conector resuelve la URL a `site_id` + `drive_id` + carpeta destino; la carpeta del parámetro `?id=` se crea automáticamente si no existe.

---

## Flujo de uso típico (API de bajo nivel)

```
1. GET /v1/graph/sites?search=soporte         → obtener site_id
2. GET /v1/graph/sites/{site_id}/lists        → obtener list_id
3. GET /v1/graph/sites/{site_id}/drives       → obtener drive_id
4. POST .../lists/{list_id}/items             → crear ítem
5. POST .../drives/{drive_id}/files?folder=X  → subir archivo
```

Los IDs de site, lista y drive son estables; solo es necesario hacer discovery una vez.

---

## Configuración

Copia `devops/.env.example` a `devops/.env` y rellena los valores:

| Variable | Requerida | Descripción |
|---|---|---|
| `TENANT_ID` | Sí | ID del tenant Azure AD |
| `CLIENT_ID` | Sí | ID del App Registration |
| `CLIENT_SECRET` | Sí | Secreto del App Registration |
| `LOG_LEVEL` | No | `DEBUG` / `INFO` / `WARNING` / `ERROR` (default: `INFO`) |
| `TENANT_TIMEZONE` | No | Zona horaria IANA del tenant para acotar periodos del upsert (default: `UTC`) |
| `LOG_DIR` | No | Directorio del log rotativo a fichero; vacío = solo consola (default: vacío) |
| `LOG_FILE` | No | Nombre del fichero de log (default: `api_server_sp_connector.log`) |
| `SP_PORT` | No | Puerto expuesto en el host (default: `8003`) |

### Permisos Azure AD requeridos

El App Registration necesita los permisos de aplicación:
- `Sites.Read.All` — para discovery y lectura
- `Sites.ReadWrite.All` — para escritura (crear ítems, subir archivos)

---

## Arranque

```bash
cp devops/.env.example devops/.env
# Editar devops/.env con los valores reales
docker compose -f devops/docker-compose.yml up -d --build
```

La documentación interactiva (Swagger UI) queda disponible en `http://localhost:8003/docs` (puerto configurable con `SP_PORT`).

---

## Subida de archivos

El endpoint de subida recibe `multipart/form-data`:

```bash
curl -X POST "http://localhost:8003/v1/graph/sites/{site_id}/drives/{drive_id}/files?folder=DailyDelivery" \
  -H "X-App-ID: mi-app" \
  -F "file=@informe.json"
```

La carpeta se crea automáticamente si no existe. El tamaño máximo es **4 MB** (límite de `PUT .../content` en Graph API); si se supera, el conector responde `413` sin llamar a Graph.

---

## Creación de ítem en lista

```bash
curl -X POST "http://localhost:8003/v1/graph/sites/{site_id}/lists/{list_id}/items" \
  -H "Content-Type: application/json" \
  -H "X-App-ID: mi-app" \
  -d '{
    "fields": {
      "Title": "LATSUP-6585",
      "organization": "Acme Corp",
      "score": 9.5,
      "resolved": true
    }
  }'
```

Los nombres de campo deben ser los **nombres internos** de las columnas en SharePoint.

---

## Cabeceras HTTP

| Cabecera | Dirección | Descripción |
|---|---|---|
| `X-App-ID` | Request | Identificador del caller — se registra en todos los logs |
| `X-Request-ID` | Response | UUID de trazabilidad generado por el middleware |

---

Ver [ARQUITECTURA.md](ARQUITECTURA.md) para la referencia completa de la API, diagramas de arquitectura y detalles de despliegue.

## Repositorio

Actualmente, el repositorio de la aplicación se accede por medio de esta URL:

'''https://devg.corp.latiniaservices.com/juclopezal/SharepointConnector'''