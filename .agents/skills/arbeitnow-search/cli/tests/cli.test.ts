import { afterEach, describe, expect, test } from "bun:test"
import { readFileSync } from "fs"
import { join } from "path"
import { runCLI } from "./helpers"
import { runSearch } from "../src/commands/search"

const page = readFileSync(join(import.meta.dir, "fixtures", "api-page.json"), "utf8")
const originalFetch = globalThis.fetch
const originalOut = process.stdout.write
afterEach(() => {
  globalThis.fetch = originalFetch
  process.stdout.write = originalOut
})
function capture(): () => string {
  let out = ""
  process.stdout.write = ((c: string | Uint8Array) => ((out += c.toString()), true)) as typeof process.stdout.write
  return () => out
}

describe("runSearch (mocked fetch, no network)", () => {
  test("scans the requested pages, filters client-side, contract JSON shape", async () => {
    const urls: string[] = []
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const u = String(input)
      if (!u.startsWith("https://www.arbeitnow.com/api/job-board-api?")) throw new Error(`unexpected request ${u}`)
      urls.push(u)
      return new Response(page)
    }) as typeof fetch
    const read = capture()
    expect(await runSearch({ query: "backend", remote: false, visa: true, page: 1, pages: 2, format: "json" })).toBe(0)
    const out = JSON.parse(read())
    expect(urls).toEqual([
      "https://www.arbeitnow.com/api/job-board-api?page=1&visa_sponsorship=true",
      "https://www.arbeitnow.com/api/job-board-api?page=2&visa_sponsorship=true",
    ])
    expect(out.meta).toMatchObject({ page: 1, pages_scanned: 2, visa_sponsorship_filter: true })
    expect(out.results.every((j: { title: string }) => /backend/i.test(j.title))).toBe(true)
  })
  test("--location narrows by substring", async () => {
    globalThis.fetch = (async () => new Response(page)) as typeof fetch
    const read = capture()
    await runSearch({ location: "cardiff", remote: false, visa: false, page: 1, pages: 1, format: "json" })
    expect(JSON.parse(read()).results.map((j: { location: string }) => j.location)).toEqual(["Cardiff"])
  })
})

describe("CLI argument validation (exits before any request)", () => {
  test("unknown flag", async () => {
    const r = await runCLI(["search", "--salary", "1"])
    expect(r.exitCode).toBe(1)
    expect(r.stdout).toBe("")
    expect(JSON.parse(r.stderr).code).toBe("UNKNOWN_FLAG")
  })
  test("--pages above the cap", async () => {
    const r = await runCLI(["search", "--pages", "9"])
    expect(JSON.parse(r.stderr).code).toBe("BAD_ARG")
  })
  test("detail with a foreign URL", async () => {
    const r = await runCLI(["detail", "https://example.com/jobs/companies/a/b"])
    expect(JSON.parse(r.stderr).code).toBe("BAD_ID")
  })
})
