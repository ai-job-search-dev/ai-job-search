import { buildApiUrl, getText, matchesQuery, parseApiPage, stripInvisible, writeError, type Job } from "../helpers.js"

export interface SearchOpts {
  query?: string
  location?: string
  remote: boolean
  visa: boolean
  jobage?: number
  page: number
  pages: number
  limit?: number
  format: "json" | "table" | "plain"
  now?: Date
}

function renderTable(jobs: Job[]): string {
  if (jobs.length === 0) return "No results."
  const header = "TITLE".padEnd(44) + " " + "COMPANY".padEnd(24) + " " + "LOCATION".padEnd(18) + " DATE"
  const rows = jobs.map(
    (j) => `${j.title.slice(0, 44).padEnd(44)} ${(j.company || "—").slice(0, 24).padEnd(24)} ${(j.location || "—").slice(0, 18).padEnd(18)} ${j.date || "—"}`,
  )
  return [header, "-".repeat(header.length), ...rows].join("\n")
}

export async function runSearch(opts: SearchOpts): Promise<number> {
  try {
    const all: Job[] = []
    let scanned = 0
    for (let p = opts.page; p < opts.page + opts.pages; p++) {
      const body = await getText(buildApiUrl(p, opts.visa), "application/json")
      if (body === null) break
      const { jobs, hasNext } = parseApiPage(body)
      all.push(...jobs)
      scanned++
      if (!hasNext || jobs.length === 0) break
    }
    let jobs = stripInvisible(all)
    if (opts.query) jobs = jobs.filter((j) => matchesQuery(j, opts.query!))
    if (opts.location) {
      const loc = opts.location.toLowerCase()
      jobs = jobs.filter((j) => (j.location || "").toLowerCase().includes(loc))
    }
    if (opts.remote) jobs = jobs.filter((j) => j.remote)
    if (opts.jobage !== undefined) {
      const cutoff = new Date((opts.now ?? new Date()).getTime() - opts.jobage * 86400000).toISOString().slice(0, 10)
      jobs = jobs.filter((j) => j.date !== null && j.date >= cutoff)
    }
    if (opts.limit !== undefined) jobs = jobs.slice(0, opts.limit)

    if (opts.format === "table") process.stdout.write(renderTable(jobs) + "\n")
    else if (opts.format === "plain")
      process.stdout.write(
        jobs.map((j) => `${j.title}\n  ${j.company || "—"} · ${j.location || "—"}${j.remote ? " (remote)" : ""} · ${j.date || "—"}\n  id: ${j.id}\n  ${j.url}`).join("\n\n") + "\n",
      )
    else
      process.stdout.write(
        JSON.stringify({ meta: { count: jobs.length, page: opts.page, pages_scanned: scanned, scanned_postings: all.length, visa_sponsorship_filter: opts.visa }, results: jobs }, null, 2) + "\n",
      )
    return 0
  } catch (e) {
    writeError(e instanceof Error ? e.message : String(e), "SEARCH_FAILED")
    return 1
  }
}
