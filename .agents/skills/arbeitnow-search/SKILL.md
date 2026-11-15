---
name: arbeitnow-search
version: 1.0.0
description: >
  Search jobs in Germany on Arbeitnow (arbeitnow.com) through its free public
  job-board API - English- and German-language postings across Germany (Berlin,
  Munich, Hamburg...), some UK and remote roles, with a filter for employers that
  offer visa sponsorship. Use when the user wants jobs in Germany, visa-sponsored
  roles, or backend/engineering-manager roles in German cities. Trigger phrases:
  jobs in Germany, Germany visa sponsorship jobs, Berlin backend jobs, Arbeitnow,
  Stellenangebote, Jobs in Deutschland, Jobsuche Deutschland, Visa Sponsoring Jobs.
context: fork
enabled: true  # set to false to keep this portal installed but have /scrape skip it
allowed-tools: Bash(bun run .agents/skills/arbeitnow-search/cli/src/cli.ts *)
---

# Arbeitnow Search Skill

Reads Arbeitnow's **free public job-board API** (`https://www.arbeitnow.com/api/job-board-api`)
and its posting pages. No login, no API key, zero runtime dependencies - runs with just `bun`.
`robots.txt` allows everything; the API's own note says "free public API for jobs, please do not
abuse" and the Terms provide it "as is". Keep volume low: the CLI caps a scan at 5 pages.

## How searching works

The API has **no keyword parameter**. Each page holds the 300 newest postings, so the CLI reads
`--pages` pages (default 2 = 600 postings) and filters them locally. The one server-side filter is
`--visa` (`visa_sponsorship=true`): it changes the result set, but the field itself is not returned,
so results carry no per-job visa flag - the `meta.visa_sponsorship_filter` value tells you it was applied.

## Commands

### Search job listings

```bash
bun run .agents/skills/arbeitnow-search/cli/src/cli.ts search [flags]
```

Query syntax: matching is done by the CLI, not the portal - every bare word must appear (title, company, tags, job types or location), double quotes inside the argument make an exact phrase with word order, single quotes have no effect, no OR. Not comparable to a portal total (the API reports none).

Flags:
- `--query` / `-q <words>` - client-side filter (see Query syntax).
- `--location` / `-l <text>` - client-side substring on location (`Berlin`, `Munich`, `Hamburg`, `Remote`).
- `--visa` - only employers offering visa sponsorship (server-side).
- `--remote` - only postings flagged remote (client-side; the API's own remote parameter does nothing).
- `--jobage <days>` - posted within N days (client-side, from `created_at`).
- `--page <n>` - first API page (default 1); `--pages <n>` - pages to scan, 1-5 (default 2).
- `--limit` / `-n <n>`, `--format json|table|plain` (default `json`).

### Fetch full job detail

```bash
bun run .agents/skills/arbeitnow-search/cli/src/cli.ts detail <id|url> [--format json|plain]
```

Takes the search `id` (`<company>/<slug>`) or an `arbeitnow.com` / `arbeitnow.co.uk` posting URL.
Returns description, employment type and `validThrough` deadline from the page's JobPosting JSON-LD.

## Usage examples

```bash
# Backend roles at employers that sponsor visas
bun run .agents/skills/arbeitnow-search/cli/src/cli.ts search -q backend --visa --format table

# Engineering manager roles, wider scan
bun run .agents/skills/arbeitnow-search/cli/src/cli.ts search -q '"engineering manager"' --pages 5 --format table

# Go / Python backend roles in Berlin, last 14 days
bun run .agents/skills/arbeitnow-search/cli/src/cli.ts search -q go -l Berlin --jobage 14 --format table
bun run .agents/skills/arbeitnow-search/cli/src/cli.ts search -q python -l Munich --visa --format table

# Full description
bun run .agents/skills/arbeitnow-search/cli/src/cli.ts detail bunch/senior-backend-engineer-berlin-433276 --format plain
```

## Output formats

| Format | Use |
|--------|-----|
| `json` (default) | `{ "meta": { "count", "page", "pages_scanned", "scanned_postings", "visa_sponsorship_filter" }, "results": [...] }`; each result has `id`, `title`, `company`, `location`, `date`, `url`, `remote`, `tags`, `jobTypes`. |
| `table` | Title, company, location, date. |
| `plain` | One block per posting. |

## Notes

- Many postings are **German-language and require German** (titles with "(m/w/d)", German job types such as "Unbefristeter arbeitsvertrag"). Apply the Language Gate in `04-job-evaluation.md` - English-language postings usually carry "(f/m/d)" or "(all genders)".
- The feed also contains **UK postings** served from `arbeitnow.co.uk` (e.g. Cardiff); check `location`.
- Because filtering is local, a narrow query over 2 pages may return nothing; widen with `--pages 5` before concluding there are no matches.
