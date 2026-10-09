# SPEC-004: Búsqueda multi-campo de ítems de lista por URL

> **Estado:** ✅ Implementada (v2.4.0), extendida en v2.5.0 (proyección
> `select`) y v2.6.0 (operadores de filtro). Criterios de código y tests
> cubiertos en las tres iteraciones; la verificación empírica de cada una queda
> registrada en la **Bitácora de IA** al final — la de v2.6.0 está **pendiente**.

## 1. Contexto (El Problema)

El conector permite crear (`POST /list/item`), actualizar por un campo único
(`PATCH /list/item`) y hacer upsert por clave+periodo (`POST /list/item:upsert`)
sobre ítems de lista identificados por su URL, pero **no leer/buscar** ítems por
**varios campos a la vez**: el consumidor tendría que construir a mano un
`$filter` OData contra la API de bajo nivel `/v1/graph/...`.

Además, el primitivo de búsqueda existente (`find_list_items_by_field`) trata
todo valor como string. Con columnas **Sí/No** eso produce resultados
**silenciosamente incorrectos**: Graph **ignora sin error** las cláusulas
`fields/{col} eq true|false` (y también `eq 'true'` como string) sobre esas
columnas y devuelve TODAS las filas como si la condición no existiera. Solo
`eq 1`/`eq 0` filtra de verdad (verificado empíricamente contra la lista DSL el
2026-07-22). Ese modo de fallo — cláusula descartada en silencio, respuesta
plausible pero errónea — es exactamente lo que esta spec debe eliminar.

> **Alcance: genérico, no atado a una lista.** Igual que SPEC-003, nada del
> dominio viaja quemado en el código: la lista, los campos y los tipos van en el
> payload. La lista DSL es solo el caso motivador y el banco de pruebas.

## 2. Propuesta (La Solución)

Nuevo endpoint `POST /v1/sharepoint/list/items:search`:

```json
{
  "sharepoint_url": "https://host.sharepoint.com/Oper/Lists/DSL/Allitemsg.aspx",
  "filters": [
    { "field": "Entorno", "value": "L02" },
    { "field": "_x00da_ltima", "value": true }
  ],
  "order_by": { "field": "Created", "direction": "desc" },
  "top": 100
}
```

- **`filters`** (opcional, hasta 15): condiciones combinadas con **AND** —
  de igualdad hasta la v2.5.0; desde la v2.6.0 cada una lleva un `op` opcional
  (`eq` por defecto) de un enum cerrado `eq | in | ge | gt | le | lt`, y el OR
  existe **solo dentro** de un `in`. Omitido o `[]` → primeros `top` ítems sin
  filtrar. Es la **única**
  forma de filtrado (decisión 2026-07-22 con el propietario: no se arrastra
  `filter_by`, que solo existe en el update con otra semántica); el request usa
  `extra="forbid"` → campo desconocido = `422`.
- **Valores tipados** (`str | bool | int | float`; null y objeto → `422`; array
  solo con `op: "in"`, de 1 a 20 elementos del mismo tipo JSON).
  El caller envía booleanos JSON puros (`true`, sin comillas); la traducción al
  literal OData es interna: string → `'texto'` (comillas duplicadas), boolean →
  **`1`/`0`** (nunca `true`/`false`), número → literal sin comillas.
- **Validación contra el esquema real** (`GET .../columns`, caché por
  `(site_id, list_id)` con TTL 300 s): campo inexistente → `400` nombrándolo;
  tipo JSON ≠ tipo de columna → `400` con el tipo esperado (**sin coerción**);
  columna lookup por su nombre base → `400` (usar `{Columna}LookupId`, entero).
  Valor bien tipado sin coincidencias → `200` con `total: 0` (conducto regular).
- **`order_by`** → `$orderby`. Limitación conocida: por encima del umbral de
  vista (~5000 filas) Graph responde `notSupported` con columnas no indexadas;
  se propaga con detalle (recomendar `Created`, `Modified`, `ID`).
- **`top`** (1–5000, default 100): Graph trata `$top` como tamaño de página; el
  conector sigue `@odata.nextLink` hasta reunir `top`, trunca y devuelve
  `has_more`. Paginación por cursor: pospuesta conscientemente.
- Garantías existentes: campo `[A-Za-z0-9_]+` en doble capa (`422`/`400`),
  percent-encoding de la expresión completa, cabecera
  `Prefer: HonorNonIndexedQueriesWarningMayFailRandomly`.
- **Respuesta:** `200` con
  `{ total, items[{id, webUrl, fields}], has_more, site_id, list_id }`
  (`total` = ítems devueltos tras el truncado).

## 3. Criterios de Aceptación

1. Multi-campo AND: `Entorno="L02"` + `_x00da_ltima=true` devuelve solo filas
   que cumplen ambas condiciones.
2. Booleano JSON puro filtra correctamente (literal interno `1`/`0`); todos los
   ítems devueltos traen el valor booleano pedido en `fields`.
3. Campo inexistente o tipo que no casa con la columna → `400` explícito con el
   campo/tipo ofendido; jamás una cláusula ignorada en silencio.
4. String `"true"` contra columna Sí/No → `400` (no N filas sin filtrar).
5. Columna lookup: `{Base}LookupId` + entero aceptado; nombre base directo → `400`.
6. `filter_by` u otro campo desconocido en el body → `422`; >15 filtros → `422`;
   `top` fuera de 1–5000 → `422`; `value` null/objeto/array → `422`.
7. `filters` omitido o `[]` → `200` con los primeros `top` ítems sin filtrar.
8. Truncado por `top` con `has_more` correcto siguiendo `@odata.nextLink`.
9. `order_by` genera `$orderby`; el error de umbral de Graph se propaga con detalle.
10. Esquema de columnas cacheado: segunda búsqueda sobre la misma lista dentro
    del TTL no repite `GET .../columns`.
11. Suite completa de `pytest` en verde y verificación empírica contra la lista
    DSL real (valores de `fields` inspeccionados, no solo `total`).
12. (v2.6.0) `op` omitido ≡ `eq`, con el mismo `$filter` generado; `in` como
    grupo OR entre paréntesis que no absorbe el `and` entre filtros; operador no
    admitido por el tipo de columna → `400`; array de `in` vacío, >20 o de tipos
    mezclados → `422`; >100 cláusulas generadas → `422`; fecha no parseable →
    `400`. Detalle en la Bitácora, iteración v2.6.0.

## 4. Bitácora de IA / Historial de Implementación

### 2026-07-22 — Agente LLM (Claude Code)

Implementación completa de la spec sobre el cambio OpenSpec
`add-list-items-read-by-url` (artefactos en `openspec/changes/`).

**Archivos modificados:**
- `app/services/sharepoint.py` — helpers puros `_to_odata_literal()` (bool
  comprobado ANTES que int: `bool` es subclase de `int`),
  `_search_field_expected_type()` (resolución de columna con el caso lookup:
  directo → `400`, sufijo `LookupId` → entero) y `_check_search_value_type()`
  (sin coerción); métodos `get_list_columns()` (caché dict `(site_id, list_id)`
  → `(columns, fetched_at)`, TTL fijo 300 s vía `time.monotonic()`, sin
  dependencia nueva), `_get_bounded()` (variante acotada de `_get_all` que corta
  en `top` y calcula `has_more`) y `search_list_items()`.
- `app/schemas/sharepoint.py` — `SearchFilter`, `OrderBy`,
  `ListItemsSearchByUrlRequest` (`extra="forbid"`, `filters` con `max_length=15`
  y sin `min_length`: `[]` explícito equivale a omitirlo),
  `ListItemsSearchByUrlItem`, `ListItemsSearchByUrlResponse`.
- `app/api/v1/endpoints/sharepoint.py` — endpoint
  `POST /v1/sharepoint/list/items:search` (plural `items`, decisión consciente
  frente al singular `item:upsert`: la respuesta es una colección).
- `tests/test_sharepoint_service.py` / `tests/test_sharepoint_endpoints.py` —
  30 tests nuevos; suite completa 114/114 en verde.
- `VERSION` (2.4.0), `README.md`, `ARQUITECTURA.md`, `arquitecturasUML.md`,
  `doc/CHANGELOG.md`.

**Decisiones clave (confirmadas con el propietario el 2026-07-22):**
- Validación **reject-only** (sin coerción de `"35"`/`"true"`): fail-fast
  coherente con `extra="forbid"` y la validación de nombres de campo.
- Columna lookup base sin sufijo → `400` con guía (no best-effort como string):
  Graph no filtra de forma fiable la columna base y aceptar reintroduciría el
  fallo silencioso.
- TTL del caché de columnas fijo en 300 s (sin knob de configuración).
- Nombre `items:search` (plural) pese a la inconsistencia con `item:upsert`.

**Sin desviaciones** respecto al diseño (`openspec/changes/add-list-items-read-by-url/design.md`).

**Verificación empírica (2026-07-22, contra la lista DSL real, Docker local
`localhost:8001`, v2.4.0 confirmada en `/health`):**
- `Entorno = "no-existe-xyz"` → `total: 0`. ✓
- `_x00da_ltima = true` (solo booleano, `top: 5000`) → 3.838 ítems, **todos**
  con `_x00da_ltima: true` en `fields` (la cláusula filtra; con el literal
  `true` de Graph habría devuelto también los `false`). ✓
- `Cliente_x002d_LIBSALookupId = 35` AND `_x00da_ltima = true` → filas reales
  `Entorno "L02"` con LookupId 35 y ultima true; el combinado con las filas
  históricas `Entorno = "l01-2016"` (todas `_x00da_ltima = false`) da
  `total: 0`, correcto. ✓
- String `"true"` contra la columna Sí/No → `400` "la columna espera boolean". ✓
- `Cliente_x002d_LIBSA` (lookup base, sin sufijo) → `400` con guía hacia
  `Cliente_x002d_LIBSALookupId`. ✓
- Campo inexistente → `400` nombrándolo; `filter_by` en el body → `422`. ✓
- `order_by` sobre la lista completa (>5000 filas, columna no indexada) →
  error de Graph propagado con detalle íntegro (`code: notSupported`,
  "supera el umbral de vista de lista", innerError con request-id). ✓

### 2026-07-22 — Agente LLM (Claude Code) · Iteración v2.5.0: proyección `select`

**Requerimiento añadido (aprobado por el propietario el 2026-07-22):** campo
opcional `select` (hasta 50 nombres internos de columna) que proyecta el
`fields` de cada ítem devuelto a solo esas columnas, vía
`$expand=fields($select=...)` de Graph. Omitido o `[]` → todos los campos
(retrocompatible con v2.4.0).

**Criterios de aceptación de la iteración:**
1. Con `select`, los ítems devuelven solo las columnas pedidas (más las claves
   de sistema que Graph añade, p. ej. `@odata.etag`).
2. Columna inexistente en `select` → `400` nombrándola (Graph la ignoraría en
   silencio); patrón inválido o >50 entradas → `422`.
3. Columnas lookup: ambas formas válidas en `select` — nombre base (valor
   visible) y `{Columna}LookupId` (ID numérico) — asimetría deliberada con
   `filters`, donde el nombre base se rechaza.
4. Suite `pytest` en verde y verificación empírica contra la lista DSL real.

**Archivos modificados:** `app/schemas/sharepoint.py` (campo `select`),
`app/services/sharepoint.py` (`_validate_select_column`, construcción del
`$expand` proyectado), `app/api/v1/endpoints/sharepoint.py` (paso de `select`),
tests (14 nuevos, 128/128), docs y `VERSION` a 2.5.0.

**Verificación empírica (2026-07-22, lista DSL real, Docker local, v2.5.0 en `/health`):**
- `select: ["Title","Entorno","_x00da_ltima"]` con filtro `Entorno="L02"` → cada
  ítem trae en `fields` exactamente esas tres columnas más **una única clave de
  sistema: `@odata.etag`** (set exacto confirmado; ninguna otra). ✓
- Columna inexistente en `select` → `400` nombrándola (no ignorada). ✓
- Ambas formas lookup en `select`: `Cliente_x002d_LIBSA` proyecta el valor
  visible ("BANCOPPEL") y `Cliente_x002d_LIBSALookupId` el ID (35). ✓


### 2026-09-03 — Agente LLM (Claude Code) · Iteración v2.6.0: operadores de filtro

**Requerimiento añadido:** campo opcional `op` en cada condición de `filters`,
con `eq` por defecto, sobre el cambio OpenSpec `add-search-filter-operators`
(artefactos en `openspec/changes/`). Enum **cerrado** de seis operadores:

| `op`                | `value`                     | Columnas admitidas |
|---------------------|-----------------------------|--------------------|
| `eq`                | escalar                     | todas |
| `in`                | array de 1–20, mismo tipo   | todas menos Sí/No |
| `ge` `gt` `le` `lt` | escalar                     | número, moneda, fecha/hora, `{Columna}LookupId` |

**Contexto:** la búsqueda solo sabía expresar igualdad unida con AND, así que
«las filas en uno de estos estados» o «las finalizadas en los últimos 15 días»
no se podían formular. El caso motivador — `HistorialConsumoBolsas` con `Estado`
en *Para Entregar* / *Iniciado* / *Pausado*, más los *Completado* de los últimos
15 días — costaba **4 llamadas más un post-filtrado con `jq`**, y el corte por
fecha obligaba a descargar el histórico completo de completadas para descartar
casi todo. Con estos operadores son **2 llamadas y ningún post-filtrado**.

**Criterios de aceptación de la iteración:**
1. `op` omitido ≡ `eq`: un body de la v2.5.0 genera el **mismo** `$filter` y los
   mismos ítems (retrocompatibilidad byte a byte).
2. `in` se expande a un grupo OR de igualdades **entre paréntesis** —
   `(fields/Estado eq 'Iniciado' or fields/Estado eq 'Pausado')` — no al
   operador nativo `in` de OData; un array de un solo elemento también va entre
   paréntesis. El OR existe solo dentro de un `in`; entre condiciones, AND.
3. Combinado con un segundo filtro, la precedencia se conserva:
   `(...) and fields/Entorno eq 'L02'`. Una fila que cumple solo el `in` es un
   **bug bloqueante** (paréntesis perdidos), no una anomalía de datos.
4. `ge`/`gt`/`le`/`lt` solo contra número, moneda, fecha/hora y
   `{Base}LookupId`; contra texto o *choice* → `400` nombrando columna, tipo y
   operadores válidos. `in` contra Sí/No → `400`.
5. Un solo elemento mal tipado en un array de `in` → `400` del filtro entero;
   nada parcialmente aplicado llega a Graph.
6. Fechas: instante ISO-8601 **en UTC tal como viene** (sin aplicar
   `TENANT_TIMEZONE`, a diferencia del `period` del upsert), validado y
   canonicalizado para los operadores nuevos, y emitido **entrecomillado** — la
   forma que Graph evalúa de verdad, ver la verificación empírica. Valor no
   parseable → `400` con la columna y el formato.
7. Capa de schema (`422`, sin tocar Graph): `op` fuera del enum; `in` con
   escalar u otro operador con array; array vacío, de más de 20 o de tipos
   mezclados (`[true, 1]` incluido: `bool` no se colapsa en `int`); más de 100
   cláusulas generadas tras expandir los `in`, con el número y el límite en el
   mensaje.
8. Validar operadores **no** cuesta una segunda llamada a `GET .../columns`
   dentro del TTL del caché.
9. La expresión completa —paréntesis y espacios incluidos— se percent-encodea
   como una sola unidad.
10. Suite `pytest` en verde y verificación empírica contra la lista real, con
    los `fields` de **cada** fila devuelta inspeccionados: un `200` no prueba
    nada cuando el modo de fallo es una cláusula descartada.

**Decisiones clave:**
- **`in` como grupo OR, no como operador nativo.** OData 4.0 define `in`, pero su
  comportamiento contra `fields/{columna}` en listas de SharePoint no está
  verificado por el proyecto, y si no estuviera soportado el fallo sería el
  peligroso: cláusula descartada y `200` con todas las filas. El grupo OR usa el
  único operador probado sobre estas columnas.
- **`op` como enum cerrado**, segunda mitad de la garantía anti-inyección: con
  `field` acotado a `[A-Za-z0-9_]+` y `op` a seis tokens, todo lo que se
  interpola en una cláusula salvo el literal viene de un alfabeto finito. El
  operador se revalida además en el servicio, como el nombre de campo.
- **Rechazar `ge` sobre texto y `in` sobre Sí/No** en vez de dejarlos pasar:
  ordenar texto compara lexicográficamente y el resultado parecería plausible;
  `in` sobre dos valores es o un `eq` redundante o una tautología. Rechazar es
  barato; una respuesta equivocada que parece correcta no lo es.
- **Alcance del literal de fecha: solo los operadores nuevos validan.**
  Consultado con el propietario el 2026-09-03 ante la contradicción entre la
  decisión 6 del diseño (literal por columna, todos los operadores) y su propia
  sección de riesgos más la tarea 8.7 (no alterar `eq`). Se optó por lo segundo:
  `eq` conserva intacta la traducción de la v2.5.0, sin validación ISO-8601, de
  modo que ningún body existente cambia de comportamiento. **La mitad
  interesante de esa disyuntiva resultó inexistente:** la medición demostró que
  el literal que Graph evalúa es el *entrecomillado*, el mismo que `eq` ya
  emitía, así que ambos caminos generan la misma cláusula para un instante bien
  formado y la única diferencia real es la validación.
- **Presupuesto de 100 cláusulas** tras la expansión (frente a las 300 que
  permitirían 15 filtros × 20 valores). Es un margen de seguridad frente al
  límite de longitud de URL de Graph, **no un umbral medido**: la verificación
  empírica debe registrar dónde falla de verdad.

**Fuera de alcance, a propósito:** grupos OR anidados entre campos distintos
(exigirían un lenguaje de consulta en el body), el cursor de paginación de
`has_more`, y los operadores `ne`, `contains`, `startswith` y las comprobaciones
de nulo, cada uno con su propio perfil de fallo silencioso.

**Archivos modificados:** `app/schemas/sharepoint.py` (`op`, `value` ampliado a
array, validadores de forma/cotas/homogeneidad y del presupuesto de cláusulas),
`app/services/sharepoint.py` (familia de tipo de columna, `_check_search_operator`,
`_to_odata_datetime_literal`, `_build_search_clause`, comprobación de tipo por
elemento), `app/api/v1/endpoints/sharepoint.py` (paso de `op` y documentación en
`/docs`), tests (82 nuevos, 210/210 en verde), docs y `VERSION` a 2.6.0.

**Verificación empírica (2026-09-03, lista DSL real, Docker local
`localhost:8001`, v2.6.0 confirmada en `/health`; `op` presente en
`/openapi.json` como enum cerrado con default `eq`).**

> La lista objetivo se cambió respecto al plan: `HistorialConsumoBolsas` no era
> alcanzable —`GET /v1/graph/sites?search=*` responde `403 accessDenied` con esta
> app registration, así que no hay forma de descubrir su URL— y la verificación se
> hizo contra **DSL**, la lista ya caracterizada en las iteraciones anteriores,
> que tiene columnas de todos los tipos que la matriz necesita: texto (`Entorno`,
> `Grupo`), Sí/No (`_x00da_ltima`), lookup (`Cliente_x002d_LIBSA`) y **cuatro
> columnas `dateTime`** (`m0n4`, `l4h3`, `Created`, `Modified`). Total real de la
> lista, siguiendo todas las páginas: **9.320 filas**.

**El literal de fecha estaba mal y la medición lo cazó.** Es el hallazgo
principal, y contradice la decisión 6 del diseño:

| Cláusula enviada | Resultado de Graph |
|---|---|
| `fields/Modified ge 2018-01-01T00:00:00Z` (desnudo, lo que especifica OData 4.0) | **`400 invalidRequest`** |
| …con milisegundos, con `+00:00`, solo fecha, o `datetime'…'` (OData v3) | **`400`** en todas las variantes |
| `fields/Modified ge '2018-01-01T00:00:00Z'` (entrecomillado) | `200`, **9.127 filas**, mínimo devuelto `2018-02-15T14:27:11Z`, ninguna anterior al límite ✓ |
| `fields/Modified lt '2018-01-01T00:00:00Z'` (entrecomillado) | `200`, **193 filas**, máximo devuelto `2017-07-28T08:23:28Z`, ninguna posterior ✓ |

9.127 + 193 = **9.320**, el total exacto: la partición perfecta es lo que prueba
que la cláusula se evalúa, no que simplemente no dé error. Se corrigió el código
para emitir el literal entrecomillado y se rehizo la verificación. **Graph se
comporta aquí al revés que con los booleanos**, donde el literal desnudo `1`/`0`
es el que filtra y el entrecomillado el que se ignora; sobre columnas de listas
de SharePoint la forma del literal se decide midiendo, nunca leyendo la
especificación. Precedente que conviene recordar en la siguiente iteración.

Resto de comprobaciones, todas sobre las filas devueltas y no solo sobre el
status:

- **`in` sobre columna de texto** (8.3): `eq 'L02'` → 2.290 filas; `eq
  'l01-2016'` → 55; `in` de ambos → **2.345 = la suma exacta**, ninguna fila
  fuera del conjunto pedido, y muy por debajo de las 9.320 de la lista. ✓
- **Graph compara texto sin distinguir mayúsculas** (hallazgo lateral): entre las
  filas de `eq 'L02'` vienen las almacenadas como `l02`. No es un fallo del
  conector, pero implica que un `in` no sirve para distinguir valores que solo
  difieren en la caja. Documentado en README y ARQUITECTURA.
- **`in` + segundo filtro** (8.4): `in [L02, l01-2016]` AND `_x00da_ltima eq
  true` → 871 filas (el `in` solo daba 2.345), **todas** cumpliendo ambas
  condiciones. Los paréntesis sujetan la precedencia; ninguna fila satisfacía
  solo el `in`. ✓
- **Rango cerrado** (8.5b): `Modified ge '2022-01-01' and lt '2023-01-01'` →
  1.920 filas, rango real devuelto `2022-03-11` .. `2022-12-27`, ninguna fuera
  del intervalo. ✓
- **UTC tal como viene** (8.5c): el mismo instante escrito de cuatro formas —
  `2026-06-01T00:00:00Z`, `2026-06-01T02:00:00+02:00`, `2026-06-01T00:00:00` y
  `2026-06-01` — devolvió **590 filas en los cuatro casos**. El offset se
  normaliza y la ausencia de zona se toma como UTC. ✓
- **`eq` sobre columna de fecha, hoy** (8.7, pregunta abierta del diseño):
  `eq '2026-09-02T14:15:45Z'` → **exactamente 1 fila** de 9.320, con ese
  instante. **`eq` ya filtraba correctamente: no hay defecto preexistente** y no
  queda cambio de seguimiento por este motivo. Lo que estaba equivocado era la
  premisa de la decisión 6, no el `eq`.
- **Matriz operador × tipo** (8.8), todos `400` del conector antes de llamar a
  Graph, nunca un `200` de aspecto plausible: `ge` sobre texto (`Entorno`), `in`
  y `ge` sobre Sí/No (`_x00da_ltima`), fecha malformada `18/08/2026` con `ge`,
  `in` sobre la columna lookup base, campo inexistente. ✓
- **Elemento mal tipado en un `in`** (8.8b): un array **homogéneo** cuyo tipo no
  corresponde a la columna (`["35","41"]` sobre numérica, `[1,2]` sobre texto,
  fechas malformadas con `in`) → `400` nombrando columna y tipo esperado. Un
  array que **mezcla** tipos (`[35,"41"]`) se rechaza antes y más barato en el
  schema con `422`. Ambos caminos correctos; el escenario del delta spec se
  ajustó para reflejar cuál es cuál. ✓
- **Consulta motivadora** (8.10): `in` de tres valores → 3.767 filas con los tres
  valores presentes y ningún otro; `eq` + `ge` de fecha → 324 filas, `Modified`
  entre `2026-01-13` y `2026-08-24`, `Entorno` únicamente `l02`. **Dos llamadas,
  cero post-filtrado en cliente.** ✓
- **Regresión v2.5.0** (8.11): body sin `op` → `_x00da_ltima eq true` devuelve
  3.790 filas, todas con el booleano pedido (la iteración v2.4.0 registró 3.838;
  la lista ha cambiado de contenido en 14 meses, no es una regresión).
  `Cliente_x002d_LIBSALookupId 35` AND `ultima true` → 160 filas, todas
  cumpliendo ambas condiciones. ✓

**Techo de longitud de URL (8.9), ya medido — y el presupuesto se queda corto.**
Bisección contra la lista real; el corte es abrupto y reproducible, con Graph
respondiendo `404 UnknownError` de mensaje vacío al pasarse:

| Forma de la cláusula (longitud) | Última aceptada | Primera rechazada |
|---|---|---|
| `fields/Entorno eq 'v0000'` (29 ch) | **115** cláusulas · `$filter` 3.333 ch · URL 5.187 ch | 116 · URL 5.230 ch |
| `fields/Cliente_x002d_LIBSALookupId eq 35` (44 ch) | **88** cláusulas · `$filter` 3.861 ch · URL 4.985 ch | 89 · URL 5.039 ch |
| `fields/Modified eq '2026-08-01T00:00:00Z'` (45 ch) | **73** cláusulas · `$filter` 3.283 ch · URL 4.841 ch | 74 · URL 4.904 ch |

El límite **no se cuenta en cláusulas sino en longitud de URL**: se agota
alrededor de 4,9–5,2 KB (la horquilla la produce el percent-encoding, que
triplica espacios y comillas). Por tanto **el presupuesto de 100 cláusulas admite
peticiones que Graph va a rechazar**: 100 cláusulas de fecha se pasan del techo.
El modo de fallo es el honesto —un `404` propagado, no una cláusula descartada en
silencio— pero es una petición que el conector aceptó y no debería.
**Seguimiento propuesto, no plegado en esta iteración:** presupuestar por
longitud del `$filter` generado (~3 KB sin encodear, `400` en el servicio antes
de la llamada) y dejar el conteo de cláusulas como guarda barata en el schema.
