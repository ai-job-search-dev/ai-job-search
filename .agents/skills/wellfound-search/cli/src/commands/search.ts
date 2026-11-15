import { NoRolePageError, buildSearchUrl, htmlFetch, parseRolePage, stripInvisible, writeError, type Job } from "../helpers.js"

export interface SearchOpts {
  query: string
  location?: string
  remote: boolean
  jobage?: number
  page: number
  limit?: number
  format: "json" | "table" | "plain"
  now?: Date
}

function renderTable(jobs: Job[]): string {
  if (jobs.length === 0) return "No results."
  const header =
    "ID".padEnd(8) + " " + "TITLE".padEnd(36) + " " + "COMPANY".padEnd(22) + " " + "LOCATION".padEnd(30) + " " + "DATE".padEnd(10) + " PAY"
  const rows = jobs.map(
    (j) =>
      `${j.id.padEnd(8)} ${j.title.slice(0, 36).padEnd(36)} ${(j.company || "—").slice(0, 22).padEnd(22)} ${(j.location || "—").slice(0, 30).padEnd(30)} ${(j.date || "—").padEnd(10)} ${j.compensation || "—"}`,
  )
  return [header, "-".repeat(header.length), ...rows].join("\n")
}

export async function runSearch(opts: SearchOpts): Promise<number> {
  const url = buildSearchUrl({ role: opts.query, location: opts.location, remote: opts.remote, page: opts.page })
  try {
    const html = await htmlFetch(url)
    if (!html) {
      writeError(`No Wellfound role page for "${opts.query}" (${url}). Use a role name such as "engineering manager", "backend engineer", "staff engineer".`, "NO_ROLE_PAGE")
      return 1
    }
    const parsed = parseRolePage(html)
    if (parsed === null) {
      writeError("Role page carried no job data; Wellfound's markup may have changed (see url-reference.md)", "PARSE_FAILED")
      return 1
    }
    let jobs = stripInvisible(parsed)
    if (opts.jobage !== undefined) {
      const cutoff = new Date((opts.now ?? new Date()).getTime() - opts.jobage * 86400000).toISOString().slice(0, 10)
      jobs = jobs.filter((j) => j.date !== null && j.date >= cutoff)
    }
    if (opts.limit !== undefined) jobs = jobs.slice(0, opts.limit)

    if (opts.format === "table") {
      process.stdout.write(renderTable(jobs) + "\n")
    } else if (opts.format === "plain") {
      process.stdout.write(
        jobs
          .map((j) => `${j.title}\n  ${j.company || "—"} · ${j.location || "—"} · ${j.date || "—"} · ${j.compensation || "—"}\n  id: ${j.id}\n  ${j.url}`)
          .join("\n\n") + "\n",
      )
    } else {
      process.stdout.write(JSON.stringify({ meta: { count: jobs.length, page: opts.page }, results: jobs }, null, 2) + "\n")
    }
    return 0
  } catch (e) {
    if (e instanceof NoRolePageError) {
      writeError(
        `No Wellfound role page for "${opts.query}"${opts.location ? ` in "${opts.location}"` : ""} (${e.message}). Try another city slug (bangalore, mumbai, pune, berlin), --remote, or no location.`,
        "NO_ROLE_PAGE",
      )
      return 1
    }
    writeError(e instanceof Error ? e.message : String(e), "SEARCH_FAILED")
    return 1
  }
}
