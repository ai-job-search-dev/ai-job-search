import { getText, keyFromUrl, parseJobPage, stripInvisible, writeError } from "../helpers.js"

export interface DetailOpts {
  id: string
  format: "json" | "plain"
}

/**
 * Accept the "<company>/<slug>" id from search (resolved on arbeitnow.com) or a
 * posting URL on arbeitnow.com / arbeitnow.co.uk. Other hosts are rejected.
 */
export function resolveUrl(input: string): { key: string; url: string } | null {
  const t = input.trim()
  if (/^[a-z0-9-]+\/[a-z0-9-]+$/i.test(t)) return { key: t, url: `https://www.arbeitnow.com/jobs/companies/${t}` }
  const url = /^https?:\/\//i.test(t) ? t : `https://${t}`
  const key = keyFromUrl(url)
  return key ? { key, url: url.split(/[?#]/)[0] } : null
}

export async function runDetail(opts: DetailOpts): Promise<number> {
  const target = resolveUrl(opts.id)
  if (!target) {
    writeError(`Could not parse an Arbeitnow job id or URL from "${opts.id}"`, "BAD_ID")
    return 1
  }
  try {
    const html = await getText(target.url, "text/html,application/xhtml+xml,*/*;q=0.8")
    const job = html ? parseJobPage(html, target.key, target.url) : null
    if (!job) {
      writeError("Job not found (or no JobPosting data on the page)", "NOT_FOUND")
      return 1
    }
    const j = stripInvisible(job)
    if (opts.format === "plain") {
      const meta = [j.employmentType ? `Employment: ${j.employmentType}` : "", j.deadline ? `Valid through: ${j.deadline}` : ""].filter(Boolean)
      process.stdout.write([j.title, `${j.company || "—"} · ${j.location || "—"} · ${j.date || "—"}`, ...meta, "", j.description || "(no description)", "", `URL: ${j.url}`].join("\n") + "\n")
    } else process.stdout.write(JSON.stringify(j, null, 2) + "\n")
    return 0
  } catch (e) {
    writeError(e instanceof Error ? e.message : String(e), "DETAIL_FAILED")
    return 1
  }
}
