#!/usr/bin/env bun
// Self-contained CLI for searching the freehire.me aggregator's public JSON API.
// No external CLI framework and zero runtime dependencies, so it runs anywhere
// `bun` is available with nothing installed beyond the repo clone.
//
// Hosted-service dependency: reads are public (no API key), but they hit
// freehire.me — a personal project maintained best-effort (no formal SLA). Point
// FREEHIRE_API_URL at a self-hosted freehire backend to swap the source.

import { runSearch, DESCRIPTION_FORMATS, type DescriptionFormat, type SearchOpts } from "./commands/search.js"
import { runDetail, type DetailOpts } from "./commands/detail.js"
import { baseUrl } from "./helpers.js"

interface Flags {
  _: string[]
  [k: string]: string | boolean | string[]
}

// Short-flag aliases.
const ALIAS: Record<string, string> = { q: "query", n: "limit" }

function parseFlags(argv: string[]): Flags {
  const flags: Flags = { _: [] }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (!a.startsWith("-")) {
      ;(flags._ as string[]).push(a)
      continue
    }
    const name = a.replace(/^-+/, "")
    const key = ALIAS[name] ?? name
    const next = argv[i + 1]
    // A flag with no following value (or another flag next) is a boolean.
    let value: string | boolean = true
    if (next !== undefined && !next.startsWith("-")) {
      value = next
      i++
    }
    // --facet repeats; collect into an array. Everything else is last-wins.
    if (key === "facet") {
      const acc = Array.isArray(flags.facet) ? flags.facet : []
      // Preserve a bare occurrence so validation can reject it instead of
      // silently dropping the requested filter, even after a valid --facet.
      acc.push(typeof value === "string" ? value : "")
      flags.facet = acc
    } else {
      flags[key] = value
    }
  }
  return flags
}

type FlagValue = string | boolean | string[] | undefined

/**
 * A flag's string value. A bare flag (set without a value, i.e. `true`) yields
 * `whenBare` — e.g. `--remote` alone means work_mode "remote".
 */
function stringFlag(raw: FlagValue, whenBare?: string): string | undefined {
  if (typeof raw === "string") return raw
  if (raw === true) return whenBare
  return undefined
}

/** Split a comma-separated facet value ("eu,us") into a trimmed value list. */
function commaList(raw: FlagValue): string[] {
  if (typeof raw !== "string") return []
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
}

const HELP = `freehire-cli — search the freehire.me job aggregator (many markets, tech-focused)

USAGE
  bun run src/cli.ts search [-q "<keywords>"] [facet flags] [--format json|table|plain]
  bun run src/cli.ts detail <slug|url> [--format json|plain]

SEARCH FLAGS
  --query, -q <text>      Keywords (title, skill, role). Full-text; optional.
                          "a OR b" matches either (uppercase OR, max 5);
                          a leading title: searches the job title only.
  --jobage <days>         Posted within N days (maps to posted_within_days).
  --page <n>              1-indexed page. Default 1.
  --limit, -n <n>         Results per page (API limit). Default 25.
  --format <fmt>          json (default) | table | plain.
  --no-description        Skip description hydration for a cheap discovery pass
                          (results keep every other field; detail fetches the body).
  --description-format    markdown (default) | text | html — how each result's
                          full description is rendered (json output only).

FACET FILTERS (values from freehire.me's controlled vocabularies; comma = OR)
  --region <codes>        Macro-region: global, eu, uk, north_america, latam, apac,
                          mena, africa, cis  e.g. --region eu,uk
                          (a country such as us goes in --country, not here)
  --country <codes>       ISO-3166 alpha-2, e.g. --country DE,GB
  --city <names>          City name(s), e.g. --city Berlin
  --seniority <levels>    junior, middle, senior, staff, principal, lead, ...
  --category <cats>       backend, frontend, fullstack, devops, ml_ai, qa, ...
  --skill <names>         Canonical skill(s), e.g. --skill go,kubernetes
  --company <slug>        Company slug (from a result's company_slug).
  --remote <mode>         remote | hybrid | onsite (work_mode facet).
                          A bare --remote means remote. Comma = OR. Unknown values exit 1.
  --facet <key=value>     Any other facet param (repeatable), e.g. --facet salary_min=100000

DETAIL
  <slug|url>              A freehire public slug (from a search result's id/slug)
                          or a full https://freehire.me/jobs/<slug> URL.

EXAMPLES
  bun run src/cli.ts search -q "backend engineer" --seniority senior --limit 10 --format table
  bun run src/cli.ts search -q "react" --remote remote --region eu --format table
  bun run src/cli.ts search --category devops --country DE --jobage 14 --format table
  bun run src/cli.ts detail golang-zensar-2bxu6dxm --format plain

Reads are public (no API key). Source: ${baseUrl()} — a personal project,
best-effort, no SLA. Override with FREEHIRE_API_URL to use a self-hosted backend.
`

const WORK_MODES = ["remote", "hybrid", "onsite"] as const
type WorkMode = (typeof WORK_MODES)[number]

/**
 * Map --remote to the work_mode facet.
 * A bare --remote (boolean true) is the documented "remote" shortcut.
 * Comma-separated values are OR: each item is normalised (`on-site` ->
 * `onsite`) and re-joined. An unknown item is rejected (the API treats it
 * as a filter that matches nothing) and `bad` names that item.
 */
function parseWorkMode(raw: FlagValue): { value?: string; bad?: string } {
  if (raw === undefined) return {}
  if (raw === true) return { value: "remote" }
  if (typeof raw !== "string" || !raw.trim()) return { bad: String(raw) }
  const modes: WorkMode[] = []
  for (const item of raw.split(",")) {
    const token = item.trim()
    if (!token) continue
    switch (token.toLowerCase()) {
      case "remote":
        modes.push("remote")
        break
      case "hybrid":
        modes.push("hybrid")
        break
      case "onsite":
      case "on-site":
        modes.push("onsite")
        break
      default:
        return { bad: token }
    }
  }
  return modes.length > 0 ? { value: modes.join(",") } : { bad: raw.trim() }
}

function parseIntFlag(name: string, raw: string | boolean | string[]): number | null {
  // Number(), not parseInt(): parseInt truncates, so "--jobage 0.5" became 0,
  // which fails search.ts's `jobage > 0` guard and silently drops
  // posted_within_days from the outbound request while exiting 0 (#373).
  // Whole numbers >= 1 only — the Danish CLIs' z.coerce.number().int().min(1)
  // contract; 0 is rejected rather than kept as a "no filter" alias.
  const val = typeof raw === "string" ? Number(raw.trim()) : NaN
  if (!Number.isInteger(val) || val < 1) {
    process.stderr.write(
      JSON.stringify({ error: `--${name} must be a whole number of at least 1, got "${raw}"`, code: "BAD_ARG" }) + "\n",
    )
    return null
  }
  return val
}

// Long-form flag names each command accepts (parseFlags resolves the short
// aliases q/n to these before validation). "help"/"h" pass so `search --help`
// still prints usage. detail takes no --description-format: it always prints
// the detail endpoint's HTML body stripped to text, so accepting the flag
// there ignored it silently.
const KNOWN_FLAGS: Record<string, Set<string>> = {
  search: new Set([
    "query", "category", "city", "company", "country", "facet", "format", "jobage", "limit",
    "page", "region", "remote", "seniority", "skill", "description-format", "no-description", "help", "h",
  ]),
  detail: new Set(["format", "help", "h"]),
}

async function main(): Promise<number> {
  const argv = process.argv.slice(2)
  const flags = parseFlags(argv)
  const cmd = (flags._ as string[])[0]

  if (!cmd || flags.help || flags.h) {
    process.stdout.write(HELP)
    return cmd ? 0 : 1
  }

  // Reject unknown flags instead of silently discarding them: a discarded
  // filter changes what the search returns with no error (a wrong flag name
  // once returned an entire portal's database as if it matched the query).
  // add-portal.md's contract requires a bogus flag to exit 1 with a JSON
  // error on stderr.
  const knownFlags = KNOWN_FLAGS[cmd]
  if (knownFlags) {
    for (const key of Object.keys(flags)) {
      if (key === "_" || knownFlags.has(key)) continue
      process.stderr.write(
        JSON.stringify({
          error: `unknown flag --${key} for '${cmd}' - flags are never silently ignored, because a discarded filter changes what the search returns; see --help for the supported flags`,
          code: "UNKNOWN_FLAG",
        }) + "\n",
      )
      return 1
    }
  }

  if (cmd === "search") {
    // These options take values, unlike bare --remote and --no-description.
    // Treating a present-but-empty option as unset silently broadens the query.
    for (const name of ["query", "company", "country", "region", "city", "seniority", "category", "skill", "description-format"]) {
      const value = flags[name]
      if (value !== undefined && (typeof value !== "string" || !value.trim())) {
        process.stderr.write(
          JSON.stringify({ error: `--${name} requires a non-empty value`, code: "BAD_ARG" }) + "\n",
        )
        return 1
      }
    }

    const fmt = flags.format !== undefined ? String(flags.format) : "json"
    if (!["json", "table", "plain"].includes(fmt)) {
      process.stderr.write(
        JSON.stringify({ error: `--format must be one of json|table|plain, got "${fmt}"`, code: "BAD_ARG" }) + "\n",
      )
      return 1
    }

    // Validated here rather than server-side: the API answers an unrecognized
    // format with raw HTML instead of an error, so a typo would silently change
    // the output rather than fail.
    const descFmt = stringFlag(flags["description-format"]) ?? "markdown"
    if (!DESCRIPTION_FORMATS.includes(descFmt as DescriptionFormat)) {
      const supported = DESCRIPTION_FORMATS.join("|")
      process.stderr.write(
        JSON.stringify({ error: `--description-format must be one of ${supported}, got "${descFmt}"`, code: "BAD_ARG" }) + "\n",
      )
      return 1
    }

    for (const name of ["jobage", "page", "limit"] as const) {
      if (flags[name] !== undefined) {
        const v = parseIntFlag(name, flags[name])
        if (v === null) return 1
        flags[name] = String(v)
      }
    }

    // Generic --facet key=value list -> param -> values.
    const facets: Record<string, string[]> = {}
    const rawFacets = Array.isArray(flags.facet) ? flags.facet : []
    for (const kv of rawFacets) {
      const eq = kv.indexOf("=")
      if (eq <= 0) {
        process.stderr.write(JSON.stringify({ error: `invalid --facet "${kv}", want key=value`, code: "BAD_ARG" }) + "\n")
        return 1
      }
      const key = kv.slice(0, eq)
      const vals = commaList(kv.slice(eq + 1))
      if (vals.length === 0) {
        process.stderr.write(JSON.stringify({ error: `invalid --facet "${kv}", want key=value with at least one value`, code: "BAD_ARG" }) + "\n")
        return 1
      }
      facets[key] = (facets[key] ?? []).concat(vals)
    }

    const parsedRemote = parseWorkMode(flags.remote)
    if (parsedRemote.bad !== undefined) {
      process.stderr.write(
        JSON.stringify({
          error: `--remote must be one of remote|hybrid|onsite, got "${parsedRemote.bad}"`,
          code: "BAD_ARG",
        }) + "\n",
      )
      return 1
    }

    const opts: SearchOpts = {
      query: stringFlag(flags.query),
      jobage: flags.jobage ? parseInt(flags.jobage as string, 10) : 9999,
      page: flags.page ? Math.max(1, parseInt(flags.page as string, 10)) : 1,
      limit: flags.limit ? Math.max(1, parseInt(flags.limit as string, 10)) : 25,
      format: fmt as SearchOpts["format"],
      descriptionFormat: descFmt as DescriptionFormat,
      includeDescription: flags["no-description"] === undefined,
      regions: commaList(flags.region),
      countries: commaList(flags.country),
      cities: commaList(flags.city),
      seniority: commaList(flags.seniority),
      category: commaList(flags.category),
      skills: commaList(flags.skill),
      company: stringFlag(flags.company),
      workMode: parsedRemote.value,
      facets,
    }
    return runSearch(opts)
  }

  if (cmd === "detail") {
    const id = (flags._ as string[])[1]
    if (!id) {
      process.stderr.write(JSON.stringify({ error: "detail requires a <slug|url>", code: "NO_ID" }) + "\n")
      return 1
    }
    const fmt = flags.format !== undefined ? String(flags.format) : "json"
    if (!["json", "plain"].includes(fmt)) {
      process.stderr.write(
        JSON.stringify({ error: `--format must be one of json|plain, got "${fmt}"`, code: "BAD_ARG" }) + "\n",
      )
      return 1
    }
    const opts: DetailOpts = { id, format: fmt as DetailOpts["format"] }
    return runDetail(opts)
  }

  process.stderr.write(JSON.stringify({ error: `Unknown command "${cmd}"`, code: "BAD_CMD" }) + "\n")
  return 1
}

main()
  .then((code) => process.exit(code))
  .catch((e) => {
    process.stderr.write(
      JSON.stringify({
        error: e instanceof Error ? e.message : String(e),
        code: "INTERNAL_ERROR",
      }) + "\n",
    )
    process.exit(1)
  })
