## 1. Service (`app/services/sharepoint.py`)

- [x] 1.1 Add `get_list_item(site_id, list_id, item_id)` → `GET .../items/{item_id}`
      (returns `id`, `webUrl`, `fields`); structured log with `site_id`/`list_id`/`item_id`.
- [x] 1.2 In `search_list_items`, reject any filter whose field is `ID`
      (case-insensitive) with `GraphAPIError(400)` naming `ID` and pointing to
      `PATCH /list/item`, before any Graph call (design decision 5).

## 2. Endpoint (`app/api/v1/endpoints/sharepoint.py`)

- [x] 2.1 In `update_list_item_by_url`, add a guard branch when `filter_by.field` is `ID`
      (case-insensitive): validate value `[0-9]{1,10}` (else `400`), normalise with `int()`,
      call `get_list_item`; map Graph `404` to the existing no-match `404` message.
- [x] 2.2 Apply `data` with `update_list_item` and return the unchanged
      `ListItemUpdateByUrlResponse` (`webUrl` from the GET).
- [x] 2.3 Update the endpoint docstring (`/docs`) and the `FilterBy.field` description
      to document `ID` direct addressing.

## 3. Tests

- [x] 3.1 Endpoint: `ID` existing → 200, no `find_list_items_by_field` call.
- [x] 3.2 Endpoint: `Id` / `id` → same as `ID`.
- [x] 3.3 Endpoint: nonexistent ID (incl. `"0"`) → 404 with the no-match message, no PATCH.
- [x] 3.4 Endpoint: non-numeric values (`"abc"`, `"-1"`, `"1 or 1"`, `""`) → 400, no Graph call.
- [x] 3.5 Service: `get_list_item` builds `/items/{id}` URL; search with `ID` (each operator,
      any casing) → 400 without calling Graph.
- [x] 3.6 Regression: non-`ID` update and search paths unchanged; full suite green.

## 4. Docs and version

- [x] 4.1 `VERSION` → 2.6.1; entry at top of `doc/CHANGELOG.md`.
- [x] 4.2 Append amendment blocks to `requirements/SPEC-002` (update by `ID`) and
      `requirements/SPEC-004` (`ID` rejected in search); fill AI log.
- [x] 4.3 Update `README.md` and `ARQUITECTURA.md` where the update endpoint / version appear.

## 5. Verification against real SharePoint

- [x] 5.1 On local Docker: `PATCH /list/item` with `ID` = real `Trabajos` item and
      `data: {}` → 200; `ID` = `"0"` → 404; `ID` = `"abc"` → 400.
- [x] 5.2 `items:search` with `ID` filter → explicit 400.
- [ ] 5.3 Real update of `Estimadas` on one agreed `Trabajos` item (value restored after),
      only with the owner's go-ahead.

## 6. Release

- [ ] 6.1 Commit `v2.6.1` (message = CHANGELOG entry), push.
- [ ] 6.2 Deploy on `docker-ag` (`git pull` + `./deploy.sh update --pull`) and re-run 5.1.
