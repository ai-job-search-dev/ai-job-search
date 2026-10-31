---
name: jobnet-search
version: 1.0.0
description: >
  Make sure to use this skill whenever the user mentions anything related to Danish
  job searching, job listings, job vacancies, employment opportunities in Denmark, or
  the Danish government job portal — even if they don't mention jobnet.dk explicitly.
  Also invoke this skill for questions about specific job titles, occupations, employers,
  or regions in a Danish employment context. This skill covers the official Danish
  public job portal operated by STAR (Styrelsen for Arbejdsmarked og Rekruttering).
  Trigger phrases include: danish jobs, danish job search, jobnet, jobnet.dk, find job
  denmark, danish employment, job i danmark, job på jobnet, offentlige job, stillinger
  i det offentlige, public sector jobs denmark, government jobs denmark, STAR jobs,
  job ledige stillinger, ledig stilling, søg job, job opslag, job vacancy denmark,
  stillingopslag, jobopslag, sygepleje job, ingeniør job, lærer job, pædagog job,
  it-job denmark, jobs in copenhagen, jobs in aarhus, jobs in odense, deltidsjob,
  fuldtidsjob, fastansættelse, tidsbegrænset ansættelse, fleksjob, sygeplejerske job,
  social worker job denmark, occupation search denmark, esco occupation, job deadline,
  ansøgningsfrist, søg efter job, full time job denmark, part time job denmark.
context: fork
enabled: false  # Danish demo portal - ships opt-in; /setup enables it when your market is Denmark, or set true here yourself
allowed-tools: Bash(bun run .agents/skills/jobnet-search/cli/src/cli.ts *)
---

# Jobnet-Search Skill

Access live Danish job listings from the Jobnet.dk public API. No authentication needed.
Jobnet is operated by STAR (Styrelsen for Arbejdsmarked og Rekruttering) and is Denmark's
official government job portal — covering public sector positions as well as many private
sector listings. Approximately 21,000+ active jobs at any time.

## When to use this skill

Invoke this skill when the user wants to:

- Search for job openings in Denmark by keyword, title, or employer
- Filter jobs by region, work hours (full/part time), or employment duration (permanent/temporary)
- Find jobs near a specific postal code within a given radius
- Get full details for a specific job ad including description, contact persons, and application URL
- Discover occupation types and ESCO categories for more precise job filtering
- Explore autocomplete suggestions for Danish job titles or keywords
- Find jobs in the public sector, healthcare, IT, education, or any other Danish industry

## Commands

### Search for job ads

```bash
bun run .agents/skills/jobnet-search/cli/src/cli.ts search [flags]
```

Key flags:
- `--search-string <text>` — keyword search, e.g. `sygeplejerske`, `ingeniør`, `pædagog`
- `--region <region>` — `HovedstadenOgBornholm`, `Midtjylland`, `Syddanmark`, `OevrigeSjaelland`, `Nordjylland`
- `--postal-code <code>` — postal code for radius-based search, e.g. `2100`
- `--radius <km>` — radius in km from postal code (default: `50`)
- `--work-hours <type>` — `FullTime` or `PartTime`
- `--duration <type>` — `Permanent` or `Temporary`
- `--job-type <type>` — `Ordinaert`, `Efterloenner`, `Foertidspension`
- `--occupation-area <id>` — occupation area identifier, e.g. `10000`
- `--occupation-group <id>` — occupation group identifier, e.g. `10060`
- `--occupation <uuid>` — one ESCO occupation: the `conceptUriDa` printed by `occupations`, or its trailing UUID
- `--order <type>` — `PublicationDate` (default), `BestMatch`, `ApplicationDate`
- `--page / --per-page / --limit`
- `--format json|table|plain`

### Full job ad detail

```bash
bun run .agents/skills/jobnet-search/cli/src/cli.ts detail <jobAdId> [--format json|plain]
```

`jobAdId` is the UUID from `search` results (the `jobAdId` field, also emitted as `id`, the portal-contract name every search CLI shares). Returns the complete job
description, contact persons, application deadline, employer details, and direct application URL.

### Search occupation types

```bash
bun run .agents/skills/jobnet-search/cli/src/cli.ts occupations --search-string <text> [--per-page <n>]
```

Use this to discover ESCO occupation identifiers before passing them to `search` with
`--occupation`. The numeric `--occupation-area` / `--occupation-group` codes are a different
taxonomy: an occupation UUID passed there matches no ads.

### Typeahead suggestions

```bash
bun run .agents/skills/jobnet-search/cli/src/cli.ts suggestions --query <text> [--limit <n>]
```

Returns Danish job title autocomplete strings. Useful for exploring valid Danish
job titles before constructing a `search` query.

---

## How to use effectively

**Discover occupations first.** Use `occupations` or `suggestions` to find the right Danish
term or ESCO identifier before running a `search`:

```bash
bun run .agents/skills/jobnet-search/cli/src/cli.ts suggestions --query "syge"
bun run .agents/skills/jobnet-search/cli/src/cli.ts occupations --search-string "sygeplejerske"
bun run .agents/skills/jobnet-search/cli/src/cli.ts search --occupation http://data.star.dk/esco/occupation/56f5d45c-ce48-43ac-9720-34831a1c8302
```

**Natural workflow: `search` → `detail`.**
1. Use `search` to get a list of matching jobs with their `jobAdId`.
2. Call `detail <jobAdId>` to get the full job description, contact persons, and direct application link.

**Use `--format table` for comparisons**, `--format json` for data processing, and
`--format plain` for single-record detail views (strips HTML from job body).

**Pagination**: `--per-page` controls server-side results per page. `--limit` caps what the
CLI outputs. Use `--page` + `--per-page` to iterate through large result sets.

**Geographic search modes:**
- Use `--region` for broad regional filtering (e.g. all of Midtjylland)
- Use `--postal-code` + `--radius` for jobs near a specific location
- Do not combine `--region` and `--postal-code` in the same query

**Order matters for intent:**
- `PublicationDate` — newest postings first (default, good for "what's new")
- `BestMatch` — relevance score (best when `--search-string` is provided)
- `ApplicationDate` — earliest deadline first (good for urgent applications)

---

## Usage examples

### Jobs in Copenhagen area

```bash
bun run .agents/skills/jobnet-search/cli/src/cli.ts search \
  --region HovedstadenOgBornholm \
  --per-page 10 \
  --format table
```

### Nurse jobs nationwide

```bash
bun run .agents/skills/jobnet-search/cli/src/cli.ts search \
  --search-string "sygeplejerske" \
  --work-hours FullTime \
  --duration Permanent \
  --order BestMatch \
  --per-page 10 \
  --format table
```

### IT jobs near Aarhus within 30km

```bash
bun run .agents/skills/jobnet-search/cli/src/cli.ts search \
  --search-string "udvikler" \
  --postal-code 8000 \
  --radius 30 \
  --work-hours FullTime \
  --format table
```

### Full details of a job ad

```bash
bun run .agents/skills/jobnet-search/cli/src/cli.ts detail 9ef43bce-d82b-4ea1-a098-7ff6520f99be --format plain
```

### Jobs sorted by application deadline (urgent first)

```bash
bun run .agents/skills/jobnet-search/cli/src/cli.ts search \
  --search-string "pædagog" \
  --region OevrigeSjaelland \
  --order ApplicationDate \
  --per-page 10
```

### Discover occupation terms

```bash
bun run .agents/skills/jobnet-search/cli/src/cli.ts suggestions --query "ingeniør" --limit 5
bun run .agents/skills/jobnet-search/cli/src/cli.ts occupations --search-string "lærer" --per-page 5
```

---

## Output formats

| Format | Best for |
|--------|----------|
| `json` | Default — programmatic use, data processing, passing IDs between commands |
| `table` | Quick human-readable overviews and comparisons |
| `plain` | Single-record detail views (`detail`), strips HTML from job descriptions |

All errors are written to **stderr** as `{ "error": "...", "code": "..." }` and the process exits with code `1`.

---

## Notes

- All data is from the public `jobnet.dk/bff` REST API — no credentials required.
- The API requires the `x-csrf: 1` header; the CLI adds this automatically.
- Pagination is 1-indexed (`--page 1` is the first page).
- `search` results omit the HTML job description — use `detail` to get it.
- `detail --format plain` strips HTML tags for readable text output.
- Job ad detail pages on jobnet.dk: `https://jobnet.dk/find-job/{jobAdId}` (non-external ads; external ads use employer `jobAdUrl`).
- `suggestions` is tuned for Danish job titles — English terms may return empty results.
