import { defineCommand, option } from "@bunli/core"
import { z } from "zod"
import { BASE_URL, htmlFetch, parseSearchPage, writeError, type JobCard } from "../helpers.js"

export const search = defineCommand({
  name: "search",
  description: "Search for job listings on Jobindex.dk",
  options: {
    query: option(z.string().optional(), {
      short: "q",
      description: "Keyword search query (e.g. python, grafisk designer)",
    }),
    page: option(z.coerce.number().int().min(1).default(1), {
      description: "Page number (1-indexed)",
    }),
    jobage: option(z.coerce.number().int().min(1).default(9999), {
      description: "Max age of posting in days: 1, 7, 14, 30, or 9999 (all)",
    }),
    // An enum, not a free string: jobindex answers an unknown sort with an
    // unrelated order and no error, so a typo silently lost the date sort.
    sort: option(z.enum(["score", "date"]).default("score"), {
      description: "Sort order: score (relevance) or date (newest first)",
    }),
    limit: option(z.coerce.number().int().min(1).optional(), {
      description: "Cap total results returned by the CLI (client-side)",
    }),
    format: option(z.enum(["json", "table", "plain"]).default("json"), {
      description: "Output format: json, table, plain",
    }),
  },
  handler: async ({ flags, signal }) => {
    if (!flags.query) {
      writeError("--query is required", "MISSING_REQUIRED")
      process.exit(1)
    }

    if (signal.aborted) return

    const params = new URLSearchParams({
      q: flags.query,
      page: String(flags.page),
      jobage: String(flags.jobage),
      sort: flags.sort,
    })

    try {
      const html = await htmlFetch(`${BASE_URL}/jobsoegning?${params.toString()}`)

      if (signal.aborted) return

      const parsed = parseSearchPage(html)
      const total = parsed.total
      let results = parsed.results

      if (flags.limit !== undefined) {
        results = results.slice(0, flags.limit)
      }

      const output = {
        meta: {
          total,
          page: flags.page,
          perPage: 20,
        },
        results,
      }

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

function outputTable(results: JobCard[]): void {
  console.log("id          title                                    company              location")
  for (const r of results) {
    const id = r.id.padEnd(11)
    const title = r.title.substring(0, 40).padEnd(40)
    const company = (r.company ?? "-").substring(0, 20).padEnd(20)
    const location = r.location ?? "-"
    console.log(`${id} ${title} ${company} ${location}`)
  }
}

function outputPlain(results: JobCard[]): void {
  for (const r of results) {
    console.log(`id: ${r.id}`)
    console.log(`title: ${r.title}`)
    console.log(`company: ${r.company ?? "-"}`)
    console.log(`location: ${r.location ?? "-"}`)
    console.log(`date: ${r.date ?? "-"}`)
    console.log(`deadline: ${r.deadline ?? "-"}`)
    console.log(`url: ${r.url}`)
    if (r.description) console.log(`description: ${r.description}`)
    console.log("")
  }
}
