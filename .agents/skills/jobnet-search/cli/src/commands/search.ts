import { defineCommand, option } from "@bunli/core"
import { z } from "zod"
import { apiFetch, stripInvisible, writeError } from "../helpers.js"

export interface SearchApiResponse {
  jobAds: JobAdRaw[]
  searchFacets: SearchFacetsRaw
  totalJobAdCount: number
  searchString: string | null
}

export interface JobAdRaw {
  jobAdId: string
  title: string
  hiringOrgName: string
  occupation: string | null
  municipality: string | null
  postalCode: number | null
  postalDistrictName: string | null
  country: string
  // A TypeScript claim is not runtime validation: apiFetch casts the JSON
  // body, so a null here arrives typed as string and .slice() throws,
  // killing the whole search as API_ERROR (#418). Typed nullable so the
  // compiler enforces the guard below.
  publicationDate: string | null
  applicationDeadline: string | null
  applicationDeadlineStatus: string | null
  workHourPartTime: boolean
  isExternal: boolean
  jobAdUrl?: string | null
  hasLogo: boolean
  logoUrl: string | null
  cvr: string | null
  workPlaceAddress: string
  conceptUriDa?: string | null
  isSeen: boolean
  isFavorite: boolean
  description?: string
}

export interface SearchFacetsRaw {
  regions: Array<{ type: string; jobAdCount: number }>
  workHours: Array<{ type: string; jobAdCount: number }>
  employmentDurations: Array<{ type: string; jobAdCount: number }>
  occupationAreas: Array<{ identifier: string; jobAdCount: number }>
  countries: Array<{ label: string; identifier: string; jobAdCount: number }>
}

export interface SearchFlags {
  "search-string"?: string
  page: number
  "per-page": number
  order: string
  region?: string
  "work-hours"?: string
  duration?: string
  "job-type"?: string
  "postal-code"?: string
  radius: number
  "occupation-area"?: string
  "occupation-group"?: string
  occupation?: string
  limit?: number
}

export function buildSearchParams(flags: SearchFlags): Record<string, string> {
  const params: Record<string, string> = {
    resultsPerPage: String(flags["per-page"]),
    pageNumber: String(flags.page),
    orderType: flags.order,
  }

  if (flags["search-string"]) params["searchString"] = flags["search-string"]
  if (flags.region) params["regions"] = flags.region
  if (flags["work-hours"]) params["workHoursType"] = flags["work-hours"]
  if (flags.duration) params["employmentDurationType"] = flags.duration
  if (flags["job-type"]) params["jobAnnouncementType"] = flags["job-type"]
  if (flags["postal-code"]) {
    params["postalCode"] = flags["postal-code"]
    params["kmRadius"] = String(flags.radius)
  }
  if (flags["occupation-area"]) params["occupationAreas"] = flags["occupation-area"]
  if (flags["occupation-group"]) params["occupationGroups"] = flags["occupation-group"]
  if (flags.occupation) params["occupations"] = occupationId(flags.occupation)

  return params
}

/**
 * The search API filters one ESCO occupation by the UUID that ends the
 * `conceptUriDa` printed by `occupations`. Accept that full URI as well as the
 * bare UUID, so the value can be copied from either output unchanged.
 */
export function occupationId(value: string): string {
  const trimmed = value.trim().replace(/\/+$/, "")
  return trimmed.slice(trimmed.lastIndexOf("/") + 1)
}

/**
 * Return the destination URL for an external ad, if it has a valid http(s)
 * jobAdUrl. Only an absolute http(s) URL is used: the value is employer-supplied
 * and downstream steps fetch it. Emitted in `applyUrl` (and `jobAdUrl`); `url`
 * always keeps the canonical jobnet.dk route across all postings. Anything
 * else returns null.
 */
export function externalAdUrl(job: Pick<JobAdRaw, "isExternal" | "jobAdUrl">): string | null {
  const raw = job.jobAdUrl?.trim()
  if (!job.isExternal || !raw) return null
  try {
    const { protocol } = new URL(raw)
    return protocol === "https:" || protocol === "http:" ? raw : null
  } catch {
    return null
  }
}

export function createSearchOutput(data: SearchApiResponse, flags: SearchFlags) {
  let results = data.jobAds.map((job) => {
    const destination = externalAdUrl(job)
    return {
      jobAdId: job.jobAdId,
      title: job.title,
      hiringOrgName: job.hiringOrgName,
      occupation: job.occupation ?? null,
      municipality: job.municipality ?? null,
      postalCode: job.postalCode ?? null,
      postalDistrictName: job.postalDistrictName ?? null,
      country: job.country,
      publicationDate: job.publicationDate,
      applicationDeadline: job.applicationDeadline ?? null,
      applicationDeadlineStatus: job.applicationDeadlineStatus ?? null,
      workHourPartTime: job.workHourPartTime,
      isExternal: job.isExternal,
      jobAdUrl: destination,
      hasLogo: job.hasLogo,
      logoUrl: job.logoUrl ?? null,
      cvr: job.cvr ?? null,
      workPlaceAddress: job.workPlaceAddress ?? "",
      isSeen: job.isSeen,
      isFavorite: job.isFavorite,
      // /scrape contract aliases (add-portal.md: every result carries at least
      // id, title, company, location, date, url). `id` is the jobAdId - the
      // value `detail` takes - under the name every other portal CLI uses.
      // `url` stays canonical on jobnet.dk for all postings (internal and external)
      // so deduplication, health checks, and /apply host routing (#562) identify
      // the portal correctly; external ATS destinations are emitted as `applyUrl`.
      id: job.jobAdId,
      company: job.hiringOrgName,
      location: job.postalDistrictName ?? job.municipality ?? null,
      date: job.publicationDate ? job.publicationDate.slice(0, 10) : null,
      deadline: job.applicationDeadline && !job.applicationDeadline.startsWith("1900-01-01")
        ? job.applicationDeadline.slice(0, 10)
        : null,
      url: `https://jobnet.dk/find-job/${job.jobAdId}`,
      applyUrl: destination,
    }
  })

  if (flags.limit !== undefined) {
    results = results.slice(0, flags.limit)
  }

  const facets = {
    regions: data.searchFacets.regions ?? [],
    workHours: data.searchFacets.workHours ?? [],
    employmentDurations: data.searchFacets.employmentDurations ?? [],
    occupationAreas: data.searchFacets.occupationAreas ?? [],
    countries: data.searchFacets.countries ?? [],
  }

  const meta = {
    totalJobAdCount: data.totalJobAdCount,
    pageNumber: flags.page,
    resultsPerPage: flags["per-page"],
    searchString: data.searchString ?? null,
  }

  return stripInvisible({ meta, facets, results })
}

export const search = defineCommand({
  name: "search",
  description: "Search for job ads with filters",
  options: {
    "search-string": option(z.string().optional(), {
      description: "Free-text keyword search (job title, skills, employer)",
    }),
    page: option(z.coerce.number().int().min(1).default(1), {
      description: "Page number (1-indexed)",
    }),
    "per-page": option(z.coerce.number().int().min(1).default(10), {
      description: "Results per page",
    }),
    order: option(z.string().default("PublicationDate"), {
      description: "Sort order: PublicationDate, BestMatch, ApplicationDate",
    }),
    region: option(z.string().optional(), {
      description: "One region value",
    }),
    "work-hours": option(z.string().optional(), {
      description: "FullTime or PartTime",
    }),
    duration: option(z.string().optional(), {
      description: "Permanent or Temporary",
    }),
    "job-type": option(z.string().optional(), {
      description: "Announcement type: Ordinaert, Efterloenner, Foertidspension",
    }),
    "postal-code": option(z.string().optional(), {
      description: "Postal code for radius search",
    }),
    radius: option(z.coerce.number().int().min(1).default(50), {
      description: "Radius in km from postal code",
    }),
    "occupation-area": option(z.string().optional(), {
      description: "Occupation area identifier, e.g. 10000",
    }),
    "occupation-group": option(z.string().optional(), {
      description: "Occupation group identifier, e.g. 10060",
    }),
    occupation: option(z.string().optional(), {
      description: "ESCO occupation UUID or conceptUriDa from the occupations command",
    }),
    limit: option(z.coerce.number().int().min(1).optional(), {
      description: "Cap total results returned by CLI",
    }),
    format: option(z.enum(["json", "table", "plain"]).default("json"), {
      description: "Output format: json, table, plain",
    }),
  },
  handler: async ({ flags, signal }) => {
    if (signal.aborted) return

    const params = buildSearchParams(flags)

    try {
      const data = await apiFetch<SearchApiResponse>("/FindJob/Search", params)

      if (signal.aborted) return

      const output = createSearchOutput(data, flags)

      if (flags.format === "json") {
        console.log(JSON.stringify(output, null, 2))
      } else if (flags.format === "table") {
        outputTable(output.results)
      } else {
        outputPlain(output.results)
      }
    } catch (err) {
      writeError(err instanceof Error ? err.message : String(err), "API_ERROR")
      process.exit(1)
    }
  },
})

type JobAdResult = {
  jobAdId: string
  title: string
  hiringOrgName: string
  occupation: string | null
  municipality: string | null
  postalCode: number | null
  publicationDate: string | null
  applicationDeadline: string | null
}

function outputTable(results: JobAdResult[]): void {
  console.log("jobAdId                              title                                employer                   municipality")
  for (const r of results) {
    const id = r.jobAdId.padEnd(36)
    const title = (r.title ?? "-").substring(0, 36).padEnd(36)
    const employer = (r.hiringOrgName ?? "-").substring(0, 26).padEnd(26)
    const municipality = String(r.municipality ?? "-")
    console.log(`${id} ${title} ${employer} ${municipality}`)
  }
}

function outputPlain(results: JobAdResult[]): void {
  for (const r of results) {
    console.log(`id: ${r.jobAdId}`)
    console.log(`title: ${r.title}`)
    console.log(`employer: ${r.hiringOrgName}`)
    console.log(`occupation: ${r.occupation ?? "-"}`)
    console.log(`municipality: ${r.municipality ?? "-"}`)
    console.log(`published: ${r.publicationDate}`)
    console.log(`deadline: ${r.applicationDeadline ?? "-"}`)
    console.log("")
  }
}
