## Context

`POST /v1/sharepoint/list/items:search` (v2.4.0, projection added in v2.5.0) builds its
Graph query by turning every `SearchFilter` into `fields/{field} eq {literal}` and joining
the clauses with `" and "`. The operator is implicit and the join is hard-coded, so the
filter contract can express exactly one shape of question: a conjunction of equalities.

Everything else in the pipeline is already general enough to carry more: the list's real
column definitions are fetched and cached per `(site_id, list_id)`, every field name is
matched against that schema, values are type-checked without coercion, and the finished
expression is percent-encoded as a single unit. What is missing is the vocabulary, not the
machinery.

The constraint that shapes every decision below is the project's guiding principle:
Microsoft Graph fails silently on SharePoint list filters. It drops clauses it does not
understand and answers `200` with unfiltered rows. A new operator is therefore only
acceptable if the connector can prove, before calling Graph, that the clause it is about
to emit will actually be evaluated — otherwise the feature would reintroduce the exact
failure mode this endpoint exists to eliminate.

## Goals / Non-Goals

**Goals:**

- Let a single request express "field is one of N values" and "field is above/below a
  bound", so the motivating query drops from 4 calls to 2 and stops transferring rows the
  server could have discarded.
- Keep every existing request body byte-for-byte valid, with identical behaviour.
- Extend the schema-validation step so an operator that a column cannot support is a `400`
  naming the column and its type, never a clause Graph ignores.
- Keep operators out of caller-controlled free text: the generated OData must remain
  unforgeable by the caller.

**Non-Goals:**

- Nested OR groups across different fields, or any request-body query language. The
  boolean structure stays fixed: OR *within* one field's `in`, AND *between* filters.
- A pagination cursor for `has_more` — unchanged and still missing.
- Retries or `Retry-After` handling for Graph's `429` — unchanged.
- Other operators (`ne`, `contains`, `startswith`, `null` checks). Each has its own
  silent-failure profile against SharePoint columns and deserves its own evidence.

## Decisions

### Decision 1 — Extend the existing endpoint rather than add a new one

`items:search` already owns URL resolution, column-schema validation, literal translation,
projection and bounded pagination. A second "advanced search" endpoint would duplicate all
of it and split the contract across two routes that answer the same question.

The change is additive and backwards compatible: `op` is optional and defaults to `eq`, so
a filter written for v2.5.0 produces a byte-identical `$filter` clause under v2.6.0. No
existing response field changes shape.

*Alternative rejected:* a new `POST /list/items:query` taking a structured boolean tree.
That is the right home for nested OR groups if they are ever needed, but building it now
would mean designing a query language for a use case that two operators already cover.

### Decision 2 — `op` is a closed enum on the filter, not free text

`SearchFilter` gains `op: Literal["eq", "in", "ge", "gt", "le", "lt"] = "eq"`. The operator
that reaches the expression builder can only ever be one of six known tokens, chosen by
Pydantic from a fixed set — the caller cannot smuggle OData through it.

This matters because it is the second half of the existing anti-injection guarantee. Field
names are already constrained to `^[A-Za-z0-9_]+$` at the schema layer and independently
re-validated at the service layer before interpolation (defence in depth, per the existing
`find_list_items_by_field` pattern). With `op` closed and `field` constrained, every part
of a generated clause except the literal is drawn from a finite alphabet, and the literal
keeps going through the existing escaping.

*Alternative rejected:* an operator prefix inside `value` (`">=2026-08-18"`). It moves
parsing into a string the caller controls — the opposite of what this endpoint is for.

### Decision 3 — `in` expands to a parenthesised OR group, not OData's native `in`

OData 4.0 defines `in`, but its behaviour against `fields/{column}` on SharePoint list
items is not something this project has evidence for, and the failure mode if it is
unsupported is the dangerous one: a dropped clause and a `200` with every row. Expanding to
an OR of equalities uses only the operator already proven to work on these columns.

Exact expression generated for `{ "field": "Estado", "op": "in", "value": ["Iniciado", "Pausado"] }`:

```
(fields/Estado eq 'Iniciado' or fields/Estado eq 'Pausado')
```

The parentheses are mandatory, not cosmetic: without them, mixing an OR group with the
outer `and` join would change the meaning of the whole expression through operator
precedence. A single-element array is still wrapped in parentheses, so the generated shape
does not depend on the array's length.

Each value goes through the existing literal translation unchanged — quotes doubled for
strings, unquoted for numbers, `1`/`0` for booleans — and the finished expression is
percent-encoded as one unit with `quote(..., safe="")`, exactly as today. Parentheses and
spaces are therefore encoded like everything else; nothing about the encoding step changes.

*Alternative rejected:* emitting native `in` and comparing row counts against a control
query to detect silent dropping. Too clever, and it doubles the Graph traffic.

### Decision 4 — Operator/column-type compatibility is validated against the real schema

The existing validation answers "does this column exist, and does the value's JSON type
match it?". It gains a third question: "does this column support this operator?".

| Column type (Graph facet)     | `eq` | `in` | `ge` `gt` `le` `lt` |
| ----------------------------- | ---- | ---- | ------------------- |
| text, note, choice            | yes  | yes  | **no**              |
| number, currency              | yes  | yes  | yes                 |
| dateTime                      | yes  | yes  | yes                 |
| boolean (Yes/No)              | yes  | **no** | **no**            |
| lookup via `{Base}LookupId`   | yes  | yes  | yes                 |
| lookup via base name          | **no** (existing rule, unchanged)            |

Rationale for the two refusals:

- **Ordering text.** OData would compare text lexicographically, which is almost never what
  a caller asking for `ge` on a choice column means, and the result would look plausible
  enough to go unnoticed. Refusing is cheap; a wrong answer that looks right is not.
- **`in` on a Yes/No column.** The domain has two values, so `in` is either a redundant
  `eq` or a tautology matching everything. Both are caller mistakes worth surfacing.

A rejected combination is a `400` naming the column, its type, and the operators it does
support — consistent with how an unknown column and a type mismatch already read.

### Decision 5 — The 422/400 boundary is unchanged; only the questions grow

The existing frontier holds: Pydantic rejects what can be judged from the request body
alone; the service rejects what requires knowing the list.

**`422` (Pydantic, never reaches Graph):**
- `op` outside the six allowed tokens.
- `op: "in"` with a scalar `value`, or any other operator with an array `value`.
- An `in` array that is empty, holds more than 20 entries, or mixes JSON types.
- Everything already rejected today: unknown body fields (`extra="forbid"`), `null`/object
  values, `filters` longer than 15, `top` outside 1–5000.

**`400` (service, against the cached column schema, never reaches Graph):**
- Column does not exist (existing).
- Value's JSON type does not match the column's type (existing) — applied to every element
  of an `in` array, so one bad element rejects the filter.
- Operator not supported by the column's type (new, per decision 4).

Nothing new is deferred to Graph. This is the point of the change: the operators arrive
with their validation attached.

### Decision 6 — Date comparison values are ISO-8601 UTC, emitted unquoted

`dateTime` columns already map to the string JSON type for `eq`, so a comparison value
arrives as a string such as `"2026-08-18T00:00:00Z"`. But the existing literal translation
is *value-driven*: any Python string is quoted. OData 4.0 expects a `dateTimeOffset`
literal **unquoted** — `fields/FechaFinalizado ge 2026-08-18T00:00:00Z` — so quoting it
would produce a clause Graph either rejects or, worse, ignores.

Literal emission therefore becomes *column-aware for `dateTime` columns only*: when the
resolved column is a `dateTime`, the value is validated as an ISO-8601 instant and emitted
without quotes. Every other column type keeps the current value-driven behaviour untouched.
A value that is not a parseable ISO-8601 instant is a `400` before the call.

> ### ⚠️ CORRECTED BY MEASUREMENT — 2026-09-03
>
> **The premise above is wrong for SharePoint list items via Graph.** The unquoted
> `dateTimeOffset` literal that OData 4.0 specifies is **rejected outright**, and the
> quoted form is the one that filters. Measured against the real DSL list (column
> `Modified`, 9,320 rows, `dateTime` facet, indexed):
>
> | `$filter` clause | Result |
> | --- | --- |
> | `fields/Modified ge 2018-01-01T00:00:00Z` (bare, per OData 4.0) | **`400 invalidRequest`** |
> | …with milliseconds, with `+00:00`, date-only, or `datetime'…'` (OData v3) | **`400`** every variant |
> | `fields/Modified ge '2018-01-01T00:00:00Z'` (quoted) | **`200`, 9,127 rows**, min returned `2018-02-15T14:27:11Z` |
> | `fields/Modified lt '2018-01-01T00:00:00Z'` (quoted) | **`200`, 193 rows**, max returned `2017-07-28T08:23:28Z` |
>
> 9,127 + 193 = 9,320 — the exact row count of the list. A perfect partition is what
> proves the clause is *evaluated*, not merely accepted. A closed range
> `ge '2022-01-01' and lt '2023-01-01'` returned 1,920 rows, all inside the interval.
>
> **So Graph behaves here the opposite way round from booleans**, where the bare `1`/`0`
> is what filters and the quoted form is silently ignored. The transferable lesson, and
> the reason this project insists on the empirical phase: on SharePoint list columns the
> literal form is settled by measuring, never by reading the specification.
>
> **What the implementation does.** All operators emit the **quoted** canonical instant.
> The column-aware path is kept, but what it now contributes over generic quoting is
> (a) **validation** of the value as an ISO-8601 instant — `"18/08/2026"` is an explicit
> `400` instead of a clause that cannot match anything — and (b) **canonicalisation to
> UTC**, re-emitted from the parsed value rather than copied through, so what lands in
> the expression is never caller text. Confirmed: the four spellings
> `2026-06-01T00:00:00Z`, `2026-06-01T02:00:00+02:00`, `2026-06-01T00:00:00` and
> `2026-06-01` each returned the same 590 rows.
>
> **Scope, decided with the owner on 2026-09-03 and now moot in its interesting half.**
> The validation applies to the operators this change introduces (`in`, `ge`, `gt`, `le`,
> `lt`); `eq` keeps the v2.5.0 value-driven translation, unvalidated. Since both paths
> quote, a well-formed instant produces an identical clause either way — the divergence
> the owner was asked to rule on turned out not to exist.

Comparison values are interpreted **as given, in UTC** — the connector does not apply
`TENANT_TIMEZONE` to them. This is a deliberate divergence from the upsert's `period`,
which does resolve named periods in the tenant's zone: there the connector computes the
boundaries, so it must know the zone; here the caller supplies an explicit instant, and
silently shifting it would make the same string mean different rows on different
deployments. The divergence must be stated in the docs so the two date semantics are not
confused.

**Empirical verification is mandatory** for this decision — the exact literal form for
SharePoint `dateTime` columns via Graph is the one part of this change that cannot be
settled by reading. It follows the precedent of the boolean `1`/`0` rule, which was
established against the real DSL list on 2026-07-22 after Graph was observed returning all
rows for `eq true`. The verification must inspect returned rows, not just the status code:
a `200` proves nothing when the failure mode is a dropped clause.

### Decision 7 — Bounded clause budget

`filters` stays capped at 15 and an `in` array at 20 values, so a request can generate at
most 300 clauses. That is a long `$filter`, and Graph enforces a URL length limit that this
project has not characterised.

A total budget of **100 generated clauses** across all filters is enforced after expansion,
rejected as `422` naming the count and the limit. The number is a deliberate safety margin
rather than a measured threshold; the empirical phase should record where Graph actually
starts failing so a later change can tighten or relax it with evidence instead of a guess.

> ### ⚠️ MEASURED — 2026-09-03: the budget is in the wrong currency, and slightly too high
>
> Graph's ceiling is a **URL length** limit, not a clause count. Bisected against the real
> list, the boundary is sharp and reproducible — Graph answers `404 UnknownError` with an
> empty message past it:
>
> | Clause shape (length) | Last accepted | First rejected |
> | --- | --- | --- |
> | `fields/Entorno eq 'v0000'` (29 ch) | **115** clauses · `$filter` 3,333 ch · URL 5,187 ch | 116 · URL 5,230 ch |
> | `fields/Cliente_x002d_LIBSALookupId eq 35` (44 ch) | **88** clauses · `$filter` 3,861 ch · URL 4,985 ch | 89 · URL 5,039 ch |
> | `fields/Modified eq '2026-08-01T00:00:00Z'` (45 ch) | **73** clauses · `$filter` 3,283 ch · URL 4,841 ch | 74 · URL 4,904 ch |
>
> The clause count at which it breaks therefore depends on how long the column names and
> literals are — 115 for short text, 73 for dates — while the URL caps out around
> **4.9–5.2 KB** (the spread comes from percent-encoding, which triples spaces and quotes).
>
> **Consequence: the 100-clause budget lets through requests Graph will reject** — 100 date
> clauses is past the ceiling. The failure mode is at least the honest one (a propagated
> `404`, not a silently dropped clause), but it is a request the connector accepted and
> should not have. The fix this evidence points to is to budget on the **length of the
> generated expression** (a service-layer check on the built `$filter`, ~3 KB unencoded,
> rejected as `400` before the call) and keep the clause count only as a cheap schema-level
> guard. Left as a follow-up rather than folded in here, per this decision's own plan to
> revisit with evidence — the evidence is now above.

### Decision 8 — No change to caching, state or concurrency

The column-schema cache is reused exactly as it is: same key `(site_id, list_id)`, same
300s TTL, same rationale — columns can be added or renamed, unlike site IDs, which are
stable for the life of the process and cached without expiry. Operator validation reads the
same cached definitions the existing field and type checks already read, so a request with
operators costs no extra Graph call.

The service remains a single process with singleton services via `@lru_cache` in
`app/core/dependencies.py`. No new shared mutable state is introduced, so there is nothing
new to make concurrency-safe.

## Risks / Trade-offs

- **The `dateTime` literal form is wrong, and Graph ignores the clause silently** → the
  worst case, because it returns `200` with too many rows. Mitigated by decision 6's
  mandatory empirical verification, which must inspect the `FechaFinalizado` of every
  returned row and treat any out-of-range row as a blocking bug, not a data anomaly.

- **`eq` on `dateTime` columns may already be broken today** — the current value-driven
  translation quotes date strings, which decision 6 argues is the wrong literal form. If
  the empirical phase confirms it, that is a pre-existing defect this change surfaces
  rather than causes. It should be recorded and scoped as its own fix; silently changing
  `eq` semantics for date columns inside this change would break the backwards-compatibility
  promise of decision 1. **Settled by measurement (2026-09-03): `eq` on a `dateTime`
  column already filters correctly today.** `fields/Modified eq '2026-09-02T14:15:45Z'`
  returned exactly 1 row carrying that instant, out of 9,320. There is no pre-existing
  defect here and no follow-up fix to schedule — the quoted literal this endpoint has
  emitted since v2.4.0 was right all along, and it is decision 6's unquoted premise that
  was wrong. `eq` is left exactly as it was.

- **OR groups and comparisons make the list-view threshold more likely** → on lists past
  ~5000 rows, Graph rejects queries it cannot serve from an index, and an OR group over a
  non-indexed column is a prime candidate. Graph's `notSupported` error keeps propagating
  verbatim with its detail, per the existing `order_by` precedent. The docs must say
  plainly that these operators do not lift the threshold, and point at indexed columns.

- **A long `in` array produces a URL Graph rejects for length** → bounded by decision 7,
  with the real limit to be characterised empirically rather than assumed.

- **`has_more` still has no cursor** → unchanged by this change. `ge`/`le` reduce how often
  callers hit the ceiling, by letting the server discard out-of-range rows instead of
  spending the budget on them, but a truncated result set is still a dead end. Stated here
  so the improvement is not mistaken for a fix.

- **Two date semantics now exist in the API** — `period` (tenant timezone, connector
  computes the bounds) on upsert, and comparison filters (UTC, caller supplies the instant)
  on search. Justified in decision 6, but it is a real inconsistency and the documentation
  carries the cost of explaining it.

## Open Questions

- ~~Does `eq` against a `dateTime` column filter correctly today?~~ **Answered 2026-09-03:
  yes.** `eq '<instant>'` returned exactly the 1 matching row of 9,320. No follow-up fix
  needed; decision 6's premise was the thing that was wrong. See the corrected note there.
- ~~At what expression length does Graph start rejecting the request?~~ **Answered
  2026-09-03: the URL caps at ~4.9–5.2 KB**, which is 115 short clauses but only 73 date
  clauses. The 100-clause budget is therefore too high for realistic clause lengths — see
  the measurement note under decision 7 and the follow-up it proposes.
- **New, found while measuring:** Graph compares text columns **case-insensitively**.
  `fields/Entorno eq 'L02'` also returns rows stored as `l02`. Harmless for `eq` and `in`
  (it widens the match predictably) but it means an `in` array cannot be used to
  distinguish values that differ only in case, and it is worth stating in the docs since
  nothing in the API hints at it.
- Should `in` over a `{Base}LookupId` field accept the base column's display values and
  resolve them to IDs? Out of scope here — the caller supplies numeric IDs, as with `eq` —
  but it is the most likely next request once `in` exists.
