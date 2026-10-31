# freehire-cli

CLI for searching the [freehire.me](https://freehire.me) job aggregator across
**many markets** (tech-focused), via its public JSON API.

**Data source**: freehire.me REST API (`/api/v1/agent/jobs/search`, `/api/v1/jobs/search` for `--no-description`, `/api/v1/jobs/facets`, `/api/v1/jobs/{slug}`).
**Authentication**: None required — reads are public (only tracking mutations need a key, and those are out of scope here).
**Dependencies**: None (plain `bun` + `fetch`). `bun install` is optional and only pulls dev type defs.

> **Hosted-service dependency.** This skill talks to freehire.me, a personal
> project maintained on a **best-effort basis with no formal SLA**. If the API is
> unreachable the CLI exits non-zero with a clear error rather than hanging, so an
> outage degrades gracefully instead of breaking the caller. Point `FREEHIRE_API_URL`
> at a self-hosted [freehire](https://github.com/strelov1/freehire) backend to swap
> the source.

## Installation

```bash
cd .agents/skills/freehire-search/cli
bun install   # optional — only installs TypeScript dev types
```

The CLI runs without any install because it has zero runtime dependencies.

## Self-hosting / base URL

The base URL defaults to `https://freehire.me` and is overridable with an env var:

```bash
FREEHIRE_API_URL=http://localhost:8080 bun run src/cli.ts search -q "go"
```

The freehire backend is MIT-licensed and stands up with one command via Docker
Compose (`make up` → API on `:8080`, same `/api/v1/...` paths).

## Commands

| Command | Description |
|---------|-------------|
| `search` | Search jobs by keyword and facet filters |
| `detail` | Fetch full detail for a single job by its slug |

`search` accepts `--format json|table|plain` (default `json`); `detail` accepts `--format json|plain`.
All errors are written to **stderr** as `{ "error": "...", "code": "..." }` with exit code `1`.
One diagnostic is a warning instead: a `--region` value outside the documented vocabulary
prints `{ "warning": "...", "code": "REGION_VALUE_UNKNOWN" }` to stderr after the results,
and exits `0`, leaving stdout a valid JSON document.

`search` hits the API's agent endpoint, so every JSON result already carries the
posting's **full** description (Markdown by default, `--description-format
text|html` to change it). `detail` remains for looking a single posting up by
slug — including a closed one, which search does not return.

## Quick examples

```bash
# Senior backend roles, table view
bun run src/cli.ts search -q "backend engineer" --seniority senior --limit 10 --format table

# Remote React roles in the EU
bun run src/cli.ts search -q "react" --remote remote --region eu --format table

# DevOps roles in Germany posted in the last 14 days
bun run src/cli.ts search --category devops --country DE --jobage 14 --format table

# Full detail for one job (slug from a search result's id)
bun run src/cli.ts detail golang-zensar-2bxu6dxm --format plain
```

See `../SKILL.md` for the full flag reference and the hosted-dependency note.

## Search flags

| Flag | Alias | Description |
|------|-------|-------------|
| `--query` | `-q` | Keywords (title / skill / role). Full-text; optional. `a OR b` matches either (uppercase `OR`, max 5 alternatives); a leading `title:` searches the job title only. |
| `--jobage` | | Posted within N days (`posted_within_days`). |
| `--page` | | 1-indexed page. Default 1. |
| `--limit` | `-n` | Results per page (API limit). Default 25. |
| `--region` | | Macro-region(s), comma = OR: `global`, `eu`, `uk`, `north_america`, `latam`, `apac`, `mena`, `africa`, `cis` (e.g. `eu,uk`). A country such as `us` goes in `--country`. |
| `--country` | | ISO-3166 alpha-2 code(s). |
| `--city` | | City name(s). |
| `--seniority` | | Seniority level(s). |
| `--category` | | Role category(ies). |
| `--skill` | | Canonical skill(s). |
| `--company` | | Company slug. |
| `--remote` | | `remote` \| `hybrid` \| `onsite` (`work_mode`). |
| `--facet` | | Any other facet as `key=value` (repeatable). |
| `--format` | | `json` \| `table` \| `plain`. |
| `--description-format` | | `markdown` (default) \| `text` \| `html` — how each result's full description is rendered (`json` output only). |

Facet values come from freehire's controlled vocabularies. Discover the live
values (with counts) for a market at
[`/api/v1/jobs/facets`](https://freehire.me/api/v1/jobs/facets), or narrow it,
e.g. `https://freehire.me/api/v1/jobs/facets?q=react`.
