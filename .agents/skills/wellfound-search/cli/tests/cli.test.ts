import { afterEach, describe, expect, test } from "bun:test"
import { readFileSync } from "fs"
import { join } from "path"
import { runCLI } from "./helpers"
import { runSearch } from "../src/commands/search"

const page = readFileSync(join(import.meta.dir, "fixtures", "role-page.html"), "utf8")
const originalFetch = globalThis.fetch
const originalWrite = process.stdout.write
const originalErr = process.stderr.write

afterEach(() => {
  globalThis.fetch = originalFetch
  process.stdout.write = originalWrite
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

describe("runSearch (mocked fetch, no network)", () => {
  test("returns the contract JSON shape and requests only wellfound.com", async () => {
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url !== "https://wellfound.com/role/l/engineering-manager/bangalore") throw new Error(`unexpected request ${url}`)
      return new Response(page)
    }) as typeof fetch
    const read = capture("stdout")
    const code = await runSearch({ query: "engineering manager", location: "bangalore", remote: false, page: 1, format: "json" })
    expect(code).toBe(0)
    const out = JSON.parse(read())
    expect(out.meta).toEqual({ count: 2, page: 1 })
    expect(out.results[0]).toMatchObject({ id: "5024012-engineering-manager", company: "Realty of America" })
  })

  test("a redirect (unknown role/city pair) is a NO_ROLE_PAGE error, not results", async () => {
    globalThis.fetch = (async () => new Response(null, { status: 303, headers: { location: "https://wellfound.com/location/hyderabad" } })) as typeof fetch
    const err = capture("stderr")
    const code = await runSearch({ query: "staff engineer", location: "hyderabad", remote: false, page: 1, format: "json" })
    expect(code).toBe(1)
    expect(JSON.parse(err()).code).toBe("NO_ROLE_PAGE")
  })

  test("--jobage filters on the posting date", async () => {
    globalThis.fetch = (async () => new Response(page)) as typeof fetch
    const read = capture("stdout")
    await runSearch({ query: "engineering manager", location: "bangalore", remote: false, page: 1, jobage: 7, format: "json", now: new Date("2026-11-11T00:00:00Z") })
    expect(JSON.parse(read()).results.map((j: { id: string }) => j.id)).toEqual(["5024012-engineering-manager"])
  })
})

describe("CLI argument validation (exits before any request)", () => {
  test("missing --query exits 1 with JSON on stderr", async () => {
    const r = await runCLI(["search", "-l", "bangalore"])
    expect(r.exitCode).toBe(1)
    expect(r.stdout).toBe("")
    expect(JSON.parse(r.stderr).code).toBe("NO_QUERY")
  })
  test("unknown flag is rejected", async () => {
    const r = await runCLI(["search", "-q", "em", "--salary", "1"])
    expect(JSON.parse(r.stderr).code).toBe("UNKNOWN_FLAG")
  })
  test("--remote with --location is rejected", async () => {
    const r = await runCLI(["search", "-q", "em", "-l", "pune", "--remote"])
    expect(JSON.parse(r.stderr).code).toBe("CONFLICTING_FLAGS")
  })
  test("bare numeric detail id is rejected with a hint", async () => {
    const r = await runCLI(["detail", "4475538"])
    expect(r.exitCode).toBe(1)
    expect(JSON.parse(r.stderr).code).toBe("BAD_ID")
  })
})
