import { parse as parseHtml } from "node-html-parser"

export const BASE_URL = "https://jobbank.dk"

export const USER_AGENT = "Mozilla/5.0 (compatible; jobbank-cli/1.0)"

export function writeError(error: string, code: string): void {
  process.stderr.write(JSON.stringify({ error, code }) + "\n")
}

export async function fetchWithUA(url: string): Promise<Response> {
  const maxRetries = 6
  let delay = 500
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    const response = await fetch(url, {
      headers: { "User-Agent": USER_AGENT },
      signal: AbortSignal.timeout(15000),
    })
    if (response.status === 429 || response.status >= 500) {
      if (attempt === maxRetries) {
        throw new Error(`Request failed: ${response.status} ${response.statusText}`)
      }
      const jitter = Math.floor(Math.random() * 500)
      await new Promise((resolve) => setTimeout(resolve, delay + jitter))
      delay = Math.min(delay * 2, 5000)
      continue
    }
    return response
  }
  throw new Error("Request failed after max retries")
}

export interface RssItem {
  title: string
  description: string
  link: string
  pubDate: string
}

function extractCdata(xml: string, tag: string): string {
  // Try CDATA first
  const cdataRe = new RegExp(`<${tag}><\\!\\[CDATA\\[(.*?)\\]\\]><\\/${tag}>`, "s")
  const cdataMatch = xml.match(cdataRe)
  if (cdataMatch) return cdataMatch[1].trim()
  // Plain content
  const plainRe = new RegExp(`<${tag}>(.*?)<\\/${tag}>`, "s")
  const plainMatch = xml.match(plainRe)
  return plainMatch ? plainMatch[1].trim() : ""
}

function extractLink(xml: string): string {
  // <link> in RSS can conflict with atom namespace — extract text node after <link>
  // Try CDATA variant first
  const cdataMatch = xml.match(/<link><!\[CDATA\[(.*?)\]\]><\/link>/s)
  if (cdataMatch) return cdataMatch[1].trim()
  // Plain link
  const plainMatch = xml.match(/<link>(.*?)<\/link>/s)
  if (plainMatch) return plainMatch[1].trim()
  // Some RSS feeds put the URL as text after <link> without a closing tag (self-closing style)
  // Try matching href in atom:link
  return ""
}

function parseRssItems(xml: string): RssItem[] {
  const items: RssItem[] = []
  // Split on <item> boundaries
  const itemMatches = xml.matchAll(/<item>([\s\S]*?)<\/item>/g)
  for (const match of itemMatches) {
    const itemXml = match[1]
    const title = extractCdata(itemXml, "title")
    const description = extractCdata(itemXml, "description")
    const link = extractLink(itemXml)
    const pubDate = extractCdata(itemXml, "pubDate") || itemXml.match(/<pubDate>(.*?)<\/pubDate>/)?.[1]?.trim() || ""
    items.push({ title, description, link, pubDate })
  }
  return items
}

export async function rssFetch(params: Record<string, string | string[]>): Promise<RssItem[]> {
  const searchParams = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) {
      for (const v of value) {
        searchParams.append(key, v)
      }
    } else {
      searchParams.append(key, value)
    }
  }
  const url = `${BASE_URL}/job/rss?${searchParams.toString()}`
  const response = await fetchWithUA(url)
  if (!response.ok) {
    const body = await response.clone().text()
    if (response.status === 403 && /just a moment|cloudflare|cf-chl/i.test(body)) {
      throw new Error(
        "Jobbank is blocking automated requests with Cloudflare bot protection. Skip this portal or use the WebSearch fallback."
      )
    }
    throw new Error(`Failed to fetch RSS feed: ${response.status} ${response.statusText}`)
  }
  const xml = await response.text()
  return parseRssItems(xml)
}

export interface ParsedDescription {
  jobType: string
  company: string
  location: string
  deadline: string | null
}

export function parseRssDescription(desc: string): ParsedDescription {
  // Format: "JobType hos Company, Location (Ansøgningsfrist: DD.MM.YYYY)"
  // or: "JobType hos Company, Location (Ansøgningsfrist: løbende)"
  // or multiple types: "Fuldtidsjob, Graduate/trainee hos Company, Location (Ansøgningsfrist: ...)"

  let jobType = ""
  let company = ""
  let location = ""
  let deadline: string | null = null

  const hosIdx = desc.indexOf(" hos ")
  if (hosIdx === -1) {
    // Can't parse — return desc as company
    return { jobType: "", company: desc, location: "", deadline: null }
  }

  jobType = desc.substring(0, hosIdx).trim()
  let rest = desc.substring(hosIdx + 5) // skip " hos "

  // Extract deadline from parenthetical at the end
  const deadlineMatch = rest.match(/\(Ans[øo]gningsfrist:\s*(.*?)\)\s*$/)
  if (deadlineMatch) {
    const deadlineStr = deadlineMatch[1].trim()
    if (deadlineStr.toLowerCase() === "løbende" || deadlineStr.toLowerCase() === "lobende") {
      deadline = null
    } else {
      // The feed writes DD.MM.YYYY; the /scrape contract (and this CLI's own
      // detail command) use YYYY-MM-DD. Convert the known shape; anything else
      // passes through so an unexpected value stays visible downstream.
      const dmy = deadlineStr.match(/^(\d{2})\.(\d{2})\.(\d{4})$/)
      deadline = dmy ? `${dmy[3]}-${dmy[2]}-${dmy[1]}` : deadlineStr
    }
    // Remove the deadline portion from rest
    rest = rest.substring(0, deadlineMatch.index).trim()
  }

  // rest is now "Company, Location"
  // Split on first ", " to get company and location
  const firstComma = rest.indexOf(", ")
  if (firstComma !== -1) {
    company = rest.substring(0, firstComma).trim()
    location = rest.substring(firstComma + 2).trim()
  } else {
    company = rest.trim()
    location = ""
  }

  return { jobType, company, location, deadline }
}

export function normalizeJobId(input: string): string | null {
  const trimmed = input.trim()
  if (/^\d+$/.test(trimmed)) return trimmed
  let url: URL
  try {
    const hostLike = /^[a-z0-9.-]+\.[a-z]{2,}(\/|$)/i.test(trimmed)
    url = new URL(hostLike ? `https://${trimmed}` : trimmed)
  } catch {
    return null
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null
  if (url.hostname !== "jobbank.dk" && !url.hostname.endsWith(".jobbank.dk")) return null
  // A matching path on a different site is not a Jobbank posting. Read only
  // the parsed pathname, so a job URL embedded in a query is not an ID either.
  const match = url.pathname.match(/^\/job\/(\d+)(?:\/|$)/)
  return match ? match[1] : null
}

export function extractJobIdFromUrl(url: string): string {
  // URL format: https://jobbank.dk/job/{id}/{company-slug}/{title-slug}
  return normalizeJobId(url) ?? ""
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

function findJobPosting(value: unknown): Record<string, unknown> | null {
  if (Array.isArray(value)) {
    for (const item of value) {
      const jobPosting = findJobPosting(item)
      if (jobPosting) return jobPosting
    }
    return null
  }

  if (!value || typeof value !== "object") return null

  const record = value as Record<string, unknown>
  if (record["@type"] === "JobPosting") return record

  return findJobPosting(record["@graph"])
}

/**
 * The page's JobPosting JSON-LD, which is the employer's text: the HTML
 * description loses markup a browser never renders, and every string loses
 * invisible characters, before `detail` prints any of it.
 */
export function parseJobPostingJsonLd(html: string): Record<string, unknown> | null {
  const root = parseHtml(html)
  const scripts = root.querySelectorAll('script[type="application/ld+json"]')

  for (const script of scripts) {
    try {
      const jobPosting = findJobPosting(JSON.parse(script.text) as unknown)
      if (jobPosting) {
        const description = jobPosting["description"]
        return stripInvisible(
          typeof description === "string" ? { ...jobPosting, description: visibleHtml(description) } : jobPosting,
        )
      }
    } catch {
      // Invalid JSON-LD should not prevent later scripts from being checked.
    }
  }

  return null
}
