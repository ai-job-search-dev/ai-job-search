#!/usr/bin/env bun
// Self-contained CLI for searching Wellfound (wellfound.com) role pages.
// Zero runtime dependencies.
//
// Personal use only. Wellfound's Terms prohibit scraping and automated access
// heavier than a human browsing: keep volume low, never use it commercially or
// for bulk collection. Run it on your own responsibility.

import { runSearch, type SearchOpts } from "./commands/search.js"
import { runDetail, type DetailOpts } from "./commands/detail.js"

interface Flags {
  _: string[]
  [k: string]: string | boolean | string[]
}

function parseFlags(argv: string[]): Flags {
  const flags: Flags = { _: [] }
  const alias: Record<string, string> = { q: "query", l: "location", n: "limit" }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a.startsWith("-")) {
      const key = alias[a.replace(/^-+/, "")] ?? a.replace(/^-+/, "")
      const next = argv[i + 1]
      if (key === "remote" || key === "help" || key === "h" || next === undefined || next.startsWith("-")) {
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

const HELP = `wellfound-cli — search startup jobs on Wellfound role pages

USAGE
  bun run src/cli.ts search -q "<role>" [flags]
  bun run src/cli.ts detail <id|url> [--format json|plain]

SEARCH FLAGS
  --query, -q <role>      Role name, REQUIRED. Mapped to a Wellfound role page slug
                          ("engineering manager" -> /role/engineering-manager).
  --location, -l <city>   City slug: bangalore, mumbai, pune, delhi, berlin, ...
  --remote                Remote role page (/role/r/<role>). Conflicts with --location.
  --jobage <days>         Posted within N days (client-side, on the fetched page).
  --page <n>              1-indexed page. Default 1.
  --limit, -n <n>         Cap results emitted (client-side).
  --format <fmt>          json (default) | table | plain.

EXAMPLES
  bun run src/cli.ts search -q "engineering manager" -l bangalore --format table
  bun run src/cli.ts search -q "engineering manager" --remote --format table
  bun run src/cli.ts search -q "backend engineer" -l mumbai --jobage 14 --format table
  bun run src/cli.ts detail 5024012 --format plain

Personal use only — Wellfound's Terms prohibit scraping; keep volume low.
`

const KNOWN_FLAGS: Record<string, Set<string>> = {
  search: new Set(["query", "location", "jobage", "remote", "page", "limit", "format", "help", "h"]),
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
      return fail(
        `unknown flag --${key} for '${cmd}' - flags are never silently ignored, because a discarded filter changes what the search returns; see --help`,
        "UNKNOWN_FLAG",
      )
    }
  }

  if (cmd === "search") {
    if (typeof flags.query !== "string" || !flags.query.trim()) {
      return fail('--query/-q is required: a role name such as "engineering manager"', "NO_QUERY")
    }
    if (flags.location !== undefined && (typeof flags.location !== "string" || !flags.location.trim())) {
      return fail("--location requires a non-empty value", "BAD_ARG")
    }
    if (flags.location !== undefined && flags.remote === true) {
      return fail("--remote and --location select different role pages; pass only one", "CONFLICTING_FLAGS")
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
    const opts: SearchOpts = {
      query: flags.query.trim(),
      location: typeof flags.location === "string" ? flags.location.trim() : undefined,
      jobage: nums.jobage,
      page: nums.page ?? 1,
      limit: nums.limit,
      remote: flags.remote === true,
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
