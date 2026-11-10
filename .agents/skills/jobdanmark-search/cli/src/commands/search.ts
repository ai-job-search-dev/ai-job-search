import { defineCommand, option } from "@bunli/core"
import { z } from "zod"
import { apiPost, writeError, stripInvisible, BASE_URL } from "../helpers.js"

export interface ApiSearchItem {
  title: string
  companyName: string
  companyLogo: {
    key: string
    url: string
    focalPoint: { top: number; left: number } | null
  } | null
  companyLogoSvgMarkup: string | null
  overlayColor: string | null
  companyAddress: string | null
  jobTypes: string[]
  boostJob: boolean
  publishedDate: string
  applicationDeadline: string | null
  url: string
  coverImage: {
    key: string
    url: string
    focalPoint: { top: number; left: number } | null
  } | null
  silhouetteLogo: boolean
}

interface ApiSearchResponse {
  items: ApiSearchItem[]
  currentPage: number
  totalItems: number
  itemsPrPage: number
  totalPages: number
}

export function toContractDate(value: string | null): string | null {
  const match = value?.match(/^(\d{2})-(\d{2})-(\d{4})$/)
  return match ? `${match[3]}-${match[2]}-${match[1]}` : (value ?? null)
}

// Live companyAddress values put the city after the postcode either as
// "Lautruphoej 2, 2750 Ballerup" or "2670, Greve". The comma fallback
// requires a non-digit after the comma so a 4-digit street number
// ("Vejlevej 1234, 7100 Vejle") never wins over the real postcode.
export function extractCity(address: string | null): string | null {
  if (!address) return null
  const city =
    address.match(/\d{4}\s+(.+)$/)?.[1] ?? address.match(/\d{4}\s*,\s*([^\d,].*)$/)?.[1]
  const trimmed = city?.trim()
  return trimmed ? trimmed : null
}

export function normalizeItem(item: ApiSearchItem): Record<string, unknown> {
  const relativeUrl = item.url
  const fullUrl = relativeUrl.startsWith("http")
    ? relativeUrl
    : `${BASE_URL}${relativeUrl}`
  // Extract slug from url path: /job/<slug>
  const slug = relativeUrl.replace(/^\/job\//, "")

  // Presentation-only keys (coverImage, companyLogo, companyLogoSvgMarkup,
  // overlayColor, silhouetteLogo) are dropped: they were ~40% of a live
  // payload and the /scrape agent can never use an image or overlay colour.
  // The #340 compatibility duplicates (companyName, publishedDate,
  // applicationDeadline) stay.
  return stripInvisible({
    title: item.title,
    companyName: item.companyName,
    companyAddress: item.companyAddress,
    jobTypes: item.jobTypes,
    boostJob: item.boostJob,
    publishedDate: item.publishedDate,
    applicationDeadline: item.applicationDeadline ?? null,
    url: fullUrl,
    slug,
    // /scrape contract aliases (add-portal.md: every result carries at least
    // id, title, company, location, date, url). `id` is the slug - the value
    // `detail` takes - under the name every other portal CLI uses.
    id: slug,
    company: item.companyName,
    location: extractCity(item.companyAddress),
    date: toContractDate(item.publishedDate),
    deadline: toContractDate(item.applicationDeadline),
  })
}

export const search = defineCommand({
  name: "search",
  description: "Search job listings with filters",
  options: {
    text: option(z.string().optional(), {
      description: "Free-text keyword search (job title, keyword)",
    }),
    category: option(z.coerce.number().int().min(1).optional(), {
      description: "Category ID",
    }),
    "jobtitle-id": option(z.coerce.number().int().min(1).optional(), {
      description: "Job title ID from autocomplete results",
    }),
    municipality: option(z.string().optional(), {
      description: "Municipality name, e.g. Odense, København",
    }),
    zip: option(z.string().optional(), {
      description: "Zip code, e.g. 5000",
    }),
    region: option(z.string().optional(), {
      description: "Landsdel from locations (category region), e.g. Sjælland, Østjylland",
    }),
    "administrative-region": option(z.string().optional(), {
      description: "Administrative region from locations (category administrativeRegion), e.g. Region Midtjylland",
    }),
    "job-type": option(z.string().optional(), {
      description: "Comma-separated job types: fuldtid,deltid,fleksjob,elev,studiejob,praktik",
    }),
    page: option(z.coerce.number().int().min(1).default(1), {
      description: "Page number (30 items per page, server-enforced)",
    }),
    limit: option(z.coerce.number().int().min(1).optional(), {
      description: "Cap total results returned by CLI (client-side)",
    }),
    format: option(z.enum(["json", "table", "plain"]).default("json"), {
      description: "Output format: json, table, plain",
    }),
  },
  handler: async ({ flags, signal }) => {
    if (signal.aborted) return

    const filters: Array<{ type: string; value: string | number; displayText: string }> = []

    if (flags.text) {
      filters.push({ type: "freetext", value: flags.text, displayText: flags.text })
    }
    if (flags.category !== undefined) {
      filters.push({ type: "category", value: flags.category, displayText: String(flags.category) })
    }
    if (flags["jobtitle-id"] !== undefined) {
      filters.push({ type: "jobtitle", value: flags["jobtitle-id"], displayText: String(flags["jobtitle-id"]) })
    }
    if (flags.municipality) {
      filters.push({ type: "municipality", value: flags.municipality, displayText: flags.municipality })
    }
    if (flags.zip) {
      filters.push({ type: "zip", value: flags.zip, displayText: flags.zip })
    }
    if (flags.region) {
      filters.push({ type: "region", value: flags.region, displayText: flags.region })
    }
    // `locations` returns two kinds of region, and the API filters each by its
    // own type: a "region" filter holding "Region Midtjylland" matches no job.
    if (flags["administrative-region"]) {
      const value = flags["administrative-region"]
      filters.push({ type: "administrativeRegion", value, displayText: value })
    }

    const jobTypes = flags["job-type"]
      ? flags["job-type"].split(",").map((t) => t.trim()).filter(Boolean)
      : []

    const body = {
      jobTypes,
      filters,
      locationMode: "Text",
      distance: 50,
    }

    try {
      const data = await apiPost<ApiSearchResponse>(`/api/jobsearch/search/${flags.page}`, body)

      if (signal.aborted) return

      let results = data.items.map(normalizeItem)
      if (flags.limit !== undefined) {
        results = results.slice(0, flags.limit)
      }

      const meta = {
        currentPage: data.currentPage,
        totalItems: data.totalItems,
        itemsPrPage: data.itemsPrPage,
        totalPages: data.totalPages,
      }

      const output = { meta, results }

      if (flags.format === "json") {
        console.log(JSON.stringify(output, null, 2))
      } else if (flags.format === "table") {
        outputTable(results)
      } else {
        outputPlain(results)
      }
    } catch (err) {
      writeError(err instanceof Error ? err.message : String(err), "API_ERROR")
      process.exit(1)
    }
  },
})

function outputTable(results: Record<string, unknown>[]): void {
  console.log("title                                company                    date       url")
  for (const r of results) {
    const title = String(r.title ?? "-").substring(0, 35).padEnd(35)
    const company = String(r.companyName ?? "-").substring(0, 25).padEnd(25)
    const date = String(r.publishedDate ?? "-").padEnd(10)
    const url = String(r.url ?? "-")
    console.log(`${title} ${company} ${date} ${url}`)
  }
}

function outputPlain(results: Record<string, unknown>[]): void {
  for (const r of results) {
    console.log(`title: ${r.title}`)
    console.log(`company: ${r.companyName}`)
    console.log(`address: ${r.companyAddress}`)
    console.log(`jobTypes: ${Array.isArray(r.jobTypes) ? r.jobTypes.join(", ") : "-"}`)
    console.log(`published: ${r.publishedDate}`)
    console.log(`deadline: ${r.applicationDeadline ?? "N/A"}`)
    console.log(`url: ${r.url}`)
    console.log("")
  }
}
