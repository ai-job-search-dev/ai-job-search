# jobnet-cli

CLI for the [Jobnet.dk](https://jobnet.dk) Danish government job portal API.

**Base URL**: `https://jobnet.dk/bff`
**Authentication**: No credentials required. All public endpoints only need the `x-csrf: 1` request header.
**Format**: All responses are JSON.

---

## Installation

```bash
cd .agents/skills/jobnet-search/cli
bun install
```

---

## Commands

| Command | Description |
|---------|-------------|
| `search` | Search for job ads with filters |
| `detail` | Full detail for a single job ad |
| `occupations` | Search occupation types (for building filters) |
| `suggestions` | Typeahead suggestions for job title / keyword search |

All commands accept `--format json|table|plain` (default: `json`).
All errors are written to **stderr** as `{ "error": "...", "code": "..." }` and the process exits with code `1`.

---

## Regions

| Value | Danish region |
|-------|--------------|
| `HovedstadenOgBornholm` | Hovedstaden og Bornholm |
| `Midtjylland` | Midtjylland |
| `Syddanmark` | Syddanmark |
| `OevrigeSjaelland` | Øvrige Sjælland |
| `Nordjylland` | Nordjylland |

---

## Order types

| Value | Description |
|-------|-------------|
| `PublicationDate` | Newest postings first (default) |
| `BestMatch` | Relevance score (requires `--search-string`) |
| `ApplicationDate` | Earliest deadline first |

---

## `search` — Search for job ads

**Endpoint**: `GET /FindJob/Search`

```bash
bun run src/cli.ts search [flags]
```

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--search-string` | string | — | Free-text keyword search (job title, skills, employer) |
| `--region` | string | — | One region value (see Regions table) |
| `--postal-code` | string | — | Postal code for radius search, e.g. `2100` |
| `--radius` | number | `50` | Radius in km from postal code (requires `--postal-code`) |
| `--work-hours` | string | — | `FullTime` or `PartTime` |
| `--duration` | string | — | `Permanent` or `Temporary` |
| `--job-type` | string | — | Announcement type: `Ordinaert`, `Efterloenner`, `Foertidspension` |
| `--occupation-area` | string | — | Occupation area identifier, e.g. `10000` |
| `--occupation-group` | string | — | Occupation group identifier, e.g. `10060` |
| `--occupation` | string | — | One ESCO occupation: the `conceptUriDa` from `occupations`, or its trailing UUID (sent as `occupations`) |
| `--page` | number | `1` | Page number (1-indexed) |
| `--per-page` | number | `10` | Results per page |
| `--limit` | number | — | Cap total results returned by CLI |
| `--order` | string | `PublicationDate` | Sort order (see Order types table) |
| `--format` | string | `json` | Output format: `json`, `table`, `plain` |

### Example

```bash
bun run src/cli.ts search \
  --search-string "sygeplejerske" \
  --region HovedstadenOgBornholm \
  --work-hours FullTime \
  --duration Permanent \
  --per-page 5 \
  --format table

bun run src/cli.ts search \
  --postal-code 8000 \
  --radius 25 \
  --per-page 10
```

### Response shape

```json
{
  "meta": {
    "totalJobAdCount": 21452,
    "pageNumber": 1,
    "resultsPerPage": 10,
    "searchString": "developer"
  },
  "facets": {
    "regions": [
      { "type": "HovedstadenOgBornholm", "jobAdCount": 6642 }
    ],
    "workHours": [
      { "type": "FullTime", "jobAdCount": 17919 },
      { "type": "PartTime", "jobAdCount": 3533 }
    ],
    "employmentDurations": [
      { "type": "Permanent", "jobAdCount": 18533 },
      { "type": "Temporary", "jobAdCount": 2919 }
    ],
    "occupationAreas": [
      { "identifier": "10000", "jobAdCount": 3235 }
    ],
    "countries": [
      { "label": "Danmark", "identifier": "DK", "jobAdCount": 21164 }
    ]
  },
  "results": [
    {
      "jobAdId": "9ef43bce-d82b-4ea1-a098-7ff6520f99be",
      "title": "Akademisk medarbejder med interesse for arbejdsmiljø og uddannelse",
      "hiringOrgName": "Region Midtjylland",
      "occupation": "Personalekonsulent",
      "municipality": "Viborg",
      "postalCode": 8800,
      "postalDistrictName": "Viborg",
      "country": "Danmark",
      "publicationDate": "2026-03-13T00:00:00+01:00",
      "applicationDeadline": "2026-04-05T21:59:00+02:00",
      "applicationDeadlineStatus": "ExpirationDate",
      "workHourPartTime": false,
      "isExternal": false,
      "jobAdUrl": null,
      "hasLogo": true,
      "logoUrl": "/bff/SharedComponents/JobAdCard/CompanyLogo/ByJobAdId/9ef43bce-d82b-4ea1-a098-7ff6520f99be",
      "cvr": "29190925",
      "workPlaceAddress": "",
      "conceptUriDa": "http://data.star.dk/esco/occupation/426e017f-ebe5-4bea-b1eb-7d2d5ab3c6db",
      "isSeen": false,
      "isFavorite": false,
      "company": "Region Midtjylland",
      "location": "Viborg",
      "date": "2026-03-13",
      "deadline": "2026-04-05",
      "url": "https://jobnet.dk/find-job/9ef43bce-d82b-4ea1-a098-7ff6520f99be",
      "applyUrl": null
    }
  ]
}
```

> **Note**: The `description` field (raw HTML) is intentionally omitted from `search` results for brevity. Use `detail` to retrieve the full job description.

> **Note**: Every result also carries the cross-portal contract fields `company`, `location`, `date`, `deadline`, `url`, and `applyUrl` - derived respectively from `hiringOrgName`, `postalDistrictName`/`municipality`, and the canonical portal URL `https://jobnet.dk/find-job/{jobAdId}`. `url` is always canonical on jobnet.dk for all ads so that deduplication, health checks, and `/apply` host routing identify the portal correctly. External ads carry their employer ATS destination in `applyUrl` (and `jobAdUrl`), while internal ads set `applyUrl` to `null`. `/scrape` Step 2 expects search output to include title, company, location, date, and URL, and dates follow the `YYYY-MM-DD` convention of the other portal CLIs. The API's `1900-01-01` deadline sentinel (deadline not disclosed) maps to `null`. The native fields above are preserved unchanged.

> **Note**: `resultsPerPage` and `pageNumber` must always be provided — omitting them while also providing `searchString` causes the API to return error 1014 ("Fejl i formatering af inputs").

---

## `detail` — Full job ad detail

**Endpoint**: `GET /FindJob/JobAdDetails/{id}` (falls back to `GET /FindJob/Search?searchString={id}` on 404 for external ads)

```bash
bun run src/cli.ts detail <id> [--format json|table|plain]
```

The `id` is the `jobAdId` UUID from `search` results.

By default the CLI passes `incrementViews=false` to avoid polluting view counts.

### Example

```bash
bun run src/cli.ts detail 9ef43bce-d82b-4ea1-a098-7ff6520f99be
bun run src/cli.ts detail 9ef43bce-d82b-4ea1-a098-7ff6520f99be --format plain
```

### Response shape

```json
{
  "id": "9ef43bce-d82b-4ea1-a098-7ff6520f99be",
  "title": "Akademisk medarbejder med interesse for arbejdsmiljø og uddannelse",
  "body": "<p>Full HTML job description...</p>",
  "publicationDateTime": "2026-03-13T08:06:58.0578635+01:00",
  "unpublicationDateTime": "2026-04-05T21:59:00+02:00",
  "approvalStatus": "Godkendt",
  "views": 2,
  "createdDateTime": "2026-03-13T08:06:58.3936145+01:00",
  "updatedDateTime": "2026-03-13T08:06:58.3936145+01:00",
  "isAnonymousEmployer": false,
  "hasLogo": true,
  "logoUrl": "/bff/SharedComponents/JobAdCard/CompanyLogo/ByJobAdId/9ef43bce-d82b-4ea1-a098-7ff6520f99be",
  "employer": {
    "cvrNumber": "29190925",
    "pNumber": "1003367314",
    "name": "Region Midtjylland",
    "hasCompanyLogo": true
  },
  "job": {
    "type": "Ordinaert",
    "address": {
      "streetName": "Heibergs Alle 5A",
      "city": "Viborg",
      "postalCode": "8800",
      "municipality": "Viborg",
      "countryCode": "DK",
      "countryName": "Danmark"
    },
    "noFixedWorkplace": false,
    "isLimitedPeriod": false,
    "isDisabilityFriendly": false,
    "isPartTime": false,
    "employmentDate": "2026-06-01T00:00:00+02:00",
    "conceptUriDa": "http://data.star.dk/esco/occupation/426e017f-ebe5-4bea-b1eb-7d2d5ab3c6db",
    "preferredLabelDa": "Personalekonsulent",
    "driversLicenses": [],
    "classifications": [],
    "shifts": [],
    "isFavorite": false
  },
  "application": {
    "deadlineDate": "2026-04-05T21:59:00+02:00",
    "availablePositions": 1,
    "contactPersons": [
      {
        "firstNames": "Jane",
        "lastName": "Doe",
        "phoneNumber": "+4512345678"
      }
    ],
    "url": "https://midtjob.dk/ad/...",
    "urlText": "",
    "isApplicationDeadlineASAP": false
  },
  "organisationTypeId": 24,
  "user": "Emply 31430747"
}
```

> **Note**: `body` contains raw HTML. In `--format plain` output the CLI strips HTML tags to produce readable text.

> **Note**: The optional `source` field indicates whether detail was synthesized from the search endpoint fallback (`"search-fallback"`) when `JobAdDetails` returned 404 for an external ad. Direct responses omit `source`. When present, plain and table formats render a `Source: search-fallback (detail unavailable, synthesised from search)` line.

> **Note**: `application.url` may be empty for some job ads — the employer may only accept applications through Jobnet's internal system.

---

## `occupations` — Search occupation types

**Endpoint**: `GET /OccupationSearch`

```bash
bun run src/cli.ts occupations --search-string <text> [flags]
```

Use this command to find occupation identifiers (ESCO concept URIs) to pass to `search --occupation`.

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--search-string` | string | **required** | Search term for occupation, e.g. `sygeplejerske` |
| `--per-page` | number | `10` | Max results to return |
| `--format` | string | `json` | Output format: `json`, `table`, `plain` |

### Example

```bash
bun run src/cli.ts occupations --search-string "sygeplejerske" --per-page 5
```

### Response shape

```json
[
  {
    "conceptUriDa": "http://data.star.dk/esco/occupation/56f5d45c-1234-4321-abcd-000000000000",
    "preferredLabelDa": "Sygeplejerske",
    "aliases": [
      {
        "aliasIdentifier": "some-uuid",
        "conceptUriDa": "http://data.star.dk/esco/occupation/56f5d45c-1234-4321-abcd-000000000000",
        "alternativeLabelDa": "Operationssygeplejerske"
      }
    ]
  }
]
```

> **Note**: `conceptUriDa` is the full ESCO concept URI. Pass it, or its UUID (last path segment), to `search --occupation`. The numeric `--occupation-area` / `--occupation-group` codes are a different taxonomy and match no ads for a UUID.

---

## `suggestions` — Typeahead suggestions

**Endpoint**: `GET /FindJob/GetTypeaheadSuggestions`

```bash
bun run src/cli.ts suggestions --query <text> [flags]
```

Returns autocomplete strings for the search box — useful for exploring valid Danish job titles before running a `search`.

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--query` | string | **required** | Partial search string to complete |
| `--limit` | number | — | Cap number of suggestions returned |
| `--format` | string | `json` | Output format: `json`, `table`, `plain` |

### Example

```bash
bun run src/cli.ts suggestions --query "syge"
bun run src/cli.ts suggestions --query "ingeniør" --limit 5
```

### Response shape

```json
[
  "sygepleje",
  "Sygeplejerske",
  "Sygeplejerske \"Lægeassistent\""
]
```

> **Note**: Suggestions are tuned for Danish job titles. English terms like "developer" may return an empty array.

---

## Error handling

All errors are written to **stderr** in JSON format and exit with code `1`:

```json
{ "error": "Job ad not found", "code": "NOT_FOUND" }
{ "error": "API request failed: 500 Internal Server Error", "code": "API_ERROR" }
{ "error": "--query is required", "code": "MISSING_REQUIRED" }
{ "error": "--search-string is required", "code": "MISSING_REQUIRED" }
```

---

## URL construction

Job ad detail pages on jobnet.dk:

```
https://jobnet.dk/find-job/{jobAdId}
```

The legacy `https://jobnet.dk/job/{jobAdId}` route redirects anonymous visitors into the
MitID login flow, so it is never emitted. External ads (`isExternal: true`) return 404 in a browser
on `/find-job/` and hit the login wall on `/job/`. The search command keeps `url` canonical
as `https://jobnet.dk/find-job/{jobAdId}` for all ads to preserve portal identity and route
to `jobnet detail`, while providing the external destination in `applyUrl` (and `jobAdUrl`).

Company logo images (prefix relative logoUrl from API):

```
https://jobnet.dk{logoUrl}
```

Example: `https://jobnet.dk/bff/SharedComponents/JobAdCard/CompanyLogo/ByJobAdId/9ef43bce-d82b-4ea1-a098-7ff6520f99be`

---

## Notes

- All data is from the public `jobnet.dk/bff` REST API — no credentials required.
- The `x-csrf: 1` header must be sent with every request.
- Pagination is 1-indexed (`--page 1` is the first page).
- `search` results intentionally omit the HTML `description` field — use `detail` to fetch it.
- `body` in `detail` responses is raw HTML; use `--format plain` to get stripped text.
- The `occupations` command helps discover ESCO occupation URIs to pass to `search --occupation` for narrowing search results.
