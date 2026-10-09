## Why

`POST /v1/sharepoint/list/items:search` can only express equality, and only joined with
AND. Any question of the form "give me the rows in one of these states" or "give me the
rows finished in the last 15 days" therefore cannot be asked at all — the caller has to
issue one HTTP request per accepted value, merge the responses, and re-filter them
client-side.

The concrete case that triggered this: listing the `HistorialConsumoBolsas` items whose
`Estado` is one of *Para Entregar*, *Iniciado* or *Pausado*, plus the *Completado* ones
whose `FechaFinalizado` falls within the last 15 days. Today that is **4 calls to the
connector plus a `jq` post-filter**, and the date cut-off forces the caller to download
the entire history of completed rows just to discard most of it — which also collides
with the `top`/`has_more` ceiling on large lists.

## What Changes

- New optional `op` field on each `SearchFilter`, defaulting to `eq`. Supported operators:
  `eq` (current behaviour), `in`, `ge`, `gt`, `le`, `lt`.
- `in` accepts an **array** of values (2–20 entries, homogeneous type) and is rendered as a
  parenthesised OR group of equalities over the same field —
  `(fields/Estado eq 'Iniciado' or fields/Estado eq 'Pausado')` — rather than the native
  OData `in` operator, whose support on SharePoint list items is not dependable.
- `ge`/`gt`/`le`/`lt` accept a single scalar and are restricted, by schema validation, to
  the column types where they are meaningful: number, currency and dateTime. Requesting a
  comparison against a text, choice, boolean or lookup column is a `400` naming the column
  and its type — never a clause that Graph quietly drops or misevaluates.
- Date comparisons take an ISO-8601 UTC string (`"2026-08-18T00:00:00Z"`) and are emitted
  as the unquoted OData literal Graph expects for `dateTime` columns, so the caller never
  writes OData syntax by hand.
- Clause budget made explicit: the existing 15-filter ceiling stays, and an `in` filter
  additionally caps at 20 values, bounding the generated `$filter` expression so a request
  cannot produce a URL Graph rejects for length.
- **BREAKING**: none. `op` is optional; a filter without it behaves exactly as today, so
  every existing caller and every stored request body keeps working unchanged.

### Out of scope (deliberately)

- **Nested OR groups** across different fields — i.e. expressing "(three open states) OR
  (Completado AND recent date)" in a single request. That requires designing a query
  language in the request body, and it would complicate schema validation and the
  anti-injection guarantees for the sake of collapsing two calls into one. The operators
  in this change already take the motivating case from 4 calls down to 2.
- **A pagination cursor** for `has_more`. Still a known gap, unchanged by this change,
  though `ge`/`le` reduces how often callers run into it by letting the server discard
  out-of-range rows.

## Capabilities

### New Capabilities

_None — this extends the filtering contract of an existing endpoint rather than adding a
new one._

### Modified Capabilities

- `list-items-search-by-url`: the "Multi-field AND search by list URL" and "Typed filter
  values translate to correct OData literals" requirements change. A filter is no longer
  always an equality: it carries an optional operator, its `value` may be an array when
  the operator is `in`, and the set of column types a filter may target now depends on
  that operator. The AND composition *between* filters is unchanged.

## Impact

- **Schemas** (`app/schemas/sharepoint.py`): `SearchFilter` gains `op`; `value` widens to
  accept a list for `in`; cross-field validation (operator ↔ value shape) added.
- **Service** (`app/services/sharepoint.py`): `_to_odata_literal` reused unchanged per
  value; the clause builder stops assuming `eq` and learns to emit comparison clauses and
  parenthesised OR groups; the schema-validation step gains an operator/column-type
  compatibility check alongside the existing value/column-type check.
- **Endpoint** (`app/api/v1/endpoints/sharepoint.py`): no signature change — the new field
  flows through the existing request model.
- **No change** to `app/services/resolver.py`, to the column cache, or to any other
  endpoint.
- **Known limitations touched**: OR groups and comparisons over non-indexed columns are
  more likely to hit SharePoint's list-view threshold on large lists; Graph's
  `notSupported` error keeps propagating verbatim, and this needs to be stated in the docs
  rather than papered over.
- **Docs**: `README.md`, `ARQUITECTURA.md`, `arquitecturasUML.md`, `doc/CHANGELOG.md`,
  `VERSION` (minor bump 2.5.0 → 2.6.0), and `requirements/SPEC-004_List_Items_Search_By_Url.md`
  extended in place with a new Bitácora entry (same endpoint — no separate SPEC).
- **Deployment**: manual post-merge step on local Docker Desktop and on the `docker-ag`
  host (`deploy.sh update --pull`), as always — not automated by this change.
