#!/usr/bin/env bun
// apify-jobs-search: runs Apify Store job-board scrapers (configured in ../actors.json)
// under the shared free-tier budget ledger in src/budget.ts.
//   search  - one actor run per term, capped by --max-charge, -n and --timeout
//   detail  - free: the description the last search returned (local cache)
//   boards  - list configured boards
//   budget  - show (and optionally sync) the monthly USD ledger

import { runBoards, runBudget, runDetail, runSearch } from "./commands/search.js"
import { fail, failFrom } from "./helpers.js"

interface Flags {
  _: string[]
  [k: string]: string | boolean | string[]
}

const BOOLEAN = new Set(["allow-reserve", "sync", "help", "h"])

function parseFlags(argv: string[]): Flags {
  const flags: Flags = { _: [] }
  const alias: Record<string, string> = { q: "query", l: "location", n: "limit", b: "board" }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a.startsWith("-") && a.length > 1) {
      const key = alias[a.replace(/^-+/, "")] ?? a.replace(/^-+/, "")
      const next = argv[i + 1]
      if (BOOLEAN.has(key) || next === undefined || next.startsWith("-")) flags[key] = true
      else {
        flags[key] = next
        i++
      }
    } else flags._.push(a)
  }
  return flags
}

const HELP = `apify-jobs-search - Apify Store job-board scrapers on the free tier, budgeted

USAGE
  bun run src/cli.ts search -b <board> -q "<term>" [-l <place>] [--country <CC>] [-n <1-50>] [--jobage <days>]
  bun run src/cli.ts detail <url> [--format json|plain]
  bun run src/cli.ts boards
  bun run src/cli.ts budget [--sync] [--format json|plain]

SEARCH FLAGS
  --board, -b <name>     Board from actors.json (indeed, naukri, ...). REQUIRED.
  --query, -q <term>     One plain search term. REQUIRED.
  --location, -l <place> Passed to the actor's location input.
  --country <CC>         Passed where the actor takes a country (Indeed). Default from actors.json.
  --limit, -n <n>        Max items (actor maxItems). Default 20, max 50.
  --jobage <days>        Client-side filter on the posting date (undated items are kept).
  --max-charge <usd>     Hard cap for this one run. Default 0.25.
  --timeout <secs>       Actor run timeout. Default 180. --memory <MB> default 1024.
  --run-id <id> / --run-budget <usd> / --allow-reserve   Shared budget controls.
  --format json|table|plain

Env: APIFY_API_TOKEN (required), APIFY_MONTHLY_USD=5, APIFY_CYCLE_DAY=1, APIFY_RPM=4,
     APIFY_CONCURRENCY=1, SCRAPE_BUDGET_RESERVE=0.2, SCRAPE_RUNS_PER_MONTH=8,
     SCRAPE_BUDGET_FILE, APIFY_ACTORS_FILE.
`

const KNOWN: Record<string, Set<string>> = {
  search: new Set(["board", "query", "location", "country", "limit", "jobage", "max-charge", "timeout", "memory", "run-id", "run-budget", "allow-reserve", "format", "help", "h"]),
  detail: new Set(["format", "help", "h"]),
  boards: new Set(["help", "h"]),
  budget: new Set(["sync", "format", "help", "h"]),
}

function numFlag(flags: Flags, name: string, min: number, max: number, dflt: number, int = true): number | string {
  const raw = flags[name]
  if (raw === undefined) return dflt
  const v = typeof raw === "string" ? Number(raw.trim()) : NaN
  if (!Number.isFinite(v) || v < min || v > max || (int && !Number.isInteger(v))) return `--${name} must be ${int ? "a whole number" : "a number"} from ${min} to ${max}, got "${raw}"`
  return v
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
  const format = flags.format === undefined ? "json" : String(flags.format)

  if (cmd === "boards") return runBoards()
  if (cmd === "budget") {
    if (!["json", "plain"].includes(format)) return fail("--format must be json|plain", "BAD_ARG")
    return runBudget({ sync: flags.sync === true, format: format as "json" | "plain" })
  }
  if (cmd === "detail") {
    const url = flags._[1]
    if (!url) return fail("detail requires a <url> from search results", "NO_URL")
    if (!["json", "plain"].includes(format)) return fail("--format must be json|plain", "BAD_ARG")
    return runDetail({ url, format: format as "json" | "plain" })
  }

  for (const k of ["board", "query"]) {
    if (typeof flags[k] !== "string" || !(flags[k] as string).trim()) return fail(`--${k} is required`, k === "query" ? "NO_QUERY" : "NO_BOARD")
  }
  for (const k of ["location", "country", "run-id"]) {
    if (flags[k] !== undefined && (typeof flags[k] !== "string" || !(flags[k] as string).trim())) return fail(`--${k} needs a value`, "BAD_ARG")
  }
  if (typeof flags["run-id"] === "string" && !/^[\w.:-]{1,64}$/.test(flags["run-id"])) return fail("--run-id must be 1-64 characters of [A-Za-z0-9_.:-]", "BAD_ARG")
  if (!["json", "table", "plain"].includes(format)) return fail("--format must be json|table|plain", "BAD_ARG")
  const n: Record<string, number> = {}
  for (const [name, min, max, d, int] of [
    ["limit", 1, 50, 20, true],
    ["jobage", 0, 365, 0, true],
    ["max-charge", 0.01, 5, 0.25, false],
    ["timeout", 30, 900, 180, true],
    ["memory", 128, 8192, 1024, true],
    ["run-budget", 0, 1000, -1, false],
  ] as const) {
    const v = numFlag(flags, name, min, max, d, int)
    if (typeof v === "string") return fail(v, "BAD_ARG")
    n[name] = v
  }
  if ((n.memory & (n.memory - 1)) !== 0) return fail("--memory must be a power of 2 (128, 256, ... 8192)", "BAD_ARG")
  if (n["run-budget"] >= 0 && flags["run-id"] === undefined) return fail("--run-budget needs --run-id", "BAD_ARG")

  return runSearch({
    board: (flags.board as string).trim(),
    query: (flags.query as string).trim(),
    location: typeof flags.location === "string" ? flags.location.trim() : undefined,
    country: typeof flags.country === "string" ? flags.country.trim() : undefined,
    limit: n.limit,
    jobage: n.jobage || undefined,
    maxCharge: n["max-charge"],
    timeoutSecs: n.timeout,
    memoryMb: n.memory,
    runId: typeof flags["run-id"] === "string" ? flags["run-id"] : undefined,
    runBudget: n["run-budget"] >= 0 ? n["run-budget"] : undefined,
    allowReserve: flags["allow-reserve"] === true,
    format: format as "json" | "table" | "plain",
  })
}

if (import.meta.main) {
  main()
    .then((code) => process.exit(code))
    .catch((e) => process.exit(failFrom(e)))
}
