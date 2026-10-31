---
name: jobindex-search
version: 1.0.0
description: >
  Make sure to use this skill whenever the user wants to search for jobs in Denmark,
  find Danish job listings, look up a specific job posting, or asks anything about
  the Danish job market — even if they don't mention jobindex.dk explicitly. Invoke
  this skill for questions about open positions, job vacancies, hiring in Denmark,
  job opportunities in Danish cities or sectors, or when the user wants to find work
  in Denmark. Also trigger for phrases like "find me a job", "are there any jobs for
  X in Copenhagen", or "what jobs are available in Aarhus" when the context is Denmark.
  Trigger phrases include: jobindex, jobsøgning, job i Danmark, ledige stillinger,
  job opslag, find job, stillingopslag, jobannonce, job vacancy denmark, danish jobs,
  jobs in denmark, job search denmark, work in denmark, find work denmark, IT jobs
  denmark, engineer jobs denmark, developer jobs copenhagen, marketing jobs aarhus,
  jobs aarhus, jobs copenhagen, jobs odense, jobs aalborg, job openings denmark,
  hiring denmark, job listings denmark, python jobs denmark, grafisk designer job,
  data engineer job, softwareudvikler job, full stack developer job danmark.
context: fork
enabled: false  # Danish demo portal - ships opt-in; /setup enables it when your market is Denmark, or set true here yourself
allowed-tools: Bash(bun run .agents/skills/jobindex-search/cli/src/cli.ts *)
---

# Jobindex Search Skill

Search live Danish job listings from Jobindex.dk. No authentication needed.
Covers thousands of job postings across all sectors, updated in real time.

## When to use this skill

Invoke this skill when the user wants to:

- Search for job openings in Denmark by keyword, job title, or technology
- Find jobs in a specific Danish city (use keyword with city name, e.g. `python aarhus`)
- Filter jobs by recency (posted today, last 7 days, last 30 days)
- Get the full description of a specific job listing
- Explore the Danish job market for a given profession or skill set

## Commands

### Search job listings

```bash
bun run .agents/skills/jobindex-search/cli/src/cli.ts search --query "<text>" [flags]
```

Query syntax: phrase quoting honoured; no OR.

Key flags:
- `--query <text>` / `-q <text>` — keyword search (job title, skill, company, city). **Required.** Searches without a query exit with `MISSING_REQUIRED`.
- `--jobage <days>` — filter by posting age: `1` (today), `7`, `14`, `30`, or `9999` (all, default)
- `--sort <order>` — `score` (relevance, default) or `date` (newest first)
- `--page <n>` — page number (1-indexed, 20 results per page, fixed)
- `--limit <n>` — cap total results the CLI outputs (client-side)
- `--format json|table|plain`

> **Area note**: The Jobindex API does not support area filtering via params. To find jobs in a specific city, include the city in `--query` (e.g. `--query "data engineer københavn"` or `--query "python aarhus"`).

### Fetch full job detail

```bash
bun run .agents/skills/jobindex-search/cli/src/cli.ts detail <id> [--format json|plain]
```

`id` is the job ID from `search` results (e.g. `h1647303`). You may also pass the full Jobindex URL. Returns the full job description, deadline, employment type, hours, and apply link.

---

## How to use effectively

**Always start with `search`.** Pass the job title, skill, or profession as `--query`. Combine with a city name in the query to narrow by location (e.g. `--query "frontend developer odense"`).

**Use `--jobage 7` or `--jobage 1` for fresh listings.** Without it, results include all historical postings.

**Use `--sort date` to see the most recently posted jobs first.** Default `score` sorts by relevance.

**Natural workflow: `search` → `detail`.**
1. Use `search` to find matching jobs and their `id` values.
2. Call `detail <id>` to get the full description, deadline, and apply link.

**Use `--format table` for quick scanning**, `--format json` for data processing, and `--format plain` for reading a single job's full details.

**Pagination**: The API always returns 20 results per page. Use `--page` to navigate pages. Use `--limit` to cap results across one page fetch.

---

## Usage examples

### Find Python jobs posted in the last 7 days

```bash
bun run .agents/skills/jobindex-search/cli/src/cli.ts search \
  --query python \
  --jobage 7 \
  --sort date \
  --format table
```

### Data engineer jobs in Copenhagen

```bash
bun run .agents/skills/jobindex-search/cli/src/cli.ts search \
  --query "data engineer københavn" \
  --sort score \
  --format table
```

### Graphic designer jobs — all time, by relevance

```bash
bun run .agents/skills/jobindex-search/cli/src/cli.ts search \
  --query "grafisk designer" \
  --limit 10 \
  --format table
```

### Full-stack developer jobs, page 2

```bash
bun run .agents/skills/jobindex-search/cli/src/cli.ts search \
  --query "full stack developer" \
  --page 2 \
  --format json
```

### Jobs posted today for a keyword

```bash
bun run .agents/skills/jobindex-search/cli/src/cli.ts search \
  --query python \
  --jobage 1 \
  --sort date \
  --limit 20 \
  --format table
```

### Get full details for a specific job

```bash
bun run .agents/skills/jobindex-search/cli/src/cli.ts detail h1647303 --format plain
```

### Marketing jobs in Aarhus

```bash
bun run .agents/skills/jobindex-search/cli/src/cli.ts search \
  --query "marketing aarhus" \
  --jobage 30 \
  --sort date \
  --format table
```

---

## Output formats

| Format | Best for |
|--------|----------|
| `json` | Default — programmatic use, data processing, passing IDs to `detail` |
| `table` | Quick human-readable overview and scanning |
| `plain` | Reading a single job's full detail (`detail` command) |

All errors are written to **stderr** as `{ "error": "...", "code": "..." }` and the process exits with code `1`.

---

## Notes

- All data is from the public `jobindex.dk` API — no credentials required.
- Page size is fixed at 20 results per page (Jobindex API limitation).
- Area/region filtering via API params does not work — include city names in `--query` instead.
- `--jobage 9999` is the default and includes all postings regardless of age.
- Total count in `meta.total` comes from the numeric `searchResponse.hitcount` embedded in the search page.
- Job IDs are string-prefixed (e.g. `h1647303`) — pass them as-is to `detail`.
