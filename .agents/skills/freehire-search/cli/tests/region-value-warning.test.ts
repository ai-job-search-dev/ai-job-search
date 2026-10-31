import { afterEach, describe, expect, test } from "bun:test"
import { runSearch, type SearchOpts } from "../src/commands/search"

// freehire's contract defines a closed region vocabulary (nine buckets plus `none`),
// so a value outside it is a wrong parameter, not a thin market: `us` is a country
// code that still matches a handful of stray-tagged postings. The warning therefore
// fires on the value alone - no network, and not only when the result is empty,
// which is the case a count-based check would miss.

const originalFetch = globalThis.fetch
const originalStdoutWrite = process.stdout.write
const originalStderrWrite = process.stderr.write

const DOCUMENTED = ["global", "north_america", "latam", "eu", "uk", "mena", "africa", "apac", "cis", "none"]

function capture(stream: "stdout" | "stderr"): { read: () => string } {
  let buf = ""
  const write = ((chunk: string | Uint8Array) => {
    buf += chunk.toString()
    return true
  }) as typeof process.stdout.write
  if (stream === "stdout") process.stdout.write = write
  else process.stderr.write = write
  return { read: () => buf }
}

function job() {
  return {
    public_slug: "senior-fullstack-engineer-acme-1a2b3c4d",
    source: "greenhouse",
    external_id: "1",
    url: "https://example.com/jobs/1",
    title: "Senior Fullstack Engineer",
    company: "Acme",
    company_slug: "acme",
    location: "Remote",
    description: "body",
    skills: ["typescript"],
    work_mode: "remote",
    regions: ["us"],
    countries: [],
    cities: [],
    posted_at: "2026-10-17T00:00:00Z",
    created_at: "2026-10-17T00:00:00Z",
    enrichment: {},
  }
}

/** Serve one canned search answer; returns the URLs the CLI asked for. */
function stubSearch(total: number): { urls: string[] } {
  const urls: string[] = []
  globalThis.fetch = (async (input: string | URL | Request) => {
    urls.push(typeof input === "string" ? input : input instanceof URL ? input.href : input.url)
    const results = total > 0 ? [job()] : []
    return Response.json({ data: results, meta: { total } })
  }) as typeof fetch
  return { urls }
}

function opts(over: Partial<SearchOpts> = {}): SearchOpts {
  return {
    jobage: 14,
    page: 1,
    limit: 25,
    format: "json",
    descriptionFormat: "markdown",
    regions: [],
    countries: [],
    cities: [],
    seniority: [],
    category: [],
    skills: [],
    facets: {},
    ...over,
  }
}

afterEach(() => {
  globalThis.fetch = originalFetch
  process.stdout.write = originalStdoutWrite
  process.stderr.write = originalStderrWrite
})

describe("--region outside the closed vocabulary", () => {
  test("warns on stderr, keeps stdout parseable, exits 0", async () => {
    stubSearch(0)
    const stdout = capture("stdout")
    const stderr = capture("stderr")
    const code = await runSearch(opts({ query: "typescript", regions: ["us"] }))
    expect(code).toBe(0)
    expect(JSON.parse(stdout.read()).meta.total).toBe(0)
    const line = stderr.read()
    expect(line).toContain("REGION_VALUE_UNKNOWN")
    expect(line).toContain("--country US")
    // One line, as asked: a stderr consumer reads a line, not a paragraph.
    expect(line.trimEnd().split("\n")).toHaveLength(1)
  })

  test("warns even when the query returns postings", async () => {
    // The case a zero-result check misses: a broad query returns real hits under a
    // stray tag and the parameter is still wrong.
    stubSearch(3)
    capture("stdout")
    const stderr = capture("stderr")
    const code = await runSearch(opts({ query: "software engineer", regions: ["us"] }))
    expect(code).toBe(0)
    expect(stderr.read()).toContain("REGION_VALUE_UNKNOWN")
  })

  test("covers the --facet regions= escape hatch", async () => {
    stubSearch(0)
    capture("stdout")
    const stderr = capture("stderr")
    await runSearch(opts({ facets: { regions: ["us"] } }))
    expect(stderr.read()).toContain("REGION_VALUE_UNKNOWN")
  })

  test("compares case-insensitively", async () => {
    stubSearch(0)
    capture("stdout")
    const upperUnknown = capture("stderr")
    await runSearch(opts({ regions: ["US"] }))
    expect(upperUnknown.read()).toContain("REGION_VALUE_UNKNOWN")

    process.stderr.write = originalStderrWrite
    const upperKnown = capture("stderr")
    await runSearch(opts({ regions: ["EU"] }))
    expect(upperKnown.read()).toBe("")
  })

  test("names only the values outside the vocabulary", async () => {
    stubSearch(0)
    capture("stdout")
    const stderr = capture("stderr")
    await runSearch(opts({ regions: ["eu", "us", "north_america"] }))
    const warning = JSON.parse(stderr.read()).warning as string
    expect(warning).toContain("--region us is not a region value")
    expect(warning).not.toContain("--region eu")
  })

  test("stays silent for every documented value", async () => {
    for (const value of DOCUMENTED) {
      stubSearch(0)
      capture("stdout")
      const stderr = capture("stderr")
      await runSearch(opts({ regions: [value] }))
      expect(stderr.read()).toBe("")
      process.stderr.write = originalStderrWrite
    }
  })

  test("warns after a table, not only after JSON", async () => {
    stubSearch(1)
    const stdout = capture("stdout")
    const stderr = capture("stderr")
    await runSearch(opts({ format: "table", regions: ["us"] }))
    expect(stdout.read()).toContain("Senior Fullstack Engineer")
    expect(stderr.read()).toContain("REGION_VALUE_UNKNOWN")
  })

  test("does not blame none when another value is the invalid one", async () => {
    stubSearch(0)
    capture("stdout")
    const stderr = capture("stderr")
    await runSearch(opts({ regions: ["none", "us"] }))
    const warning = JSON.parse(stderr.read()).warning as string
    expect(warning).toContain("--region us is not a region value")
    expect(warning).toContain("--country US")
  })

  test("no region filter means no warning and one request", async () => {
    const stub = stubSearch(0)
    capture("stdout")
    const stderr = capture("stderr")
    await runSearch(opts({ query: "typescript" }))
    expect(stderr.read()).toBe("")
    expect(stub.urls).toHaveLength(1)
  })
})
