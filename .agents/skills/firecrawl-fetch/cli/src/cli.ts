#!/usr/bin/env bun
// firecrawl-fetch: Firecrawl free-tier client for /scrape and /apply.
//   search  - find single postings on employer ATS boards (Greenhouse, Lever, Ashby, ...)
//   detail  - fetch one posting page as markdown when WebFetch and fetch_page.py fail
//   budget  - show (and optionally sync) the monthly credit ledger
// Every paid call goes through src/budget.ts: monthly allowance, reserve,
// per-run cap, requests/minute and concurrency, shared across processes.

import { runBudget, runDetail, runSearch } from "./commands.js"
import { fail, failFrom } from "./helpers.js"

type Val = string | boolean | string[]
interface Flags {
  _: string[]
  [k: string]: Val
}

const BOOLEAN = new Set(["allow-reserve", "sync", "help", "h"])
const REPEATABLE = new Set(["site"])

export function parseFlags(argv: string[]): Flags {
  const flags: Flags = { _: [] }
  const alias: Record<string, string> = { q: "query", l: "location", n: "limit" }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a.startsWith("-") && a.length > 1) {
      const key = alias[a.replace(/^-+/, "")] ?? a.replace(/^-+/, "")
      const next = argv[i + 1]
      if (BOOLEAN.has(key) || next === undefined || next.startsWith("-")) {
        flags[key] = true
      } else {
        if (REPEATABLE.has(key)) flags[key] = [...((flags[key] as string[]) ?? []), next]
        else flags[key] = next
        i++
      }
    } else {
      flags._.push(a)
    }
  }
  return flags
}

const HELP = `firecrawl-fetch - Firecrawl free-tier search and page fetch, budgeted

USAGE
  bun run src/cli.ts search -q "<term>" [-l <place>] [--jobage <days>] [-n <1-20>] [--site <domain>]...
  bun run src/cli.ts detail <url> [--max-chars N] [--format json|plain]
  bun run src/cli.ts budget [--sync] [--format json|plain]

BUDGET FLAGS (search, detail)
  --run-id <id>         Group calls into one /scrape run.
  --run-budget <n>      Credits this run may spend in total (see 'budget' suggested_run_budget).
  --allow-reserve       Allow spending the reserved share (single-posting fetches from /apply).

SEARCH    Costs 2 credits per 10 results. Default -n 10.
DETAIL    Costs 1 credit. robots.txt is checked first (tools/robots_check.py).

Env: FIRECRAWL_API_TOKEN (required for search/detail/--sync), FIRECRAWL_MONTHLY_CREDITS=1000,
     FIRECRAWL_CYCLE_DAY=1, FIRECRAWL_RPM=8, FIRECRAWL_CONCURRENCY=2,
     SCRAPE_BUDGET_RESERVE=0.2, SCRAPE_RUNS_PER_MONTH=8, SCRAPE_BUDGET_FILE.
`

const KNOWN: Record<string, Set<string>> = {
  search: new Set(["query", "location", "jobage", "limit", "site", "format", "run-id", "run-budget", "allow-reserve", "help", "h"]),
  detail: new Set(["max-chars", "format", "run-id", "run-budget", "allow-reserve", "help", "h"]),
  budget: new Set(["sync", "format", "help", "h"]),
}

function posInt(name: string, raw: Val | undefined, min: number, max: number, dflt: number): number | string {
  if (raw === undefined) return dflt
  const v = typeof raw === "string" ? Number(raw.trim()) : NaN
  if (!Number.isInteger(v) || v < min || v > max) return `--${name} must be a whole number from ${min} to ${max}, got "${raw}"`
  return v
}

function fmt(raw: Val | undefined, allowed: string[]): string | null {
  const f = raw === undefined ? "json" : String(raw)
  return allowed.includes(f) ? f : null
}

function paid(flags: Flags): { runId?: string; runBudget?: number; allowReserve: boolean } | string {
  const out: { runId?: string; runBudget?: number; allowReserve: boolean } = { allowReserve: flags["allow-reserve"] === true }
  if (flags["run-id"] !== undefined) {
    if (typeof flags["run-id"] !== "string" || !/^[\w.:-]{1,64}$/.test(flags["run-id"])) return "--run-id must be 1-64 characters of [A-Za-z0-9_.:-]"
    out.runId = flags["run-id"]
  }
  if (flags["run-budget"] !== undefined) {
    const v = typeof flags["run-budget"] === "string" ? Number(flags["run-budget"]) : NaN
    if (!Number.isFinite(v) || v < 0) return `--run-budget must be a non-negative number, got "${flags["run-budget"]}"`
    if (!out.runId) return "--run-budget needs --run-id"
    out.runBudget = v
  }
  return out
}

async function main(): Promise<number> {
  const flags = parseFlags(process.argv.slice(2))
  const cmd = flags._[0]
  if (!cmd || flags.help || flags.h) {
    process.stdout.write(HELP)
    return cmd ? 0 : 1
  }
  const known = KNOWN[cmd]
  if (!known) return fail(`Unknown command "${cmd}"`, "BAD_CMD")
  for (const key of Object.keys(flags)) {
    if (key !== "_" && !known.has(key)) return fail(`unknown flag --${key} for '${cmd}'; see --help`, "UNKNOWN_FLAG")
  }

  if (cmd === "budget") {
    const f = fmt(flags.format, ["json", "plain"])
    if (!f) return fail("--format must be json|plain", "BAD_ARG")
    return runBudget({ sync: flags.sync === true, format: f as "json" | "plain" })
  }

  const p = paid(flags)
  if (typeof p === "string") return fail(p, "BAD_ARG")

  if (cmd === "search") {
    if (typeof flags.query !== "string" || !flags.query.trim()) return fail("--query/-q is required", "NO_QUERY")
    if (flags.location !== undefined && (typeof flags.location !== "string" || !flags.location.trim())) return fail("--location needs a value", "BAD_ARG")
    const limit = posInt("limit", flags.limit, 1, 20, 10)
    if (typeof limit === "string") return fail(limit, "BAD_ARG")
    const jobage = posInt("jobage", flags.jobage, 1, 365, 0)
    if (typeof jobage === "string") return fail(jobage, "BAD_ARG")
    const sites = (flags.site as string[] | undefined) ?? []
    if (flags.site === true || sites.some((s) => !/^[a-z0-9.-]+\.[a-z]{2,}$/i.test(s))) return fail("--site takes a bare domain such as jobs.lever.co", "BAD_ARG")
    const f = fmt(flags.format, ["json", "table", "plain"])
    if (!f) return fail("--format must be json|table|plain", "BAD_ARG")
    return runSearch({
      ...p,
      query: flags.query.trim(),
      location: typeof flags.location === "string" ? flags.location.trim() : undefined,
      jobage: jobage || undefined,
      limit,
      sites,
      format: f as "json" | "table" | "plain",
    })
  }

  const url = flags._[1]
  if (!url) return fail("detail requires a <url>", "NO_URL")
  const maxChars = posInt("max-chars", flags["max-chars"], 500, 100_000, 12_000)
  if (typeof maxChars === "string") return fail(maxChars, "BAD_ARG")
  const f = fmt(flags.format, ["json", "plain"])
  if (!f) return fail("--format must be json|plain", "BAD_ARG")
  return runDetail({ ...p, url, maxChars, format: f as "json" | "plain" })
}

if (import.meta.main) {
  main()
    .then((code) => process.exit(code))
    .catch((e) => process.exit(failFrom(e)))
}
