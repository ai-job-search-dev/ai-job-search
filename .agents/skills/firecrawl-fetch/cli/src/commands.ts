import { formatStatus, reserve, settle, status, syncRemote, type Ticket } from "./budget.js"
import {
  ATS_DOMAINS,
  ApiError,
  api,
  atsSlug,
  cleanTitle,
  failFrom,
  fail,
  humanizeSlug,
  robotsGate,
  safeUrl,
  stripInvisible,
} from "./helpers.js"

export interface PaidOpts {
  runId?: string
  runBudget?: number
  allowReserve?: boolean
}

/** Settle a ticket after an API failure: nothing billed, plus the provider signal it carried. */
async function settleFailure(t: Ticket, e: unknown): Promise<void> {
  if (e instanceof ApiError) {
    if (e.code === "RATE_LIMITED") return settle(t, 0, { rateLimitedFor: e.retryAfter ?? 60 })
    if (e.code === "BUDGET_EXHAUSTED") return settle(t, 0, { exhausted: true })
  }
  return settle(t, 0)
}

// --- search ------------------------------------------------------------------

export interface SearchOpts extends PaidOpts {
  query: string
  location?: string
  jobage?: number
  limit: number
  sites?: string[]
  format: "json" | "table" | "plain"
}

interface SearchResponse {
  success?: boolean
  creditsUsed?: number
  data?: { web?: { url?: string; title?: string; description?: string }[] } | { url?: string; title?: string; description?: string }[]
}

export interface Job {
  id: string
  title: string
  company: string
  location: string | null
  date: string | null
  url: string
  snippet: string | null
}

/** Firecrawl bills search at 2 credits per 10 results, rounded up. */
export function searchCost(limit: number): number {
  return 2 * Math.ceil(limit / 10)
}

export function tbsFor(jobage?: number): string | undefined {
  if (!jobage) return undefined
  if (jobage <= 1) return "qdr:d"
  if (jobage <= 7) return "qdr:w"
  if (jobage <= 31) return "qdr:m"
  return "qdr:y"
}

export function mapResults(items: { url?: string; title?: string; description?: string }[], location?: string): Job[] {
  const out: Job[] = []
  const seen = new Set<string>()
  for (const it of items) {
    const url = typeof it.url === "string" ? it.url.trim() : ""
    const slug = url ? atsSlug(url) : null
    if (!slug || seen.has(url)) continue
    const company = humanizeSlug(slug)
    const title = cleanTitle(String(it.title ?? ""), company)
    if (!title) continue
    seen.add(url)
    out.push({
      id: url,
      title,
      company,
      location: location ?? null,
      date: null,
      url,
      snippet: typeof it.description === "string" ? it.description.slice(0, 300) : null,
    })
  }
  return out
}

export async function runSearch(o: SearchOpts): Promise<number> {
  const estimate = searchCost(o.limit)
  let t: Ticket
  try {
    t = await reserve("firecrawl", estimate, o)
  } catch (e) {
    return failFrom(e)
  }
  let resp: SearchResponse
  try {
    resp = await api<SearchResponse>("POST", "/search", {
      query: o.location ? `${o.query} ${o.location}` : o.query,
      limit: o.limit,
      sources: [{ type: "web" }],
      includeDomains: o.sites?.length ? o.sites : ATS_DOMAINS,
      ...(tbsFor(o.jobage) ? { tbs: tbsFor(o.jobage) } : {}),
    })
  } catch (e) {
    await settleFailure(t, e)
    return failFrom(e)
  }
  await settle(t, typeof resp.creditsUsed === "number" ? resp.creditsUsed : estimate)
  const items = Array.isArray(resp.data) ? resp.data : (resp.data?.web ?? [])
  const results = stripInvisible(mapResults(items, o.location))
  if (o.format === "json") {
    process.stdout.write(JSON.stringify({ meta: { count: results.length, page: 1, credits: resp.creditsUsed ?? estimate }, results }, null, 2) + "\n")
  } else if (o.format === "table") {
    const rows = results.map((j) => `${j.title.slice(0, 50).padEnd(52)}${j.company.slice(0, 28).padEnd(30)}${j.url}`)
    process.stdout.write([`${"TITLE".padEnd(52)}${"COMPANY".padEnd(30)}URL`, ...rows].join("\n") + "\n")
  } else {
    process.stdout.write(results.map((j) => `${j.title}\n${j.company}\n${j.url}\n`).join("\n"))
  }
  return 0
}

// --- detail ------------------------------------------------------------------

export interface DetailOpts extends PaidOpts {
  url: string
  maxChars: number
  format: "json" | "plain"
}

interface ScrapeResponse {
  success?: boolean
  data?: {
    markdown?: string
    metadata?: { title?: string; statusCode?: number; sourceURL?: string; url?: string; provider?: { creditsCost?: number } }
  }
}

export async function runDetail(o: DetailOpts): Promise<number> {
  const u = safeUrl(o.url)
  if (!u) return fail(`not an http(s) URL without credentials: "${o.url}"`, "BAD_URL")
  const g = robotsGate(u.href)
  if (!g.allowed) return fail(`robots.txt does not permit fetching ${u.href}: ${g.message}`, "ROBOTS_DISALLOWED")

  let t: Ticket
  try {
    t = await reserve("firecrawl", 1, o)
  } catch (e) {
    return failFrom(e)
  }
  let resp: ScrapeResponse
  try {
    resp = await api<ScrapeResponse>("POST", "/scrape", { url: u.href, formats: ["markdown"], onlyMainContent: true, timeout: 60_000 })
  } catch (e) {
    await settleFailure(t, e)
    return failFrom(e)
  }
  const meta = resp.data?.metadata ?? {}
  const cost = typeof meta.provider?.creditsCost === "number" ? meta.provider.creditsCost : 1
  await settle(t, cost)

  // Firecrawl follows redirects itself; a final URL on another host gets its own gate.
  const finalUrl = safeUrl(meta.url ?? meta.sourceURL ?? u.href) ?? u
  if (finalUrl.hostname !== u.hostname) {
    const g2 = robotsGate(finalUrl.href)
    if (!g2.allowed) return fail(`redirected to ${finalUrl.href}, which robots.txt does not permit: ${g2.message}; content discarded`, "ROBOTS_DISALLOWED")
  }
  const code = meta.statusCode ?? 200
  if (code === 404 || code === 410) return fail(`posting not found (HTTP ${code}) - likely expired`, "NOT_FOUND")
  if (code >= 400) return fail(`page answered HTTP ${code} through Firecrawl`, "UPSTREAM_ERROR")

  const md = (resp.data?.markdown ?? "").trim()
  if (!md) return fail("Firecrawl returned an empty page", "EMPTY_PAGE")
  const slug = atsSlug(finalUrl.href)
  const company = slug ? humanizeSlug(slug) : null
  const job = stripInvisible({
    id: finalUrl.href,
    url: finalUrl.href,
    title: meta.title ? cleanTitle(meta.title, company ?? "") : null,
    company,
    location: null,
    date: null,
    statusCode: code,
    credits: cost,
    truncated: md.length > o.maxChars,
    description: md.slice(0, o.maxChars),
  })
  if (o.format === "plain") {
    process.stdout.write(`${job.title ?? "(no title)"}\n${job.company ?? "—"}\n${job.url}\n\n${job.description}\n${job.truncated ? "\n[truncated]\n" : ""}`)
  } else {
    process.stdout.write(JSON.stringify(job, null, 2) + "\n")
  }
  return 0
}

// --- budget ------------------------------------------------------------------

interface CreditUsage {
  data?: { remainingCredits?: number; planCredits?: number; billingPeriodStart?: string | null }
}

export async function runBudget(o: { sync: boolean; format: "json" | "plain" }): Promise<number> {
  let syncNote: string | null = null
  try {
    if (o.sync) {
      try {
        const r = await api<CreditUsage>("GET", "/team/credit-usage", undefined, 30_000)
        const rem = r.data?.remainingCredits
        const plan = r.data?.planCredits
        const used = typeof rem === "number" && typeof plan === "number" ? Math.max(0, plan - rem) : null
        await syncRemote("firecrawl", { used, limit: typeof plan === "number" ? plan : null, periodStart: r.data?.billingPeriodStart ?? null })
      } catch (e) {
        if (e instanceof ApiError && e.code === "MISSING_CREDENTIALS") throw e
        syncNote = `sync failed, showing local ledger only: ${e instanceof Error ? e.message : String(e)}`
      }
    }
    const st = await status("firecrawl")
    if (o.format === "json") process.stdout.write(JSON.stringify({ ...st, sync_error: syncNote }, null, 2) + "\n")
    else process.stdout.write(formatStatus(st) + (syncNote ? `  ${syncNote}\n` : ""))
    return 0
  } catch (e) {
    return failFrom(e)
  }
}
