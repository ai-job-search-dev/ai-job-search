import { defineCommand, option } from "@bunli/core"
import { z } from "zod"
import { rssFetch, fetchWithUA, writeError, parseRssDescription, extractJobIdFromUrl, stripInvisible, BASE_URL, type RssItem } from "../helpers.js"

export function normalizeSearchItem(item: RssItem): Record<string, unknown> {
  const parsed = parseRssDescription(item.description)
  const id = extractJobIdFromUrl(item.link)
  // Guard the parse: new Date(<unparseable>) is an Invalid Date whose
  // toISOString() throws RangeError, and this runs inside an unguarded
  // items.map() - one bad feed item would kill the whole search as
  // API_ERROR (#416). An unparseable pubDate degrades to the same shape
  // as an absent one: posted "", date null.
  const parsedDate = item.pubDate ? new Date(item.pubDate) : null
  const posted = parsedDate && !Number.isNaN(parsedDate.getTime()) ? parsedDate.toISOString() : ""
  return stripInvisible({
    id,
    title: item.title,
    company: parsed.company,
    location: parsed.location,
    jobType: parsed.jobType,
    description: item.description,
    url: item.link,
    posted,
    date: posted ? posted.slice(0, 10) : null,
    deadline: parsed.deadline,
  })
}

export const search = defineCommand({
  name: "search",
  description: "Search job listings via RSS feed",
  options: {
    key: option(z.string().optional(), {
      description: "Keyword search (title, company, keyword)",
    }),
    exclude: option(z.string().optional(), {
      description: "Exclude keywords (antikey)",
    }),
    type: option(z.union([z.string(), z.array(z.string())]).optional(), {
      repeatable: true,
      description: "Job type code (cvtype). Repeatable: --type 3 --type 6",
    }),
    education: option(z.union([z.string(), z.array(z.string())]).optional(), {
      repeatable: true,
      description: "Education field code (udd). Repeatable.",
    }),
    location: option(z.union([z.string(), z.array(z.string())]).optional(), {
      repeatable: true,
      description: "Region code (amt). Repeatable.",
    }),
    "work-area": option(z.union([z.string(), z.array(z.string())]).optional(), {
      repeatable: true,
      description: "Work area / function code (erf). Repeatable.",
    }),
    industry: option(z.union([z.string(), z.array(z.string())]).optional(), {
      repeatable: true,
      description: "Industry code (branche). Repeatable.",
    }),
    "suitable-for": option(z.union([z.string(), z.array(z.string())]).optional(), {
      repeatable: true,
      description: "Suitable-for code (andet). Repeatable.",
    }),
    company: option(z.coerce.number().int().min(1).optional(), {
      description: "Company ID (virk)",
    }),
    remote: option(z.string().optional(), {
      description: "Remote work: helt or delvist (fjernarbejde)",
    }),
    since: option(z.string().optional(), {
      description: "Posted on or after date, format YYYY-MM-DD (oprettet)",
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

    // Require at least one filter
    const hasFilter =
      flags.key ||
      flags.exclude ||
      flags.type ||
      flags.education ||
      flags.location ||
      flags["work-area"] ||
      flags.industry ||
      flags["suitable-for"] ||
      flags.company !== undefined ||
      flags.remote ||
      flags.since

    if (!hasFilter) {
      writeError("--key or at least one filter is required", "MISSING_REQUIRED")
      process.exit(1)
    }

    const params: Record<string, string | string[]> = {}

    if (flags.key) params["key"] = flags.key
    if (flags.exclude) params["antikey"] = flags.exclude
    if (flags.type) {
      const vals = Array.isArray(flags.type) ? flags.type : [flags.type]
      params["cvtype"] = vals.flatMap((v) => v.split(","))
    }
    if (flags.education) {
      const vals = Array.isArray(flags.education) ? flags.education : [flags.education]
      params["udd"] = vals.flatMap((v) => v.split(","))
    }
    if (flags.location) {
      const vals = Array.isArray(flags.location) ? flags.location : [flags.location]
      params["amt"] = vals.flatMap((v) => v.split(","))
    }
    if (flags["work-area"]) {
      const vals = Array.isArray(flags["work-area"]) ? flags["work-area"] : [flags["work-area"]]
      params["erf"] = vals.flatMap((v) => v.split(","))
    }
    if (flags.industry) {
      const vals = Array.isArray(flags.industry) ? flags.industry : [flags.industry]
      params["branche"] = vals.flatMap((v) => v.split(","))
    }
    if (flags["suitable-for"]) {
      const vals = Array.isArray(flags["suitable-for"]) ? flags["suitable-for"] : [flags["suitable-for"]]
      params["andet"] = vals.flatMap((v) => v.split(","))
    }
    if (flags.company !== undefined) params["virk"] = String(flags.company)
    if (flags.remote) params["fjernarbejde"] = flags.remote
    if (flags.since) params["oprettet"] = flags.since

    try {
      // Fetch RSS feed
      const items = await rssFetch(params)

      if (signal.aborted) return

      // Also fetch total count from HTML page (secondary request)
      let total: number | null = null
      try {
        // Small delay to be polite
        await new Promise((resolve) => setTimeout(resolve, 300))
        const searchParams = new URLSearchParams()
        for (const [key, value] of Object.entries(params)) {
          if (Array.isArray(value)) {
            for (const v of value) searchParams.append(key, v)
          } else {
            searchParams.append(key, value)
          }
        }
        const htmlUrl = `${BASE_URL}/job/?${searchParams.toString()}`
        const htmlResp = await fetchWithUA(htmlUrl)
        if (htmlResp.ok) {
          const html = await htmlResp.text()
          // Extract from <title> tag: "457 relevante job og karriereopslag i Akademikernes Jobbank"
          const titleMatch = html.match(/<title[^>]*>\s*(\d[\d.,]*)\s+relevante job/i)
          if (titleMatch) {
            total = parseInt(titleMatch[1].replace(/[.,]/g, ""), 10)
          }
        }
      } catch {
        // Secondary request failed — total stays null
      }

      // Normalize items
      let results = items.map(normalizeSearchItem)

      // Apply limit
      if (flags.limit !== undefined) {
        results = results.slice(0, flags.limit)
      }

      const output = { meta: { total }, results }

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

function outputTable(results: Array<Record<string, unknown>>): void {
  console.log("id        title                                company                location           deadline")
  for (const r of results) {
    const id = String(r.id ?? "-").padEnd(9)
    const title = String(r.title ?? "-").substring(0, 36).padEnd(36)
    const company = String(r.company ?? "-").substring(0, 22).padEnd(22)
    const location = String(r.location ?? "-").substring(0, 18).padEnd(18)
    const deadline = String(r.deadline ?? "-")
    console.log(`${id} ${title} ${company} ${location} ${deadline}`)
  }
}

function outputPlain(results: Array<Record<string, unknown>>): void {
  for (const r of results) {
    console.log(`id: ${r.id}`)
    console.log(`title: ${r.title}`)
    console.log(`company: ${r.company}`)
    console.log(`location: ${r.location}`)
    console.log(`jobType: ${r.jobType}`)
    console.log(`posted: ${r.posted}`)
    console.log(`deadline: ${r.deadline ?? "none"}`)
    console.log(`url: ${r.url}`)
    console.log("")
  }
}
