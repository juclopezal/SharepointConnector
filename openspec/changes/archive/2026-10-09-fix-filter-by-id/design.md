## Context

`PATCH /v1/sharepoint/list/item` resolves the list URL, then calls
`SharePointService.find_list_items_by_field()`, which always issues
`GET .../items?$expand=fields&$filter=fields/{field} eq '{value}'`. With `field = ID`
Graph answers `400 "A provided field name is not recognized"` for every value (observed
2026-10-08 on `Oper/Lists/Trabajos`, request_id `89765346-d5b8-4042-8641-4e64f20bd90b`).
`ID` is the list item's own identifier — the same number Graph uses in
`/items/{item-id}` — not an entry of the filterable `fields` bag. `items:search` builds the
same `fields/ID` clause and fails identically, after passing column-schema validation
because `ID` is a real column of the list.

## Goals / Non-Goals

**Goals:**
- Unblock updates located by `ID` on `PATCH /list/item`, with the same request and
  response contract callers already use.
- Replace Graph's cryptic `400` on `items:search` with an explicit, actionable `400`.

**Non-Goals:**
- Searching by `ID` (Graph's top-level `id` filter on list items is unverified).
- Upsert by `ID` (`POST /list/item:upsert`): no meaningful create branch; follow-up.
- `order_by` on `ID`: not observed failing; left untouched.

## Decisions

1. **Branch on `ID` in the endpoint, not inside `find_list_items_by_field`.** The update
   endpoint already owns the "locate → 404/409 → patch" orchestration; the `ID` path is a
   different way to locate, so it is a guard clause at the top of that flow. A small endpoint helper
   (`_find_list_item_by_id`) returns `[item]` or `[]` on Graph `404`, so the existing
   no-match `404` path and message are reused unchanged. Keeping
   `find_list_items_by_field` a pure `$filter` search avoids a method whose name lies.
   *Alternative:* special-case inside the service finder returning a one-element list —
   rejected, it hides a different Graph call behind a "find by field" name.

2. **`GET .../items/{id}` then `PATCH .../items/{id}/fields`** (new service method
   `get_list_item(site_id, list_id, item_id)`). The GET gives a clean `404` *before* any
   write and provides `webUrl` for the unchanged response shape (the `PATCH .../fields`
   response is a `fieldValueSet` without `webUrl`). Same number of Graph calls as today.
   *Alternative:* PATCH directly and map its `404` — rejected, loses `webUrl` and would
   change the response contract.

3. **Validate the value as 1–10 ASCII digits (`[0-9]{1,10}`), normalise with `int()`.** `\d` is avoided because it also matches non-ASCII Unicode digits. The value is
   interpolated into a URL path, so it must not carry `/`, `..`, spaces or OData syntax
   (input-safety rule in AGENTS.md). Non-digits → `400` before Graph. `"0"` and other
   unknown IDs are digits and fall through to Graph's `404`, which the endpoint rewrites
   to the existing no-match message so callers see one `404` format.

4. **Case-insensitive match on `ID`.** SharePoint's internal name is `ID`, but Graph
   also exposes `id` on the item; accepting any casing costs nothing and avoids a second
   trap. A custom column literally named `Id` cannot exist alongside the system one.

5. **Search: reject `ID` in the service, before `get_list_columns`.** One guard in
   `search_list_items` (defence where the `$filter` is built), raising `GraphAPIError(400)`
   that names `ID` and points to `PATCH /list/item` for single-item access. Applies to all
   operators.

## Risks / Trade-offs

- [Graph `404` on GET could mean "list not found" rather than "item not found"] → The list
  was just resolved from the URL by the resolver, so a `404` here is the item; message
  stays the existing no-match one.
- [A caller relied on Graph's raw `400` text for `ID`] → Not plausible: the request never
  worked; the new `400` is strictly more informative.
- [Other system columns (`Created`, `Author`…) may fail the same way] → Out of scope; no
  consumer reported it. Noted in the SPEC amendment as a watch item.

## Migration Plan

Patch release 2.6.1. No data or configuration migration. Deploy with the usual
`deploy.sh update` flow on `docker-ag`; rollback = redeploy 2.6.0 (only `ID` requests
change behaviour, and they failed on 2.6.0).

## Open Questions

None blocking.
