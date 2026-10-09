## Why

`PATCH /v1/sharepoint/list/item` cannot update an item located by its SharePoint `ID`.
The endpoint always locates the target with `$filter=fields/{field} eq '{value}'`, and
Microsoft Graph rejects `fields/ID` with `400 "A provided field name is not recognized"`
for any value — `ID` is the item's own identifier, not a filterable entry of the `fields`
bag. `POST /v1/sharepoint/list/items:search` fails the same way when a filter targets `ID`.

This blocks a real consumer now: step 5 of the *cierre* flow updates `Estimadas` on the
`Trabajos` list by `ID`, and `Trabajos` has no other unique column (`Cod_x002e_Propuesta`
has 279 repeated values; `Ticket` has blanks and duplicates). Without the fix, every real
*cierre* writes its history record but leaves `Estimadas` stale, to be fixed by hand.

## What Changes

- `PATCH /v1/sharepoint/list/item`: when `filter_by.field` is `ID` (case-insensitive), the
  connector **addresses the item directly** (`GET .../items/{id}` then
  `PATCH .../items/{id}/fields`) instead of searching with `$filter`.
  - `filter_by.value` must be a non-negative integer (digits only) → otherwise `400`.
  - Item does not exist (including `0`) → `404` with the same message as today's no-match case.
  - No `409` path: an `ID` is unique by construction.
  - Response shape (`id`, `webUrl`, `site_id`, `list_id`) unchanged.
- `POST /v1/sharepoint/list/items:search`: a filter on `ID` is rejected with
  an explicit `400` naming the column and pointing to `PATCH /list/item`, instead of
  forwarding Graph's cryptic `400`. No new search capability is added.
- Behaviour for every other `filter_by.field` / search filter is unchanged.

Not breaking: requests on `ID` fail today on both endpoints, so no working caller changes
behaviour. Version **2.6.1** (patch).

## Capabilities

### New Capabilities
- `list-item-update-by-url`: `PATCH /v1/sharepoint/list/item` — locating the item to
  update via `filter_by` (unique field match, or direct addressing when the field is `ID`)
  and the `404`/`409`/`400` contract. The endpoint predates OpenSpec and has no spec yet;
  this change captures it.

### Modified Capabilities
- `list-items-search-by-url`: new requirement adding an explicit
  rejection of the system `ID` column in `filters` (`order_by` untouched — not observed failing).

## Impact

- **Code:** `app/api/v1/endpoints/sharepoint.py` (update endpoint branch on `ID`),
  `app/services/sharepoint.py` (new `get_list_item`; `ID` guard in `search_list_items`).
- **Tests:** `tests/test_sharepoint_endpoints.py`, `tests/test_sharepoint_service.py`.
- **Docs:** `VERSION` → 2.6.1, `doc/CHANGELOG.md`, `README.md`, `ARQUITECTURA.md`,
  amendment appended to `requirements/SPEC-002` and `requirements/SPEC-004`.
- **Out of scope:** `POST /list/item:upsert` with `key_field: ID` fails the same way, but
  upserting by `ID` has no sensible "create" branch; left as-is and noted as follow-up.
  Searching by `ID` (top-level `id` filter in Graph) is not added — unverified, and not
  needed by any consumer.
- No SharePoint, schema or downstream design change.
