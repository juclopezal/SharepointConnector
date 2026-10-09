## 1. Schemas (`app/schemas/sharepoint.py`)

- [x] 1.1 Add `op: Literal["eq", "in", "ge", "gt", "le", "lt"] = "eq"` to `SearchFilter`
      (design.md decision 2). A closed enum, so no caller-supplied token can reach the
      generated OData expression; `extra="forbid"` already rejects anything else.
- [x] 1.2 Widen `SearchFilter.value` to `str | bool | int | float | list[str | bool | int |
      float]`, keeping Pydantic v2 smart-mode's `bool`/`int` distinction intact for both
      the scalar and the list case.
- [x] 1.3 Add a `model_validator(mode="after")` on `SearchFilter` enforcing the
      operator/value-shape agreement (422 per design.md decision 5): `op="in"` requires a
      list; every other operator requires a scalar.
- [x] 1.4 Enforce the `in` array constraints in the same validator: length 1–20, and
      homogeneous JSON type across elements (`bool` checked before `int`, so
      `[true, 1]` is a mixed array, not a valid one).
- [x] 1.5 Add a `model_validator(mode="after")` on `ListItemsSearchByUrlRequest`
      enforcing the total clause budget of 100 after expanding every `in` array
      (design.md decision 7), with an error message stating the generated count and the
      limit. The existing `max_length=15` on `filters` is unchanged.

## 2. Operator/column-type compatibility (`app/services/sharepoint.py`)

- [x] 2.1 Extend the column resolution helper so it returns the column's type family
      alongside the expected JSON type, without changing the existing `{Base}LookupId`
      fallback or the base-lookup rejection.
- [x] 2.2 Add an operator/column-type compatibility check implementing the matrix in
      design.md decision 4: ordering operators only on number, currency, `dateTime` and
      `{Base}LookupId`; `in` on every type that supports `eq` except boolean. Reject with
      `GraphAPIError(400)` naming the column, its type, and the operators it does support.
- [x] 2.3 Apply the existing value/column-type check to **every element** of an `in`
      array, rejecting the whole filter with `400` if any element fails.
- [x] 2.4 Confirm this check reads only the cached column definitions — no additional
      `GET .../lists/{id}/columns` call for operator validation.

## 3. Literal translation and clause building (`app/services/sharepoint.py`)

- [x] 3.1 Make literal emission column-aware for `dateTime` columns only (design.md
      decision 6): validate the value as an ISO-8601 instant and emit it **unquoted**;
      a non-parseable value is a `400` naming the column and the expected format. Every
      other column type keeps the current value-driven `_to_odata_literal` behaviour
      unchanged.
- [x] 3.2 Replace the hard-coded `eq` clause construction with operator-aware
      construction: `fields/{field} {op} {literal}` for `eq`/`ge`/`gt`/`le`/`lt`.
- [x] 3.3 Build `in` as a parenthesised OR group of equalities over the same field —
      `(fields/X eq 'a' or fields/X eq 'b')` — always wrapped in parentheses, including
      for a single-element array (design.md decision 3).
- [x] 3.4 Keep the outer join as `" and "` and keep percent-encoding the complete
      expression as one unit with `quote(..., safe="")`; verify parentheses and spaces
      are encoded, not passed through.
- [x] 3.5 Re-validate each filter's `field` against `_FIELD_NAME_RE` at the service layer
      (defence in depth, unchanged pattern) and confirm `op` reaching the builder can
      only be one of the six enum tokens.

## 4. Endpoint (`app/api/v1/endpoints/sharepoint.py`)

- [x] 4.1 Confirm no signature change is needed — the new field flows through the
      existing request model — and that the endpoint still returns
      `ListItemsSearchByUrlResponse` unchanged.
- [x] 4.2 Update the endpoint's OpenAPI description/examples to show an `in` filter and a
      date-range pair, so `/docs` documents the new vocabulary.

## 5. Tests — schema layer, 422 (`tests/test_sharepoint_endpoints.py`)

- [x] 5.1 Unknown `op` token (`"contains"`, `"ne"`, `"eq 1 or 1"`) → 422, and assert none
      of the supplied text appears in any generated expression.
- [x] 5.2 Operator/value-shape disagreement → 422: `op="in"` with a scalar; `op="ge"`
      with an array.
- [x] 5.3 `in` array bounds → 422 for `[]` and for 21 entries; 20 entries accepted.
- [x] 5.4 Mixed-type `in` array → 422, including the `[true, 1]` case that proves `bool`
      is not collapsed into `int`.
- [x] 5.5 Total clause budget → 422 when the expanded clause count exceeds 100, with the
      count and limit present in the message; a request just under the budget is accepted.
- [x] 5.6 Regression: existing bodies without `op` still validate, and `filters` >15 and
      `top` outside `[1, 5000]` still return 422.

## 6. Tests — service layer, 400 and expression construction (`tests/test_sharepoint_service.py`)

- [x] 6.1 Backwards compatibility: a filter without `op` produces a `$filter` clause
      byte-identical to the one produced today (guards design.md decision 1's promise).
- [x] 6.2 `in` expression construction: parentheses present, `or` between equalities,
      each element translated by its own type (`'O''Hara'` escaping, unquoted numerics),
      single-element array still parenthesised.
- [x] 6.3 AND/OR precedence: an `in` filter combined with a second filter yields
      `(...) and ...`, never a flattened expression.
- [x] 6.4 Comparison clause construction for `ge`/`gt`/`le`/`lt` on numeric columns.
- [x] 6.5 `dateTime` literal emitted unquoted; a quoted date literal must never appear in
      the generated expression.
- [x] 6.6 Malformed date value → 400 naming the column and expected format.
- [x] 6.7 Operator/column-type matrix → 400 for `ge` on a text/choice column and for `in`
      on a boolean column; accepted for `in` on text/choice, `ge` on numeric, `le` on
      `dateTime`, and ordering on `{Base}LookupId`.
- [x] 6.8 One badly-typed element in an `in` array → 400 for the whole filter.
- [x] 6.9 Operator validation triggers no second `GET .../columns` call within the cache
      TTL.
- [x] 6.10 Percent-encoding: the complete expression, parentheses included, is encoded as
      one unit.
- [x] 6.11 Run the full suite (`pytest`) and confirm no regressions.

## 7. Documentation

- [x] 7.1 `README.md`: document `op` in the items:search section — the six operators, the
      `in` array shape and its 1–20 bound, the ISO-8601 UTC date contract, the
      operator/column-type matrix, and the 100-clause budget. Include the
      `HistorialConsumoBolsas` example that motivated the change, showing it now takes 2
      calls instead of 4.
- [x] 7.2 `README.md`: state plainly that these operators do **not** lift SharePoint's
      list-view threshold, and that OR groups over non-indexed columns are likely to hit
      it on large lists.
- [x] 7.3 `ARQUITECTURA.md`: extend the items:search subsection with the exact generated
      OData for each operator, the column-aware `dateTime` literal rule, and the
      422-vs-400 split for the new validations.
- [x] 7.4 `ARQUITECTURA.md`: document the UTC-as-given semantics of comparison values and
      contrast it explicitly with the upsert's tenant-timezone `period`, so the two date
      semantics are not confused (design.md decision 6).
- [x] 7.5 `arquitecturasUML.md`: update the diagram if the validation flow gains a
      distinct step; note explicitly if no diagram change is warranted.
- [x] 7.6 `doc/CHANGELOG.md`: add a `## v2.6.0 — <date>` entry with context, the operator
      table, a JSON example, and the backwards-compatibility statement.
- [x] 7.7 Bump `VERSION` to `2.6.0` and update the version headers in `README.md`,
      `ARQUITECTURA.md` and `arquitecturasUML.md`.
- [x] 7.8 Extend `requirements/SPEC-004_List_Items_Search_By_Url.md` in place with the
      operator requirement and a new Bitácora de IA entry (same endpoint — no separate
      SPEC-005, per the precedent set by the `select` iteration).

## 8. Empirical verification (mandatory — against the real tenant)

Mocks cannot reproduce Graph's silent-failure modes. Every check below inspects the
returned rows, not just the status code: a `200` proves nothing when the failure mode is
a dropped clause.

> **Ejecutada el 2026-09-03 contra la lista DSL, no `HistorialConsumoBolsas`.** Esa lista
> resultó inalcanzable: `GET /v1/graph/sites?search=*` responde `403 accessDenied` con
> esta app registration, así que no hay manera de descubrir su URL. DSL sirve igual —
> tiene columnas de todos los tipos que la matriz necesita, incluidas cuatro `dateTime` —
> y además ya estaba caracterizada por las iteraciones v2.4.0 y v2.5.0, lo que permite
> comparar. Registro fila a fila en `requirements/SPEC-004`.
>
> **La verificación encontró un fallo bloqueante y se corrigió:** el literal de fecha
> desnudo que especifica OData 4.0 produce `400 invalidRequest` en Graph; el
> entrecomillado es el que filtra. Ver la nota de corrección en la decisión 6 de
> `design.md`. Sin esta fase, la funcionalidad se habría entregado con todas las
> comparaciones de fecha rotas.

- [x] 8.1 Local Docker rebuild (`docker compose -f devops/docker-compose.yml up -d
      --build`); `/health` reports `2.6.0` and `/openapi.json` shows `op` on the filter
      schema.
      → **Rebuild local hecho; `/health` reporta `2.6.0` y `/openapi.json` expone `op` como enum cerrado con default `eq`.**
- [x] 8.2 ~~`HistorialConsumoBolsas`~~ → **DSL** (lista inalcanzable, ver la nota de
      arriba). Nombres internos y tipos confirmados con una llamada `top: 1` y con
      `GET .../columns`, registrados en SPEC-004: texto `Entorno`/`Grupo`/`Title`, Sí/No
      `_x00da_ltima`, lookup `Cliente_x002d_LIBSA`, y `dateTime` `m0n4`, `l4h3`,
      `Created`, `Modified` (esta última indexada).
- [x] 8.3 `Estado in ["Para Entregar", "Iniciado", "Pausado"]` → inspect **every**
      returned row's `Estado` and confirm it is one of the three. **Any row with a
      different state is a blocking bug** (the OR group was mis-parenthesised or
      dropped), not a data anomaly.
      → **`in` de 2 valores → 2.345 filas = suma exacta de los `eq` individuales (2.290 + 55), ninguna fila fuera del conjunto. Hallazgo lateral: Graph compara texto sin distinguir mayúsculas.**
- [x] 8.4 The same `in` filter combined with a second equality filter → confirm both
      conditions hold on every returned row. A row satisfying only the `in` proves the
      parentheses are missing and is a blocking bug.
      → **`in` + `eq true` → 871 filas, todas cumpliendo ambas condiciones; ninguna satisfacía solo el `in`. Los paréntesis sujetan la precedencia.**
- [x] 8.5 `FechaFinalizado ge <now-15d>` (ISO-8601 UTC) → inspect every returned row's
      `FechaFinalizado` and confirm none is older than the bound. **Rows outside the
      range, or a row count equal to the unfiltered total, mean Graph ignored the clause
      — blocking bug.** Record the exact literal form that worked, with the date, in
      design.md and SPEC-004, following the boolean `1`/`0` precedent.
      → **`ge '2018-01-01'` → 9.127 filas (mínimo `2018-02-15`), `lt` complementario → 193 (máximo `2017-07-28`), suma = 9.320 = total exacto de la lista. **Requirió corregir el literal a la forma entrecomillada.****
- [x] 8.6 Same date filter sent with a **quoted** literal, as a control → record whether
      Graph errors or silently returns everything. This is the evidence that justifies
      decision 6; capture it verbatim.
      → **Control hecho al revés de lo previsto y con resultado inverso: el literal **desnudo** es el que falla (`400 invalidRequest`, en todas sus variantes) y el **entrecomillado** el que filtra. Capturado literalmente en SPEC-004 y en la decisión 6 de design.md.**
- [x] 8.7 Answer design.md's open question: does `eq` against a `dateTime` column filter
      correctly **today**, before this change? Record the answer. If it does not, log it
      as a pre-existing defect for a separate change — do **not** alter `eq` semantics
      here.
      → ****Respondida: `eq` sobre columna de fecha SÍ filtra hoy.** `eq '2026-09-02T14:15:45Z'` → exactamente 1 fila de 9.320. No hay defecto preexistente ni cambio de seguimiento por este motivo.**
- [x] 8.8 `ge` on a text/choice column and `in` on a Yes/No column → confirm `400` from
      the connector, not a `200` with plausible-looking rows.
      → **`ge` sobre texto, `in` y `ge` sobre Sí/No, fecha malformada, lookup base y campo inexistente → todos `400` del conector antes de llamar a Graph.**
- [x] 8.9 Characterise the URL-length ceiling: grow an `in` array (across several filters
      if needed) until Graph rejects the request, and record the clause count at which it
      breaks. Feed the finding back into design.md decision 7 so the budget of 100 stops
      being a guess.
      → ****Medido: el techo es la longitud de URL (~4,9–5,2 KB), no el número de cláusulas.** Caben 115 cláusulas cortas de texto, 88 de lookup y solo 73 de fecha; Graph responde `404`. El presupuesto de 100 admite peticiones que Graph rechazará: seguimiento propuesto en design.md decisión 7 y en ARQUITECTURA (presupuestar por longitud del `$filter`).**
- [x] 8.10 Run the motivating query end to end: the three open states in one call, and
      `Estado eq "Completado"` with `FechaFinalizado ge <now-15d>` in a second — confirm
      2 calls with no client-side `jq` filtering.
      → **`in` de 3 valores → 3.767 filas con esos tres valores y ningún otro; `eq` + `ge` de fecha → 324 filas en rango y del entorno pedido. Dos llamadas, cero post-filtrado.**
- [x] 8.11 Regression against a v2.5.0 request body (no `op`) → identical results to
      before the change.
      → **Body sin `op`: `ultima=true` → 3.790 filas, todas con el booleano pedido (v2.4.0 registró 3.838; la lista cambió de contenido en 14 meses). `LookupId 35` AND `ultima true` → 160 filas, todas cumpliendo ambas.**

## 9. Deployment (manual follow-up, not automated by this change)

- [ ] 9.1 Merge to `main` and push.
- [ ] 9.2 On the `docker-ag` host over SSH: `git pull` + `./deploy.sh update --pull`;
      verify `/health` reports `2.6.0`.
- [ ] 9.3 Re-run checks 8.3, 8.5 and 8.10 against the deployed instance to confirm the
      remote environment behaves identically to local.
