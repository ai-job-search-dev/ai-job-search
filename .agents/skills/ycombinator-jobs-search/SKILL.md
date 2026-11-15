---
name: ycombinator-jobs-search
version: 1.0.0
description: >
  Search startup jobs at Y Combinator companies via ycombinator.com/jobs role
  pages (the public face of Work at a Startup). Covers YC startups worldwide,
  with India, remote, Berlin and US-city pages; every result shows salary,
  experience and visa sponsorship. Use when the user wants YC startup jobs,
  AI startup roles, or startups that sponsor visas. Trigger phrases: YC jobs,
  Y Combinator jobs, Work at a Startup, YC startup jobs in India, remote YC
  jobs, startups that sponsor visas, look up this ycombinator.com job.
context: fork
enabled: true  # set to false to keep this portal installed but have /scrape skip it
allowed-tools: Bash(bun run .agents/skills/ycombinator-jobs-search/cli/src/cli.ts *)
---

# Y Combinator Jobs Search Skill

Reads YC's public, server-rendered job pages: role pages `/jobs/role/<role>[/<location>]` and
job pages `/companies/<company>/jobs/<key>`. No login, no API key, zero runtime dependencies.
`www.workatastartup.com` itself is not used (it answers tools with HTTP 406 and needs a YC
account to list jobs).

## ⚠️ Personal use only

`robots.txt` permits these paths, but **YC's Terms of Use prohibit "data mining, robots,
scraping or similar data gathering"**. Keep volume low (a few pages per session), never use it
commercially or to collect or redistribute data in bulk, and run it on your own
responsibility. If YC ever blocks your access, the Terms forbid working around the block - stop
using the skill.

## Commands

### Search job listings

```bash
bun run .agents/skills/ycombinator-jobs-search/cli/src/cli.ts search [flags]
```

Query syntax: `--query` is a client-side filter, not a portal search - every word must appear in the title, company, one-liner or role type; quotes are stripped and there is no phrase matching or OR.

Flags:
- `--role` / `-r <role>` - YC role page, default `software-engineer`. Valid: `software-engineer`, `product-manager`, `designer`, `science`, `sales-manager`, `marketing`, `support`, `operations`, `recruiting-hr`. There is **no engineering-manager page**: EM, staff and principal roles sit under `software-engineer` - use `-q manager` / `-q staff` / `-q principal`. An unknown role exits 1 with `UNKNOWN_ROLE` (YC would otherwise silently show software-engineer jobs).
- `--location` / `-l <slug>` - `india`, `remote`, `berlin`, `san-francisco`, `new-york`, `los-angeles`, `seattle`, `austin`, `chicago`, `boston` (availability varies by role). A slug YC does not recognise exits 1 with `UNKNOWN_LOCATION`.
- `--query` / `-q <words>` - client-side filter (see Query syntax).
- `--jobage <days>` - posted within N days, computed from YC's relative age ("9 days", "about 1 month"); approximate.
- `--page <n>` - only `1`; role pages are not paginated (up to ~50 jobs).
- `--limit` / `-n <n>` - cap results emitted.
- `--format json|table|plain` - default `json`.

### Fetch full job detail

```bash
bun run .agents/skills/ycombinator-jobs-search/cli/src/cli.ts detail <id|url> [--format json|plain]
```

Takes the search `id` (`<company>/<key>`, e.g. `karat-financial/gD75RKp-founding-security-engineer`)
or a `ycombinator.com/companies/<co>/jobs/<key>` URL.

## Usage examples

```bash
# YC software jobs listed for India
bun run .agents/skills/ycombinator-jobs-search/cli/src/cli.ts search -l india --format table

# Remote engineering-manager-type roles
bun run .agents/skills/ycombinator-jobs-search/cli/src/cli.ts search -l remote -q manager --format table

# Staff / principal roles anywhere, last 30 days
bun run .agents/skills/ycombinator-jobs-search/cli/src/cli.ts search -q principal --jobage 30 --format table

# Berlin YC roles
bun run .agents/skills/ycombinator-jobs-search/cli/src/cli.ts search -l berlin --format table

# Full description
bun run .agents/skills/ycombinator-jobs-search/cli/src/cli.ts detail karat-financial/gD75RKp-founding-security-engineer --format plain
```

## Output formats

| Format | Use |
|--------|-----|
| `json` (default) | `{ "meta": { "count", "page" }, "results": [...] }`; each result has `id`, `title`, `company`, `location`, `date` (approximate), `url`, plus `age`, `type`, `roleType`, `salary`, `experience`, `visa`, `companyBatch`, `companyOneLiner`. |
| `table` | Title, company, location, age, visa, salary. |
| `plain` | One block per posting. |

## Notes

- **Check `visa` and `location` on every result.** YC location pages are loose: the `india` page also lists US-remote roles, and many postings say `US citizen/visa only`. For India-based or abroad applications, keep `Will sponsor` or roles whose location names the candidate's country.
- `date` is derived from the relative age and can be off by days for older postings; `detail` returns the exact `datePosted`.
- Role pages show a curated set (newest and most active first), not every YC job.
