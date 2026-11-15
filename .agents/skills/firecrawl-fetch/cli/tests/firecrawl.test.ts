import { afterEach, beforeEach, describe, expect, test } from "bun:test"
import { mkdtempSync, rmSync } from "fs"
import { tmpdir } from "os"
import { join } from "path"
import { mapResults, runBudget, runDetail, runSearch } from "../src/commands"
import { atsSlug, cleanTitle, setRobotsGate } from "../src/helpers"
import { status } from "../src/budget"

const originalFetch = globalThis.fetch
const originalWrite = process.stdout.write
const originalErr = process.stderr.write
let dir: string
const saved = { ...process.env }

function capture(stream: "stdout" | "stderr"): () => string {
  let out = ""
  process[stream].write = ((c: string | Uint8Array) => {
    out += c.toString()
    return true
  }) as typeof process.stdout.write
  return () => out
}

type Call = { url: string; body: unknown; auth: string | null }
function mockFetch(handler: (c: Call) => Response): Call[] {
  const calls: Call[] = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const c = { url: String(input), body: init?.body ? JSON.parse(String(init.body)) : null, auth: new Headers(init?.headers).get("authorization") }
    calls.push(c)
    return handler(c)
  }) as typeof fetch
  return calls
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "fc-test-"))
  process.env.SCRAPE_BUDGET_FILE = join(dir, "api_budget.json")
  process.env.FIRECRAWL_API_TOKEN = "fc-test"
  process.env.FIRECRAWL_RPM = "100"
  setRobotsGate(() => ({ allowed: true, message: "ok" }))
})
afterEach(() => {
  globalThis.fetch = originalFetch
  process.stdout.write = originalWrite
  process.stderr.write = originalErr
  process.env = { ...saved }
  rmSync(dir, { recursive: true, force: true })
})

describe("ATS parsing", () => {
  test("company slug only from single-posting URLs", () => {
    expect(atsSlug("https://boards.greenhouse.io/razorpay/jobs/4410000")).toBe("razorpay")
    expect(atsSlug("https://jobs.lever.co/acme-ai/0a1b2c3d-1111-2222-3333-444455556666")).toBe("acme-ai")
    expect(atsSlug("https://jobs.ashbyhq.com/acme/8f2b1c3d-1111-2222-3333-444455556666")).toBe("acme")
    expect(atsSlug("https://jobs.lever.co/acme")).toBeNull()
    expect(atsSlug("https://example.com/jobs/1")).toBeNull()
  })

  test("title boilerplate is removed", () => {
    expect(cleanTitle("Job Application for Engineering Manager, Payments at Razorpay", "Razorpay")).toBe("Engineering Manager, Payments")
    expect(cleanTitle("Acme Ai - Staff Backend Engineer", "Acme Ai")).toBe("Staff Backend Engineer")
    expect(cleanTitle("Engineering Manager @ Acme", "Acme")).toBe("Engineering Manager")
  })

  test("listing pages and other sites are dropped", () => {
    const r = mapResults([
      { url: "https://jobs.lever.co/acme", title: "Acme jobs" },
      { url: "https://boards.greenhouse.io/razorpay/jobs/1", title: "Job Application for EM at Razorpay" },
      { url: "https://news.example.com/x", title: "x" },
    ])
    expect(r.map((j) => j.company)).toEqual(["Razorpay"])
  })
})

describe("search (mocked API)", () => {
  test("sends ATS domains and a recency filter, and records the credits used", async () => {
    const calls = mockFetch(() =>
      Response.json({ success: true, creditsUsed: 2, data: { web: [{ url: "https://boards.greenhouse.io/razorpay/jobs/1", title: "Job Application for EM at Razorpay" }] } }),
    )
    const out = capture("stdout")
    expect(await runSearch({ query: "engineering manager", location: "Bengaluru", jobage: 14, limit: 10, format: "json" })).toBe(0)
    expect(calls[0].url).toBe("https://api.firecrawl.dev/v2/search")
    expect(calls[0].auth).toBe("Bearer fc-test")
    expect(calls[0].body).toMatchObject({ query: "engineering manager Bengaluru", limit: 10, tbs: "qdr:m" })
    expect((calls[0].body as { includeDomains: string[] }).includeDomains).toContain("jobs.lever.co")
    const parsed = JSON.parse(out())
    expect(parsed.results[0]).toMatchObject({ title: "EM", company: "Razorpay", date: null })
    expect((await status("firecrawl")).used).toBe(2)
  })

  test("a 429 refunds the estimate and pauses later calls", async () => {
    mockFetch(() => new Response(JSON.stringify({ error: "Request rate limit exceeded" }), { status: 429, headers: { "retry-after": "90" } }))
    const err = capture("stderr")
    expect(await runSearch({ query: "x", limit: 10, format: "json" })).toBe(1)
    expect(JSON.parse(err()).code).toBe("RATE_LIMITED")
    const st = await status("firecrawl")
    expect(st.used).toBe(0)
    expect(st.paused_until).not.toBeNull()
  })

  test("a 402 marks the cycle exhausted", async () => {
    mockFetch(() => new Response(JSON.stringify({ error: "Insufficient credits" }), { status: 402 }))
    capture("stderr")
    expect(await runSearch({ query: "x", limit: 10, format: "json" })).toBe(1)
    expect((await status("firecrawl")).exhausted).toBe(true)
  })

  test("missing token fails before any request is sent", async () => {
    delete process.env.FIRECRAWL_API_TOKEN
    const calls = mockFetch(() => Response.json({}))
    const err = capture("stderr")
    expect(await runSearch({ query: "x", limit: 10, format: "json" })).toBe(1)
    expect(JSON.parse(err()).code).toBe("MISSING_CREDENTIALS")
    expect(calls.length).toBe(0)
    expect((await status("firecrawl")).used).toBe(0)
  })
})

describe("detail (mocked API)", () => {
  test("robots refusal sends nothing and spends nothing", async () => {
    setRobotsGate(() => ({ allowed: false, message: "Disallow: /" }))
    const calls = mockFetch(() => Response.json({}))
    const err = capture("stderr")
    expect(await runDetail({ url: "https://jobs.lever.co/acme/0a1b2c3d-1111-2222-3333-444455556666", maxChars: 1000, format: "json" })).toBe(1)
    expect(JSON.parse(err()).code).toBe("ROBOTS_DISALLOWED")
    expect(calls.length).toBe(0)
  })

  test("returns markdown with invisible characters stripped", async () => {
    mockFetch(() =>
      Response.json({ success: true, data: { markdown: "# EM​ role\n\nBuild payments.", metadata: { title: "Acme - EM", statusCode: 200, url: "https://jobs.lever.co/acme/0a1b2c3d-1111-2222-3333-444455556666" } } }),
    )
    const out = capture("stdout")
    expect(await runDetail({ url: "https://jobs.lever.co/acme/0a1b2c3d-1111-2222-3333-444455556666", maxChars: 1000, format: "json" })).toBe(0)
    const job = JSON.parse(out())
    expect(job.description).toBe("# EM role\n\nBuild payments.")
    expect(job.company).toBe("Acme")
    expect((await status("firecrawl")).used).toBe(1)
  })

  test("a 404 page is NOT_FOUND (expired) and still billed", async () => {
    mockFetch(() => Response.json({ success: true, data: { markdown: "gone", metadata: { statusCode: 404 } } }))
    const err = capture("stderr")
    expect(await runDetail({ url: "https://example.com/job/1", maxChars: 1000, format: "json" })).toBe(1)
    expect(JSON.parse(err()).code).toBe("NOT_FOUND")
    expect((await status("firecrawl")).used).toBe(1)
  })

  test("non-http URLs are rejected", async () => {
    const err = capture("stderr")
    expect(await runDetail({ url: "file:///etc/passwd", maxChars: 1000, format: "json" })).toBe(1)
    expect(JSON.parse(err()).code).toBe("BAD_URL")
  })
})

describe("budget --sync", () => {
  test("adopts the provider's higher usage figure", async () => {
    mockFetch(() => Response.json({ success: true, data: { remainingCredits: 700, planCredits: 1000, billingPeriodStart: null } }))
    const out = capture("stdout")
    expect(await runBudget({ sync: true, format: "json" })).toBe(0)
    expect(JSON.parse(out()).used).toBe(300)
  })
})
