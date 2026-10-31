import { defineCommand, option } from "@bunli/core"
import { z } from "zod"
import { htmlFetch, stripInvisible, visibleHtml, writeError } from "../helpers.js"

const BASE_URL = "https://www.jobindex.dk"

interface DetailResult {
  id: string
  title: string
  company: string | null
  companyUrl: string | null
  location: string | null
  date: string | null
  deadline: string | null
  employmentType: string | null
  hours: string | null
  applyUrl: string | null
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

/**
 * Decode HTML entities in text
 */
function decodeHtmlEntities(text: string): string {
  return text
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&apos;/g, "'")
    // Danish letters appear as named entities in employer-hosted ad markup.
    .replace(/&oslash;/g, "ø")
    .replace(/&Oslash;/g, "Ø")
    .replace(/&aelig;/g, "æ")
    .replace(/&AElig;/g, "Æ")
    .replace(/&aring;/g, "å")
    .replace(/&Aring;/g, "Å")
    // Numeric character references: decimal (&#233;) and hexadecimal (&#xE9;).
    .replace(/&#(\d+);/g, (_, dec) => numericEntity(parseInt(dec, 10)))
    .replace(/&#[xX]([0-9a-fA-F]+);/g, (_, hex) => numericEntity(parseInt(hex, 16)))
    .replace(/&nbsp;/g, " ")
}

/**
 * Strip HTML tags from text
 */
function stripTags(html: string): string {
  return html.replace(/<[^>]+>/g, "").trim()
}

/**
 * Parse a detail invocation's <id|url> into a canonical fetch target, or null.
 *
 * This is the gate between a stored (untrusted) URL and a network fetch, so it
 * must never trust the raw string: the previous version fetched any http(s)
 * URL verbatim and, when the path didn't match, used the whole input URL as
 * the id - a non-posting page (a redirect target, a look-alike host, the
 * homepage) came back as a well-formed fake posting with exit 0 (#447). A URL
 * input now needs a jobindex.dk host (apex or subdomain) and a
 * /jobannonce/<id> path, and the fetch URL is rebuilt from the extracted id -
 * the canonical short form the bare-id path always used. A bare id stays a
 * permissive scheme- and slash-free token (the jobnet precedent): the server
 * 404s unknowns loudly, which is the honest failure. Exported for tests.
 */
export function buildUrl(idOrUrl: string): { url: string; id: string } | null {
  const trimmed = idOrUrl.trim()
  if (/^https?:\/\//i.test(trimmed)) {
    let host: string
    try {
      host = new URL(trimmed).hostname.toLowerCase()
    } catch {
      return null
    }
    if (host !== "jobindex.dk" && !host.endsWith(".jobindex.dk")) return null
    // Match IDs like h1647303, r13677312, etc.
    const match = trimmed.match(/\/jobannonce\/([a-zA-Z]\d+)/)
    if (!match) return null
    return { url: `${BASE_URL}/jobannonce/${match[1]}`, id: match[1] }
  }
  if (/^[a-zA-Z0-9_-]+$/.test(trimmed)) {
    return { url: `${BASE_URL}/jobannonce/${trimmed}`, id: trimmed }
  }
  return null
}

const DANISH_MONTHS: Record<string, string> = {
  januar: "01", februar: "02", marts: "03", april: "04", maj: "05", juni: "06",
  juli: "07", august: "08", september: "09", oktober: "10", november: "11", december: "12",
}

/**
 * Normalize a date found on a detail page to YYYY-MM-DD, or null.
 * Live pages carry three shapes: ISO, DD-MM-YYYY (the hr-manager widget),
 * and Danish long form ("13. september 2026", the jobindex-native facts box).
 */
export function toIsoDate(value: string | null | undefined): string | null {
  if (!value) return null
  const text = value.trim()
  let m = text.match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (m) return `${m[1]}-${m[2]}-${m[3]}`
  m = text.match(/^(\d{2})-(\d{2})-(\d{4})/)
  if (m) return `${m[3]}-${m[2]}-${m[1]}`
  m = text.match(/^(\d{1,2})\.?\s+([a-zæøå]+)\s+(\d{4})/i)
  if (m) {
    const month = DANISH_MONTHS[m[2].toLowerCase()]
    if (month) return `${m[3]}-${month}-${m[1].padStart(2, "0")}`
  }
  return null
}

function metaContent(html: string, matcher: string): string | null {
  const re = new RegExp(
    `<meta[^>]+(?:property|name|itemprop)="${matcher}"[^>]+content="([^"]*)"|<meta[^>]+content="([^"]*)"[^>]+(?:property|name|itemprop)="${matcher}"`,
    "i",
  )
  const m = html.match(re)
  const value = m ? (m[1] ?? m[2]) : null
  return value ? decodeHtmlEntities(value).trim() || null : null
}

/** The text of the <p> inside a jobindex-native jd-* facts block. */
function jdBlockValue(html: string, cls: string): string | null {
  const m = html.match(new RegExp(`class="${cls}"[^>]*>[\\s\\S]*?<p[^>]*>([\\s\\S]*?)</p>`, "i"))
  return m ? decodeHtmlEntities(stripTags(m[1])).replace(/\s+/g, " ").trim() || null : null
}

/** Drop head content and unrendered markup so text scans never read CSS, JS or hidden text. */
function visibleBody(html: string): string {
  return visibleHtml(html.replace(/<head[\s\S]*?<\/head>/gi, ""))
}

function bodyText(html: string): string | null {
  const text = decodeHtmlEntities(stripTags(visibleBody(html).replace(/<(br|\/p|\/div|\/li|\/h[1-6])[^>]*>/gi, "\n")))
    .split("\n")
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter(Boolean)
    .join("\n")
  return text || null
}

/**
 * Parse a live detail page. Jobindex serves two shapes (verified live
 * 2026-08-19; the selectors the previous parser used exist in neither):
 *
 * - the jobindex-native shape, recognisable by its `jd-*` facts blocks
 *   (jd-deadline, jd-location, ...), with the company as the `<title>`
 *   prefix ("COMPANY - Job title");
 * - an external ATS passthrough (hr-manager/Talentech and similar), where
 *   the page IS the employer's hosted ad: `og:url` points at the ATS,
 *   `og:site_name` is the ATS brand, and there is no reliable company
 *   anchor at all - so `company` is honestly null there, never the ATS.
 *
 * `id` and `url` are always the caller's jobindex id and its jobannonce
 * URL: the canonical/og:url on these pages is the external ATS, and
 * storing that broke /scrape's "store a URL that resolves to the posting".
 */
export function parseDetailPage(html: string, url: string, id: string): DetailResult {
  const isNative = html.includes('class="jd-')

  const ogTitle = metaContent(html, "og:title")
  const itempropName = metaContent(html, "name")
  const h1 = html.match(/<h1[^>]*>([\s\S]*?)<\/h1>/i)
  const h1Title = h1 ? decodeHtmlEntities(stripTags(h1[1])).replace(/\s+/g, " ").trim() : null
  const title = ogTitle ?? itempropName ?? h1Title ?? ""
  if (!title) {
    throw new Error("Failed to parse job listing HTML")
  }

  // Company: only the native shape carries one - as the <title> prefix,
  // "VELLIV - Udvikler til Camunda/AWS". Require the suffix to be the job
  // title so an unrelated <title> never becomes a company name.
  let company: string | null = null
  if (isNative) {
    const titleTag = html.match(/<title>([\s\S]*?)<\/title>/i)
    const pageTitle = titleTag ? decodeHtmlEntities(titleTag[1]).replace(/\s+/g, " ").trim() : ""
    if (pageTitle.endsWith(` - ${title}`)) {
      company = pageTitle.slice(0, -(title.length + 3)).trim() || null
    }
  }

  let location: string | null = null
  let deadline: string | null = null
  let employmentType: string | null = null
  let hours: string | null = null
  let description: string | null = null

  if (isNative) {
    location = jdBlockValue(html, "jd-location")
    deadline = toIsoDate(jdBlockValue(html, "jd-deadline"))
    employmentType = jdBlockValue(html, "jd-type")
    hours = jdBlockValue(html, "jd-workhours")
    const desc = visibleHtml(html).match(/class="jd-description"[^>]*>([\s\S]*?)<\/div>/i)
    description = desc
      ? decodeHtmlEntities(stripTags(desc[1])).replace(/\s+/g, " ").trim() || null
      : null
  } else {
    // hr-manager-style widget: a rowheader label followed by the value span.
    const workplace = visibleBody(html).match(
      /class="workplace[^"]*"[\s\S]*?<span class="empty">([\s\S]*?)<\/span>/i,
    )
    location = workplace
      ? decodeHtmlEntities(stripTags(workplace[1])).replace(/\s+/g, " ").trim() || null
      : null

    // Deadline: label + a real date within range, scanned only over visible
    // markup - the label also appears inside a CSS comment on these pages,
    // which the previous parser captured verbatim as the deadline.
    const due = visibleBody(html).match(
      /(?:Ansøgningsfrist|Application\s*due|Frist)[\s\S]{0,300}?(\d{2}-\d{2}-\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}\.?\s+[a-zæøå]+\s+\d{4})/i,
    )
    deadline = due ? toIsoDate(due[1]) : null

    description = bodyText(html)
    if (!description || description.length < 100) {
      description = metaContent(html, "og:description") ?? metaContent(html, "description") ?? description
    }
  }

  if (!description) {
    description = metaContent(html, "og:description")
  }

  // Apply URL: jobindex's own /c?t= redirect when present.
  let applyUrl: string | null = null
  const ctMatch = html.match(/href="(\/c\?t=[^"]+)"/)
  if (ctMatch) {
    applyUrl = `${BASE_URL}${decodeHtmlEntities(ctMatch[1])}`
  }

  const timeMatch = html.match(/<time[^>]+datetime="([^"]+)"/)

  return stripInvisible({
    id,
    title,
    company,
    companyUrl: null,
    location,
    date: timeMatch ? toIsoDate(timeMatch[1]) : null,
    deadline,
    employmentType,
    hours,
    applyUrl,
    url,
    description,
  })
}

export const detail = defineCommand({
  name: "detail",
  description: "Fetch full job listing detail by ID or URL",
  options: {
    format: option(z.enum(["json", "plain"]).default("json"), {
      description: "Output format: json, plain",
    }),
  },
  handler: async ({ positional, flags, signal }) => {
    if (signal.aborted) return

    const idArg = positional[0]
    if (!idArg) {
      writeError("Job ID or URL is required", "MISSING_REQUIRED")
      process.exit(1)
    }

    const parsed = buildUrl(idArg)
    if (!parsed) {
      writeError(
        `Could not parse a jobindex job id or jobannonce URL from "${idArg}"`,
        "BAD_ID",
      )
      process.exit(1)
    }
    const { url, id } = parsed

    try {
      const html = await htmlFetch(url)

      if (signal.aborted) return

      let data: DetailResult
      try {
        data = parseDetailPage(html, url, id)
      } catch (parseErr) {
        const msg = parseErr instanceof Error ? parseErr.message : String(parseErr)
        writeError(msg, "PARSE_ERROR")
        process.exit(1)
      }

      // Verify it's a valid job page (has a title)
      if (!data.title) {
        writeError("Failed to parse job listing HTML", "PARSE_ERROR")
        process.exit(1)
      }

      if (flags.format === "json") {
        console.log(JSON.stringify(data, null, 2))
      } else {
        outputPlain(data)
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      if (message.includes("Job not found") || message.includes("404") || message.includes("NOT_FOUND")) {
        writeError("Job not found", "NOT_FOUND")
      } else if (message.includes("Failed to parse") || message.includes("PARSE_ERROR")) {
        writeError(message, "PARSE_ERROR")
      } else {
        writeError(message, "API_ERROR")
      }
      process.exit(1)
    }
  },
})

function outputPlain(data: DetailResult): void {
  console.log(`id: ${data.id}`)
  console.log(`title: ${data.title}`)
  console.log(`company: ${data.company ?? "-"}`)
  console.log(`location: ${data.location ?? "-"}`)
  console.log(`date: ${data.date ?? "-"}`)
  console.log(`deadline: ${data.deadline ?? "-"}`)
  console.log(`employmentType: ${data.employmentType ?? "-"}`)
  console.log(`hours: ${data.hours ?? "-"}`)
  console.log(`applyUrl: ${data.applyUrl ?? "-"}`)
  console.log(`url: ${data.url}`)
  console.log("")
  if (data.description) {
    console.log(data.description)
  }
}
