## ADDED Requirements

### Requirement: The system ID column is rejected in search filters
The system SHALL reject any entry of `filters` whose `field` is `ID` (compared
case-insensitively) with a `400` that names the column and explains that Microsoft Graph
cannot filter list items on it, instead of forwarding the request to Graph. The check
SHALL run before any request to Graph and SHALL apply to every operator. `order_by` is
not affected by this requirement.

#### Scenario: Equality filter on ID is rejected explicitly
- **WHEN** the request includes `filters: [{ "field": "ID", "value": "1087" }]`
- **THEN** the response is `400` naming `ID`, and no `$filter` on `ID` is sent to Graph

#### Scenario: Any operator on ID is rejected
- **WHEN** a filter on `ID` (or `Id`, `id`) uses `in`, `ge`, `gt`, `le` or `lt`
- **THEN** the response is `400` naming `ID`

#### Scenario: Filters on other columns are unaffected
- **WHEN** no filter targets `ID`
- **THEN** the search behaves exactly as before this change
