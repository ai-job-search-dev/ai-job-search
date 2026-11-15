---
name: apify-jobs-search
version: 1.0.0
description: >
  Search job boards that have no portal CLI here, such as Indeed India, by
  running Apify Store scraper actors on the Apify free plan ($5 a month) under a shared
  monthly budget and rate limiter. Boards and their actors are configured in actors.json.
  Trigger phrases: Apify, Indeed jobs, India job boards, search Indeed.
context: fork
enabled: true  # set to false to keep this portal installed but have /scrape skip it
allowed-tools: Bash(bun run .agents/skills/apify-jobs-search/cli/src/cli.ts *)
---

# Apify Jobs Search Skill

Starts one Apify actor run per search term (`POST /v2/acts/<user>~<actor>/runs`), waits
for it to finish, and maps the actor's dataset to the standard portal output. Each run
is capped three ways before it starts:
- `--max-charge` (default $0.25), passed as `maxTotalChargeUsd` to pay-per-event actors
- `maxItems` (`-n`)
- `--timeout` and `--memory`, which bound compute cost

The run is charged against the ledger shared with `firecrawl-fetch`. After the run, the
ledger records its real `usageTotalUsd` and syncs with `GET /v2/users/me/limits`. One
run at a time by default.

**Setup.** Put `APIFY_API_TOKEN=apify_api_...` in the repo-root `.env` (gitignored; Bun
loads it when you run from the repo root). Never pass the token as a flag.

## ⚠️ Terms and verification

- **Third-party scrapers.** Store actors are written by independent developers and scrape
  the board on Apify's infrastructure. The board's own terms (for example Indeed's) still apply
  to you: keep volume low and use it for your own job search only. LinkedIn is
  deliberately not configured; the `linkedin-search` CLI covers it.
- **Field names drift.** Input and output names differ per actor and change over
  time. `actors.json` lists candidate output fields per board, and a `_verified` note
  records what was last confirmed. `indeed` was confirmed by a 3-listing run on
  2026-11-16, at $0.006 per listing on the free tier. Before relying on any other board,
  check the actor's Input and Output tabs on apify.com, then run one search with `-n 3`.
  If the actor returns items whose fields don't match the configured names, the CLI exits
  1 with `PARSE_FAILED` rather than reporting zero jobs. An item missing a title, company
  or URL is counted in `meta.invalid_items`, never invented.

## Commands

### Search a board

```bash
bun run .agents/skills/apify-jobs-search/cli/src/cli.ts search -b <board> -q "<term>" [flags]
```

Query syntax: passed verbatim to the actor's keyword input; each actor interprets it like the board's own search box (quotes and OR follow that board, not Google). One term per call. Not verified against a portal total.

Flags:
- `--board` / `-b <name>` - **required.** List boards with `boards`. Shipped: `indeed` (enabled) and `naukri` (**disabled**: naukri.com's robots.txt disallows Claude user agents, and this workspace does not route around a site that said no; enable it in `actors.json` only as your own informed choice).
- `--query` / `-q <term>` - **required.**
- `--location` / `-l <place>` - the actor's location input (`Bengaluru`, `Hyderabad`, `Remote`, ...).
- `--country <CC>` - for actors that take a country (Indeed; default `IN`).
- `--limit` / `-n <1-50>` - default 20.
- `--jobage <days>` - client-side filter on the posting date. Undated items are kept, and `30+ days ago` stays undated.
- `--max-charge <usd>` - default 0.25; `--timeout <secs>` default 180; `--memory <MB>` default 1024.
- `--run-id <id>`, `--run-budget <usd>`, `--allow-reserve` - shared budget controls (see `firecrawl-fetch` SKILL.md).
- `--format json|table|plain` - default `json`.

### Detail (free)

```bash
bun run .agents/skills/apify-jobs-search/cli/src/cli.ts detail <url> [--format json|plain]
```

Returns the description the last search already returned. It reads a local cache in the
system temp directory and makes no request. `NOT_CACHED` or `NO_DESCRIPTION` means:
fetch the posting with WebFetch, then `firecrawl-fetch detail`.

### Boards and budget (free)

```bash
bun run .agents/skills/apify-jobs-search/cli/src/cli.ts boards
bun run .agents/skills/apify-jobs-search/cli/src/cli.ts budget [--sync] [--format json|plain]
```

Settings: `APIFY_MONTHLY_USD` (5), `APIFY_CYCLE_DAY` (1), `APIFY_RPM` (4),
`APIFY_CONCURRENCY` (1), `APIFY_ACTORS_FILE`, and the shared `SCRAPE_BUDGET_RESERVE`,
`SCRAPE_RUNS_PER_MONTH` and `SCRAPE_BUDGET_FILE`.

Error codes match `firecrawl-fetch`, plus `BAD_BOARD`, `BOARD_DISABLED`, `ACTOR_NOT_FOUND`,
`ACTOR_TIMEOUT` (aborted; the ledger is charged the full cap) and `ACTOR_FAILED`.
Budget and rate errors are never evidence that the board is broken.

## Usage examples

```bash
bun run .agents/skills/apify-jobs-search/cli/src/cli.ts budget --sync --format plain
bun run .agents/skills/apify-jobs-search/cli/src/cli.ts search -b indeed -q "engineering manager" -l Hyderabad -n 3 --format table
bun run .agents/skills/apify-jobs-search/cli/src/cli.ts search -b indeed -q "staff backend engineer" -l Bengaluru --jobage 14 -n 20 --run-id scrape-2026-11-15 --run-budget 0.6
```

## Output formats

| Format | Use |
|--------|-----|
| `json` (default) | `{ "meta": { "count", "page", "board", "run_id", "run_status", "cost_usd", "invalid_items" }, "results": [...] }`; each result has `id`, `title`, `company`, `location`, `date`, `url`, `salary`, `jobType` (the board's label, verbatim), `applyUrl` (the employer's own posting, which may pass through a tracking redirect, so prefer it for `/apply` research), `isExpired` (`true` only when the board marks the listing closed; treat it like `linkedin-search`'s `isActive: false` and store the entry as `expired`), `board`, `has_description`. |
| `table` | Title, company, location, date, URL, plus the run cost. |
| `plain` | One block per posting. |
