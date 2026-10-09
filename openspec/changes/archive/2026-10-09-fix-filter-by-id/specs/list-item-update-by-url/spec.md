## ADDED Requirements

### Requirement: Update a list item located by a unique field
The system SHALL expose `PATCH /v1/sharepoint/list/item`, accepting a JSON body with
`sharepoint_url` (the list URL as seen in the browser), `filter_by` (`field` + `value`)
and `data` (internal column name → new value). When `filter_by.field` is any column other
than `ID`, the system SHALL locate the item with an exact-equality match on that column
and apply `data` to it. `filter_by.field` SHALL match `^[A-Za-z0-9_]+$`.

#### Scenario: Single match is updated
- **WHEN** exactly one item has `filter_by.field` equal to `filter_by.value`
- **THEN** `data` is applied to that item and the response is `200` with
  `{ id, webUrl, site_id, list_id }`

#### Scenario: No match
- **WHEN** no item matches `filter_by`
- **THEN** the response is `404` naming the field and value, and nothing is written

#### Scenario: Several matches
- **WHEN** more than one item matches `filter_by`
- **THEN** the response is `409` stating how many matched, and nothing is written

#### Scenario: Invalid field name
- **WHEN** `filter_by.field` contains characters other than letters, digits or `_`
- **THEN** the response is `422`

### Requirement: Update a list item addressed by its ID
When `filter_by.field` is `ID` (compared case-insensitively), the system SHALL NOT search
the list with an OData `$filter`. It SHALL address the item directly by its identifier,
taken from `filter_by.value`, and apply `data` to it. `filter_by.value` SHALL consist of
digits only. The response shape SHALL be the same as for any other `filter_by.field`.

#### Scenario: Existing ID is updated
- **WHEN** `filter_by` is `{ "field": "ID", "value": "1087" }` and item `1087` exists
- **THEN** `data` is applied to item `1087` and the response is `200` with
  `{ id: "1087", webUrl, site_id, list_id }`

#### Scenario: Field name is matched case-insensitively
- **WHEN** `filter_by.field` is `Id` or `id`
- **THEN** the request behaves exactly as with `ID`

#### Scenario: Nonexistent ID
- **WHEN** `filter_by.value` is a digit string for which no item exists (including `"0"`)
- **THEN** the response is `404` with the same message format as the no-match case, and
  nothing is written

#### Scenario: Non-numeric ID value
- **WHEN** `filter_by.field` is `ID` and `filter_by.value` is not a digit string
  (e.g. `"abc"`, `"-1"`, `"1 or 1"`, `""`)
- **THEN** the response is `400` stating that `ID` requires a numeric value, and Graph is
  not called

#### Scenario: Graph never receives a filter on ID
- **WHEN** an update is located by `ID`
- **THEN** no request with `$filter=fields/ID ...` is sent to Graph
