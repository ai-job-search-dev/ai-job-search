#!/usr/bin/env bun
// Self-contained CLI for searching Y Combinator startup jobs (ycombinator.com/jobs).
// Zero runtime dependencies.
//
// Personal use only. YC's Terms prohibit robots, scraping and data mining:
// keep volume low, never use it commercially or for bulk collection. Run it on
// your own responsibility.

import { runSearch, type SearchOpts } from "./commands/search.js"
import { runDetail, type DetailOpts } from "./commands/detail.js"

interface Flags {
  _: string[]
  [k: string]: string | boolean | string[]
}

function parseFlags(argv: string[]): Flags {
  const flags: Flags = { _: [] }
  const alias: Record<string, string> = { q: "query", l: "location", r: "role", n: "limit" }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a.startsWith("-")) {
      const key = alias[a.replace(/^-+/, "")] ?? a.replace(/^-+/, "")
      const next = argv[i + 1]
      if (key === "help" || key === "h" || next === undefined || next.startsWith("-")) {
        flags[key] = true
      } else {
        flags[key] = next
        i++
      }
    } else {
      ;(flags._ as string[]).push(a)
    }
  }
  return flags
}

const HELP = `ycombinator-jobs-cli — search Y Combinator startup jobs (ycombinator.com/jobs)

USAGE
  bun run src/cli.ts search [flags]
  bun run src/cli.ts detail <id|url> [--format json|plain]

SEARCH FLAGS
  --role, -r <role>       YC role page. Default: software-engineer. One of:
                          software-engineer, product-manager, designer, science,
                          sales-manager, marketing, support, operations, recruiting-hr.
  --location, -l <slug>   india, remote, san-francisco, new-york, los-angeles,
                          seattle, austin, chicago, boston (availability varies by role).
  --query, -q <words>     Client-side filter: every word must appear in the title,
                          company, one-liner or role type ("manager", "backend").
  --jobage <days>         Posted within N days (from YC's relative age; approximate).
  --page <n>              Only 1: role pages are not paginated.
  --limit, -n <n>         Cap results emitted.
  --format <fmt>          json (default) | table | plain.

EXAMPLES
  bun run src/cli.ts search -l india --format table
  bun run src/cli.ts search -l remote -q manager --format table
  bun run src/cli.ts search -q backend --jobage 30 --format table
  bun run src/cli.ts detail karat-financial/gD75RKp-founding-security-engineer --format plain

Personal use only — YC's Terms prohibit scraping; keep volume low.
`

const KNOWN_FLAGS: Record<string, Set<string>> = {
  search: new Set(["role", "location", "query", "jobage", "page", "limit", "format", "help", "h"]),
  detail: new Set(["format", "help", "h"]),
}

function fail(error: string, code: string): number {
  process.stderr.write(JSON.stringify({ error, code }) + "\n")
  return 1
}

function intFlag(name: string, raw: string | boolean | string[], min: number): number | string {
  const val = typeof raw === "string" ? Number(raw.trim()) : NaN
  if (!Number.isInteger(val) || val < min) return `--${name} must be a whole number of at least ${min}, got "${raw}"`
  return val
}

async function main(): Promise<number> {
  const flags = parseFlags(process.argv.slice(2))
  const cmd = (flags._ as string[])[0]
  if (!cmd || flags.help || flags.h) {
    process.stdout.write(HELP)
    return cmd ? 0 : 1
  }
  const known = KNOWN_FLAGS[cmd]
  if (!known) return fail(`Unknown command "${cmd}"`, "BAD_CMD")
  for (const key of Object.keys(flags)) {
    if (key !== "_" && !known.has(key)) {
      return fail(`unknown flag --${key} for '${cmd}' - flags are never silently ignored, because a discarded filter changes what the search returns; see --help`, "UNKNOWN_FLAG")
    }
  }

  if (cmd === "search") {
    for (const name of ["role", "location", "query"]) {
      if (flags[name] !== undefined && (typeof flags[name] !== "string" || !(flags[name] as string).trim())) {
        return fail(`--${name} requires a non-empty value`, "BAD_ARG")
      }
    }
    const fmt = flags.format !== undefined ? String(flags.format) : "json"
    if (!["json", "table", "plain"].includes(fmt)) return fail(`--format must be one of json|table|plain, got "${fmt}"`, "BAD_ARG")
    const nums: Record<string, number | undefined> = {}
    for (const [name, min] of [["jobage", 1], ["page", 1], ["limit", 0]] as const) {
      if (flags[name] === undefined) continue
      const v = intFlag(name, flags[name], min)
      if (typeof v === "string") return fail(v, "BAD_ARG")
      nums[name] = v
    }
    if (nums.page !== undefined && nums.page > 1) {
      return fail("YC role pages are not paginated; only --page 1 exists. Narrow with --role/--location instead", "BAD_ARG")
    }
    const opts: SearchOpts = {
      role: typeof flags.role === "string" ? flags.role.trim() : "software-engineer",
      location: typeof flags.location === "string" ? flags.location.trim() : undefined,
      query: typeof flags.query === "string" ? flags.query.trim() : undefined,
      jobage: nums.jobage,
      limit: nums.limit,
      format: fmt as SearchOpts["format"],
    }
    return runSearch(opts)
  }

  const id = (flags._ as string[])[1]
  if (!id) return fail("detail requires an <id|url>", "NO_ID")
  const fmt = flags.format !== undefined ? String(flags.format) : "json"
  if (!["json", "plain"].includes(fmt)) return fail(`--format must be one of json|plain, got "${fmt}"`, "BAD_ARG")
  const opts: DetailOpts = { id, format: fmt as DetailOpts["format"] }
  return runDetail(opts)
}

main()
  .then((code) => process.exit(code))
  .catch((e) => {
    process.stderr.write(JSON.stringify({ error: e instanceof Error ? e.message : String(e), code: "INTERNAL_ERROR" }) + "\n")
    process.exit(1)
  })
