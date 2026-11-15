---
name: wellfound-search
version: 1.0.0
description: >
  Search startup job postings on Wellfound (wellfound.com, formerly AngelList
  Talent) role pages - startups worldwide, strong in India (Bengaluru, Mumbai,
  Pune, Delhi NCR), the US and remote. Use when the user wants startup jobs,
  AI startup jobs, or remote startup roles such as engineering manager, staff
  engineer, backend engineer. Trigger phrases: Wellfound jobs, AngelList jobs,
  startup jobs, startup jobs in Bangalore, remote startup jobs, look up this
  Wellfound posting.
context: fork
enabled: true  # set to false to keep this portal installed but have /scrape skip it
allowed-tools: Bash(bun run .agents/skills/wellfound-search/cli/src/cli.ts *)
---

# Wellfound Search Skill

Reads Wellfound's public, server-rendered **role pages** (`/role/<role>`,
`/role/l/<role>/<city>`, `/role/r/<role>`) and **job pages** (`/jobs/<id>-<slug>`).
No login, no API key, zero runtime dependencies - runs with just `bun`.

## ⚠️ Personal use only

`robots.txt` permits these paths, but **Wellfound's Terms prohibit "scraping" content and
automated access that produces more load than a human browsing.** Keep volume low (a few
role pages per session, not a crawl), never use it commercially or to collect or
redistribute data in bulk, and run it on your own responsibility. Searching remains possible
manually at wellfound.com if you prefer not to use this.

## Commands

### Search job listings

```bash
bun run .agents/skills/wellfound-search/cli/src/cli.ts search -q "<role>" [flags]
```

Query syntax: not a keyword search - the query is turned into a Wellfound role-page slug ("engineering manager" -> `engineering-manager`); quotes and OR are stripped and have no effect. Not verified against a portal total (role pages report none).

Flags:
- `--query` / `-q <role>` - **required.** A role name Wellfound has a page for: `engineering manager`, `staff engineer`, `backend engineer`, `software engineer`, `machine learning engineer`, ...
- `--location` / `-l <city>` - city slug: `bangalore`, `mumbai`, `pune`, `delhi`, `berlin`, ... Unknown role/city pairs exit 1 with `NO_ROLE_PAGE` (Wellfound redirects them; e.g. `staff engineer` + `hyderabad`).
- `--remote` - remote role page. Conflicts with `--location`.
- `--jobage <days>` - posted within N days (client-side filter on the fetched page).
- `--page <n>` - 1-indexed (about 20 companies per page).
- `--limit` / `-n <n>` - cap results emitted.
- `--format json|table|plain` - default `json`.

### Fetch full job detail

```bash
bun run .agents/skills/wellfound-search/cli/src/cli.ts detail <id|url> [--format json|plain]
```

Takes the `id` from search results, which is the `<id>-<slug>` key (e.g.
`4475538-engineering-manager-backend`), or a `wellfound.com/jobs/...` URL. A bare number is
rejected: Wellfound 404s `/jobs/<id>` without the slug.

## Usage examples

```bash
# Engineering manager roles at Bengaluru startups
bun run .agents/skills/wellfound-search/cli/src/cli.ts search -q "engineering manager" -l bangalore --format table

# Remote engineering manager roles
bun run .agents/skills/wellfound-search/cli/src/cli.ts search -q "engineering manager" --remote --format table

# Backend roles in Mumbai posted in the last 14 days
bun run .agents/skills/wellfound-search/cli/src/cli.ts search -q "backend engineer" -l mumbai --jobage 14 --format table

# Staff engineer roles, any location
bun run .agents/skills/wellfound-search/cli/src/cli.ts search -q "staff engineer" --format table

# Full description
bun run .agents/skills/wellfound-search/cli/src/cli.ts detail 4475538-engineering-manager-backend --format plain
```

## Output formats

| Format | Use |
|--------|-----|
| `json` (default) | `{ "meta": { "count", "page" }, "results": [...] }`; each result has `id`, `title`, `company`, `location`, `date`, `url`, plus `remote`, `compensation`, `jobType`, `yearsExperienceMin`. |
| `table` | Fixed-width table including pay. |
| `plain` | One block per posting. |

## Notes

- Results come in Wellfound's page order (grouped by company), not strictly newest first. Older listings can appear; use `--jobage`.
- `compensation` is Wellfound's display string (`$50k – $55k`, `₹20L – ₹50L • No equity`). Detail returns the structured `baseSalary` as `salary`.
- `location` joins office cities and, for remote roles, the accepted remote regions: `Bengaluru · Remote (India)`.
- If a role page ever returns no Apollo data (bot challenge or redesign), search exits 1 with `PARSE_FAILED` rather than reporting zero jobs.
