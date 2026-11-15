import { readFileSync, writeFileSync } from "fs"
import { tmpdir } from "os"
import { join } from "path"
import { formatStatus, reserve, settle, status, syncRemote, type Ticket } from "../budget.js"
import {
  ApiError,
  api,
  buildInput,
  fail,
  failFrom,
  htmlToText,
  httpUrl,
  loadBoards,
  normalizeDate,
  pick,
  stripInvisible,
  type Board,
} from "../helpers.js"

export interface Job {
  id: string
  title: string
  company: string
  location: string | null
  date: string | null
  url: string
  salary: string | null
  /** The board's own engagement label ("Full-time"), verbatim; /scrape Step 3 still reads the posting for employment_type. */
  jobType: string | null
  /** Link the board says leads to the employer's own posting (may pass through a tracking redirect). */
  applyUrl: string | null
  /** True only when the board itself marks the listing expired; false/absent is not proof it is open. */
  isExpired: boolean
  board: string
  description: string | null
}

/** True when any path holds exactly `want` (so a missing field never counts either way). */
function flag(item: unknown, paths: string[] | undefined, want = true): boolean {
  for (const p of paths ?? []) {
    let cur: unknown = item
    for (const part of p.split(".")) cur = cur !== null && typeof cur === "object" ? (cur as Record<string, unknown>)[part] : undefined
    if (cur === want) return true
  }
  return false
}

interface Run {
  id: string
  status: string
  defaultDatasetId: string
  usageTotalUsd?: number | null
}

const TERMINAL = new Set(["SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"])

export const timing = {
  sleep: (ms: number) => new Promise<void>((r) => setTimeout(r, ms)),
  /** The run's first terminal response can carry preliminary costs; Apify says to re-read after ~10 s. */
  settleDelayMs: 10_000,
}

const CACHE = () => process.env.APIFY_JOBS_CACHE?.trim() || join(tmpdir(), "apify-jobs-search-cache.json")

function readCache(): Record<string, Job> {
  try {
    const d = JSON.parse(readFileSync(CACHE(), "utf8"))
    return d && typeof d === "object" ? d : {}
  } catch {
    return {}
  }
}

export function mapItems(items: unknown[], board: string, cfg: Board, now = new Date()): { jobs: Job[]; invalid: number } {
  const jobs: Job[] = []
  let invalid = 0
  const seen = new Set<string>()
  for (const it of items) {
    const title = pick(it, cfg.fields.title)
    const company = pick(it, cfg.fields.company)
    const url = httpUrl(pick(it, cfg.fields.url))
    if (!title || !company || !url) {
      invalid++
      continue
    }
    if (seen.has(url)) continue
    seen.add(url)
    const desc = pick(it, cfg.fields.description)
    jobs.push({
      id: pick(it, cfg.fields.id) ?? url,
      title: title,
      company: company,
      location: pick(it, cfg.fields.location),
      date: normalizeDate(pick(it, cfg.fields.date), now),
      url: url,
      salary: pick(it, cfg.fields.salary),
      jobType: pick(it, cfg.fields.jobType),
      applyUrl: httpUrl(pick(it, cfg.fields.applyUrl)),
      // Boards say "closed" either as isExpired/isClosed: true or as isActive: false.
      isExpired: flag(it, cfg.fields.isExpired) || flag(it, cfg.fields.isActive, false),
      board,
      description: desc ? htmlToText(desc) : null,
    })
  }
  return { jobs, invalid }
}

export interface SearchOpts {
  board: string
  query: string
  location?: string
  country?: string
  limit: number
  jobage?: number
  maxCharge: number
  timeoutSecs: number
  memoryMb: number
  runId?: string
  runBudget?: number
  allowReserve?: boolean
  format: "json" | "table" | "plain"
  now?: Date
}

async function bestEffortSync(): Promise<void> {
  try {
    const r = await api<{ data?: { current?: { monthlyUsageUsd?: number }; limits?: { maxMonthlyUsageUsd?: number }; monthlyUsageCycle?: { startAt?: string } } }>(
      "GET",
      "/users/me/limits",
      undefined,
      20_000,
    )
    const used = r.data?.current?.monthlyUsageUsd
    const limit = r.data?.limits?.maxMonthlyUsageUsd
    await syncRemote("apify", { used: typeof used === "number" ? used : null, limit: typeof limit === "number" ? limit : null, periodStart: r.data?.monthlyUsageCycle?.startAt ?? null })
  } catch (e) {
    if (e instanceof ApiError && e.code === "MISSING_CREDENTIALS") throw e
    // the local ledger stays authoritative when the limits endpoint is unreachable
  }
}

export async function runSearch(o: SearchOpts): Promise<number> {
  let boards: Record<string, Board>
  try {
    boards = loadBoards()
  } catch (e) {
    return failFrom(e)
  }
  const cfg = boards[o.board]
  if (!cfg) return fail(`unknown board "${o.board}"; configured: ${Object.keys(boards).join(", ")} (see 'boards')`, "BAD_BOARD")
  if (!cfg.enabled) return fail(`board "${o.board}" is disabled in actors.json`, "BOARD_DISABLED")

  const countryCode = o.country ?? cfg.defaults.country
  let country: string | undefined = countryCode
  if (cfg.countryMap && countryCode !== undefined) {
    country = cfg.countryMap[countryCode.toUpperCase()]
    if (country === undefined) {
      return fail(`board "${o.board}" has no country "${countryCode}"; mapped: ${Object.keys(cfg.countryMap).join(", ")}`, "BAD_ARG")
    }
  }
  const input = buildInput(cfg.input, {
    query: o.query,
    location: o.location ?? cfg.defaults.location,
    country,
    limit: o.limit,
    // Server-side recency where the actor has it, so stale postings are never billed.
    jobage: o.jobage,
  })
  const actor = cfg.actor.replace("/", "~")

  let t: Ticket
  try {
    t = await reserve("apify", o.maxCharge, { runId: o.runId, runBudget: o.runBudget, allowReserve: o.allowReserve })
  } catch (e) {
    return failFrom(e)
  }

  let run: Run
  let cost = o.maxCharge
  try {
    const qs = new URLSearchParams({
      waitForFinish: "60",
      timeout: String(o.timeoutSecs),
      memory: String(o.memoryMb),
      maxItems: String(o.limit),
      maxTotalChargeUsd: String(o.maxCharge),
    })
    run = (await api<{ data: Run }>("POST", `/acts/${encodeURIComponent(actor)}/runs?${qs}`, input)).data
    const deadline = Date.now() + (o.timeoutSecs + 120) * 1000
    while (!TERMINAL.has(run.status) && Date.now() < deadline) {
      run = (await api<{ data: Run }>("GET", `/actor-runs/${encodeURIComponent(run.id)}?waitForFinish=60`)).data
    }
    if (!TERMINAL.has(run.status)) {
      await api("POST", `/actor-runs/${encodeURIComponent(run.id)}/abort`).catch(() => undefined)
      await settle(t, o.maxCharge)
      return fail(`actor run ${run.id} did not finish in time and was aborted; charged the cap ${o.maxCharge} USD to the ledger`, "ACTOR_TIMEOUT")
    }
    await timing.sleep(timing.settleDelayMs)
    const again = (await api<{ data: Run }>("GET", `/actor-runs/${encodeURIComponent(run.id)}`).catch(() => ({ data: run }))).data
    if (typeof again.usageTotalUsd === "number") cost = again.usageTotalUsd
  } catch (e) {
    if (e instanceof ApiError && e.code === "RATE_LIMITED") await settle(t, 0, { rateLimitedFor: 60 })
    else if (e instanceof ApiError && e.code === "BUDGET_EXHAUSTED") await settle(t, 0, { exhausted: true })
    else await settle(t, 0)
    return failFrom(e)
  }
  await settle(t, cost)
  await bestEffortSync().catch(() => undefined)

  let items: unknown[]
  try {
    const qs = new URLSearchParams({ clean: "true", format: "json", limit: String(o.limit) })
    items = await api<unknown[]>("GET", `/datasets/${encodeURIComponent(run.defaultDatasetId)}/items?${qs}`)
  } catch (e) {
    return failFrom(e)
  }
  if (!Array.isArray(items)) return fail("dataset items were not a JSON array", "PARSE_FAILED")
  if (run.status !== "SUCCEEDED" && items.length === 0) return fail(`actor run ${run.id} ended ${run.status} with no items`, "ACTOR_FAILED")

  const now = o.now ?? new Date()
  let { jobs, invalid } = mapItems(items, o.board, cfg, now)
  if (items.length > 0 && jobs.length === 0) {
    return fail(
      `actor returned ${items.length} items but none had title, company and an http(s) url under the configured field names; check "fields" for "${o.board}" in actors.json against the actor's Output tab`,
      "PARSE_FAILED",
    )
  }
  if (o.jobage) {
    const cutoff = new Date(now.getTime() - o.jobage * 86_400_000).toISOString().slice(0, 10)
    jobs = jobs.filter((j) => j.date === null || j.date >= cutoff)
  }
  jobs = stripInvisible(jobs)

  const cache = readCache()
  for (const j of jobs) cache[j.url] = j
  try {
    writeFileSync(CACHE(), JSON.stringify(cache))
  } catch {
    // cache is a convenience for 'detail'; search output is complete without it
  }

  const meta = { count: jobs.length, page: 1, board: o.board, run_id: run.id, run_status: run.status, cost_usd: Math.round(cost * 10000) / 10000, invalid_items: invalid }
  if (o.format === "json") {
    process.stdout.write(JSON.stringify({ meta, results: jobs.map(({ description, ...rest }) => ({ ...rest, has_description: !!description })) }, null, 2) + "\n")
  } else if (o.format === "table") {
    const rows = jobs.map((j) => `${j.title.slice(0, 44).padEnd(46)}${j.company.slice(0, 26).padEnd(28)}${(j.location ?? "—").slice(0, 20).padEnd(22)}${(j.date ?? "—").padEnd(12)}${j.url}`)
    process.stdout.write([`${"TITLE".padEnd(46)}${"COMPANY".padEnd(28)}${"LOCATION".padEnd(22)}${"DATE".padEnd(12)}URL`, ...rows, `cost: $${meta.cost_usd}`].join("\n") + "\n")
  } else {
    process.stdout.write(jobs.map((j) => `${j.title}\n${j.company} · ${j.location ?? "—"} · ${j.date ?? "—"}\n${j.url}\n`).join("\n"))
  }
  return 0
}

/** Free: reads the description the last search already paid for. */
export function runDetail(o: { url: string; format: "json" | "plain" }): number {
  const job = readCache()[o.url.trim()]
  if (!job) {
    return fail(
      "not in the local cache of recent apify-jobs-search results (the cache lives in the system temp dir); fetch the posting with firecrawl-fetch detail or WebFetch instead",
      "NOT_CACHED",
    )
  }
  if (!job.description) return fail("this board's actor returned no description; fetch the posting with firecrawl-fetch detail or WebFetch", "NO_DESCRIPTION")
  if (o.format === "plain") {
    process.stdout.write(`${job.title}\n${job.company} · ${job.location ?? "—"} · ${job.date ?? "—"}\n${job.salary ? `Salary: ${job.salary}\n` : ""}${job.url}\n\n${job.description}\n`)
  } else {
    process.stdout.write(JSON.stringify(job, null, 2) + "\n")
  }
  return 0
}

export function runBoards(): number {
  try {
    const boards = loadBoards()
    const rows = Object.entries(boards).map(([name, b]) => ({ board: name, enabled: b.enabled, actor: b.actor, url: b.url ?? `https://apify.com/${b.actor}` }))
    process.stdout.write(JSON.stringify({ boards: rows }, null, 2) + "\n")
    return 0
  } catch (e) {
    return failFrom(e)
  }
}

export async function runBudget(o: { sync: boolean; format: "json" | "plain" }): Promise<number> {
  try {
    if (o.sync) await bestEffortSync()
    const st = await status("apify")
    process.stdout.write(o.format === "json" ? JSON.stringify(st, null, 2) + "\n" : formatStatus(st))
    return 0
  } catch (e) {
    return failFrom(e)
  }
}
