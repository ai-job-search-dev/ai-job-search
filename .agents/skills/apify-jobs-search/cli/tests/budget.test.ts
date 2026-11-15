import { afterEach, beforeEach, describe, expect, test } from "bun:test"
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "fs"
import { tmpdir } from "os"
import { join } from "path"
import { BudgetError, cycleStart, reserve, settle, status, syncRemote, type Clock } from "../src/budget"

let dir: string
let path: string
let t: number
const clock: Clock = {
  now: () => t,
  sleep: async (ms) => {
    t += ms
  },
}
const env = { FIRECRAWL_RPM: "3", FIRECRAWL_CONCURRENCY: "2", APIFY_RPM: "100", APIFY_CONCURRENCY: "1" }
const o = () => ({ env, clock, path })

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "budget-test-"))
  path = join(dir, "api_budget.json")
  t = Date.parse("2026-11-15T10:00:00Z")
})
afterEach(() => rmSync(dir, { recursive: true, force: true }))

async function code(p: Promise<unknown>): Promise<string> {
  try {
    await p
    return "OK"
  } catch (e) {
    return e instanceof BudgetError ? e.code : String(e)
  }
}

describe("cycleStart", () => {
  test("calendar month by default, previous month before the cycle day", () => {
    expect(cycleStart(new Date("2026-11-15T00:00:00Z"), 1)).toBe("2026-11-01")
    expect(cycleStart(new Date("2026-11-15T00:00:00Z"), 20)).toBe("2026-10-20")
    expect(cycleStart(new Date("2026-01-05T00:00:00Z"), 10)).toBe("2025-12-10")
  })
})

describe("allowance, reserve and run budget", () => {
  test("scrape stops at 80% of the allowance; --allow-reserve may use the rest", async () => {
    const big = { ...env, FIRECRAWL_RPM: "1000", FIRECRAWL_CONCURRENCY: "100" }
    for (let i = 0; i < 4; i++) await settle(await reserve("firecrawl", 200, { env: big, clock, path }), 200, { env: big, clock, path })
    expect(await code(reserve("firecrawl", 1, { env: big, clock, path }))).toBe("BUDGET_CEILING")
    const tk = await reserve("firecrawl", 150, { env: big, clock, path, allowReserve: true })
    await settle(tk, 150, { env: big, clock, path })
    expect(await code(reserve("firecrawl", 51, { env: big, clock, path, allowReserve: true }))).toBe("BUDGET_CEILING")
    expect((await status("firecrawl", { env: big, clock, path })).used).toBe(950)
  })

  test("per-run budget is enforced by run id", async () => {
    const a = await reserve("apify", 0.3, { ...o(), runId: "r1", runBudget: 0.5 })
    await settle(a, 0.3, o())
    expect(await code(reserve("apify", 0.3, { ...o(), runId: "r1", runBudget: 0.5 }))).toBe("RUN_BUDGET")
    expect(await code(reserve("apify", 0.3, { ...o(), runId: "r2", runBudget: 0.5 }).then((tk) => settle(tk, 0.3, o())))).toBe("OK")
  })

  test("settle replaces the estimate with the actual cost", async () => {
    const tk = await reserve("firecrawl", 4, o())
    await settle(tk, 2, o())
    expect((await status("firecrawl", o())).used).toBe(2)
  })

  test("a new billing cycle resets usage", async () => {
    await settle(await reserve("firecrawl", 5, o()), 5, o())
    t = Date.parse("2026-12-01T00:00:01Z")
    const st = await status("firecrawl", o())
    expect(st.used).toBe(0)
    expect(st.period_start).toBe("2026-12-01")
  })
})

describe("rate and concurrency limits", () => {
  test("waits for a per-minute slot, then proceeds", async () => {
    for (let i = 0; i < 3; i++) await settle(await reserve("firecrawl", 1, o()), 1, o())
    const before = t
    await settle(await reserve("firecrawl", 1, o()), 1, o())
    expect(t - before).toBeGreaterThanOrEqual(59_000)
  })

  test("gives up with RATE_LIMITED rather than sending when no slot frees in time", async () => {
    for (let i = 0; i < 3; i++) await settle(await reserve("firecrawl", 1, o()), 1, o())
    expect(await code(reserve("firecrawl", 1, { ...o(), maxWaitMs: 5_000 }))).toBe("RATE_LIMITED")
  })

  test("in-flight requests count against concurrency until settled", async () => {
    await reserve("apify", 0.1, o())
    expect(await code(reserve("apify", 0.1, { ...o(), maxWaitMs: 3_000 }))).toBe("RATE_LIMITED")
  })

  test("a 429 pauses the provider; a 402 stops it for the cycle", async () => {
    await settle(await reserve("firecrawl", 1, o()), 0, { ...o(), rateLimitedFor: 120 })
    expect((await status("firecrawl", o())).paused_until).not.toBeNull()
    expect(await code(reserve("firecrawl", 1, { ...o(), maxWaitMs: 60_000 }))).toBe("RATE_LIMITED")
    await settle(await reserve("apify", 0.1, o()), 0, { ...o(), exhausted: true })
    expect(await code(reserve("apify", 0.1, o()))).toBe("BUDGET_EXHAUSTED")
  })
})

describe("provider sync", () => {
  test("remote usage only raises the local figure", async () => {
    await settle(await reserve("apify", 1, o()), 1, o())
    await syncRemote("apify", { used: 0.4, limit: 5 }, o())
    expect((await status("apify", o())).used).toBe(1)
    await syncRemote("apify", { used: 2.5, limit: 5 }, o())
    expect((await status("apify", o())).used).toBe(2.5)
    await syncRemote("apify", { used: 5, limit: 5 }, o())
    expect((await status("apify", o())).exhausted).toBe(true)
  })

  test("a corrupt ledger is an error, never a silent reset", async () => {
    writeFileSync(path, "{not json")
    expect(await code(status("firecrawl", o()))).toBe("BAD_LEDGER")
    expect(readFileSync(path, "utf8")).toBe("{not json")
  })

  test("suggested run budget splits what is left over the remaining runs", async () => {
    const st = await status("firecrawl", { ...o(), env: { ...env, SCRAPE_RUNS_PER_MONTH: "8" } })
    expect(st.scrape_ceiling).toBe(800)
    expect(st.suggested_run_budget).toBeCloseTo(800 / st.runs_left_estimate, 1)
  })
})
