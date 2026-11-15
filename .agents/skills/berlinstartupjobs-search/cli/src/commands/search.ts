import { buildSearchUrl, jsonFetch, parsePosts, stripInvisible, writeError, type Job } from "../helpers.js"

export interface SearchOpts {
  query?: string
  jobage?: number
  page: number
  limit?: number
  remote: boolean
  format: "json" | "table" | "plain"
}

function renderTable(jobs: Job[]): string {
  if (jobs.length === 0) return "No results."
  const header =
    "ID".padEnd(8) + " " + "TITLE".padEnd(44) + " " + "COMPANY".padEnd(24) + " " + "LOCATION".padEnd(18) + " DATE"
  const rows = jobs.map(
    (j) =>
      `${j.id.padEnd(8)} ${j.title.slice(0, 44).padEnd(44)} ${(j.company || "—").slice(0, 24).padEnd(24)} ${(j.location || "—").slice(0, 18).padEnd(18)} ${j.date || "—"}`,
  )
  return [header, "-".repeat(header.length), ...rows].join("\n")
}

export async function runSearch(opts: SearchOpts): Promise<number> {
  try {
    const { body, total } = await jsonFetch(buildSearchUrl(opts))
    let jobs = stripInvisible(parsePosts(body))
    if (opts.remote) jobs = jobs.filter((j) => j.remote)
    if (opts.limit !== undefined) jobs = jobs.slice(0, opts.limit)

    if (opts.format === "table") {
      process.stdout.write(renderTable(jobs) + "\n")
    } else if (opts.format === "plain") {
      process.stdout.write(
        jobs
          .map((j) => `${j.title}\n  ${j.company || "—"} · ${j.location || "—"} · ${j.date || "—"}\n  id: ${j.id}\n  ${j.url}`)
          .join("\n\n") + "\n",
      )
    } else {
      process.stdout.write(
        JSON.stringify({ meta: { count: jobs.length, page: opts.page, total }, results: jobs }, null, 2) + "\n",
      )
    }
    return 0
  } catch (e) {
    writeError(e instanceof Error ? e.message : String(e), "SEARCH_FAILED")
    return 1
  }
}
