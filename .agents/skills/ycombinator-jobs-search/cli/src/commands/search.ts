import { NoRolePageError, buildSearchUrl, htmlFetch, parseRolePage, slugify, stripInvisible, writeError, type Job } from "../helpers.js"

export interface SearchOpts {
  role: string
  location?: string
  query?: string
  jobage?: number
  limit?: number
  format: "json" | "table" | "plain"
  now?: Date
}

function renderTable(jobs: Job[]): string {
  if (jobs.length === 0) return "No results."
  const header =
    "TITLE".padEnd(38) + " " + "COMPANY".padEnd(22) + " " + "LOCATION".padEnd(30) + " " + "AGE".padEnd(14) + " " + "VISA".padEnd(22) + " SALARY"
  const rows = jobs.map(
    (j) =>
      `${j.title.slice(0, 38).padEnd(38)} ${(j.company || "—").slice(0, 22).padEnd(22)} ${(j.location || "—").slice(0, 30).padEnd(30)} ${(j.age || "—").slice(0, 14).padEnd(14)} ${(j.visa || "—").slice(0, 22).padEnd(22)} ${j.salary || "—"}`,
  )
  return [header, "-".repeat(header.length), ...rows].join("\n")
}

/** Every whitespace-separated term must appear in title, company, one-liner or role type (case-insensitive). */
function matches(job: Job, query: string): boolean {
  const hay = [job.title, job.company, job.companyOneLiner, job.roleType].filter(Boolean).join(" ").toLowerCase()
  return query.toLowerCase().replace(/["']/g, "").split(/\s+/).filter(Boolean).every((t) => hay.includes(t))
}

export async function runSearch(opts: SearchOpts): Promise<number> {
  const url = buildSearchUrl({ role: opts.role, location: opts.location })
  try {
    const html = await htmlFetch(url)
    const page = html ? parseRolePage(html, opts.role, opts.now) : null
    if (!html || page === null) {
      writeError(html ? "Page carried no job data; YC's markup may have changed (see url-reference.md)" : `No YC role page at ${url}`, html ? "PARSE_FAILED" : "NO_ROLE_PAGE")
      return 1
    }
    if (!page.roleMatched) {
      writeError(`"${opts.role}" is not a YC role page; YC would silently show software-engineer jobs. Valid roles: ${page.roles.join(", ")}`, "UNKNOWN_ROLE")
      return 1
    }
    if (opts.location && page.locationSlug !== slugify(opts.location)) {
      writeError(`"${opts.location}" is not a YC location page for this role. Known locations: ${[...page.locations, "remote"].join(", ")}`, "UNKNOWN_LOCATION")
      return 1
    }
    let jobs = stripInvisible(page.jobs)
    if (opts.query) jobs = jobs.filter((j) => matches(j, opts.query!))
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
          .map((j) => `${j.title}\n  ${j.company || "—"} (${j.companyBatch || "YC"}) · ${j.location || "—"} · ${j.age || "—"} ago · ${j.salary || "—"} · visa: ${j.visa || "—"}\n  id: ${j.id}\n  ${j.url}`)
          .join("\n\n") + "\n",
      )
    } else {
      process.stdout.write(JSON.stringify({ meta: { count: jobs.length, page: 1 }, results: jobs }, null, 2) + "\n")
    }
    return 0
  } catch (e) {
    if (e instanceof NoRolePageError) {
      writeError(`No YC page for role "${opts.role}"${opts.location ? ` in "${opts.location}"` : ""} (${e.message})`, "NO_ROLE_PAGE")
      return 1
    }
    writeError(e instanceof Error ? e.message : String(e), "SEARCH_FAILED")
    return 1
  }
}
