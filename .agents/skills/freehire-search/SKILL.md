---
name: freehire-search
version: 1.0.0
description: >
  Use this skill to search live software / tech / data / engineering job listings
  across many countries and markets (and remote) via the freehire.me aggregator's
  public API, or to look up a specific posting. It aggregates roles from ~50 ATS
  platforms into one schema, so a single skill covers many markets — but its faceted
  filtering (skills, category, seniority) is tuned tech-first, so scope triggers to
  technical roles. Trigger phrases: find a tech job, software job search, developer
  jobs, engineering vacancies, data/ML jobs, DevOps roles, remote developer jobs,
  "are there any <tech role> jobs in <place>", look up this freehire job posting.
context: fork
enabled: true  # set to false to keep this portal installed but have /scrape skip it
allowed-tools: Bash(bun run .agents/skills/freehire-search/cli/src/cli.ts *)
---

# freehire Search Skill

Search live job listings from the **[freehire.me](https://freehire.me)** job
aggregator — an open-source IT job board that normalizes postings from ~50 ATS
platforms across many countries into one schema. No authentication, no API key,
and **zero runtime dependencies** — it runs with just `bun`. The market is chosen
per query via facet flags (`--region`, `--country`), so the same skill works for a
forker in any market out of the box.

> This is a country-agnostic worked example of the repo's job-portal-skill pattern,
> like `linkedin-search`. Unlike the HTML-scraping portals, it queries freehire's
> public JSON API, so results are structured (skills, seniority, region facets)
> rather than parsed from markup.

## ⚠️ Scope: tech-focused

freehire's corpus already includes some non-tech postings (it crawls whole company
career pages), **but its faceted filtering — skills, categories, and seniority
dictionaries — is tuned tech-first today**, so this skill scopes its triggers to
software / data / engineering / tech roles, where the filtering is strong. Non-tech
coverage exists but is still maturing; don't rely on this skill for general
(non-technical) job coverage yet.

## ℹ️ Hosted-service dependency (best-effort, no SLA)

This skill depends on a third-party hosted service, freehire.me. Reads are
**public and unauthenticated** — the same zero-signup bar as `linkedin-search`.

**freehire.me is a personal project but actively maintained; it runs on a
best-effort basis (no formal SLA).** If the API is unreachable, the CLI fails
gracefully — a non-zero exit with a clear error message — so an outage degrades
this source rather than breaking the surrounding workflow.

**Self-hosting / swappable base URL.** The freehire backend is a separate
MIT-licensed repo — [`strelov1/freehire`](https://github.com/strelov1/freehire)
(Go + PostgreSQL + Meilisearch) — that stands up with one command via Docker
Compose (`make up` → API on `:8080`, same `/api/v1/...` paths). The skill honors a
base-URL env var, `FREEHIRE_API_URL` (default `https://freehire.me`), so pointing
it at a local instance is a one-line change:

```bash
FREEHIRE_API_URL=http://localhost:8080 bun run .agents/skills/freehire-search/cli/src/cli.ts search -q "go"
```

Caveat: standing up the *API* is light, but keeping a *full, continuously-fresh*
mirror (millions of postings across ~50 platforms) is resource-heavy — a
self-hoster would either crawl a scoped subset of sources or point the env var back
at the hosted API.

## When to use this skill

- Search for tech job openings by keyword, in a given region/country or remotely —
  each result comes back with its **full description**, no per-hit follow-up needed
- Filter by seniority, category, skills, or recency (posted within N days)
- Look one freehire posting up by its slug (including a closed one)

## Commands

### Search job listings

```bash
bun run .agents/skills/freehire-search/cli/src/cli.ts search [-q "<keywords>"] [facet flags]
```

Key flags:
- `--query <text>` / `-q <text>` — keyword search (title, skill, role). Full-text; optional.
  Supports `OR` and `title:` (see "Query operators" below).
- `--jobage <days>` — posted within N days (maps to `posted_within_days`).
- `--page <n>` — 1-indexed page. Default 1.
- `--limit <n>` / `-n <n>` — results per page (API limit). Default 25.
- `--format json|table|plain` — default `json`.
- `--description-format markdown|text|html` — how each result's full description is
  rendered. Default `markdown`, which keeps the posting's headings and requirement
  lists intact. `json` output only.

**Query operators.** `-q` understands two operators; the CLI passes the text through
unchanged and the API parses it:

- `OR` between alternatives returns jobs matching **any** of them, e.g.
  `-q "golang OR rust"`. Uppercase only: a lowercase `or` is an ordinary search
  word. Plain space-separated words are **not** a union (`-q "golang rust"` returns
  fewer jobs than `-q golang` alone), so use `OR` for synonyms and alternative stacks
  instead of running one search per keyword. At most 5 alternatives; a 6th is
  rejected with an error. An `OR` inside double quotes is literal text.
- A leading `title:` restricts that alternative to the job **title**, e.g.
  `-q "title:staff engineer"` skips postings that merely mention the words in their
  description. It applies per alternative, so repeat it on each one:
  `-q "title:staff engineer OR title:principal engineer"`. It only counts at the start
  of an alternative; `title:` in the middle is searched as text. Quotes work inside it:
  `-q 'title:"staff engineer"'` matches the phrase.

Facet flags, `--jobage`, `--page` and `--limit` apply to the combined result: every
alternative is filtered the same way, and a job matching several alternatives
appears once.

**Search results already carry the full description.** This skill queries freehire's
agent search endpoint, which replaces the index's truncated preview with each
posting's complete text, so a search of 20 roles is 1 request rather than 1 + 20.
Do **not** loop `detail` over search hits to read their descriptions — reach for
`detail` only to look one posting up by slug (e.g. from the tracker, or a posting
already closed and therefore absent from search). Full descriptions are verbose:
keep `--limit` modest, and pre-filter on title/company before reading bodies -
or pass `--no-description` for a cheap discovery pass that keeps every other
field and drops the bodies entirely (fetch a shortlisted job's body with
`detail`, or re-run the search without the flag).

Facet filters (values come from freehire's controlled vocabularies; comma-separate for OR within a facet):
- `--region <codes>` - macro-region: `global`, `eu`, `uk`, `north_america`, `latam`, `apac`, `mena`, `africa`, `cis`. `--region eu,uk`. `eu` does not include the UK; add `uk` for Europe as a whole. For a single country use `--country` instead - `us` is a country code, not a region, and `--region us` matches nothing. `--region`, `--country` and `--city` form one OR-group, so `--country us` alone scopes to the US. Use `none` to match jobs whose region could **not** be resolved (see "Partial data" below).
- `--country <codes>` — ISO-3166 alpha-2, e.g. `--country DE,GB`
- `--city <names>` — city name(s), e.g. `--city Berlin`
- `--seniority <levels>` — `junior`, `middle`, `senior`, `staff`, `principal`, `lead`, …
- `--category <cats>` — `backend`, `frontend`, `fullstack`, `devops`, `ml_ai`, `qa`, …
- `--skill <names>` — canonical skill(s), e.g. `--skill go,kubernetes`
- `--company <slug>` — company slug (from a result's `company_slug`)
- `--remote <mode>` — `remote` | `hybrid` | `onsite` (`work_mode` facet). A bare `--remote` means `remote`. Comma-separate for OR (`remote,hybrid`; `on-site` normalises to `onsite`). An unknown item exits 1 with `BAD_ARG`.
- `--facet <key=value>` — any other facet param (repeatable), e.g. `--facet salary_min=100000`

> **Location is a facet, not free text.** Unlike `linkedin-search`'s `--location`,
> freehire filters geography through the structured `--region`/`--country`/`--city`
> facets. Discover the live values for a market at
> [`/api/v1/jobs/facets`](https://freehire.me/api/v1/jobs/facets) (append `?q=<role>`
> to scope it) — never invent facet values.

### Fetch full job detail

```bash
bun run .agents/skills/freehire-search/cli/src/cli.ts detail <slug|url> [--format json|plain]
```

`slug` is the `id` from a `search` result (e.g. `golang-zensar-2bxu6dxm`). You may
also pass a full `https://freehire.me/jobs/<slug>` URL. A URL on any other host - including the employer apply link a search result's `url` field carries - is rejected with `BAD_ID` before any request. Returns the full (HTML-stripped)
description, skills, region/country, and — when the posting is enriched — seniority,
category, employment type, and salary. `salary` keeps its period when freehire records
one (`INR 300000–300000/year`, `INR 12000/month`); compare pay only in one period.

Use it for a posting you already have a slug for — a tracked application, a shared
link, or a closed posting search no longer lists. Re-fetching a hit that `search`
just returned only re-reads a description you already have.

## Usage examples

```bash
# Senior backend roles, table view
bun run .agents/skills/freehire-search/cli/src/cli.ts search -q "backend engineer" --seniority senior --limit 10 --format table

# Go or Rust roles, one search (OR must be uppercase)
bun run .agents/skills/freehire-search/cli/src/cli.ts search -q "golang OR rust" --remote remote --format table

# Staff or principal titles only, not postings that mention them in passing
bun run .agents/skills/freehire-search/cli/src/cli.ts search -q "title:staff engineer OR title:principal engineer" --format table

# Remote React roles in the EU
bun run .agents/skills/freehire-search/cli/src/cli.ts search -q "react" --remote remote --region eu --format table

# DevOps roles in Germany posted in the last 14 days
bun run .agents/skills/freehire-search/cli/src/cli.ts search --category devops --country DE --jobage 14 --format table

# ML/AI roles anywhere, fully remote
bun run .agents/skills/freehire-search/cli/src/cli.ts search -q "machine learning" --category ml_ai --remote remote --format table

# Descriptions as plain text instead of Markdown
bun run .agents/skills/freehire-search/cli/src/cli.ts search -q "platform engineer" --limit 5 --description-format text

# Full details for a specific job
bun run .agents/skills/freehire-search/cli/src/cli.ts detail golang-zensar-2bxu6dxm --format plain
```

## Output formats

| Format | Best for |
|--------|----------|
| `json` | Default — programmatic use; the only format carrying each hit's description |
| `table` | Quick human-readable scanning |
| `plain` | Reading a single job's full detail (`detail` command) |

Search JSON is `{ "meta": { "count", "page", "total" }, "results": [...] }`; each
result carries at least `id` (the freehire slug), `title`, `company`, `location`,
`date`, `url`, and `description` (missing values are `null`). `table` and `plain`
omit the description — it would swamp a scannable list. All errors are written to
**stderr** as `{ "error": "...", "code": "..." }` and the process exits with code `1`.

One diagnostic is not an error: a `--region` (or `--facet regions=`) value outside the
documented vocabulary - `us` is a country code, not a region - prints
`{ "warning": "...", "code": "REGION_VALUE_UNKNOWN" }` on **stderr** after the results,
and the process still exits `0` with stdout a valid JSON document.

## Partial data

Facets are derived per-posting and can be **incomplete** — geography especially.
A job may resolve its `work_mode` (e.g. `remote`) but leave its **region or
country undetermined** when the source's location text is ambiguous (freehire's
dictionaries never guess). So:

- A missing region/country means "not resolved", **not** "not applicable" —
  filtering on `--region eu` silently drops jobs whose region wasn't resolved,
  even if they are in fact EU. Widen or drop the facet if you need those back.
- There is a dedicated facet value for the unresolved bucket: `--region none`
  matches jobs with **no** resolved region — useful to sweep up remote roles that
  never pinned a geography. It ORs with real regions, e.g. `--region eu,none`.
- `result.regions` / `countries` / `cities` may be empty arrays for the same
  reason; treat empty as unknown, not as "none of the above".

## Notes

- Data is from freehire.me's public API — no credentials required. Only per-user
  tracking (apply/save) needs a key, and this skill deliberately does not touch it:
  it is **search + detail only**.
- `id` in search results is the freehire `public_slug` — pass it as-is to `detail`.
- `date` is the posting date as `YYYY-MM-DD` (the date part of the API's `posted_at` timestamp, the shape every portal CLI emits); it may be `null` for undated postings.
- Facet values are controlled vocabularies. Use `/api/v1/jobs/facets` to see the
  live values (with counts) for a query before filtering.
- The API retries 429/5xx with exponential backoff; an unreachable API exits
  non-zero with a clear message (best-effort service, see the dependency note above).
- `search` calls `/api/v1/agent/jobs/search` (public, like the rest); `--no-description`
  calls the lighter `/api/v1/jobs/search`, which serves no full bodies. A self-hosted
  instance older than that endpoint answers 404, and the CLI reports it as an error
  naming the endpoint — never as an empty result set.
