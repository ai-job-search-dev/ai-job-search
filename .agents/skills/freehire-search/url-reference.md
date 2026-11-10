# freehire.me API reference

The endpoints, parameters, and response shapes this skill depends on. This is the
file to update if the freehire API changes. Base URL defaults to
`https://freehire.me` and is overridable via the `FREEHIRE_API_URL` env var.

## Authentication

None for reads. `GET /api/v1/jobs/*` and `/companies/*` are public; only per-user
tracking mutations (`apply`/`save`/`me`) require a bearer API key, and this skill
does not use them.

Verified against the live API:

| Endpoint | Status |
|----------|--------|
| `GET /api/v1/agent/jobs/search` | 200 |
| `GET /api/v1/jobs/search` | 200 (the web variant; used by `--no-description`) |
| `GET /api/v1/jobs/facets` | 200 |
| `GET /api/v1/jobs/{slug}` | 200 |
| `GET /api/v1/auth/me` | 401 (auth required — not used here) |

## Envelope

Every response is `{ "data": ..., "meta": {...}, "error": "..." }`. Lists put the
array in `data` and pagination in `meta` (`{ total, limit, offset }`); a single
item puts the object in `data`. Errors are `{ "error": "<message>" }` with a 4xx/5xx
status (e.g. 404 → `{ "error": "not found" }`).

## `GET /api/v1/agent/jobs/search`

The endpoint the skill's `search` command uses. Full-text + facet search over open
jobs, returning `data: [job, …]` with `meta.total` = the estimated match count.

It runs the **same query** as the web-facing `/api/v1/jobs/search` — same `q`, same
facets, same ranking, same pagination guard (`offset + limit ≤ 10000`) — and differs
in one respect: it replaces the search index's truncated `description`
preview with the posting's **full** description read from the database. That is what
lets a search of N roles stay one request instead of N + 1.

It always does so - there is no parameter to turn hydration off - so the skill's
`--no-description` mode calls `/api/v1/jobs/search` instead. One extra parameter
controls the rendering:

| Param | Maps to CLI flag | Notes |
|-------|------------------|-------|
| `description_format` | `--description-format` | `markdown` (the skill's default), `text`, or `html`. **An unrecognized value is not an error** — the API falls back to `html`, so the CLI validates the flag itself. |

Hydration is best-effort per hit: a result whose row has vanished from the database
(the index lagging a just-removed job) keeps the preview rather than being dropped,
so `description` is a full text in practice but never guaranteed to be.

A `404` from this path means the instance predates the endpoint (a self-hosted
freehire behind `FREEHIRE_API_URL`), not a missing job; the CLI reports it as an
error naming the path rather than as an empty result set.

## `GET /api/v1/jobs/search`

The web variant of the same search — identical query surface, but `description` is
always the index's truncated preview. The skill calls it for `--no-description`,
where reading every full body from the database would be wasted work.

Query parameters used by the skill:

| Param | Maps to CLI flag | Notes |
|-------|------------------|-------|
| `q` | `--query` / `-q` | Keyword full-text query. Uppercase `OR` separates up to 5 alternatives (more is a 400); a leading `title:` restricts an alternative to the title. |
| `limit` | `--limit` / `-n` | Page size. Default 25 in the CLI. |
| `offset` | (derived) | `offset = (page - 1) * limit`. |
| `posted_within_days` | `--jobage` | Restrict to postings from the last N days. |
| `regions` | `--region` | Repeatable; OR within the facet. Values: `global`, `eu`, `uk`, `north_america`, `latam`, `apac`, `mena`, `africa`, `cis`. `us` is a country (`countries`), not a region. |
| `countries` | `--country` | Repeatable; ISO-3166 alpha-2 (lowercased). |
| `cities` | `--city` | Repeatable; display-name city. |
| `seniority` | `--seniority` | Repeatable; `junior`, `middle`, `senior`, `staff`, … |
| `category` | `--category` | Repeatable; `backend`, `frontend`, `fullstack`, `devops`, `ml_ai`, … |
| `skills` | `--skill` | Repeatable; canonical skill names. |
| `company_slug` | `--company` | Single company. |
| `work_mode` | `--remote` | `remote` \| `hybrid` \| `onsite`. |
| any facet param | `--facet key=value` | Escape hatch for the long tail (e.g. `salary_min`, `visa_sponsorship`, `employment_type`, `english_level`). |

Repeated params (`?seniority=senior&seniority=staff`) are ORed within a facet;
different facets are ANDed (geography ORs into one location group). Deep paging is
bounded server-side (`offset + limit ≤ 10000`).

### Job object (the fields the skill reads)

```jsonc
{
  "public_slug": "golang-zensar-2bxu6dxm", // -> result.id, and detail's <slug>
  "source": "oracle",
  "external_id": "…",
  "url": "https://…",                       // the real posting URL (ATS host)
  "title": "GOLANG",
  "company": "Zensar",
  "company_slug": "zensar",
  "location": "India",                       // free-text ATS location
  "description": "- …",                      // agent search: full text in the requested
                                             // format; elsewhere HTML, stripped client-side
  "skills": ["go", "kubernetes", …],         // dictionary facet (top-level)
  "work_mode": "remote",                     // may be absent
  "regions": ["apac"],                       // dictionary/hybrid facet
  "countries": ["in"],
  "cities": [],
  "collections": [],
  "posted_at": "2026-07-06T00:00:00Z",       // -> result.date (nullable)
  "created_at": "2026-07-06T15:25:…Z",
  "enrichment": {                             // nested, typed; {} when unenriched
    "seniority": "senior",
    "category": "backend",
    "employment_type": "full_time",
    "salary_min": 90000, "salary_max": 120000, "salary_currency": "EUR"
  }
}
```

The internal numeric id is deliberately never exposed; `public_slug` is the stable
identifier.

## `GET /api/v1/jobs/{slug}`

A single job by its `public_slug`. Returns the same job object in `data`. A closed
posting is still served here (with a non-null `closed_at`); a missing slug is 404
`{ "error": "not found" }`. The skill's `detail` command maps a 404 to a
`NOT_FOUND` error on stderr.

## `GET /api/v1/jobs/facets`

The market's facet-value distributions under an optional filter — each facet's live
values with counts. `data.facets` is `{ <facet>: { <value>: <count> } }`. This skill
does not call it programmatically, but it is the vocabulary source the SKILL.md
points users to (`?q=<role>` scopes the counts). Example:
`GET /api/v1/jobs/facets?q=react`.

## Parsing notes

- The response is JSON, so there is no HTML card parsing (unlike the scraping
  portals). The only markup handling left client-side is `detail`'s: `/jobs/{slug}`
  serves HTML, which `cleanHtml` (`cli/src/helpers.ts`) strips into readable text.
  Search descriptions arrive already rendered by the API and are passed through
  verbatim — stripping them again would undo the Markdown structure.
- Fetch sends `User-Agent: ai-job-search-dev/ai-job-search/1.0 (freehire-search)` (no contact
  URL: a host literal in `cli/src` makes `/apply` treat that host as this portal's board)
  and `Accept: application/json`, and uses exponential backoff with jitter on 429/5xx (max 6 retries). A connection error (API
  unreachable) fails fast with a clear message — no retry, since it is not
  transient server load — which is the graceful-degradation contract: an outage
  degrades this source quickly instead of hanging the caller.
