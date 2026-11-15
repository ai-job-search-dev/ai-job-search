import { BASE_URL, NoRolePageError, htmlFetch, parseDetailPage, stripInvisible, writeError } from "../helpers.js"

export interface DetailOpts {
  id: string
  format: "json" | "plain"
}

const WF_HOST = /^(www\.)?wellfound\.com$/i

/**
 * Accept the "<id>-<slug>" key from `search`, or a wellfound.com/jobs/<id>-<slug>
 * URL. A bare numeric id cannot be resolved (Wellfound 404s /jobs/<id>), so it
 * is rejected with a hint. Other hosts are rejected.
 */
export function normalizeId(input: string): string | null {
  const trimmed = input.trim()
  if (/^\d+-[a-z0-9-]+$/i.test(trimmed)) return trimmed.toLowerCase()
  const hasScheme = /^https?:\/\//i.test(trimmed)
  let parsed: URL
  try {
    parsed = new URL(hasScheme ? trimmed : `https://${trimmed}`)
  } catch {
    return null
  }
  if (!WF_HOST.test(parsed.hostname) || parsed.username || parsed.password) return null
  const m = parsed.pathname.match(/^\/jobs\/(\d+-[a-z0-9-]+)\/?$/i)
  return m ? m[1].toLowerCase() : null
}

export async function runDetail(opts: DetailOpts): Promise<number> {
  const id = normalizeId(opts.id)
  if (!id) {
    writeError(
      `Could not parse a Wellfound job key from "${opts.id}". Use the "<id>-<slug>" id from search (e.g. 4475538-engineering-manager-backend) or the job URL; a bare number is not enough`,
      "BAD_ID",
    )
    return 1
  }
  try {
    const html = await htmlFetch(`${BASE_URL}/jobs/${id}`)
    const job = html ? parseDetailPage(html, id) : null
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
        j.industry ? `Industry: ${j.industry}` : "",
      ].filter(Boolean)
      const lines = [
        j.title,
        `${j.company || "—"} · ${j.location || "—"} · ${j.date || "—"}`,
        ...meta,
        "",
        j.description || "(no description)",
        "",
        `URL: ${j.url}`,
      ]
      process.stdout.write(lines.join("\n") + "\n")
    } else {
      process.stdout.write(JSON.stringify(j, null, 2) + "\n")
    }
    return 0
  } catch (e) {
    if (e instanceof NoRolePageError) {
      writeError(`Job ${id} redirected (${e.message}); it may be closed`, "NOT_FOUND")
      return 1
    }
    writeError(e instanceof Error ? e.message : String(e), "DETAIL_FAILED")
    return 1
  }
}
