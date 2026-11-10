// Data source: the freehire.me public REST API (JSON, `{data, meta}` envelope).
// Reads are unauthenticated — no API key, the same bar as linkedin-search — and
// unlike the HTML-scraping portals there is no markup to parse: we fetch JSON and
// reshape it into the portal-skill contract's result fields. The base URL is
// swappable via FREEHIRE_API_URL for self-hosting.

export const DEFAULT_BASE_URL = "https://freehire.me"

/** API base URL: FREEHIRE_API_URL (for a self-hosted instance) or the default. */
export function baseUrl(): string {
  const raw = (process.env.FREEHIRE_API_URL ?? "").trim()
  return (raw || DEFAULT_BASE_URL).replace(/\/+$/, "")
}

export function writeError(error: string, code: string): void {
  process.stderr.write(JSON.stringify({ error, code }) + "\n")
}

// Names this project in the owner/project/version shape freehire's API docs ask
// for, so the API's operators can find the maintainer before a limit or a field
// changes. The contact URL the docs make optional is left out on purpose: /apply
// learns which hosts a portal serves by reading the host literals in its
// cli/src, so a github.com URL here made every github.com link classify as an
// installed job board instead of an unverified host. The owner/project name
// already identifies the repo on GitHub.
const UA = "ai-job-search-dev/ai-job-search/1.0 (freehire-search)"

/** The shared API response envelope: {data, meta, error}. */
export interface Envelope<T> {
  data: T
  meta?: { total?: number; limit?: number; offset?: number }
  error?: string
}

// The most apiGet sleeps across all retries of one request. Its own backoff
// schedule (500ms doubling to 8s, six retries) stays under it; a Retry-After
// that would push past it fails the request at once instead, so an outage still
// degrades this source quickly rather than outlasting the caller's tool timeout.
const RETRY_BUDGET_MS = 30_000

/**
 * The wait a 429/503 asks for via `Retry-After`, in ms, or null when absent or
 * unreadable. Accepts both RFC 9110 forms: delta-seconds and an HTTP-date.
 */
export function retryAfterMs(header: string | null, now = Date.now()): number | null {
  if (!header) return null
  const value = header.trim()
  if (/^\d+$/.test(value)) return Number(value) * 1000
  const date = Date.parse(value)
  if (Number.isNaN(date)) return null
  return Math.max(date - now, 0)
}

/**
 * GET a JSON envelope from the freehire API. Retries 429/5xx (transient server
 * states) with backoff, waiting at least as long as a `Retry-After` header asks
 * within a total budget of RETRY_BUDGET_MS; returns `null` on a 404. A connection failure fails fast
 * with a clear message — no retry, so an outage degrades this source quickly
 * rather than hanging the caller (the graceful-degradation contract).
 */
export async function apiGet<T>(path: string): Promise<Envelope<T> | null> {
  const url = `${baseUrl()}${path}`
  const maxRetries = 6
  let delay = 500
  let waited = 0

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    let response: Response
    try {
      response = await fetch(url, {
        headers: { "User-Agent": UA, Accept: "application/json" },
        redirect: "follow",
        signal: AbortSignal.timeout(15000),
      })
    } catch (e) {
      // Connection refused / DNS failure / timeout: the API is unreachable.
      throw new Error(
        `could not reach the freehire API at ${baseUrl()} (${e instanceof Error ? e.message : String(e)})`,
      )
    }

    if (response.status === 429 || response.status >= 500) {
      if (attempt === maxRetries) {
        throw new Error(`freehire API request failed: ${response.status} ${response.statusText}`)
      }
      const backoff = delay + Math.floor(Math.random() * 500)
      const retryAfter = retryAfterMs(response.headers.get("Retry-After"))
      const wait = Math.max(backoff, retryAfter ?? 0)
      if (waited + wait > RETRY_BUDGET_MS) {
        throw new Error(
          `freehire API request failed: ${response.status} ${response.statusText}; ` +
            `the server asked to retry in ${Math.ceil(wait / 1000)}s, past this CLI's ${RETRY_BUDGET_MS / 1000}s retry budget`,
        )
      }
      await sleep(wait)
      waited += wait
      delay = Math.min(delay * 2, 8000)
      continue
    }
    if (response.status === 404) return null

    // Read the body once, tolerantly: an error response's JSON gives us its
    // `error` message; a 2xx must parse (a malformed one is surfaced, not swallowed).
    const body = (await response.json().catch(() => null)) as Envelope<T> | null
    if (!response.ok) {
      throw new Error(body?.error || `freehire API request failed: ${response.status} ${response.statusText}`)
    }
    if (!body) throw new Error("freehire API returned an unparseable response body")
    return body
  }
  // Unreachable in practice; the loop returns or throws on the last attempt.
  throw new Error("freehire API request failed after retries")
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}

/**
 * A freehire job — the fields this skill reads (the wire shape carries more).
 */
export interface FreehireJob {
  public_slug: string
  source: string
  external_id: string
  url: string
  title: string
  company: string
  company_slug: string
  location: string
  description: string
  skills: string[]
  work_mode?: string
  regions: string[]
  countries: string[]
  cities: string[]
  posted_at: string | null
  created_at: string | null
  // Always present in the wire shape (an unenriched job serializes it as `{}`);
  // the individual fields are what may be absent.
  enrichment: {
    seniority?: string
    category?: string
    employment_type?: string
    salary_min?: number
    salary_max?: number
    salary_currency?: string
    salary_period?: string // e.g. "year", "month"
  }
}

/**
 * A search result in the portal-skill contract shape. `id` is the public_slug
 * (what `detail <slug>` consumes) and `date` is the posting date as `YYYY-MM-DD`
 * (the contract shape every portal CLI emits; the API's ISO timestamp is cut to
 * its date part); missing values are `null`, never omitted. The extra facet
 * fields are a permitted superset.
 *
 * `description` is the posting's full text in the format the search asked the API
 * for — the agent search endpoint hydrates it server-side, so it arrives already
 * rendered and is passed through verbatim rather than run through `cleanHtml`.
 */
export interface JobResult {
  id: string
  title: string
  company: string | null
  company_slug: string | null
  location: string | null
  date: string | null
  url: string
  work_mode: string | null
  regions: string[]
  countries: string[]
  skills: string[]
  description: string | null
}

/** A job detail: the search result plus the cleaned description and enrichment. */
export interface JobDetailResult extends JobResult {
  cities: string[]
  seniority: string | null
  category: string | null
  employment_type: string | null
  salary: string | null
  description: string | null
}

/**
 * Reshape a freehire job into the contract search-result fields. The title comes
 * from the source ATS as indexed, which can carry HTML entities and stray
 * whitespace ("Intern - Fullstack &amp; AI Innovation "), so it is decoded and trimmed.
 */
/**
 * The portal-contract date (`YYYY-MM-DD`) from the API's `posted_at`, which is
 * an ISO timestamp (`2026-07-06T00:00:00Z`). Every other portal CLI emits the
 * date part only, and `/scrape` stores it as `posted_date: "YYYY-MM-DD"`; the
 * table and plain renderers already cut it, so only JSON consumers saw the raw
 * timestamp. Null when absent or not a date.
 */
export function contractDate(value: string | null | undefined): string | null {
  const m = /^(\d{4}-\d{2}-\d{2})/.exec(value ?? "")
  return m ? m[1] : null
}

export function toResult(j: FreehireJob): JobResult {
  return {
    id: j.public_slug,
    title: decodeHtmlEntities(j.title || "").trim() || "(untitled)",
    company: j.company || null,
    company_slug: j.company_slug || null,
    location: j.location || null,
    date: contractDate(j.posted_at),
    url: j.url,
    work_mode: j.work_mode || null,
    regions: j.regions,
    countries: j.countries,
    skills: j.skills,
    description: j.description ? visibleHtml(j.description) : null,
  }
}

/** Reshape a freehire job into the detail result (adds cleaned description + enrichment). */
export function toDetail(j: FreehireJob): JobDetailResult {
  const e = j.enrichment
  return {
    ...toResult(j),
    cities: j.cities,
    seniority: e.seniority || null,
    category: e.category || null,
    employment_type: e.employment_type || null,
    salary: formatSalary(e),
    description: cleanHtml(j.description),
  }
}

/**
 * Human-readable salary line from the enrichment fields, or null when absent.
 * The period is kept when freehire records one ("INR 300000–300000/year"):
 * without it a yearly figure is indistinguishable from a monthly one.
 */
function formatSalary(e: FreehireJob["enrichment"]): string | null {
  if (e.salary_min == null && e.salary_max == null) return null
  const cur = e.salary_currency ? `${e.salary_currency} ` : ""
  const per = e.salary_period ? `/${e.salary_period}` : ""
  if (e.salary_min != null && e.salary_max != null) return `${cur}${e.salary_min}–${e.salary_max}${per}`
  return `${cur}${e.salary_min ?? e.salary_max}${per}`
}

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

/**
 * Strip a freehire description's HTML into readable prose: block/line-break tags
 * become newlines, entities are decoded, tags removed. Null for empty input.
 */
export function cleanHtml(html: string | null | undefined): string | null {
  if (!html) return null
  const withBreaks = visibleHtml(html)
    .replace(/<\s*br\s*\/?>/gi, "\n")
    .replace(/<\/(p|li|ul|ol|div|h\d)>/gi, "\n")
  const text = decodeHtmlEntities(withBreaks.replace(/<[^>]+>/g, " "))
    .replace(/[ \t]+/g, " ")
    .replace(/ *\n */g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim()
  return text || null
}

const FREEHIRE_HOST = /(^|\.)freehire\.me$/i

/** True for freehire.me, any of its subdomains, or the configured self-hosted base URL's host. */
function isFreehireHost(host: string): boolean {
  if (FREEHIRE_HOST.test(host)) return true
  try {
    const self = new URL(baseUrl()).hostname.toLowerCase()
    return host === self || host.endsWith(`.${self}`)
  } catch {
    return false
  }
}

/**
 * Extract a freehire public slug from a bare slug or a freehire `/jobs/<slug>` URL.
 *
 * A URL on any other host is rejected. The previous pattern took the segment
 * after `/jobs/` from ANY string, so a Greenhouse apply link - which is what a
 * freehire search result's own `url` field carries - was reduced to its last
 * path segment and `detail` asked the API for a slug that never existed,
 * reporting "job not found" for a URL that was never a freehire posting. URLs
 * are parsed for real, so look-alike hosts (freehire.me.evil.io) and userinfo
 * tricks (freehire.me@evil.io) fall on the reject side; FREEHIRE_API_URL's host
 * is accepted for a self-hosted instance.
 */
export function normalizeSlug(input: string): string | null {
  const trimmed = input.trim()
  if (!trimmed) return null
  const hasScheme = /^https?:\/\//i.test(trimmed)
  if (hasScheme || /^[a-z0-9.-]+\.[a-z]{2,}(\/|$)/i.test(trimmed)) {
    let parsed: URL
    try {
      parsed = new URL(hasScheme ? trimmed : `https://${trimmed}`)
    } catch {
      return null
    }
    if (!isFreehireHost(parsed.hostname.toLowerCase())) return null
    const m = parsed.pathname.match(/^\/jobs\/([a-z0-9][a-z0-9-]*)\/?$/i)
    return m ? m[1] : null
  }
  // A bare slug: lowercase alphanumerics and hyphens (no path/scheme).
  if (/^[a-z0-9][a-z0-9-]*$/i.test(trimmed)) return trimmed
  return null
}
