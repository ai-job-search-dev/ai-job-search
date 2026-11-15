import { afterEach, describe, expect, test } from "bun:test"
import { runCLI } from "./helpers"
import { runSearch } from "../src/commands/search"
import fixture from "./fixtures/search-posts.json"

const originalFetch = globalThis.fetch
const originalWrite = process.stdout.write

afterEach(() => {
  globalThis.fetch = originalFetch
  process.stdout.write = originalWrite
})

function captureStdout(): () => string {
  let out = ""
  process.stdout.write = ((c: string | Uint8Array) => {
    out += c.toString()
    return true
  }) as typeof process.stdout.write
  return () => out
}

describe("runSearch (mocked fetch, no network)", () => {
  test("returns the contract JSON shape from the fixture", async () => {
    const urls: string[] = []
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input)
      urls.push(url)
      if (!url.startsWith("https://berlinstartupjobs.com/wp-json/")) throw new Error(`unexpected request ${url}`)
      return new Response(JSON.stringify(fixture), { headers: { "x-wp-total": "12" } })
    }) as typeof fetch
    const read = captureStdout()
    const code = await runSearch({ query: "engineering manager", page: 1, remote: false, format: "json" })
    expect(code).toBe(0)
    const out = JSON.parse(read())
    expect(out.meta).toEqual({ count: 3, page: 1, total: 12 })
    expect(out.results[0]).toMatchObject({ id: "453406", title: "Tech Lead (m/f/d)", url: "https://berlinstartupjobs.com/engineering/tech-lead-m-f-d-superchat/" })
    expect(urls).toHaveLength(1)
  })

  test("a page past the end is an empty result, not an error", async () => {
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ code: "rest_post_invalid_page_number" }), { status: 400 })) as typeof fetch
    const read = captureStdout()
    const code = await runSearch({ page: 99, remote: false, format: "json" })
    expect(code).toBe(0)
    expect(JSON.parse(read()).results).toEqual([])
  })

  test("--limit 0 emits zero results", async () => {
    globalThis.fetch = (async () => new Response(JSON.stringify(fixture))) as typeof fetch
    const read = captureStdout()
    await runSearch({ page: 1, limit: 0, remote: false, format: "json" })
    expect(JSON.parse(read()).results).toHaveLength(0)
  })
})

describe("CLI argument validation (exits before any request)", () => {
  test("unknown flag exits 1 with JSON on stderr", async () => {
    const r = await runCLI(["search", "--location", "Berlin"])
    expect(r.exitCode).toBe(1)
    expect(r.stdout).toBe("")
    expect(JSON.parse(r.stderr).code).toBe("UNKNOWN_FLAG")
  })
  test("empty query is rejected", async () => {
    const r = await runCLI(["search", "-q", ""])
    expect(r.exitCode).toBe(1)
    expect(JSON.parse(r.stderr).code).toBe("BAD_ARG")
  })
  test("bad --jobage is rejected", async () => {
    const r = await runCLI(["search", "--jobage", "0.5"])
    expect(r.exitCode).toBe(1)
    expect(JSON.parse(r.stderr).code).toBe("BAD_ARG")
  })
  test("detail without an id is rejected", async () => {
    const r = await runCLI(["detail"])
    expect(r.exitCode).toBe(1)
    expect(JSON.parse(r.stderr).code).toBe("NO_ID")
  })
  test("detail with a foreign URL is rejected", async () => {
    const r = await runCLI(["detail", "https://example.com/jobs/1"])
    expect(r.exitCode).toBe(1)
    expect(JSON.parse(r.stderr).code).toBe("BAD_ID")
  })
})
