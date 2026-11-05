import { describe, expect, test } from "bun:test"
import { normalizeSlug } from "../src/helpers.js"
import { runCLI } from "./helpers.js"

describe("jobdanmark-search normalizeSlug", () => {
  test("accepts portal URLs without a scheme", () => {
    for (const host of ["jobdanmark.dk", "www.jobdanmark.dk", "m.jobdanmark.dk", "JOBDANMARK.DK"]) {
      expect(normalizeSlug(` ${host}/job/software-udvikler-12345?ref=search#apply `)).toBe("software-udvikler-12345")
    }
  })

  test("rejects foreign hosts, non-web schemes and job paths only in a query", () => {
    for (const value of [
      "https://example.com/job/304212/",
      "https://greenhouse.io/job/12345",
      "https://jobdanmark.dk.example.com/job/304212/",
      "ftp://jobdanmark.dk/job/304212/",
      "https://jobdanmark.dk/?next=/job/304212/",
      "example.com/job/304212/",
      "jobdanmark.dk.evil.io/job/304212/",
      "jobdanmark.dk@evil.io/job/304212/",
      "jobdanmark.dk/?next=/job/304212/",
    ]) expect(normalizeSlug(value)).toBeNull()
  })

  test("accepts the portal's www host and HTTP links", () => {
    expect(normalizeSlug("http://www.jobdanmark.dk/job/software-udvikler-12345/")).toBe("software-udvikler-12345")
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

  test("accepts bare slug", () => {
    expect(normalizeSlug("software-udvikler-12345")).toBe("software-udvikler-12345")
    expect(normalizeSlug("  senior_dev_67890  ")).toBe("senior_dev_67890")
  })

  test("extracts slug from full URL with trailing slash", () => {
    expect(normalizeSlug("https://jobdanmark.dk/job/software-udvikler-12345/")).toBe("software-udvikler-12345")
  })

  test("extracts slug from full URL without trailing slash", () => {
    expect(normalizeSlug("https://jobdanmark.dk/job/software-udvikler-12345")).toBe("software-udvikler-12345")
  })

  test("extracts slug from relative URL path", () => {
    expect(normalizeSlug("/job/software-udvikler-12345")).toBe("software-udvikler-12345")
    expect(normalizeSlug("/job/software-udvikler-12345/")).toBe("software-udvikler-12345")
  })

  test("extracts slug from URL with query parameters and hash fragments", () => {
    expect(normalizeSlug("https://jobdanmark.dk/job/software-udvikler-12345?utm_source=test&ref=1")).toBe(
      "software-udvikler-12345",
    )
    expect(normalizeSlug("https://jobdanmark.dk/job/software-udvikler-12345#apply")).toBe(
      "software-udvikler-12345",
    )
  })

  test("rejects empty string and invalid URLs", () => {
    expect(normalizeSlug("")).toBeNull()
    expect(normalizeSlug("   ")).toBeNull()
    expect(normalizeSlug("https://example.com/other/test")).toBeNull()
  })

  test("CLI detail command rejects invalid slug format with BAD_ID", async () => {
    const result = await runCLI(["detail", "https://invalid.com/not-a-job"])
    expect(result.exitCode).toBe(1)
    const err = JSON.parse(result.stderr)
    expect(err.code).toBe("BAD_ID")
  })
})
