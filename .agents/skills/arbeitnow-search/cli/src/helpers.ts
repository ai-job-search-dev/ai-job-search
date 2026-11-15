// Data source: Arbeitnow's free public job-board API (https://www.arbeitnow.com/api/job-board-api).
// The API has no keyword search: each page returns up to 300 newest postings, so
// --query/--location/--remote/--jobage filter client-side over a bounded number of
// pages. visa_sponsorship=true is a real server-side filter (verified: it changes
// the result set) although the field is not echoed in the response.
// Detail: a posting page carries schema.org JobPosting JSON-LD inside an @graph.
// The API's own terms: "free public API for jobs, please do not abuse" - keep the
// page count low.

export const API_URL = "https://www.arbeitnow.com/api/job-board-api"
export const MAX_PAGES = 5

export function writeError(error: string, code: string): void {
  process.stderr.write(JSON.stringify({ error, code }) + "\n")
}

const UA = "Mozilla/5.0 (compatible; arbeitnow-cli/1.0)"

/** GET with exponential backoff + jitter on 429/5xx. Returns null on 404. */
export async function getText(url: string, accept: string): Promise<string | null> {
  const maxRetries = 6
  let delay = 500
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    const response = await fetch(url, {
      headers: { "User-Agent": UA, Accept: accept },
      redirect: "follow",
      signal: AbortSignal.timeout(20000),
    })
    if (response.status === 429 || response.status >= 500) {
      if (attempt === maxRetries) throw new Error(`Request failed: ${response.status} ${response.statusText}`)
      const jitter = Math.floor(Math.random() * 500)
      await new Promise((r) => setTimeout(r, delay + jitter))
      delay = Math.min(delay * 2, 8000)
      continue
    }
    if (response.status === 404) return null
    if (!response.ok) throw new Error(`Request failed: ${response.status} ${response.statusText}`)
    return response.text()
  }
  throw new Error("Request failed after max retries")
}

export interface Job {
  id: string
  title: string
  company: string | null
  location: string | null
  date: string | null
  url: string
  remote: boolean
  tags: string[]
  jobTypes: string[]
}

export interface JobDetail {
  id: string
  title: string
  company: string | null
  location: string | null
  date: string | null
  deadline: string | null
  employmentType: string | null
  url: string
  description: string | null
}

/**
 * Convert a Unicode code point to a string. Uses `fromCodePoint` (not
 * `fromCharCode`) so supplementary-plane code points (e.g. emoji, U+1F600)
 * decode correctly, and drops out-of-range values instead of throwing.
 */
function numericEntity(cp: number): string {
  return cp >= 0 && cp <= 0x10ffff ? String.fromCodePoint(cp) : ""
}

function decodeHtmlEntities(text: string): string {
  return text
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&apos;/g, "'")
    // Numeric character references: decimal (&#233;) and hexadecimal (&#xE9;).
    .replace(/&#(\d+);/g, (_, dec) => numericEntity(parseInt(dec, 10)))
    .replace(/&#[xX]([0-9a-fA-F]+);/g, (_, hex) => numericEntity(parseInt(hex, 16)))
    .replace(/&nbsp;/g, " ")
}

/**
 * Characters a reader of the posting never sees but a model reading this
 * output does: C0/C1 controls other than tab, newline and carriage return, the
 * soft hyphen, bidi marks/embeddings/overrides/isolates, zero-width characters
 * and invisible operators, the BOM, variation selectors, and Unicode tag
 * characters (U+E0000-E007F), which can spell out a whole hidden instruction.
 * Listing text is third-party data, and these arrive raw in a portal's JSON or
 * markup as easily as through a numeric entity.
 */
const INVISIBLE_CHARS =
  /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F­؜᠎​-‏‪-‮⁠-⁯︀-️﻿\u{E0000}-\u{E007F}\u{E0100}-\u{E01EF}]/gu

/** Strip INVISIBLE_CHARS from every string in a result, recursively: the CLI's output boundary. */
export function stripInvisible<T>(value: T): T {
  if (typeof value === "string") return value.replace(INVISIBLE_CHARS, "") as T
  if (Array.isArray(value)) return value.map(stripInvisible) as T
  if (value !== null && typeof value === "object" && Object.getPrototypeOf(value) === Object.prototype) {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, stripInvisible(v)])) as T
  }
  return value
}

const VOID_TAGS = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"])

/** Whether an opening tag's attributes keep it from rendering: `hidden`, or an inline style that hides it. */
function isHiddenTag(attrs: string): boolean {
  for (const m of attrs.matchAll(/([^\s"'=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g)) {
    const name = m[1].toLowerCase()
    const value = m[2] ?? m[3] ?? m[4] ?? ""
    if (name === "hidden") return true
    if (name === "style" && /(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden|mso-hide\s*:\s*all)\b/i.test(value)) {
      return true
    }
  }
  return false
}

/**
 * Drop markup a browser never renders - comments (including Word's
 * conditional ones), <script>/<style>/<template> blocks, and elements marked
 * hidden - before tags are stripped. A plain tag strip keeps their text, which
 * hands the model words the posting's reader never saw. CSS-only tricks
 * (off-screen, zero font size) need a renderer and are not caught here.
 */
export function visibleHtml(html: string): string {
  const src = html
    .replace(/<!--[\s\S]*?(?:-->|$)/g, "")
    .replace(/<(script|style|template)\b[^>]*>[\s\S]*?(?:<\/\1\s*>|$)/gi, "")
  // One pass, collecting what is kept: slicing the string per hidden element
  // would make a page full of them quadratic.
  const open = /<([a-zA-Z][a-zA-Z0-9-]*)(\s[^>]*)?>/g
  const kept: string[] = []
  let from = 0
  let m: RegExpExecArray | null
  while ((m = open.exec(src)) !== null) {
    if (!m[2] || !isHiddenTag(m[2])) continue
    const tag = m[1].toLowerCase()
    let end = open.lastIndex
    if (!VOID_TAGS.has(tag) && !m[0].endsWith("/>")) {
      // Skip to the matching close tag, counting nested same-name elements. An
      // unclosed hidden element runs to the end: failing closed drops visible
      // text, failing open would leak the hidden text.
      const same = new RegExp(`<(/?)${tag}\\b[^>]*>`, "gi")
      same.lastIndex = end
      end = src.length
      let depth = 1
      let s: RegExpExecArray | null
      while ((s = same.exec(src)) !== null) {
        if (s[1]) depth--
        else if (!s[0].endsWith("/>")) depth++
        if (depth === 0) {
          end = same.lastIndex
          break
        }
      }
    }
    kept.push(src.slice(from, m.index))
    from = end
    open.lastIndex = end
  }
  kept.push(src.slice(from))
  return kept.join("")
}

function stripTags(html: string): string {
  return visibleHtml(html).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim()
}

function clean(html: string): string {
  return decodeHtmlEntities(stripTags(html))
}

/** HTML block -> readable text, keeping paragraph and list-item breaks. */
export function htmlToText(html: string): string | null {
  const text = visibleHtml(html)
    .replace(/\s+/g, " ")
    .replace(/<\s*br\s*\/?>/gi, "\n")
    .replace(/<li\b[^>]*>/gi, "\n- ")
    .replace(/<\/(p|li|ul|ol|div|h\d)>/gi, "\n")
    .split("\n")
    .map((line) => (line.startsWith("- ") ? "- " + clean(line.slice(2)) : clean(line)))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/^(- .*)\n\n(?=- )/gm, "$1\n")
    .replace(/^(- .*)\n\n(?=- )/gm, "$1\n")
    .trim()
  return text || null
}

type Rec = Record<string, unknown>
const isRec = (v: unknown): v is Rec => v !== null && typeof v === "object" && !Array.isArray(v)
const str = (v: unknown): string | null => (typeof v === "string" && v.trim() ? clean(v) : null)
const strList = (v: unknown): string[] => (Array.isArray(v) ? v.map(str).filter((x): x is string => !!x) : [])

export const HOSTS = /^(www\.)?arbeitnow\.(com|co\.uk)$/i

/** "/jobs/companies/<company>/<slug>" -> "<company>/<slug>". */
export function keyFromUrl(url: string): string | null {
  let u: URL
  try {
    u = new URL(url)
  } catch {
    return null
  }
  if (!HOSTS.test(u.hostname) || u.username || u.password) return null
  const m = u.pathname.match(/^\/jobs\/companies\/([a-z0-9-]+)\/([a-z0-9-]+)\/?$/i)
  return m ? `${m[1]}/${m[2]}` : null
}

/** Parse one API posting; null for anything unusable, so one bad entry cannot break the rest. */
export function parseApiJob(v: unknown): Job | null {
  if (!isRec(v)) return null
  const title = str(v.title)
  const url = typeof v.url === "string" ? v.url : ""
  const key = keyFromUrl(url)
  if (!title || !key) return null
  const ts = typeof v.created_at === "number" ? v.created_at : typeof v.created_at === "string" && /^\d+$/.test(v.created_at) ? Number(v.created_at) : null
  const d = ts !== null ? new Date(ts * 1000) : null
  return {
    id: key,
    title,
    company: str(v.company_name),
    location: str(v.location),
    date: d && !isNaN(d.getTime()) ? d.toISOString().slice(0, 10) : null,
    url,
    remote: v.remote === true,
    tags: strList(v.tags),
    jobTypes: strList(v.job_types),
  }
}

export function parseApiPage(body: string): { jobs: Job[]; hasNext: boolean } {
  let d: unknown
  try {
    d = JSON.parse(body)
  } catch {
    throw new Error("API returned non-JSON")
  }
  if (!isRec(d) || !Array.isArray(d.data)) throw new Error("API response has no data array")
  const links = isRec(d.links) ? d.links : {}
  return {
    jobs: d.data.map(parseApiJob).filter((j): j is Job => j !== null),
    hasNext: typeof links.next === "string" && links.next.length > 0,
  }
}

export function buildApiUrl(page: number, visa: boolean): string {
  const p = new URLSearchParams()
  p.set("page", String(page))
  if (visa) p.set("visa_sponsorship", "true")
  return `${API_URL}?${p.toString()}`
}

/**
 * Query matching: double-quoted parts must appear as a phrase, remaining words
 * must each appear; all case-insensitive, over title, company, tags, job types
 * and location.
 */
export function matchesQuery(job: Job, query: string): boolean {
  const hay = [job.title, job.company, job.location, ...job.tags, ...job.jobTypes].filter(Boolean).join(" ").toLowerCase()
  const phrases = [...query.matchAll(/"([^"]+)"/g)].map((m) => m[1].toLowerCase().trim()).filter(Boolean)
  const words = query.replace(/"[^"]*"/g, " ").toLowerCase().split(/\s+/).filter(Boolean)
  return phrases.every((p) => hay.includes(p)) && words.every((w) => hay.includes(w))
}

/** Parse the JobPosting JSON-LD (inside @graph) of a posting page. */
export function parseJobPage(html: string, key: string, url: string): JobDetail | null {
  for (const m of html.matchAll(/<script[^>]*type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/g)) {
    let ld: unknown
    try {
      ld = JSON.parse(m[1])
    } catch {
      continue
    }
    const nodes = isRec(ld) && Array.isArray(ld["@graph"]) ? ld["@graph"] : Array.isArray(ld) ? ld : [ld]
    for (const n of nodes) {
      if (!isRec(n) || n["@type"] !== "JobPosting") continue
      const title = str(n.title)
      if (!title) continue
      const places = Array.isArray(n.jobLocation) ? n.jobLocation : isRec(n.jobLocation) ? [n.jobLocation] : []
      const locs = places
        .map((p) => (isRec(p) && isRec(p.address) ? [str(p.address.addressLocality), str(p.address.addressCountry)].filter(Boolean).join(", ") : ""))
        .filter(Boolean)
      const et = Array.isArray(n.employmentType) ? strList(n.employmentType).join(", ") : str(n.employmentType)
      return {
        id: key,
        title,
        company: isRec(n.hiringOrganization) ? str(n.hiringOrganization.name) : null,
        location: locs.join(" / ") || null,
        date: typeof n.datePosted === "string" ? n.datePosted.slice(0, 10) : null,
        deadline: typeof n.validThrough === "string" ? n.validThrough.slice(0, 10) : null,
        employmentType: et || null,
        url,
        description: typeof n.description === "string" ? htmlToText(n.description) : null,
      }
    }
  }
  return null
}
