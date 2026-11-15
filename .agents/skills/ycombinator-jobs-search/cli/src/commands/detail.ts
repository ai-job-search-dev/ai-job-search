import { BASE_URL, NoRolePageError, htmlFetch, keyFromPath, parseDetailPage, stripInvisible, writeError } from "../helpers.js"

export interface DetailOpts {
  id: string
  format: "json" | "plain"
}

const YC_HOST = /^(www\.)?ycombinator\.com$/i

/** Accept the "<company>/<key>" id from search, or a ycombinator.com/companies/<co>/jobs/<key> URL. */
export function normalizeId(input: string): string | null {
  const trimmed = input.trim()
  if (/^[a-z0-9-]+\/[A-Za-z0-9]+(?:-[a-z0-9-]*)?$/.test(trimmed)) return trimmed
  const hasScheme = /^https?:\/\//i.test(trimmed)
  let parsed: URL
  try {
    parsed = new URL(hasScheme ? trimmed : `https://${trimmed}`)
  } catch {
    return null
  }
  if (!YC_HOST.test(parsed.hostname) || parsed.username || parsed.password) return null
  return keyFromPath(parsed.pathname)
}

export async function runDetail(opts: DetailOpts): Promise<number> {
  const key = normalizeId(opts.id)
  if (!key) {
    writeError(`Could not parse a YC job id from "${opts.id}". Use the "<company>/<key>" id from search or the job URL`, "BAD_ID")
    return 1
  }
  const [co, slug] = key.split("/")
  try {
    const html = await htmlFetch(`${BASE_URL}/companies/${co}/jobs/${slug}`)
    const job = html ? parseDetailPage(html, key) : null
    if (!job) {
      writeError("Job not found (or no JobPosting data on the page)", "NOT_FOUND")
      return 1
    }
    const j = stripInvisible(job)
    if (opts.format === "plain") {
      const meta = [
        j.employmentType ? `Employment: ${j.employmentType}` : "",
        j.remote ? `Remote: yes${j.applicantLocations.length ? ` (applicants in ${j.applicantLocations.join(", ")})` : ""}` : "",
        j.salary ? `Salary: ${j.salary}` : "",
        j.experience ? `Experience: ${j.experience}` : "",
        j.visa ? `Visa: ${j.visa}` : "",
      ].filter(Boolean)
      const lines = [j.title, `${j.company || "—"} · ${j.location || "—"} · ${j.date || "—"}`, ...meta, "", j.description || "(no description)", "", `URL: ${j.url}`]
      process.stdout.write(lines.join("\n") + "\n")
    } else {
      process.stdout.write(JSON.stringify(j, null, 2) + "\n")
    }
    return 0
  } catch (e) {
    if (e instanceof NoRolePageError) {
      writeError(`Job ${key} redirected (${e.message}); it may be closed`, "NOT_FOUND")
      return 1
    }
    writeError(e instanceof Error ? e.message : String(e), "DETAIL_FAILED")
    return 1
  }
}
