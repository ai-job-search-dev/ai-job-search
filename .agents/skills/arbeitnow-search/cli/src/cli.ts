#!/usr/bin/env bun
// Self-contained CLI for Arbeitnow's free public job-board API (Germany, plus some
// UK/remote postings). Zero runtime dependencies. The API asks users not to abuse
// it, so page scans are capped at MAX_PAGES.

import { runSearch, type SearchOpts } from "./commands/search.js"
import { runDetail, type DetailOpts } from "./commands/detail.js"
import { MAX_PAGES } from "./helpers.js"

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
      if (key === "help" || key === "h" || key === "remote" || key === "visa" || next === undefined || next.startsWith("-")) {
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

const HELP = `arbeitnow-cli — search jobs in Germany on Arbeitnow (free public API)

USAGE
  bun run src/cli.ts search [flags]
  bun run src/cli.ts detail <id|url> [--format json|plain]

SEARCH FLAGS
  --query, -q <words>     Client-side filter: every word must appear in title, company,
                          tags, job types or location; "double quotes" make a phrase.
  --location, -l <text>   Client-side substring match on location (Berlin, Munich, Remote).
  --visa                  Only postings marked as offering visa sponsorship (server-side).
  --remote                Only postings flagged remote (client-side).
  --jobage <days>         Posted within N days (client-side, from created_at).
  --page <n>              First API page to read (300 newest postings per page). Default 1.
  --pages <n>             How many API pages to scan, 1-${MAX_PAGES}. Default 2.
  --limit, -n <n>         Cap results emitted.
  --format <fmt>          json (default) | table | plain.

EXAMPLES
  bun run src/cli.ts search -q backend --visa --format table
  bun run src/cli.ts search -q '"engineering manager"' --pages 5 --format table
  bun run src/cli.ts search -q engineer -l Berlin --jobage 14 --format table
  bun run src/cli.ts detail bunch/senior-backend-engineer-berlin-433276 --format plain
`

const KNOWN_FLAGS: Record<string, Set<string>> = {
  search: new Set(["location", "query", "visa", "remote", "jobage", "page", "pages", "limit", "format", "help", "h"]),
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
    for (const name of ["location", "query"]) {
      if (flags[name] !== undefined && (typeof flags[name] !== "string" || !(flags[name] as string).trim())) {
        return fail(`--${name} requires a non-empty value`, "BAD_ARG")
      }
    }
    const fmt = flags.format !== undefined ? String(flags.format) : "json"
    if (!["json", "table", "plain"].includes(fmt)) return fail(`--format must be one of json|table|plain, got "${fmt}"`, "BAD_ARG")
    const nums: Record<string, number | undefined> = {}
    for (const [name, min] of [["jobage", 1], ["page", 1], ["pages", 1], ["limit", 0]] as const) {
      if (flags[name] === undefined) continue
      const v = intFlag(name, flags[name], min)
      if (typeof v === "string") return fail(v, "BAD_ARG")
      nums[name] = v
    }
    if (nums.pages !== undefined && nums.pages > MAX_PAGES) {
      return fail(`--pages is capped at ${MAX_PAGES} (the API asks users not to abuse it)`, "BAD_ARG")
    }
    const opts: SearchOpts = {
      query: typeof flags.query === "string" ? flags.query.trim() : undefined,
      location: typeof flags.location === "string" ? flags.location.trim() : undefined,
      remote: flags.remote === true,
      visa: flags.visa === true,
      jobage: nums.jobage,
      page: nums.page ?? 1,
      pages: nums.pages ?? 2,
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
