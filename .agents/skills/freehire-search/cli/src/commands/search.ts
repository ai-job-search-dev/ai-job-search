import { apiGet, stripInvisible, toResult, writeError, type FreehireJob, type JobResult } from "../helpers.js"

// The agent variant of the job search: the same query, ranking, and facets as the
// web's /jobs/search, but each hit carries the posting's full description instead
// of the search index's truncated preview — so a run reads every result without a
// follow-up `detail` per hit.
const SEARCH_PATH = "/api/v1/agent/jobs/search"

// The web search: identical query surface, but each hit carries only the index's
// truncated preview. --no-description uses it, because the agent endpoint reads
// every full body from the database whether or not the caller wants them - the
// lean mode would otherwise pay the expensive request and discard its payload.
const LEAN_SEARCH_PATH = "/api/v1/jobs/search"

/** How the API renders each result's full description. */
export type DescriptionFormat = "markdown" | "text" | "html"

export const DESCRIPTION_FORMATS: DescriptionFormat[] = ["markdown", "text", "html"]

export interface SearchOpts {
  query?: string
  jobage: number
  page: number
  limit: number
  format: "json" | "table" | "plain"
  descriptionFormat: DescriptionFormat
  // Hydrate full description bodies (the documented default). False keeps a
  // discovery pass cheap: bodies are ~73% of a default search payload, and
  // /scrape pre-filters by title before reading bodies anyway.
  includeDescription?: boolean
  // Facet filters (already parsed into value lists; empty means unset).
  regions: string[]
  countries: string[]
  cities: string[]
  seniority: string[]
  category: string[]
  skills: string[]
  company?: string
  workMode?: string // work_mode facet: remote | hybrid | onsite
  // Arbitrary facet escape hatch: param -> values, for the long tail of the vocabulary.
  facets: Record<string, string[]>
}

function buildQuery(opts: SearchOpts): URLSearchParams {
  const p = new URLSearchParams()
  if (opts.query) p.set("q", opts.query)
  p.set("limit", String(opts.limit))
  p.set("offset", String((opts.page - 1) * opts.limit))
  if (opts.includeDescription !== false) p.set("description_format", opts.descriptionFormat)
  if (opts.jobage > 0 && opts.jobage < 9999) p.set("posted_within_days", String(opts.jobage))
  if (opts.workMode) p.set("work_mode", opts.workMode)
  if (opts.company) p.set("company_slug", opts.company)

  // Named facets and the generic --facet escape hatch append the same way; values
  // are already split into lists, so each becomes one repeated query param.
  const facets: Array<[string, string[]]> = [
    ["regions", opts.regions],
    ["countries", opts.countries],
    ["cities", opts.cities],
    ["seniority", opts.seniority],
    ["category", opts.category],
    ["skills", opts.skills],
    ...Object.entries(opts.facets),
  ]
  for (const [param, values] of facets) {
    for (const value of values) p.append(param, value)
  }
  return p
}

/** The date portion (YYYY-MM-DD) of an ISO timestamp, or "—" when absent. */
function shortDate(date: string | null): string {
  return date ? date.slice(0, 10) : "—"
}

// Table columns: header, width, and the cell value. The SLUG column is sized to
// the longest slug so it is never truncated — a cut slug can't be looked up in
// `detail`; the fixed-width columns truncate for scanning.
interface Column {
  header: string
  width: number
  cell: (r: JobResult) => string
}

function renderTable(rows: JobResult[]): string {
  if (rows.length === 0) return "No results."
  const columns: Column[] = [
    { header: "SLUG", width: Math.max(4, ...rows.map((r) => r.id.length)), cell: (r) => r.id },
    { header: "TITLE", width: 38, cell: (r) => r.title },
    { header: "COMPANY", width: 22, cell: (r) => r.company ?? "—" },
    { header: "LOCATION", width: 20, cell: (r) => r.location ?? "—" },
    { header: "DATE", width: 10, cell: (r) => shortDate(r.date) },
  ]
  const row = (cells: string[]) => cells.map((c, i) => c.slice(0, columns[i].width).padEnd(columns[i].width)).join("  ")

  const header = row(columns.map((c) => c.header))
  const body = rows.map((r) => row(columns.map((c) => c.cell(r))))
  return [header, "-".repeat(header.length), ...body].join("\n")
}

function renderPlain(rows: JobResult[]): string {
  if (rows.length === 0) return "No results."
  const block = (r: JobResult) =>
    [
      r.title,
      `  ${r.company ?? "—"} · ${r.location ?? "—"} · ${shortDate(r.date)}`,
      `  slug: ${r.id}`,
      `  ${r.url}`,
    ].join("\n")
  return rows.map(block).join("\n\n")
}

// freehire's contract - the openapi `Regions` enum and `RegionValues` in vocab.go -
// defines a closed set: these nine buckets plus `none`. Anything else is a stray
// tag, `us` above all, which is a country code that matches only a handful of
// postings. A search filtered on one therefore looks empty or nearly empty, so the
// CLI says the value is wrong instead of letting it read as a thin market.
const REGION_VALUES = [
  "global",
  "north_america",
  "latam",
  "eu",
  "uk",
  "mena",
  "africa",
  "apac",
  "cis",
  "none",
] as const

/** Region values the caller filtered on: `--region` plus the `--facet regions=` escape hatch. */
function requestedRegions(opts: SearchOpts): string[] {
  return [...opts.regions, ...(opts.facets.regions ?? [])]
}

/**
 * One line on stderr when a region filter uses a value outside the closed
 * vocabulary, written after stdout and without touching the exit code, so a bad
 * parameter never reads as "no postings". The result count is deliberately not
 * consulted: a broad query returns real hits under a stray tag and is still wrong,
 * which is exactly the case a zero-result check would miss.
 */
function warnOnUnknownRegion(opts: SearchOpts): void {
  const requested = requestedRegions(opts)
  if (requested.length === 0) return
  const known = new Set<string>(REGION_VALUES)
  const unknown = [...new Set(requested.map((value) => value.trim().toLowerCase()))].filter(
    (value) => value !== "" && !known.has(value),
  )
  if (unknown.length === 0) return
  const country = unknown.includes("us")
    ? " A country belongs in --country, e.g. --country US."
    : " A country belongs in --country."
  process.stderr.write(
    JSON.stringify({
      warning:
        `--region ${unknown.join(", ")} is not a region value: freehire's regions are ` +
        `${REGION_VALUES.join(", ")}.${country}`,
      code: "REGION_VALUE_UNKNOWN",
    }) + "\n",
  )
}

export async function runSearch(opts: SearchOpts): Promise<number> {
  try {
    const path = opts.includeDescription === false ? LEAN_SEARCH_PATH : SEARCH_PATH
    const env = await apiGet<FreehireJob[]>(`${path}?${buildQuery(opts).toString()}`)
    // A 404 here is a missing endpoint, not a missing job: a freehire instance
    // older than the agent search surface answers that way, and reporting it as
    // an empty result set would hide the misconfiguration behind plausible output.
    if (!env) {
      writeError(
        `${path} not found — this freehire instance predates the search endpoint; upgrade it or unset FREEHIRE_API_URL to use the hosted API`,
        "SEARCH_FAILED",
      )
      return 1
    }
    let rows = stripInvisible((env.data ?? []).map(toResult))
    // The web search still carries each hit's truncated preview, and the cost
    // this flag exists to avoid is the share of CLI output descriptions occupy
    // in agent context - so the lean mode drops them client-side.
    if (opts.includeDescription === false) {
      rows = rows.map((r) => ({ ...r, description: null }))
    }
    const total = env.meta?.total ?? rows.length

    if (opts.format === "table") {
      process.stdout.write(renderTable(rows) + "\n")
    } else if (opts.format === "plain") {
      process.stdout.write(renderPlain(rows) + "\n")
    } else {
      process.stdout.write(
        JSON.stringify(
          { meta: { count: rows.length, page: opts.page, total }, results: rows },
          null,
          2,
        ) + "\n",
      )
    }
    // After stdout: a diagnostic must never interleave with the JSON, and the exit
    // code stays 0 because a wrong filter value is not a failure of the request.
    warnOnUnknownRegion(opts)
    return 0
  } catch (e) {
    writeError(e instanceof Error ? e.message : String(e), "SEARCH_FAILED")
    return 1
  }
}
