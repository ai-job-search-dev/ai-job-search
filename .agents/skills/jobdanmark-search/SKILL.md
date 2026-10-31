---
name: jobdanmark-search
version: 1.0.0
description: >
  Make sure to use this skill whenever the user mentions anything related to Danish
  job listings, job search in Denmark, finding work in Denmark, or job vacancies on
  Jobdanmark — even if they don't explicitly mention jobdanmark.dk. Also invoke this
  skill for questions about specific Danish job categories, municipalities, job types,
  or salaries in a job-search context. Trigger phrases include:
  danish jobs, jobs in denmark, find job denmark, job search denmark, danish job listings,
  jobdanmark, job opslag, find job, jobsøgning, ledige stillinger, stillingsopslag,
  job i Danmark, fuldtidsjob, deltidsjob, studiejob, praktikplads, elev, fleksjob,
  IT job denmark, sygeplejersker job, håndværker job, ingeniør job, pædagog job,
  kontor job, leder job, salg job, hotel job, kirke job, job aarhus, job københavn,
  job odense, job aalborg, job sjælland, job jylland, job fyn, jobkategorier denmark,
  ledige job, ansøgningsfrist, søg job, job opslaget, jobopslag, danish vacancies,
  work in denmark, employment denmark, job denmark, jobs near me denmark,
  apprentice denmark, internship denmark, part-time denmark, full-time denmark.
context: fork
enabled: false  # Danish demo portal - ships opt-in; /setup enables it when your market is Denmark, or set true here yourself
allowed-tools: Bash(bun run .agents/skills/jobdanmark-search/cli/src/cli.ts *)
---

# Jobdanmark Search Skill

Access live Danish job listings from the Jobdanmark.dk public API. No authentication needed.
Covers ~15,000+ active job listings across 10 categories and all Danish municipalities.

## When to use this skill

Invoke this skill when the user wants to:

- Search for job listings in Denmark (by keyword, location, category, or job type)
- Browse available jobs in a specific Danish city, municipality, zip code, or region
- Filter jobs by employment type (full-time, part-time, student job, apprentice, etc.)
- Look up the full details of a specific job posting including description and deadline
- List all job categories with current live job counts
- Resolve a job search term into a job title ID or category ID for precise filtering
- Find location suggestions (municipalities, zip codes, regions) for job search filters

## Commands

### Search job listings

```bash
bun run .agents/skills/jobdanmark-search/cli/src/cli.ts search [flags]
```

Key flags:
- `--text <keyword>` — free-text search, e.g. `elektriker`, `sygeplejerske`, `softwareudvikler`
- `--category <id>` — category ID (see Categories table below)
- `--jobtitle-id <id>` — job title ID from `autocomplete` results
- `--municipality <name>` — e.g. `Odense`, `København`, `Aarhus`
- `--zip <code>` — zip code, e.g. `5000`, `8000`, `2100`
- `--region <name>` — landsdel (`locations` category `region`), e.g. `Sjælland`, `Østjylland`, `Fyn`
- `--administrative-region <name>` — one of the five regions (`locations` category `administrativeRegion`), e.g. `Region Midtjylland`
- `--job-type <types>` — comma-separated: `fuldtid`, `deltid`, `fleksjob`, `elev`, `studiejob`, `praktik`
- `--page <n>` — page number (30 items per page, server-enforced)
- `--limit <n>` — cap total results returned by CLI
- `--format json|table|plain`

> Per-page is fixed at 30 by the API. Use `--page` to paginate.

### Full job detail

```bash
bun run .agents/skills/jobdanmark-search/cli/src/cli.ts detail <slug> [--format json|plain]
```

`slug` is the URL path segment returned as `slug` in `search` results (e.g. `it-chef-soeges-til-rah`).
Returns full structured job data from the job page. The CLI prefers Schema.org JSON-LD when present and falls back to parsing the rendered HTML when Jobdanmark omits JSON-LD.

### List categories with live counts

```bash
bun run .agents/skills/jobdanmark-search/cli/src/cli.ts categories [--format json|table|plain]
```

Returns all 10 job categories with current live job counts. Useful for giving the user an overview of the job market.

### Autocomplete job titles and categories

```bash
bun run .agents/skills/jobdanmark-search/cli/src/cli.ts autocomplete --query "<text>" [--limit <n>]
```

Use this to resolve search terms into precise job title IDs (`--jobtitle-id`) or category IDs (`--category`) for `search`. The `value` field in results is the ID to pass to `search`.

### Suggest locations

```bash
bun run .agents/skills/jobdanmark-search/cli/src/cli.ts locations --query "<text>" [--limit <n>]
```

Returns matching municipalities, zip codes, and regions. Pass an item's `value` to the `search` flag named by its `category`: `municipality` → `--municipality`, `zip` → `--zip`, `region` → `--region`, `administrativeRegion` → `--administrative-region`. A value under the wrong flag matches no jobs.

---

## Categories

| ID | Danish | English |
|----|--------|---------|
| 227972 | Pædagogik, Uddannelse og Forskning | Education, Research |
| 227973 | Håndværk, Industri, Transport og Landbrug | Crafts, Industry, Transport, Agriculture |
| 227974 | Salg, Kommunikation, Marketing, og Design | Sales, Communication, Marketing, Design |
| 227975 | Pleje, Social og Sundhed | Care, Social, Health |
| 227976 | Hotel, Service, Restauration og Sikkerhed | Hotel, Service, Restaurant, Security |
| 227977 | Kontor, Finans og Økonomi | Office, Finance, Economy |
| 227978 | IT, Ingeniør og Energi | IT, Engineering, Energy |
| 227979 | Ledelse, HR og projektstyring | Management, HR, Project Management |
| 543415 | Kirke, Kultur og Underholdning | Church, Culture, Entertainment |
| 227980 | Øvrige job | Other jobs |

---

## How to use effectively

**Resolve locations first.** Use `locations` to find the correct municipality name or zip code before passing them to `search`:

```bash
bun run .agents/skills/jobdanmark-search/cli/src/cli.ts locations --query "Aarhus" --format plain
```

**Resolve job titles for precision.** Use `autocomplete` to get the exact job title ID when the user wants a specific role:

```bash
bun run .agents/skills/jobdanmark-search/cli/src/cli.ts autocomplete --query "sygeplejerske" --format plain
```

**Natural workflow: `search` → `detail`.**
1. Use `search` to get a list of matching jobs with their `slug`.
2. Call `detail <slug>` to get the full job posting with description, deadline, and organization details.

**Use `--format table` for comparisons**, `--format json` for data processing, and `--format plain` for single-record detail views.

**Pagination**: The API returns 30 items per page (server-enforced). Use `--page` to navigate through results. Use `--limit` to cap CLI output regardless of page size.

**Combine filters** for precise results — e.g. `--text` + `--job-type` + `--municipality` all work together in a single search request.

---

## Usage examples

### IT jobs in Copenhagen

```bash
bun run .agents/skills/jobdanmark-search/cli/src/cli.ts search \
  --category 227978 \
  --municipality "København" \
  --job-type fuldtid \
  --format table
```

### Nursing jobs anywhere in Denmark

```bash
bun run .agents/skills/jobdanmark-search/cli/src/cli.ts search \
  --text "sygeplejerske" \
  --category 227975 \
  --format table
```

### Student jobs in Aarhus

```bash
bun run .agents/skills/jobdanmark-search/cli/src/cli.ts search \
  --municipality "Aarhus" \
  --job-type studiejob \
  --format table
```

### What job categories are most active right now?

```bash
bun run .agents/skills/jobdanmark-search/cli/src/cli.ts categories --format table
```

### Full details for a specific job posting

```bash
bun run .agents/skills/jobdanmark-search/cli/src/cli.ts detail it-chef-soeges-til-rah --format plain
```

### Find jobs in zip code 8000

```bash
bun run .agents/skills/jobdanmark-search/cli/src/cli.ts search --zip 8000 --format table
```

### What electrician jobs are available?

```bash
bun run .agents/skills/jobdanmark-search/cli/src/cli.ts search \
  --text "elektriker" \
  --category 227973 \
  --job-type "fuldtid,deltid" \
  --format table
```

### Apprentice positions in all of Denmark

```bash
bun run .agents/skills/jobdanmark-search/cli/src/cli.ts search \
  --job-type elev \
  --page 1 \
  --format table
```

---

## Output formats

| Format | Best for |
|--------|----------|
| `json` | Default — programmatic use, data processing, passing IDs between commands |
| `table` | Quick human-readable overviews and comparisons |
| `plain` | Single-record detail views (`detail`) |

All errors are written to **stderr** as `{ "error": "...", "code": "..." }` and the process exits with code `1`.

---

## Notes

- All data is from the public Jobdanmark.dk API — no credentials required.
- Pagination is 1-indexed (`--page 1` is the first page). 30 items per page, server-enforced.
- The `detail` command fetches the HTML job page and parses embedded JSON-LD when available, with a rendered-HTML fallback for pages that omit structured data. It does not use a separate JSON API.
- `slug` in search results is extracted from the API's relative `url` field (the path after `/job/`); it is also emitted as `id`, the portal-contract name every search CLI shares.
- `applicationDeadline` in search results can be `null` (no deadline set).
- Job type values for filters: `fuldtid`, `deltid`, `fleksjob`, `elev`, `studiejob`, `praktik`.
