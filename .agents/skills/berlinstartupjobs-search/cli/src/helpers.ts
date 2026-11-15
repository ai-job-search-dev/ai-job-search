// Data source: Berlin Startup Jobs' public WordPress REST API (wp-json/wp/v2).
// Job postings are ordinary WordPress posts; company, location and category are
// taxonomies (job_company, job_location, category) that `_embed=wp:term` inlines,
// so one request returns everything a result row needs. No authentication.

export const BASE_URL = "https://berlinstartupjobs.com"
export const POSTS_URL = `${BASE_URL}/wp-json/wp/v2/posts`
export const PER_PAGE = 20
const FIELDS = "id,date,link,title,excerpt,content,_links,_embedded"

export function writeError(error: string, code: string): void {
  process.stderr.write(JSON.stringify({ error, code }) + "\n")
}

const UA = "Mozilla/5.0 (compatible; berlinstartupjobs-cli/1.0)"

export interface FetchResult {
  body: unknown
  total: number | null
}

/** GET JSON with exponential backoff + jitter on 429/5xx. Returns body null on a 404. */
export async function jsonFetch(url: string): Promise<FetchResult> {
  const maxRetries = 6
  let delay = 500
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    const response = await fetch(url, {
      headers: { "User-Agent": UA, Accept: "application/json" },
      redirect: "follow",
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
    if (response.status === 404) return { body: null, total: null }
    // WordPress answers a page past the last one with 400 rest_post_invalid_page_number:
    // that is an empty page, not a failure.
    if (response.status === 400) {
      const err = await response.json().catch(() => null)
      if (err && typeof err === "object" && (err as { code?: unknown }).code === "rest_post_invalid_page_number") {
        return { body: [], total: null }
      }
      throw new Error(`Request failed: 400 ${response.statusText}`)
    }
    if (!response.ok) {
      throw new Error(`Request failed: ${response.status} ${response.statusText}`)
    }
    const totalHeader = response.headers.get("x-wp-total")
    const total = totalHeader !== null && /^\d+$/.test(totalHeader) ? Number(totalHeader) : null
    return { body: await response.json(), total }
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
  category: string | null
  remote: boolean
}

export interface JobDetail extends Job {
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
  const withBreaks = visibleHtml(html)
    .replace(/\s+/g, " ")
    .replace(/<\s*br\s*\/?>/gi, "\n")
    .replace(/<li\b[^>]*>/gi, "\n- ")
    .replace(/<\/(p|li|ul|ol|div|h\d)>/gi, "\n")
  const text = withBreaks
    .split("\n")
    .map((line) => (line.startsWith("- ") ? "- " + clean(line.slice(2)) : clean(line)))
    .filter((line, i, arr) => !(line === "- " || (line === "" && arr[i - 1] === "")))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    // Keep consecutive bullets together: </li> plus the next <li> leave a blank line between them.
    .replace(/^(- .*)\n\n(?=- )/gm, "$1\n")
    .replace(/^(- .*)\n\n(?=- )/gm, "$1\n")
    .trim()
  return text || null
}

type Rec = Record<string, unknown>
const isRec = (v: unknown): v is Rec => v !== null && typeof v === "object" && !Array.isArray(v)

function rendered(v: unknown): string {
  return isRec(v) && typeof v.rendered === "string" ? v.rendered : ""
}

/** Collect embedded taxonomy term names by taxonomy. */
function termNames(post: Rec): Record<string, string[]> {
  const out: Record<string, string[]> = {}
  const emb = isRec(post._embedded) ? post._embedded["wp:term"] : undefined
  if (!Array.isArray(emb)) return out
  for (const group of emb) {
    if (!Array.isArray(group)) continue
    for (const t of group) {
      if (!isRec(t) || typeof t.taxonomy !== "string" || typeof t.name !== "string") continue
      ;(out[t.taxonomy] ??= []).push(clean(t.name))
    }
  }
  return out
}

/**
 * Parse one WordPress post into a Job. Returns null for anything that is not a
 * usable posting, so one malformed entry cannot break the rest.
 * Titles carry the company after " // " ("Tech Lead (m/f/d) // Superchat");
 * the job_company term is preferred, the title suffix is the fallback.
 */
export function parsePost(post: unknown): Job | null {
  if (!isRec(post)) return null
  const id = typeof post.id === "number" || typeof post.id === "string" ? String(post.id) : null
  if (!id || !/^\d+$/.test(id)) return null
  const rawTitle = clean(rendered(post.title))
  if (!rawTitle) return null
  const split = rawTitle.split(/\s+\/\/\s+/)
  const title = split.length > 1 ? split.slice(0, -1).join(" // ") : rawTitle
  const titleCompany = split.length > 1 ? split[split.length - 1] : null
  const terms = termNames(post)
  const locations = terms["job_location"] ?? []
  const link = typeof post.link === "string" && post.link.startsWith(BASE_URL) ? post.link : `${BASE_URL}/?p=${id}`
  return {
    id,
    title,
    company: terms["job_company"]?.[0] ?? titleCompany,
    location: locations.length ? locations.join(" / ") : null,
    date: typeof post.date === "string" ? post.date.slice(0, 10) : null,
    url: link,
    category: terms["category"]?.[0] ?? null,
    remote: locations.some((l) => /remote/i.test(l)),
  }
}

export function parsePosts(body: unknown): Job[] {
  if (!Array.isArray(body)) return []
  return body.map(parsePost).filter((j): j is Job => j !== null)
}

export function parseDetail(post: unknown): JobDetail | null {
  const job = parsePost(post)
  if (!job || !isRec(post)) return null
  return { ...job, description: htmlToText(rendered(post.content)) }
}

/** Build the search URL. jobage (days) maps to WordPress' `after` date filter. */
export function buildSearchUrl(opts: { query?: string; jobage?: number; page: number; now?: Date }): string {
  const params = new URLSearchParams()
  if (opts.query) params.set("search", opts.query)
  if (opts.jobage !== undefined) {
    const now = opts.now ?? new Date()
    const after = new Date(now.getTime() - opts.jobage * 86400000)
    params.set("after", after.toISOString().slice(0, 19))
  }
  params.set("per_page", String(PER_PAGE))
  params.set("page", String(opts.page))
  params.set("orderby", "date")
  params.set("_embed", "wp:term")
  params.set("_fields", FIELDS.replace(",content", ""))
  return `${POSTS_URL}?${params.toString()}`
}

export function buildDetailUrl(id: string): string {
  return `${POSTS_URL}/${id}?_embed=wp:term&_fields=${FIELDS}`
}

export function buildSlugUrl(slug: string): string {
  return `${POSTS_URL}?slug=${encodeURIComponent(slug)}&_embed=wp:term&_fields=${FIELDS}`
}
