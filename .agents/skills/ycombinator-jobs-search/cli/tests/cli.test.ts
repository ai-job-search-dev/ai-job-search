import { afterEach, describe, expect, test } from "bun:test"
import { readFileSync } from "fs"
import { join } from "path"
import { runCLI } from "./helpers"
import { runSearch } from "../src/commands/search"

const page = readFileSync(join(import.meta.dir, "fixtures", "role-page.html"), "utf8")
const NOW = new Date("2026-11-11T00:00:00Z")
const originalFetch = globalThis.fetch
const originalOut = process.stdout.write
const originalErr = process.stderr.write

afterEach(() => {
  globalThis.fetch = originalFetch
  process.stdout.write = originalOut
  process.stderr.write = originalErr
})

function capture(stream: "stdout" | "stderr"): () => string {
  let out = ""
  process[stream].write = ((c: string | Uint8Array) => {
    out += c.toString()
    return true
  }) as typeof process.stdout.write
  return () => out
}

function mockPage() {
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input)
    if (!url.startsWith("https://www.ycombinator.com/jobs/role/")) throw new Error(`unexpected request ${url}`)
    return new Response(page)
  }) as typeof fetch
}

describe("runSearch (mocked fetch, no network)", () => {
  test("contract JSON shape", async () => {
    mockPage()
    const read = capture("stdout")
    expect(await runSearch({ role: "software-engineer", format: "json", now: NOW })).toBe(0)
    const out = JSON.parse(read())
    expect(out.meta).toEqual({ count: 3, page: 1 })
    expect(out.results[0].id).toBe("karat-financial/gD75RKp-founding-security-engineer")
  })
  test("--query filters client-side, every word must match", async () => {
    mockPage()
    const read = capture("stdout")
    await runSearch({ role: "software-engineer", query: "security engineer", format: "json", now: NOW })
    expect(JSON.parse(read()).results.map((j: { company: string }) => j.company)).toEqual(["Karat Financial"])
  })
  test("--jobage uses the approximate date", async () => {
    mockPage()
    const read = capture("stdout")
    await runSearch({ role: "software-engineer", jobage: 30, format: "json", now: NOW })
    expect(JSON.parse(read()).results).toHaveLength(1)
  })
  test("unknown role -> UNKNOWN_ROLE instead of software-engineer results", async () => {
    mockPage()
    const err = capture("stderr")
    expect(await runSearch({ role: "engineering-manager", format: "json", now: NOW })).toBe(1)
    expect(JSON.parse(err()).code).toBe("UNKNOWN_ROLE")
  })
  test("location page that does not match -> UNKNOWN_LOCATION", async () => {
    mockPage()
    const err = capture("stderr")
    expect(await runSearch({ role: "software-engineer", location: "nowhere", format: "json", now: NOW })).toBe(1)
    expect(JSON.parse(err()).code).toBe("UNKNOWN_LOCATION")
  })
})

describe("CLI argument validation (exits before any request)", () => {
  test("unknown flag", async () => {
    const r = await runCLI(["search", "--salary", "1"])
    expect(r.exitCode).toBe(1)
    expect(r.stdout).toBe("")
    expect(JSON.parse(r.stderr).code).toBe("UNKNOWN_FLAG")
  })
  test("--page 2 is rejected", async () => {
    const r = await runCLI(["search", "--page", "2"])
    expect(JSON.parse(r.stderr).code).toBe("BAD_ARG")
  })
  test("detail with a foreign URL", async () => {
    const r = await runCLI(["detail", "https://example.com/companies/a/jobs/b"])
    expect(JSON.parse(r.stderr).code).toBe("BAD_ID")
  })
})
