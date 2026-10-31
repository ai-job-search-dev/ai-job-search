import { describe, expect, test } from "bun:test"
import { normalizeJobId } from "../src/helpers.js"
import { runCLI } from "./helpers.js"

describe("jobbank-search normalizeJobId", () => {
  test("accepts portal URLs without a scheme", () => {
    for (const host of ["jobbank.dk", "www.jobbank.dk", "m.jobbank.dk", "JOBBANK.DK"]) {
      expect(normalizeJobId(` ${host}/job/3117065/company/role?ref=search#apply `)).toBe("3117065")
    }
  })

  test("rejects foreign hosts, non-web schemes and job paths only in a query", () => {
    for (const value of [
      "https://example.com/job/304212/",
      "https://jobbank.dk.example.com/job/304212/",
      "ftp://jobbank.dk/job/304212/",
      "https://jobbank.dk/?next=/job/304212/",
      "example.com/job/304212/",
      "jobbank.dk.evil.io/job/304212/",
      "jobbank.dk@evil.io/job/304212/",
      "jobbank.dk/?next=/job/304212/",
    ]) expect(normalizeJobId(value)).toBeNull()
  })

  test("accepts the portal's www host and HTTP links", () => {
    expect(normalizeJobId("http://www.jobbank.dk/job/304212/")).toBe("304212")
  })

  test("CLI rejects another site's matching job path before any fetch", async () => {
    const proc = Bun.spawn([
      process.execPath, "--preload", `${import.meta.dir}/reject-network.ts`,
      `${import.meta.dir}/../src/cli.ts`, "detail", "https://example.com/job/304212/",
    ], { stdout: "pipe", stderr: "pipe" })
    const [stdout, stderr, code] = await Promise.all([
      new Response(proc.stdout).text(), new Response(proc.stderr).text(), proc.exited,
    ])
    expect(code).toBe(1)
    expect(stdout).toBe("")
    expect(JSON.parse(stderr).code).toBe("BAD_ID")
  })

  test("accepts bare numeric ID", () => {
    expect(normalizeJobId("304212")).toBe("304212")
    expect(normalizeJobId("  12345  ")).toBe("12345")
  })

  test("extracts ID from full URL with trailing slash", () => {
    expect(normalizeJobId("https://jobbank.dk/job/304212/")).toBe("304212")
  })

  test("extracts ID from full URL without trailing slash", () => {
    expect(normalizeJobId("https://jobbank.dk/job/304212")).toBe("304212")
  })

  test("extracts ID from full URL with company/role slug segments", () => {
    expect(normalizeJobId("https://jobbank.dk/job/304212/acme-corp/software-developer")).toBe("304212")
    expect(normalizeJobId("https://jobbank.dk/job/304212/acme-corp/software-developer/")).toBe("304212")
  })

  test("extracts ID from URL with query parameters and hash fragments", () => {
    expect(normalizeJobId("https://jobbank.dk/job/304212?ref=search&page=1")).toBe("304212")
    expect(normalizeJobId("https://jobbank.dk/job/304212#apply")).toBe("304212")
  })

  test("rejects invalid non-numeric strings and unrelated URLs", () => {
    expect(normalizeJobId("abc")).toBeNull()
    expect(normalizeJobId("https://example.com/other/12345")).toBeNull()
    expect(normalizeJobId("")).toBeNull()
  })

  test("CLI detail command rejects invalid ID format with BAD_ID", async () => {
    const result = await runCLI(["detail", "invalid-id-format"])
    expect(result.exitCode).toBe(1)
    const err = JSON.parse(result.stderr)
    expect(err.code).toBe("BAD_ID")
  })
})
