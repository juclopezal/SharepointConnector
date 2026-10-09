## MODIFIED Requirements

### Requirement: Multi-field AND search by list URL
The system SHALL expose `POST /v1/sharepoint/list/items:search`, accepting a
`sharepoint_url` and an optional `filters` array of up to 15 conditions
(`{ "field": string, "op": string (optional), "value": string | boolean | integer |
number | array }`), and SHALL combine all provided conditions with logical AND when
querying Microsoft Graph. Each condition SHALL carry an optional `op` selecting its
comparison operator from the closed set `eq`, `in`, `ge`, `gt`, `le`, `lt`, defaulting
to `eq` when omitted. A condition with `op: "in"` SHALL match an item when the field
equals any one of the listed values; that disjunction SHALL apply only within that
condition and SHALL NOT alter the AND composition between conditions. When `filters` is
omitted, or is present but an empty array, the system SHALL treat both cases identically
and return the first `top` items of the list without applying any filter.

#### Scenario: Single filter matches a subset of items
- **WHEN** the request has one filter `{ "field": "Entorno", "value": "L02" }`
- **THEN** the response contains only items whose `Entorno` field equals `"L02"`

#### Scenario: Omitting op is equality
- **WHEN** a filter omits `op` entirely, e.g. `{ "field": "Entorno", "value": "L02" }`
- **THEN** the request behaves identically to `{ "field": "Entorno", "op": "eq",
  "value": "L02" }`, producing the same Graph `$filter` clause and the same items

#### Scenario: Multiple filters are combined with AND
- **WHEN** the request has filters `{ "field": "Entorno", "value": "L02" }` and
  `{ "field": "_x00da_ltima", "value": true }`
- **THEN** the response contains only items matching both conditions simultaneously,
  never items matching only one of them

#### Scenario: in matches any of the listed values
- **WHEN** a filter is `{ "field": "Estado", "op": "in", "value": ["Para Entregar",
  "Iniciado", "Pausado"] }`
- **THEN** the response contains every item whose `Estado` is any one of those three
  values, and no item whose `Estado` is a different value

#### Scenario: in is parenthesised so it does not absorb the AND join
- **WHEN** the request combines `{ "field": "Estado", "op": "in", "value": ["Iniciado",
  "Pausado"] }` with `{ "field": "Entorno", "value": "L02" }`
- **THEN** the Graph `$filter` sent groups the disjunction in parentheses —
  `(fields/Estado eq 'Iniciado' or fields/Estado eq 'Pausado') and fields/Entorno eq 'L02'`
  — and the response contains only items in `L02`, never an item in another environment
  that merely matched one of the states

#### Scenario: Single-element in is still parenthesised
- **WHEN** a filter is `{ "field": "Estado", "op": "in", "value": ["Pausado"] }`
- **THEN** the generated clause is `(fields/Estado eq 'Pausado')`, so the emitted shape
  does not depend on the array's length

#### Scenario: Comparison operators bound a range
- **WHEN** the request combines `{ "field": "FechaFinalizado", "op": "ge", "value":
  "2026-08-18T00:00:00Z" }` and `{ "field": "FechaFinalizado", "op": "lt", "value":
  "2026-09-02T00:00:00Z" }`
- **THEN** the response contains only items whose `FechaFinalizado` falls within that
  half-open interval

#### Scenario: No filters returns the first page unfiltered
- **WHEN** the request omits `filters` and sets `top: 100`
- **THEN** the response contains up to 100 items from the list with no `$filter` applied

#### Scenario: Explicit empty filters array behaves the same as omitting it
- **WHEN** the request includes `"filters": []` (an explicit empty array, not an
  omitted key) and sets `top: 100`
- **THEN** the response is `200`, contains up to 100 items from the list with no
  `$filter` applied, and is treated identically to omitting `filters` entirely —
  it is NOT rejected with `422`

#### Scenario: A filter value matching no row returns an empty result, not an error
- **WHEN** a filter's `value` is well-typed for its column but matches zero rows
- **THEN** the response is `200` with `total: 0` and an empty `items` array

### Requirement: Typed filter values translate to correct OData literals
The system SHALL accept `value` as a JSON string, boolean, integer, or floating-point
number, and — when `op` is `in` — as a homogeneous array of those scalars, and SHALL
translate each to the OData literal that Microsoft Graph evaluates correctly for the
corresponding SharePoint column, without requiring the caller to know OData syntax. In
particular, boolean values SHALL be encoded as the unquoted literals `1` or `0` — never
`true`/`false` in either quoted or unquoted form — because Microsoft Graph silently
ignores `fields/{column} eq true` and `fields/{column} eq 'true'` clauses against
SharePoint Yes/No columns, returning all rows as if the condition were absent, whereas
`eq 1` / `eq 0` filters correctly. Values targeting a `dateTime` column with any operator
other than `eq` SHALL be validated as ISO-8601 instants and emitted as **quoted** OData
literals — the form Microsoft Graph actually evaluates on SharePoint list columns, where
the bare `dateTimeOffset` literal of OData 4.0 is rejected with `400 invalidRequest`
(measured 2026-09-03) — re-emitted canonically from the parsed value rather than copied
through, and SHALL be interpreted exactly as given in UTC: a value without a zone is
taken as UTC, one with an offset is normalised to UTC, and the system SHALL NOT
reinterpret either in the tenant timezone. A `dateTime` column addressed with `eq` SHALL
keep the value-driven translation unchanged, which produces the same quoted clause for a
well-formed instant but performs no ISO-8601 validation.

#### Scenario: Boolean true value filters correctly
- **WHEN** a filter is `{ "field": "_x00da_ltima", "value": true }`
- **THEN** every item in the response has `_x00da_ltima: true` in its `fields`, and no
  item with `_x00da_ltima: false` is returned

#### Scenario: Boolean false value filters correctly
- **WHEN** a filter is `{ "field": "_x00da_ltima", "value": false }`
- **THEN** every item in the response has `_x00da_ltima: false` in its `fields`

#### Scenario: String value is quoted with internal quotes doubled
- **WHEN** a filter is `{ "field": "Title", "value": "O'Hara" }`
- **THEN** the Graph `$filter` sent contains the literal `'O''Hara'` for that clause

#### Scenario: Numeric value is emitted unquoted
- **WHEN** a filter is `{ "field": "Cliente_x002d_LIBSALookupId", "value": 35 }`
- **THEN** the Graph `$filter` sent contains the unquoted literal `35` for that clause

#### Scenario: Integer true is distinguished from boolean true
- **WHEN** a filter is `{ "field": "Cliente_x002d_LIBSALookupId", "value": 1 }`
  (a JSON integer, not a JSON boolean)
- **THEN** the value is treated as the number `1`, not as a boolean, and validated
  against the column's expected type as an integer

#### Scenario: Each element of an in array is translated by its own type
- **WHEN** a filter is `{ "field": "Cliente_x002d_LIBSALookupId", "op": "in", "value":
  [35, 41] }`
- **THEN** the generated clause is
  `(fields/Cliente_x002d_LIBSALookupId eq 35 or fields/Cliente_x002d_LIBSALookupId eq 41)`,
  with both literals unquoted

#### Scenario: A string element with a quote is escaped inside an in group
- **WHEN** a filter is `{ "field": "Title", "op": "in", "value": ["O'Hara", "Smith"] }`
- **THEN** the generated clause contains `'O''Hara'` and `'Smith'`, joined with `or`
  inside parentheses

#### Scenario: Date comparison value is emitted as the literal Graph evaluates
- **WHEN** a filter is `{ "field": "FechaFinalizado", "op": "ge", "value":
  "2026-08-18T00:00:00Z" }`
- **THEN** the Graph `$filter` sent contains
  `fields/FechaFinalizado ge '2026-08-18T00:00:00Z'`, and the response contains only
  items on or after that instant — NOT every item in the list, which is what a
  silently-dropped clause would return, and NOT a `400`, which is what the bare
  `dateTimeOffset` literal produces

#### Scenario: The same instant written four ways selects the same items
- **WHEN** the same comparison is sent as `"2026-06-01T00:00:00Z"`,
  `"2026-06-01T02:00:00+02:00"`, `"2026-06-01T00:00:00"` and `"2026-06-01"`
- **THEN** all four select the identical set of items, because the offset form is
  normalised to UTC and the zone-less forms are taken as UTC as given

#### Scenario: A malformed date value is rejected before calling Graph
- **WHEN** a filter targets a `dateTime` column with `in`, `ge`, `gt`, `le` or `lt`
  and a value that is not a parseable ISO-8601 instant, e.g. `"18/08/2026"` or
  `"last week"`
- **THEN** the response is `400` naming the column and the expected format, and the
  request never reaches Microsoft Graph

#### Scenario: eq against a date column keeps its pre-existing translation
- **WHEN** a filter is `{ "field": "FechaFinalizado", "value": "2026-08-18T00:00:00Z" }`,
  with `op` omitted or set to `eq`
- **THEN** the generated clause is `fields/FechaFinalizado eq '2026-08-18T00:00:00Z'` —
  the quoted form produced before this change — so no existing request body changes
  behaviour, and the value is NOT validated as an ISO-8601 instant

#### Scenario: The whole expression is percent-encoded as one unit
- **WHEN** a request generates a filter expression containing parentheses, spaces and
  quotes
- **THEN** the complete expression is percent-encoded as a single `$filter` value, with
  no character left unencoded on the assumption that Graph tolerates it

#### Scenario: Unsupported value type is rejected before calling Graph
- **WHEN** a filter's `value` is `null` or a JSON object, or is a JSON array while `op`
  is anything other than `in`
- **THEN** the request is rejected with `422` and never reaches Microsoft Graph

### Requirement: Filters are validated against the list's real column schema
Before building the Graph `$filter` expression, the system SHALL validate every
filter's `field` against the target list's actual columns (fetched via Microsoft
Graph and cached per `site_id` + `list_id`). A filter referencing a field that does
not exist in the list, or whose `value`'s JSON type does not correspond to that
column's type, SHALL be rejected explicitly rather than silently dropped or passed
through to Graph unfiltered. When `op` is `in`, the system SHALL apply the type check
to **every element** of the array, and SHALL reject the whole filter if any element
fails. Validating operators SHALL NOT cost an additional Microsoft Graph call: the
operator check SHALL read the same cached column definitions as the field and type
checks.

#### Scenario: Nonexistent field is rejected
- **WHEN** a filter's `field` does not match any column on the resolved list
- **THEN** the response is `400`, and the error names the offending field

#### Scenario: Type mismatch against a Yes/No column is rejected
- **WHEN** a filter targets a Yes/No (boolean) column with a string value, e.g.
  `{ "field": "_x00da_ltima", "value": "true" }`
- **THEN** the response is `400` explaining the expected type, and the request never
  reaches Graph as an unfiltered or partially-filtered query

#### Scenario: Type mismatch against a numeric column is rejected
- **WHEN** a filter targets a numeric column with a string value, e.g.
  `{ "field": "Cliente_x002d_LIBSALookupId", "value": "35" }`
- **THEN** the response is `400` explaining the expected type

#### Scenario: One badly-typed element rejects the whole in filter
- **WHEN** a filter is `{ "field": "Cliente_x002d_LIBSALookupId", "op": "in", "value":
  ["35", "41"] }`, where the column is numeric and the elements are strings — a
  homogeneous array whose type does not match the column
- **THEN** the response is `400` naming the column and the expected type, and no
  partially-applied filter reaches Graph
- **NOTE**: an array that *mixes* JSON types, such as `[35, "41"]`, is rejected earlier
  and more cheaply by the schema layer with `422` — see the mixed-type scenario under
  strict request validation

#### Scenario: Lookup column addressed via its LookupId field is recognized
- **WHEN** a filter's `field` is `{BaseColumnName}LookupId` (e.g.
  `Cliente_x002d_LIBSALookupId`) and no column by that exact name exists, but a
  lookup-type column named `{BaseColumnName}` does exist
- **THEN** the field is recognized as valid, its expected JSON type is treated as
  numeric, and a well-typed integer `value` is accepted

#### Scenario: Filtering directly on a base lookup column is rejected
- **WHEN** a filter's `field` matches an existing column whose type is a lookup
  column, addressed by its base name directly (i.e. NOT the `{BaseColumnName}LookupId`
  synthetic field — e.g. `Cliente_x002d_LIBSA` instead of
  `Cliente_x002d_LIBSALookupId`)
- **THEN** the response is `400`, explaining that lookup columns must be filtered via
  their `LookupId` field, rather than being accepted and silently mishandled by Graph

#### Scenario: Column schema is cached across requests
- **WHEN** two search requests target the same `site_id` + `list_id` within the
  cache's freshness window
- **THEN** the second request SHALL NOT trigger a second `GET .../lists/{id}/columns`
  call to Microsoft Graph

### Requirement: Strict request validation
The system SHALL reject any request body field not defined by the search request
schema (including a `filter_by` field carried over from other endpoints) with `422`,
rather than ignoring it silently. Each filter's `field` SHALL match the internal
SharePoint column name pattern (letters, digits, underscore only); a `field` outside
that pattern SHALL be rejected at the schema layer, and independently re-validated
at the service layer before being interpolated into the Graph filter expression. Each
filter's `op` SHALL be one of the closed set `eq`, `in`, `ge`, `gt`, `le`, `lt`; any
other token SHALL be rejected with `422` at the schema layer, so that no
caller-supplied text can reach the generated OData expression. An `in` filter's `value`
SHALL be an array of 1 to 20 homogeneously-typed scalars. The total number of clauses
generated after expanding every `in` filter SHALL NOT exceed 100.

#### Scenario: Unknown top-level field is rejected
- **WHEN** the request body includes a field not defined by the schema, e.g.
  `filter_by`
- **THEN** the response is `422`, and the unknown field is never interpreted as a
  filter or otherwise acted upon

#### Scenario: Filter field name outside the allowed pattern is rejected
- **WHEN** a filter's `field` contains characters other than letters, digits, or
  underscore (e.g. `"Title eq 'x'"` or `"fields/Other"`)
- **THEN** the response is `422`

#### Scenario: Unknown operator is rejected at the schema layer
- **WHEN** a filter's `op` is a token outside the allowed set, e.g. `"contains"`,
  `"ne"`, or `"eq 1 or 1"`
- **THEN** the response is `422`, and no part of the supplied token reaches the
  generated `$filter` expression

#### Scenario: Operator and value shape must agree
- **WHEN** `op` is `"in"` and `value` is a scalar, or `op` is `"eq"`, `"ge"`, `"gt"`,
  `"le"` or `"lt"` and `value` is an array
- **THEN** the response is `422`

#### Scenario: Empty or oversized in array is rejected
- **WHEN** an `in` filter's `value` is `[]`, or contains more than 20 entries
- **THEN** the response is `422`

#### Scenario: Mixed-type in array is rejected
- **WHEN** an `in` filter's `value` mixes JSON types, e.g. `["Iniciado", 3]`
- **THEN** the response is `422`, and no partially-typed clause is built

#### Scenario: Exceeding the total clause budget is rejected
- **WHEN** the filters, after expanding every `in` array into its equality clauses,
  would generate more than 100 clauses
- **THEN** the response is `422` stating the generated count and the limit, rather
  than sending Graph a request that may be rejected for URL length

#### Scenario: Filters array above the allowed size is rejected
- **WHEN** `filters` contains more than `15` conditions
- **THEN** the response is `422`
- **NOTE**: an empty `filters` array (`0` conditions) is NOT rejected — see the
  "Explicit empty filters array" scenario above; only exceeding the maximum is a
  `422`

## ADDED Requirements

### Requirement: Operator support is validated against the column type
The system SHALL reject, with `400` and before calling Microsoft Graph, any filter
whose operator is not meaningful for the resolved column's type. Ordering operators
(`ge`, `gt`, `le`, `lt`) SHALL be accepted only against number, currency, `dateTime`
and `{Base}LookupId` columns. The `in` operator SHALL be accepted against every column
type that supports `eq`, except Yes/No (boolean) columns, whose two-value domain makes
`in` either a redundant equality or a tautology. The error SHALL name the column, its
type, and the operators that type does support.

#### Scenario: Ordering a text column is rejected
- **WHEN** a filter is `{ "field": "Estado", "op": "ge", "value": "Iniciado" }` and
  `Estado` is a choice or text column
- **THEN** the response is `400` naming the column and its type, rather than returning
  rows ordered lexicographically as if the request made sense

#### Scenario: Ordering a date column is accepted
- **WHEN** a filter is `{ "field": "FechaFinalizado", "op": "le", "value":
  "2026-09-02T00:00:00Z" }` and `FechaFinalizado` is a `dateTime` column
- **THEN** the filter is accepted and applied

#### Scenario: Ordering a numeric column is accepted
- **WHEN** a filter targets a number or currency column with `ge`, `gt`, `le` or `lt`
  and a numeric value
- **THEN** the filter is accepted and applied

#### Scenario: in against a Yes/No column is rejected
- **WHEN** a filter is `{ "field": "_x00da_ltima", "op": "in", "value": [true, false] }`
- **THEN** the response is `400` explaining that Yes/No columns are filtered with `eq`,
  rather than being accepted as a tautology that returns every row

#### Scenario: in against a text or choice column is accepted
- **WHEN** a filter is `{ "field": "Estado", "op": "in", "value": ["Iniciado",
  "Pausado"] }` and `Estado` is a choice or text column
- **THEN** the filter is accepted and applied

#### Scenario: Operator validation costs no extra Graph call
- **WHEN** a request containing operator filters targets a `site_id` + `list_id` whose
  column definitions are already cached
- **THEN** the operator check is satisfied from the cache, and no additional
  `GET .../lists/{id}/columns` call is made
