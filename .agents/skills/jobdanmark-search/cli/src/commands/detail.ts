import { defineCommand, option } from "@bunli/core"
import { z } from "zod"
import { parse } from "node-html-parser"
import { BASE_URL, htmlFetch, normalizeSlug, stripInvisible, visibleHtml, writeError } from "../helpers.js"
import { extractCity, toContractDate } from "./search.js"

interface JsonLdJobPosting {
  "@context"?: string
  "@type"?: string
  title?: string
  datePosted?: string
  validThrough?: string
  employmentType?: string | string[]
  hiringOrganization?: {
    "@type"?: string
    name?: string
    logo?: string
  }
  jobLocation?: {
    "@type"?: string
    address?: {
      "@type"?: string
      streetAddress?: string
      addressLocality?: string
      addressRegion?: string
      postalCode?: string
      addressCountry?: string
    }
  }
  description?: string
}

interface DetailResult {
  slug: string
  url: string
  title: string
  datePosted: string
  validThrough: string | null
  employmentType: string[]
  hiringOrganization: {
    name: string
    logo: string | null
  }
  jobLocation: {
    streetAddress: string | null
    addressLocality: string | null
    addressRegion: string | null
    postalCode: string | null
    addressCountry: string | null
  }
  description: string
  applyUrl: string | null
}

// A malformed closing tag that reached the text layer. node-html-parser
// consumes well-formed markup (`<strong>`, `<br/>`, `</li >`) but does not
// recognise a closing tag with a space after the `<` or the slash, so `</ li >`
// survives as text; a live jobdanmark page emitted
// `employmentType: ["Fuldtid </ li >"]` that way. Only the closing shape is
// stripped: the parser decodes entities before this runs, so author text the
// page encoded as `&lt;div&gt;` or `&lt;T&gt;` arrives here as a literal
// `<div>` / `<T>` and must stay.
const STRAY_CLOSING_TAG = /<\s*\/\s*[a-z][a-z0-9-]*\s*>/gi

function cleanText(text: string): string {
  return text.replace(STRAY_CLOSING_TAG, " ").replace(/\s+/g, " ").trim()
}

/**
 * JSON-LD descriptions arrive as HTML (schema.org allows it, jobdanmark uses
 * it). Render them to the same newline-separated plain text the rendered-HTML
 * branch produces, so `detail` emits one description shape whichever page
 * layout it hit: tags gone, entities decoded, one line per block element.
 */
function descriptionToText(html: string | undefined): string {
  if (!html) return ""
  return parse(visibleHtml(html))
    .structuredText.split("\n")
    .map(cleanText)
    .filter(Boolean)
    .join("\n")
}

function normalizeUrl(value: string | null | undefined): string | null {
  if (!value) return null
  const decoded = value.replace(/&amp;/g, "&")
  if (decoded.startsWith("http")) return decoded
  if (decoded.startsWith("/")) return `${BASE_URL}${decoded}`
  return decoded
}

function findJobPostingJsonLd(root: ReturnType<typeof parse>): JsonLdJobPosting | null {
  const ldJsonScripts = root.querySelectorAll('script[type="application/ld+json"]')

  for (const script of ldJsonScripts) {
    try {
      const parsed = JSON.parse(script.text) as unknown
      if (isJobPosting(parsed)) return parsed
      if (Array.isArray(parsed)) {
        const found = parsed.find(isJobPosting)
        if (found) return found
      }
    } catch {
      // Continue to next script.
    }
  }

  return null
}

function isJobPosting(value: unknown): value is JsonLdJobPosting {
  return Boolean(value && typeof value === "object" && (value as JsonLdJobPosting)["@type"] === "JobPosting")
}

function fromJsonLd(jobPosting: JsonLdJobPosting, slug: string, url: string): DetailResult {
  const hiringOrg = jobPosting.hiringOrganization
  const address = jobPosting.jobLocation?.address

  const employmentType = Array.isArray(jobPosting.employmentType)
    ? jobPosting.employmentType
    : jobPosting.employmentType
      ? [jobPosting.employmentType]
      : []

  return {
    slug,
    url,
    title: jobPosting.title ?? "",
    datePosted: jobPosting.datePosted ?? "",
    validThrough: jobPosting.validThrough ?? null,
    employmentType,
    hiringOrganization: {
      name: hiringOrg?.name ?? "",
      logo: hiringOrg?.logo ?? null,
    },
    jobLocation: {
      streetAddress: address?.streetAddress ?? null,
      addressLocality: address?.addressLocality ?? null,
      addressRegion: address?.addressRegion ?? null,
      postalCode: address?.postalCode ?? null,
      addressCountry: address?.addressCountry ?? null,
    },
    description: descriptionToText(jobPosting.description),
    applyUrl: null,
  }
}

/**
 * Normalize a date read from the rendered page's overview list. The page
 * writes DD-MM-YYYY (sometimes with a trailing time, "02-08-2026 23.59"),
 * and the deadline can be the free-text "Løbende" (rolling) - jobbank maps
 * its equivalent to null, and every consumer does date arithmetic on the
 * value. The JSON-LD branch gets schema.org ISO dates and needs none of this.
 */
function normalizeOverviewDate(value: string | null): string | null {
  if (!value) return null
  const trimmed = value.trim()
  if (/^løbende$/iu.test(trimmed)) return null
  const match = trimmed.match(/^(\d{2})-(\d{2})-(\d{4})/)
  return match ? `${match[3]}-${match[2]}-${match[1]}` : toContractDate(trimmed)
}

function overviewValue(root: ReturnType<typeof parse>, label: string): string | null {
  const normalizedLabel = label.toLowerCase()
  for (const item of root.querySelectorAll(".job-overview li")) {
    const strong = item.querySelector("strong")
    const itemLabel = cleanText(strong?.text ?? "").replace(/:$/, "").toLowerCase()
    if (itemLabel !== normalizedLabel) continue

    const value = cleanText(item.text.replace(strong?.text ?? "", ""))
    return value.replace(/^:\s*/, "") || null
  }
  return null
}

function fromRenderedHtml(root: ReturnType<typeof parse>, slug: string, url: string): DetailResult {
  const pageTitle = cleanText(root.querySelector("title")?.text ?? "")
  const titleLower = pageTitle.toLowerCase()
  const bodyText = root.text.toLowerCase()
  if (
    bodyText.includes("siden blev ikke fundet") ||
    titleLower.startsWith("404") ||
    titleLower.includes("page not found") ||
    titleLower.includes("siden blev ikke fundet")
  ) {
    throw new Error("NOT_FOUND")
  }

  const title =
    cleanText(root.querySelector(".job-list-details .title")?.text ?? "") ||
    cleanText(root.querySelector("h1")?.text ?? "") ||
    cleanText(root.querySelector("h3")?.text ?? "").replace(/\s+\|\s+jobdanmark$/, "")

  if (!title) {
    throw new Error("Failed to parse job page HTML")
  }

  const companyLink = root.querySelector('.job-details-head a[href^="/virksomheder/"]')
  const companyName = cleanText(companyLink?.text ?? "")
  const logoSrc =
    root.querySelector(".company-logo img")?.getAttribute("src") ??
    root.querySelector(".company-logo source")?.getAttribute("srcset")?.split(/\s+/)[0]
  const workplace = overviewValue(root, "Arbejdssted")

  const description = root
    .querySelectorAll(".job-list-details p, .job-list-details li")
    .map((node) => cleanText(node.text))
    .filter(Boolean)
    .join("\n")

  const employmentType = overviewValue(root, "Jobtype")
  const applyUrl = normalizeUrl(root.querySelector("a.action.primary")?.getAttribute("href"))

  return {
    slug,
    url,
    title,
    datePosted: normalizeOverviewDate(overviewValue(root, "Udgivet")) ?? "",
    validThrough: normalizeOverviewDate(overviewValue(root, "Ansøgningsfrist")),
    employmentType: employmentType ? [employmentType] : [],
    hiringOrganization: {
      name: companyName,
      logo: normalizeUrl(logoSrc),
    },
    jobLocation: {
      streetAddress: workplace,
      addressLocality: extractCity(workplace),
      addressRegion: null,
      postalCode: null,
      addressCountry: "DK",
    },
    description,
    applyUrl,
  }
}

export function parseJobPostingFromHtml(html: string, slug: string, url: string): DetailResult {
  const jobPosting = findJobPostingJsonLd(parse(html))
  // The rendered branch reads text nodes, so it parses the page without the
  // markup a browser never shows; the JSON-LD lookup needs the scripts.
  return stripInvisible(
    jobPosting ? fromJsonLd(jobPosting, slug, url) : fromRenderedHtml(parse(visibleHtml(html)), slug, url),
  )
}

export const detail = defineCommand({
  name: "detail",
  description: "Full detail for a single job posting (by slug)",
  options: {
    format: option(z.enum(["json", "plain"]).default("json"), {
      description: "Output format: json, plain",
    }),
  },
  handler: async ({ flags, positional, signal }) => {
    if (signal.aborted) return

    const rawSlug = positional[0]
    if (!rawSlug) {
      writeError("slug argument is required", "MISSING_REQUIRED")
      process.exit(1)
    }

    const slug = normalizeSlug(rawSlug)
    if (!slug) {
      writeError(`Could not extract slug from "${rawSlug}"`, "BAD_ID")
      process.exit(1)
    }

    const url = `${BASE_URL}/job/${slug}`

    try {
      // htmlFetch carries the portal contract's 429/5xx backoff, the request
      // timeout, and the shared User-Agent; a bare fetch() here had none.
      const html = await htmlFetch(url)

      if (html === null) {
        writeError("Job not found", "NOT_FOUND")
        process.exit(1)
      }

      if (signal.aborted) return

      const output = parseJobPostingFromHtml(html, slug, url)

      if (flags.format === "json") {
        console.log(JSON.stringify(output, null, 2))
      } else {
        outputPlain(output)
      }
    } catch (err) {
      if (err instanceof Error && err.message.includes("NOT_FOUND")) {
        writeError("Job not found", "NOT_FOUND")
      } else if (err instanceof Error && err.message.includes("Failed to parse")) {
        writeError(err.message, "PARSE_ERROR")
      } else {
        writeError(err instanceof Error ? err.message : String(err), "API_ERROR")
      }
      process.exit(1)
    }
  },
})

function outputPlain(data: DetailResult): void {
  console.log(`slug: ${data.slug}`)
  console.log(`url: ${data.url}`)
  console.log(`title: ${data.title}`)
  console.log(`datePosted: ${data.datePosted}`)
  console.log(`validThrough: ${data.validThrough ?? "N/A"}`)
  const empType = Array.isArray(data.employmentType) ? data.employmentType.join(", ") : "-"
  console.log(`employmentType: ${empType}`)
  const org = data.hiringOrganization
  console.log(`company: ${org.name}`)
  const loc = data.jobLocation
  console.log(`location: ${[loc.streetAddress, loc.addressLocality, loc.postalCode, loc.addressCountry].filter(Boolean).join(", ")}`)
  if (data.applyUrl) console.log(`applyUrl: ${data.applyUrl}`)
  console.log(`description: ${data.description}`)
}
