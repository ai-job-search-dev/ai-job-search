// Data source: Y Combinator's public job pages on www.ycombinator.com.
// Search: /jobs/role/<role>[/<location>] is an Inertia.js page; its job list
// is JSON in the data-page attribute of the app root element.
// Detail: /companies/<company>/jobs/<key> carries a schema.org JobPosting
// JSON-LD block plus the same data-page JSON (visa, experience, salary).
//
// Personal use only. YC's Terms prohibit robots, scraping and data mining.
// Keep volume low, never use it commercially or for bulk collection, and run
// it on your own responsibility.

export const BASE_URL = "https://www.ycombinator.com"

export function writeError(error: string, code: string): void {
  process.stderr.write(JSON.stringify({ error, code }) + "\n")
}

const UA = "Mozilla/5.0 (compatible; ycombinator-jobs-cli/1.0)"

export class NoRolePageError extends Error {}

/**
 * Fetch HTML with exponential backoff + jitter on 429/5xx. Returns "" on 404.
 * Redirects are not followed: a redirect means the requested page does not exist
 * as asked, and its target must not be parsed as matching results.
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
  age: string | null
  type: string | null
  roleType: string | null
  salary: string | null
  experience: string | null
  visa: string | null
  companyBatch: string | null
  companyOneLiner: string | null
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
  experience: string | null
  visa: string | null
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

export function slugify(text: string): string {
  return text.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "")
}

export function buildSearchUrl(opts: { role: string; location?: string }): string {
  return `${BASE_URL}/jobs/role/${slugify(opts.role)}${opts.location ? `/${slugify(opts.location)}` : ""}`
}

/** The Inertia page object from the data-page attribute (HTML-entity encoded JSON). */
export function inertiaPage(html: string): Rec | null {
  const m = html.match(/\sdata-page="([^"]*)"/)
  if (!m) return null
  try {
    const d = JSON.parse(decodeHtmlEntities(m[1]))
    return isRec(d) ? d : null
  } catch {
    return null
  }
}

/**
 * Convert YC's relative age ("9 days", "about 1 month", "over 2 years",
 * "3 hours") to an approximate ISO date. Months count as 30 days, years 365.
 */
export function ageToDate(age: string, now: Date = new Date()): string | null {
  const m = age.toLowerCase().match(/^(?:about|almost|over|less than)?\s*(a|an|\d+)\s+(minute|hour|day|week|month|year)s?$/)
  if (!m) return null
  const n = m[1] === "a" || m[1] === "an" ? 1 : Number(m[1])
  const unitDays: Record<string, number> = { minute: 0, hour: 0, day: 1, week: 7, month: 30, year: 365 }
  const days = n * unitDays[m[2]]
  return new Date(now.getTime() - days * 86400000).toISOString().slice(0, 10)
}

/** "/companies/<co>/jobs/<key>" -> "<co>/<key>"; anything else -> null. */
export function keyFromPath(path: string): string | null {
  const m = path.match(/^\/companies\/([a-z0-9-]+)\/jobs\/([A-Za-z0-9]+-[a-z0-9-]*|[A-Za-z0-9]+)\/?$/)
  return m ? `${m[1]}/${m[2]}` : null
}

export interface RolePage {
  jobs: Job[]
  roles: string[]
  roleMatched: boolean
  locationSlug: string | null
  locations: string[]
}

/**
 * Parse a role page. Each posting is parsed on its own so one malformed entry
 * cannot break the rest. Returns null when the page has no Inertia data.
 * roleMatched/locationSlug let the caller detect YC's silent fallback: an
 * unknown role renders the software-engineer list under the requested URL.
 */
export function parseRolePage(html: string, requestedRole: string, now: Date = new Date()): RolePage | null {
  const page = inertiaPage(html)
  const props = page && isRec(page.props) ? page.props : null
  if (!props || !Array.isArray(props.jobPostings)) return null
  const roleList = Array.isArray(props.jobRoles) ? props.jobRoles : []
  const roles = roleList.map((r) => (isRec(r) && typeof r.slug === "string" ? r.slug : "")).filter(Boolean)
  const roleMatched = roles.includes(slugify(requestedRole))
  const loc = isRec(props.location) ? props.location : null
  const locationSlug = loc && typeof loc.slug === "string" && loc.is_local !== true ? loc.slug : null
  const locations = (Array.isArray(props.jobLocations) ? props.jobLocations : [])
    .map((l) => (isRec(l) && typeof l.slug === "string" ? l.slug : ""))
    .filter(Boolean)

  const jobs: Job[] = []
  for (const j of props.jobPostings) {
    if (!isRec(j)) continue
    const t = str(j.title)
    const key = typeof j.url === "string" ? keyFromPath(j.url) : null
    if (!t || !key) continue
    const age = str(j.createdAt)
    jobs.push({
      id: key,
      title: t,
      company: str(j.companyName),
      location: str(j.location),
      date: age ? ageToDate(age, now) : null,
      url: `${BASE_URL}${j.url}`,
      age,
      type: str(j.type),
      roleType: str(j.roleSpecificType),
      salary: str(j.salaryRange),
      experience: str(j.minExperience),
      visa: str(j.visa),
      companyBatch: str(j.companyBatchName),
      companyOneLiner: str(j.companyOneLiner),
    })
  }
  return { jobs, roles, roleMatched, locationSlug, locations }
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

/** Parse a job page: JobPosting JSON-LD, enriched with visa/experience from the Inertia props. */
export function parseDetailPage(html: string, key: string): JobDetail | null {
  const page = inertiaPage(html)
  const props = page && isRec(page.props) ? page.props : null
  const job = props && isRec(props.job) ? props.job : null
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
    const places = Array.isArray(ld.jobLocation) ? ld.jobLocation : isRec(ld.jobLocation) ? [ld.jobLocation] : []
    const locs = places
      .map((p) => (isRec(p) && isRec(p.address) ? [str(p.address.addressLocality), str(p.address.addressCountry)].filter(Boolean).join(", ") : ""))
      .filter(Boolean)
    const reqs = Array.isArray(ld.applicantLocationRequirements)
      ? ld.applicantLocationRequirements
      : isRec(ld.applicantLocationRequirements)
        ? [ld.applicantLocationRequirements]
        : []
    const [co, slug] = key.split("/")
    return {
      id: key,
      title,
      company: isRec(ld.hiringOrganization) ? str(ld.hiringOrganization.name) : null,
      location: (job && str(job.location)) || locs.join(" / ") || null,
      date: typeof ld.datePosted === "string" ? ld.datePosted.slice(0, 10) : null,
      url: `${BASE_URL}/companies/${co}/jobs/${slug}`,
      employmentType: str(ld.employmentType),
      remote: ld.jobLocationType === "TELECOMMUTE",
      applicantLocations: reqs.map((r) => (isRec(r) ? str(r.name) : null)).filter((x): x is string => !!x),
      salary: (job && str(job.salaryRange)) || formatSalary(ld.baseSalary),
      experience: job ? str(job.minExperience) : null,
      visa: job ? str(job.visa) : null,
      description: typeof ld.description === "string" ? htmlToText(ld.description) : null,
    }
  }
  return null
}
