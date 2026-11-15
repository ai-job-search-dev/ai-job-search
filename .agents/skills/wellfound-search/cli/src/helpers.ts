// Data source: Wellfound's public role pages (server-rendered Next.js).
// Search: /role/<role>, /role/l/<role>/<location>, /role/r/<role> embed the
// listing as Apollo cache JSON inside <script id="__NEXT_DATA__">.
// Detail: /jobs/<id>-<slug> carries a schema.org JobPosting JSON-LD block.
//
// Personal use only. Wellfound's Terms prohibit scraping content and automated
// access heavier than a human browsing. Keep volume low, never use it
// commercially or for bulk collection. Run it on your own responsibility.

export const BASE_URL = "https://wellfound.com"

export function writeError(error: string, code: string): void {
  process.stderr.write(JSON.stringify({ error, code }) + "\n")
}

const UA = "Mozilla/5.0 (compatible; wellfound-cli/1.0)"

export class NoRolePageError extends Error {}

/**
 * Fetch HTML with exponential backoff + jitter on 429/5xx. Returns "" on 404.
 * Redirects are not followed: Wellfound answers an unknown role/location pair
 * with a 3xx to a generic page, which must not be parsed as matching results.
 */
export async function htmlFetch(url: string): Promise<string> {
  const maxRetries = 6
  let delay = 500
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    const response = await fetch(url, {
      headers: {
        "User-Agent": UA,
        Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
      },
      redirect: "manual",
      signal: AbortSignal.timeout(15000),
    })
    if (response.status === 429 || response.status >= 500) {
      if (attempt === maxRetries) {
        throw new Error(`Request failed: ${response.status} ${response.statusText}`)
      }
      const jitter = Math.floor(Math.random() * 500)
      await new Promise((r) => setTimeout(r, delay + jitter))
      delay = Math.min(delay * 2, 8000)
      continue
    }
    if (response.status >= 300 && response.status < 400) {
      throw new NoRolePageError(`redirected to ${response.headers.get("location") ?? "another page"}`)
    }
    if (response.status === 404) return ""
    if (!response.ok) {
      throw new Error(`Request failed: ${response.status} ${response.statusText}`)
    }
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
  compensation: string | null
  jobType: string | null
  yearsExperienceMin: number | null
}

export interface JobDetail {
  id: string
  title: string
  company: string | null
  location: string | null
  date: string | null
  url: string
  employmentType: string | null
  remote: boolean
  applicantLocations: string[]
  salary: string | null
  industry: string | null
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

/** Lowercase, hyphenated URL slug ("Engineering Manager" -> "engineering-manager"). */
export function slugify(text: string): string {
  return text.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "")
}

export function buildSearchUrl(opts: { role: string; location?: string; remote: boolean; page: number }): string {
  const role = slugify(opts.role)
  const path = opts.remote
    ? `/role/r/${role}`
    : opts.location
      ? `/role/l/${role}/${slugify(opts.location)}`
      : `/role/${role}`
  return `${BASE_URL}${path}${opts.page > 1 ? `?page=${opts.page}` : ""}`
}

function nextData(html: string): Rec | null {
  const m = html.match(/<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/)
  if (!m) return null
  try {
    const d = JSON.parse(m[1])
    return isRec(d) ? d : null
  } catch {
    return null
  }
}

/**
 * Parse a role page's Apollo cache. Each StartupResult lists its job refs in
 * highlightedJobListings; jobs are JobListingSearchResult entries. Each job is
 * parsed on its own so one malformed entry cannot break the rest.
 * Returns null when the page carries no Apollo cache at all (markup change).
 */
export function parseRolePage(html: string): Job[] | null {
  const d = nextData(html)
  const pp = d && isRec(d.props) && isRec(d.props.pageProps) ? d.props.pageProps : null
  const state = pp && isRec(pp.apolloState) ? pp.apolloState : null
  const data = state && isRec(state.data) ? state.data : null
  if (!data) return null

  const companyOf: Record<string, string> = {}
  for (const [key, v] of Object.entries(data)) {
    if (!key.startsWith("StartupResult:") || !isRec(v)) continue
    const name = str(v.name)
    if (!name || !Array.isArray(v.highlightedJobListings)) continue
    for (const ref of v.highlightedJobListings) {
      if (isRec(ref) && typeof ref.__ref === "string") companyOf[ref.__ref] = name
    }
  }

  const jobs: Job[] = []
  for (const [key, v] of Object.entries(data)) {
    if (!key.startsWith("JobListingSearchResult:") || !isRec(v)) continue
    const id = typeof v.id === "string" || typeof v.id === "number" ? String(v.id) : null
    const title = str(v.title)
    if (!id || !/^\d+$/.test(id) || !title) continue
    const slug = typeof v.slug === "string" && /^[a-z0-9-]+$/i.test(v.slug) ? v.slug : slugify(title)
    const locs = Array.isArray(v.locationNames) ? v.locationNames.filter((x): x is string => typeof x === "string") : []
    const remoteLocs = Array.isArray(v.acceptedRemoteLocationNames)
      ? v.acceptedRemoteLocationNames.filter((x): x is string => typeof x === "string")
      : []
    const remote = v.remote === true
    let location = locs.map(clean).join(" / ") || null
    if (remote) location = [location, `Remote${remoteLocs.length ? ` (${remoteLocs.map(clean).join(", ")})` : ""}`].filter(Boolean).join(" · ")
    const started = typeof v.liveStartAt === "number" ? new Date(v.liveStartAt * 1000) : null
    jobs.push({
      // Wellfound job pages need the slug (/jobs/<id> alone is a 404), so the
      // id is the full "<id>-<slug>" key that `detail` accepts as-is.
      id: `${id}-${slug}`,
      title,
      company: companyOf[key] ?? null,
      location,
      date: started && !isNaN(started.getTime()) ? started.toISOString().slice(0, 10) : null,
      url: `${BASE_URL}/jobs/${id}-${slug}`,
      remote,
      compensation: str(v.compensation),
      jobType: str(v.jobType),
      yearsExperienceMin: typeof v.yearsExperienceMin === "number" ? v.yearsExperienceMin : null,
    })
  }
  return jobs
}

function formatSalary(v: unknown): string | null {
  if (!isRec(v) || !isRec(v.value)) return null
  const cur = typeof v.currency === "string" ? v.currency : ""
  const { minValue, maxValue, unitText } = v.value as Rec
  const fmt = (n: unknown) => (typeof n === "number" ? n.toLocaleString("en-US") : null)
  const range = [fmt(minValue), fmt(maxValue)].filter(Boolean).join(" - ")
  if (!range) return null
  return `${cur} ${range}${typeof unitText === "string" ? ` / ${unitText.toLowerCase()}` : ""}`.trim()
}

/** Parse the JobPosting JSON-LD block of a /jobs/<id> page. */
export function parseDetailPage(html: string, key: string): JobDetail | null {
  for (const m of html.matchAll(/<script[^>]*type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/g)) {
    let ld: unknown
    try {
      ld = JSON.parse(m[1])
    } catch {
      continue
    }
    if (!isRec(ld) || ld["@type"] !== "JobPosting") continue
    const title = str(ld.title)
    if (!title) continue
    const org = isRec(ld.hiringOrganization) ? str(ld.hiringOrganization.name) : null
    const places = Array.isArray(ld.jobLocation) ? ld.jobLocation : isRec(ld.jobLocation) ? [ld.jobLocation] : []
    const locs = places
      .map((p) => (isRec(p) && isRec(p.address) ? [str(p.address.addressLocality), str(p.address.addressCountry)].filter(Boolean).join(", ") : ""))
      .filter(Boolean)
    const reqs = Array.isArray(ld.applicantLocationRequirements)
      ? ld.applicantLocationRequirements
      : isRec(ld.applicantLocationRequirements)
        ? [ld.applicantLocationRequirements]
        : []
    const applicantLocations = reqs.map((r) => (isRec(r) ? str(r.name) : null)).filter((x): x is string => !!x)
    return {
      id: key,
      title,
      company: org,
      location: locs.join(" / ") || null,
      date: typeof ld.datePosted === "string" ? ld.datePosted.slice(0, 10) : null,
      url: `${BASE_URL}/jobs/${key}`,
      employmentType: str(ld.employmentType),
      remote: ld.jobLocationType === "TELECOMMUTE",
      applicantLocations,
      salary: formatSalary(ld.baseSalary),
      industry: str(ld.industry),
      description: typeof ld.description === "string" ? htmlToText(ld.description) : null,
    }
  }
  return null
}
