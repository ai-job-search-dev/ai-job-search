---
name: jobbank-search
version: 1.0.0
description: >
  Make sure to use this skill whenever the user mentions anything related to job
  searching on Akademikernes Jobbank, jobbank.dk, or looking for academic or
  highly educated positions in Denmark — even if they don't mention jobbank.dk
  explicitly. Also invoke this skill for questions about Danish job listings,
  graduate trainee positions, Ph.d. jobs, or finding work in specific industries
  or regions in Denmark. Trigger phrases include:
  jobbank, akademikernes jobbank, jobs denmark, academic jobs denmark, find job
  denmark, highly educated jobs, graduate job denmark, trainee position denmark,
  ph.d. position denmark, postdoc denmark, studiejob, fuldtidsjob, deltidsjob,
  vikariat, freelance job, praktikplads, job søgning, jobsøgning, søg job,
  ledige stillinger, nye jobs, it jobs denmark, engineering jobs denmark,
  marketing jobs denmark, finance jobs denmark, healthcare jobs denmark,
  remote job denmark, fjernarbejde, job københavn, job aarhus, job odense,
  nyuddannede job, job til nyuddannede, international job denmark,
  jobbank søgning, find stilling, data scientist job, software developer job,
  projektleder stilling, konsulent job, data analyse job.
context: fork
enabled: false  # Danish demo portal - ships opt-in; /setup enables it when your market is Denmark, or set true here yourself
allowed-tools: Bash(bun run .agents/skills/jobbank-search/cli/src/cli.ts *)
---

# Jobbank Search Skill

Search live Danish job listings from [Akademikernes Jobbank](https://jobbank.dk) — Denmark's primary job portal for highly educated candidates. Uses the RSS feed for search (up to 100 results) and JSON-LD parsing for detailed job information. Jobbank may block automated requests with Cloudflare bot protection; if that happens, report the portal as unavailable and use WebSearch fallback instead of retrying.

## When to use this skill

Invoke this skill when the user wants to:

- Search for jobs, positions, or career opportunities in Denmark
- Find academic, graduate, trainee, Ph.d., or postdoc positions
- Look for jobs by keyword, industry, location, education background, or work function
- Find remote or hybrid positions in Denmark
- Get full details for a specific job posting on jobbank.dk
- Check what positions are available at a specific company on jobbank.dk
- Browse jobs suitable for new graduates or people with international backgrounds

## Commands

### Search jobs

```bash
bun run .agents/skills/jobbank-search/cli/src/cli.ts search [flags]
```

Query syntax: phrase quoting honoured; no OR.

Key flags:
- `--key <text>` — keyword search (title, company, keyword)
- `--exclude <text>` — exclude keywords from results
- `--type <code>` — job type: `3`=Fuldtidsjob, `6`=Graduate/trainee, `13`=Deltidsjob, `8`=Vikariat, `12`=Ph.d. & Postdoc, `11`=Freelance, `9`=Praktikplads, `4`=Studiejob (repeatable)
- `--location <code>` — region: `2`=Storkøbenhavn, `8`=Østjylland (Aarhus), `7`=Midtjylland, `6`=Nordjylland, `13`=Fyn (repeatable)
- `--work-area <code>` — function: `31`=IT-Software, `43`=Data & Analyse, `26`=Ledelse, `29`=Marketing (repeatable)
- `--industry <code>` — sector: `10331`=IT & Tele, `10442`=Forskning & Uddannelse, `10358`=Finans (repeatable)
- `--education <code>` — education field: `24`=IT, `21`=Økonomi & Revision, `34`=Samfundsvidenskab (repeatable)
- `--remote <value>` — `helt` (fully remote) or `delvist` (partially remote)
- `--suitable-for <code>` — `2`=Nyuddannede, `4`=International baggrund, `5`=Erfarne
- `--company <id>` — filter by company ID
- `--since <YYYY-MM-DD>` — jobs posted on or after this date
- `--limit <n>` — cap results returned by CLI
- `--format json|table|plain`

> **RSS limitation:** The RSS feed returns max 100 items per request. No pagination is available via RSS. `meta.total` shows the true count; `results` is capped at 100.

### Full job detail

```bash
bun run .agents/skills/jobbank-search/cli/src/cli.ts detail <id> [--format json|plain]
```

`id` is the numeric job ID from `search` results. Fetches the job page and parses the embedded Schema.org `JobPosting` JSON-LD for structured data.

---

## How to use effectively

**Start with `search`, then use `detail` for full description.**

1. Use `search` with `--key` and/or filters to find matching jobs with IDs
2. Call `detail <id>` to get the full HTML job description, exact deadline, and company details

**Use repeatable flags for multi-value filters.** Most filter flags can be repeated to match any of the values:

```bash
# IT or Finance industry, Copenhagen or Aarhus
bun run .agents/skills/jobbank-search/cli/src/cli.ts search \
  --industry 10331 --industry 10358 \
  --location 2 --location 8
```

**Filter codes are documented in the README** at `skills/jobbank-search/cli/README.md`.

---

## Usage examples

### Find data scientist jobs in Copenhagen

```bash
bun run .agents/skills/jobbank-search/cli/src/cli.ts search \
  --key "data scientist" \
  --location 2 \
  --format table
```

### Graduate trainee positions for new graduates

```bash
bun run .agents/skills/jobbank-search/cli/src/cli.ts search \
  --type 6 \
  --suitable-for 2 \
  --format table
```

### Remote IT software jobs

```bash
bun run .agents/skills/jobbank-search/cli/src/cli.ts search \
  --work-area 31 \
  --remote helt \
  --format table
```

### Ph.d. and postdoc positions in research

```bash
bun run .agents/skills/jobbank-search/cli/src/cli.ts search \
  --type 12 \
  --industry 10442 \
  --format table
```

### Recent full-time jobs posted since March 1

```bash
bun run .agents/skills/jobbank-search/cli/src/cli.ts search \
  --type 3 \
  --since 2026-03-01 \
  --format table
```

### Full details for a specific job

```bash
bun run .agents/skills/jobbank-search/cli/src/cli.ts detail 1234567 --format plain
```

### IT jobs in Aarhus or Copenhagen

```bash
bun run .agents/skills/jobbank-search/cli/src/cli.ts search \
  --key developer \
  --location 2 --location 8 \
  --work-area 31 \
  --format table
```

---

## Output formats

| Format | Best for |
|--------|----------|
| `json` | Default — programmatic use, passing IDs to `detail` |
| `table` | Quick human-readable list of results |
| `plain` | Single-job detail views (`detail` command) |

All errors are written to **stderr** as `{ "error": "...", "code": "..." }` and the process exits with code `1`.

---

## Notes

- Data is from the public jobbank.dk RSS feed and HTML pages. Jobbank may still block automated CLI requests with Cloudflare bot protection; treat that as a portal availability failure and fall back to WebSearch.
- RSS feed returns max 100 results per query. For higher counts, `meta.total` shows the true total.
- The `detail` command fetches a full job page and extracts the JSON-LD structured data block.
- `location` values are region codes (e.g. `2` = Storkøbenhavn), not city names.
- All filter codes are documented in `skills/jobbank-search/cli/README.md`.
