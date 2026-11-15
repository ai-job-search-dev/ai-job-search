import {

  buildDetailUrl,
  buildSlugUrl,
  jsonFetch,
  parseDetail,
  stripInvisible,
  writeError,
} from "../helpers.js"

export interface DetailOpts {
  id: string
  format: "json" | "plain"
}

const BSJ_HOST = /^(www\.)?berlinstartupjobs\.com$/i

/**
 * Accept a numeric post id, or a berlinstartupjobs.com posting URL
 * (https://berlinstartupjobs.com/<category>/<slug>/ or ?p=<id>).
 * URLs on any other host are rejected, read through real URL parsing so
 * look-alike hosts and userinfo tricks fall on the reject side.
 */
export function normalizeInput(input: string): { id: string } | { slug: string } | null {
  const trimmed = input.trim()
  if (/^\d+$/.test(trimmed)) return { id: trimmed }
  const hasScheme = /^https?:\/\//i.test(trimmed)
  let parsed: URL
  try {
    parsed = new URL(hasScheme ? trimmed : `https://${trimmed}`)
  } catch {
    return null
  }
  if (!BSJ_HOST.test(parsed.hostname) || parsed.username || parsed.password) return null
  const p = parsed.searchParams.get("p")
  if (p && /^\d+$/.test(p)) return { id: p }
  const segs = parsed.pathname.split("/").filter(Boolean)
  const slug = segs[segs.length - 1]
  return slug && /^[a-z0-9-]+$/i.test(slug) ? { slug: slug.toLowerCase() } : null
}

export async function runDetail(opts: DetailOpts): Promise<number> {
  const target = normalizeInput(opts.id)
  if (!target) {
    writeError(`Could not parse a Berlin Startup Jobs id or URL from "${opts.id}"`, "BAD_ID")
    return 1
  }
  try {
    let post: unknown
    if ("id" in target) {
      post = (await jsonFetch(buildDetailUrl(target.id))).body
    } else {
      const { body } = await jsonFetch(buildSlugUrl(target.slug))
      post = Array.isArray(body) ? body[0] : null
    }
    const job = post ? parseDetail(post) : null
    if (!job) {
      writeError("Job not found", "NOT_FOUND")
      return 1
    }
    const clean = stripInvisible(job)
    if (opts.format === "plain") {
      const lines = [
        clean.title,
        `${clean.company || "—"} · ${clean.location || "—"} · ${clean.date || "—"}`,
        clean.category ? `Category: ${clean.category}` : "",
        "",
        clean.description || "(no description)",
        "",
        `URL: ${clean.url}`,
      ]
      process.stdout.write(lines.join("\n").replace(/\n{3,}/g, "\n\n") + "\n")
    } else {
      process.stdout.write(JSON.stringify(clean, null, 2) + "\n")
    }
    return 0
  } catch (e) {
    writeError(e instanceof Error ? e.message : String(e), "DETAIL_FAILED")
    return 1
  }
}

