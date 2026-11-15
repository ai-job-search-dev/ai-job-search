import { afterEach, beforeEach, describe, expect, test } from "bun:test"
import { mkdtempSync, rmSync } from "fs"
import { tmpdir } from "os"
import { join } from "path"
import { mapItems, runDetail, runSearch, timing } from "../src/commands/search"
import { buildInput, loadBoards, normalizeDate } from "../src/helpers"
import { status } from "../src/budget"

const originalFetch = globalThis.fetch
const originalWrite = process.stdout.write
const originalErr = process.stderr.write
const saved = { ...process.env }
let dir: string

function capture(stream: "stdout" | "stderr"): () => string {
  let out = ""
  process[stream].write = ((c: string | Uint8Array) => {
    out += c.toString()
    return true
  }) as typeof process.stdout.write
  return () => out
}

type Call = { method: string; url: string; body: unknown }
function mockFetch(handler: (c: Call) => Response): Call[] {
  const calls: Call[] = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const c = { method: init?.method ?? "GET", url: String(input), body: init?.body ? JSON.parse(String(init.body)) : null }
    calls.push(c)
    return handler(c)
  }) as typeof fetch
  return calls
}

const NOW = new Date("2026-11-15T00:00:00Z")
const base = { board: "indeed", query: "engineering manager", limit: 5, maxCharge: 0.25, timeoutSecs: 180, memoryMb: 1024, format: "json" as const, now: NOW }
const ITEMS = [
  { id: "a1", positionName: "Engineering Manager - Payments", company: "Acme Pay", location: "Bengaluru", postedAt: null, postingDateParsed: "2026-11-10T00:00:00.000Z", url: "https://in.indeed.com/viewjob?jk=a1", jobType: ["Full-time"], isExpired: false, externalApplyLink: "https://grnh.se/abc", description: "<p>Lead <b>payments</b></p>" },
  { id: "a2", positionName: "Old role", company: "Oldco", location: "Pune", postingDateParsed: "2026-09-01T00:00:00.000Z", url: "https://in.indeed.com/viewjob?jk=a2" },
  { positionName: "No company", url: "https://in.indeed.com/viewjob?jk=a3" },
]

function happy(items: unknown[] = ITEMS, usage = 0.012): Call[] {
  return mockFetch((c) => {
    if (c.method === "POST" && c.url.includes("/acts/misceres~indeed-scraper/runs")) return Response.json({ data: { id: "run1", status: "RUNNING", defaultDatasetId: "ds1" } }, { status: 201 })
    if (c.url.includes("/actor-runs/run1")) return Response.json({ data: { id: "run1", status: "SUCCEEDED", defaultDatasetId: "ds1", usageTotalUsd: usage } })
    if (c.url.includes("/users/me/limits")) return Response.json({ data: { current: { monthlyUsageUsd: 0.5 }, limits: { maxMonthlyUsageUsd: 5 }, monthlyUsageCycle: { startAt: "2026-11-01T00:00:00Z" } } })
    if (c.url.includes("/datasets/ds1/items")) return Response.json(items)
    throw new Error(`unexpected ${c.method} ${c.url}`)
  })
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "apify-test-"))
  process.env.SCRAPE_BUDGET_FILE = join(dir, "api_budget.json")
  process.env.APIFY_JOBS_CACHE = join(dir, "cache.json")
  process.env.APIFY_API_TOKEN = "apify_api_test"
  timing.settleDelayMs = 0
})
afterEach(() => {
  globalThis.fetch = originalFetch
  process.stdout.write = originalWrite
  process.stderr.write = originalErr
  process.env = { ...saved }
  rmSync(dir, { recursive: true, force: true })
})

describe("config and mapping", () => {
  test("shipped actors.json loads", () => {
    const b = loadBoards()
    expect(Object.keys(b)).toEqual(expect.arrayContaining(["indeed", "naukri"]))
  })

  test("placeholders fill, numbers stay numbers, empty ones drop their key", () => {
    expect(buildInput({ position: "{query}", location: "{location}", maxItems: "{limit}", fixed: true }, { query: "em", limit: 5 })).toEqual({ position: "em", maxItems: 5, fixed: true })
  })

  test("placeholders inside arrays fill per element; an emptied array drops its key; literal arrays pass through", () => {
    const t = { keywords: ["{query}"], locations: ["{location}"], sources: ["remoteok", "himalayas"], postedWithinDays: "{jobage}" }
    expect(buildInput(t, { query: "engineering manager", jobage: 14 })).toEqual({ keywords: ["engineering manager"], sources: ["remoteok", "himalayas"], postedWithinDays: 14 })
  })

  test("isActive: false marks a listing expired; a missing isActive does not", () => {
    const cfg = { ...loadBoards().instahyre }
    const { jobs } = mapItems(
      [
        { jobId: "1", title: "EM", companyName: "A", publicUrl: "https://www.instahyre.com/job-1/", isActive: false },
        { jobId: "2", title: "EM", companyName: "B", publicUrl: "https://www.instahyre.com/job-2/" },
      ],
      "instahyre",
      cfg,
      NOW,
    )
    expect(jobs.map((j) => j.isExpired)).toEqual([true, false])
  })

  test("every shipped board loads, and every disabled one says why", () => {
    for (const [name, b] of Object.entries(loadBoards())) {
      expect(b.actor).toMatch(/^[\w.-]+\/[\w.-]+$/)
      if (!b.enabled) expect(Boolean((b as unknown as Record<string, unknown>)._status || (b as unknown as Record<string, unknown>)._disabled_reason)).toBe(true)
      void name
    }
  })

  test("dates normalise; vague ones stay null", () => {
    expect(normalizeDate("2026-11-10T05:00:00Z", NOW)).toBe("2026-11-10")
    expect(normalizeDate("3 days ago", NOW)).toBe("2026-11-12")
    expect(normalizeDate("30+ days ago", NOW)).toBeNull()
    expect(normalizeDate("Just posted", NOW)).toBe("2026-11-15")
  })

  test("items without title, company or url are counted invalid, not invented", () => {
    const { jobs, invalid } = mapItems(ITEMS, "indeed", loadBoards().indeed, NOW)
    expect(jobs.length).toBe(2)
    expect(invalid).toBe(1)
    expect(jobs[0].description).toBe("Lead payments")
    expect(jobs[0]).toMatchObject({ jobType: "Full-time", applyUrl: "https://grnh.se/abc", isExpired: false })
  })
})

describe("search (mocked API)", () => {
  test("runs the actor with caps, records real cost, filters by date, caches descriptions", async () => {
    const calls = happy()
    const out = capture("stdout")
    expect(await runSearch({ ...base, location: "Bengaluru", jobage: 14 })).toBe(0)
    const start = calls.find((c) => c.method === "POST")!
    const qs = new URL(start.url).searchParams
    expect(qs.get("maxItems")).toBe("5")
    expect(qs.get("maxTotalChargeUsd")).toBe("0.25")
    expect(qs.get("timeout")).toBe("180")
    expect(start.body).toMatchObject({ position: "engineering manager", country: "IN", location: "Bengaluru", maxItemsPerSearch: 5 })
    const parsed = JSON.parse(out())
    expect(parsed.results.map((j: { id: string }) => j.id)).toEqual(["a1"])
    expect(parsed.meta).toMatchObject({ cost_usd: 0.012, invalid_items: 1 })
    expect((await status("apify")).used).toBe(0.5) // provider's higher figure wins

    const out2 = capture("stdout")
    expect(runDetail({ url: "https://in.indeed.com/viewjob?jk=a1", format: "json" })).toBe(0)
    expect(JSON.parse(out2()).description).toBe("Lead payments")
  })

  test("wrong field names are a PARSE_FAILED, not zero jobs", async () => {
    happy([{ foo: 1 }, { bar: 2 }])
    const err = capture("stderr")
    expect(await runSearch(base)).toBe(1)
    expect(JSON.parse(err()).code).toBe("PARSE_FAILED")
  })

  test("not-enough-usage marks the cycle exhausted and refunds the estimate", async () => {
    mockFetch(() => Response.json({ error: { type: "not-enough-usage-to-run-paid-actor", message: "no credit" } }, { status: 402 }))
    const err = capture("stderr")
    expect(await runSearch(base)).toBe(1)
    expect(JSON.parse(err()).code).toBe("BUDGET_EXHAUSTED")
    const st = await status("apify")
    expect(st.exhausted).toBe(true)
    expect(st.used).toBe(0)
  })

  test("the run budget blocks a second run before any request", async () => {
    happy()
    capture("stdout")
    expect(await runSearch({ ...base, runId: "r1", runBudget: 0.3 })).toBe(0)
    const calls = mockFetch(() => Response.json({}))
    const err = capture("stderr")
    expect(await runSearch({ ...base, runId: "r1", runBudget: 0.26 })).toBe(1)
    expect(JSON.parse(err()).code).toBe("RUN_BUDGET")
    expect(calls.length).toBe(0)
  })

  test("a disabled board (naukri ships disabled) sends nothing", async () => {
    const calls = mockFetch(() => Response.json({}))
    const err = capture("stderr")
    expect(await runSearch({ ...base, board: "naukri" })).toBe(1)
    expect(JSON.parse(err()).code).toBe("BOARD_DISABLED")
    expect(calls.length).toBe(0)
  })

  test("unknown board fails fast", async () => {
    const err = capture("stderr")
    expect(await runSearch({ ...base, board: "nope" })).toBe(1)
    expect(JSON.parse(err()).code).toBe("BAD_BOARD")
  })

  test("countryMap translates --country and rejects unmapped codes before any request", async () => {
    const file = join(dir, "actors.json")
    const boards = { boards: { gd: { enabled: true, actor: "bebity/glassdoor-jobs-scraper", input: { keyword: "{query}", country: "{country}", maxItems: "{limit}" }, defaults: { country: "IN" }, countryMap: { IN: "https://www.glassdoor.co.in", DE: "https://www.glassdoor.de" }, fields: { title: ["jobTitle"], company: ["companyName"], url: ["jobUrl"] } } } }
    await Bun.write(file, JSON.stringify(boards))
    process.env.APIFY_ACTORS_FILE = file
    const calls = mockFetch((c) => {
      if (c.method === "POST") return Response.json({ data: { id: "r", status: "SUCCEEDED", defaultDatasetId: "d", usageTotalUsd: 0.001 } }, { status: 201 })
      if (c.url.includes("/actor-runs/r")) return Response.json({ data: { id: "r", status: "SUCCEEDED", defaultDatasetId: "d", usageTotalUsd: 0.001 } })
      if (c.url.includes("/users/me/limits")) return Response.json({ data: {} })
      return Response.json([{ jobTitle: "EM", companyName: "Acme", jobUrl: "https://www.glassdoor.de/job/1" }])
    })
    capture("stdout")
    expect(await runSearch({ ...base, board: "gd", country: "de" })).toBe(0)
    expect(calls[0].body).toMatchObject({ country: "https://www.glassdoor.de" })
    const before = calls.length
    const err = capture("stderr")
    expect(await runSearch({ ...base, board: "gd", country: "LU" })).toBe(1)
    expect(JSON.parse(err()).code).toBe("BAD_ARG")
    expect(calls.length).toBe(before)
  })
})
