---
name: berlinstartupjobs-search
version: 1.0.0
description: >
  Search job postings on Berlin Startup Jobs (berlinstartupjobs.com), an
  English-language board for startup and tech roles in Berlin, Germany (plus a
  few remote roles). Use when the user wants Berlin or Germany startup jobs:
  engineering, engineering manager, staff engineer, product, AI roles. Trigger
  phrases: Berlin startup jobs, jobs in Berlin, Berlin tech jobs, Germany
  startup jobs, Stellenangebote Berlin, Jobs in Berlin, Startup-Jobs Berlin,
  look up this berlinstartupjobs posting.
context: fork
enabled: true  # set to false to keep this portal installed but have /scrape skip it
allowed-tools: Bash(bun run .agents/skills/berlinstartupjobs-search/cli/src/cli.ts *)
---

# Berlin Startup Jobs Search Skill

Searches live postings on [Berlin Startup Jobs](https://berlinstartupjobs.com) through the
site's public WordPress REST API (`/wp-json/wp/v2/posts`). No authentication, no API key,
zero runtime dependencies - runs with just `bun`. `robots.txt` permits these paths.

The board is small (roughly 100-150 live postings at a time, about a quarter in
engineering) and almost entirely Berlin-based, with postings in English.

## Commands

### Search job listings

```bash
bun run .agents/skills/berlinstartupjobs-search/cli/src/cli.ts search [flags]
```

Query syntax: double quotes inside the argument make an exact phrase, word order included; single quotes have no effect; no OR operator. Bare words are ANDed.

Flags:
- `--query` / `-q <text>` - keywords; WordPress full-text search over title and body.
- `--jobage <days>` - posted within N days (maps to the API's `after` date filter).
- `--remote` - only postings tagged *Remote* or *Remote Possible* (client-side filter on the current page).
- `--page <n>` - 1-indexed, 20 results per page.
- `--limit` / `-n <n>` - cap results emitted (client-side).
- `--format json|table|plain` - default `json`.

There is **no location flag**: nearly every posting is Berlin, Germany. Each result carries its `location`.

### Fetch full job detail

```bash
bun run .agents/skills/berlinstartupjobs-search/cli/src/cli.ts detail <id|url> [--format json|plain]
```

Accepts the numeric post id from `search`, or a `berlinstartupjobs.com` posting URL. Other hosts are rejected.

## Usage examples

```bash
# Engineering manager roles in Berlin
bun run .agents/skills/berlinstartupjobs-search/cli/src/cli.ts search -q "engineering manager" --format table

# Exact phrase, posted in the last 14 days
bun run .agents/skills/berlinstartupjobs-search/cli/src/cli.ts search -q '"staff engineer"' --jobage 14 --format table

# Backend roles that allow remote
bun run .agents/skills/berlinstartupjobs-search/cli/src/cli.ts search -q backend --remote --format table

# Payments / fintech roles
bun run .agents/skills/berlinstartupjobs-search/cli/src/cli.ts search -q payments --format table

# Full description of one posting
bun run .agents/skills/berlinstartupjobs-search/cli/src/cli.ts detail 452724 --format plain
```

## Output formats

| Format | Use |
|--------|-----|
| `json` (default) | `{ "meta": { "count", "page", "total" }, "results": [...] }`; each result has `id`, `title`, `company`, `location`, `date`, `url`, `category`, `remote`. `total` is the portal's own match count (`X-WP-Total`). |
| `table` | Fixed-width table for quick reading. |
| `plain` | One block per posting. |

## Notes

- Titles on the site embed the company after ` // ` ("Tech Lead (m/f/d) // Superchat"); the CLI strips it from `title` and takes `company` from the `job_company` taxonomy, falling back to the suffix.
- A page past the last one returns HTTP 400 `rest_post_invalid_page_number`; the CLI reports it as an empty page.
- Full-text search matches body text too, so broad words ("manager") pull in unrelated roles; prefer quoted phrases.
- German-language requirements, when present, are stated in the description - the Language Gate in `04-job-evaluation.md` applies.
