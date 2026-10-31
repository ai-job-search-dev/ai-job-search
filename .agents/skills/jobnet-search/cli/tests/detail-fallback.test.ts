import { afterEach, describe, expect, test } from "bun:test"
import { detail, mapSearchAdToDetail } from "../src/commands/detail"
import type { JobAdRaw } from "../src/commands/search"

const originalFetch = globalThis.fetch
const originalExit = process.exit
const originalLog = console.log
const originalStderrWrite = process.stderr.write

afterEach(() => {
  globalThis.fetch = originalFetch
  process.exit = originalExit
  console.log = originalLog
  process.stderr.write = originalStderrWrite
})

class ExitCalled extends Error {
  constructor(public code: number | undefined) {
    super(`process.exit(${code})`)
  }
}

function throwingExit() {
  process.exit = ((code?: number) => {
    throw new ExitCalled(code)
  }) as unknown as typeof process.exit
}

function captureOutput(): { stdout: string[]; stderr: string[] } {
  const out = { stdout: [] as string[], stderr: [] as string[] }
  console.log = ((...args: unknown[]) => out.stdout.push(args.join(" "))) as typeof console.log
  process.stderr.write = ((chunk: string | Uint8Array) => {
    out.stderr.push(String(chunk))
    return true
  }) as typeof process.stderr.write
  return out
}

async function runDetail(id: string, format = "json"): Promise<{ exit: number | null }> {
  const handler = (detail as unknown as { handler: (ctx: unknown) => Promise<void> }).handler
  try {
    await handler({ flags: { format }, positional: [id], signal: new AbortController().signal })
    return { exit: null }
  } catch (err) {
    if (err instanceof ExitCalled) return { exit: err.code ?? 0 }
    throw err
  }
}

describe("mapSearchAdToDetail (Issue #432 external ad fallback)", () => {
  const sampleAd: JobAdRaw & { jobAdUrl?: string; jobAnnouncementTypeName?: string } = {
    jobAdId: "ext-123",
    title: "AI Technical Artist",
    hiringOrgName: "Tactile Games",
    occupation: "Programmør og systemudvikler",
    conceptUriDa: "http://data.star.dk/esco/occupation/8b6456a3-ae9a-45a0-a65b-fed797521753",
    jobAnnouncementTypeName: "Almindelige vilkår",
    workHourPartTime: false,
    jobAdUrl: "https://job-boards.eu.greenhouse.io/tactilegames/jobs/4890782101",
    hasLogo: true,
    logoUrl: "/bff/logo/123",
    workPlaceAddress: "  Trekronergade 26  ",
    cvr: "32319882",
    description: "<p>Great job opening at Tactile.</p>",
    applicationDeadline: "2026-12-05T00:00:00+01:00",
    applicationDeadlineStatus: "ExpirationDate",
    country: "Danmark",
    municipality: "København",
    postalCode: 2500,
    postalDistrictName: "Valby",
    publicationDate: "2026-09-05T00:00:00+02:00",
    isExternal: true,
    isSeen: false,
    isFavorite: false,
  }

  test("maps all key fields correctly to DetailApiResponse format", () => {
    const detail = mapSearchAdToDetail(sampleAd)

    expect(detail.id).toBe("ext-123")
    expect(detail.title).toBe("AI Technical Artist")
    expect(detail.body).toBe("<p>Great job opening at Tactile.</p>")
    expect(detail.publicationDateTime).toBe("2026-09-05T00:00:00+02:00")
    expect(detail.isExternal).toBe(true)
    expect(detail.source).toBe("search-fallback")
    expect(detail.views).toBeNull()
    expect(detail.approvalStatus).toBeNull()
    expect(detail.isAnonymousEmployer).toBeNull()
    expect(detail.employer.name).toBe("Tactile Games")
    expect(detail.employer.cvrNumber).toBe("32319882")
    expect(detail.employer.hasCompanyLogo).toBe(true)
    expect(detail.job.type).toBe("Almindelige vilkår")
    expect(detail.job.address.streetName).toBe("Trekronergade 26")
    expect(detail.job.address.city).toBe("Valby")
    expect(detail.job.address.postalCode).toBe("2500")
    expect(detail.job.address.municipality).toBe("København")
    expect(detail.job.address.countryCode).toBe("DK")
    expect(detail.job.address.countryName).toBe("Danmark")
    expect(detail.job.isPartTime).toBe(false)
    expect(detail.job.noFixedWorkplace).toBeNull()
    expect(detail.job.isLimitedPeriod).toBeNull()
    expect(detail.job.isDisabilityFriendly).toBeNull()
    expect(detail.job.preferredLabelDa).toBe("Programmør og systemudvikler")
    expect(detail.job.conceptUriDa).toBe("http://data.star.dk/esco/occupation/8b6456a3-ae9a-45a0-a65b-fed797521753")
    expect(detail.application.deadlineDate).toBe("2026-12-05T00:00:00+01:00")
    expect(detail.application.availablePositions).toBeNull()
    expect(detail.application.url).toBe("https://job-boards.eu.greenhouse.io/tactilegames/jobs/4890782101")
    expect(detail.application.isApplicationDeadlineASAP).toBe(false)
  })

  test("handles empty or whitespace address gracefully", () => {
    const detail = mapSearchAdToDetail({
      ...sampleAd,
      workPlaceAddress: "   ",
      postalDistrictName: null,
      municipality: null,
      postalCode: null,
    })

    expect(detail.job.address.streetName).toBeNull()
    expect(detail.job.address.city).toBeNull()
    expect(detail.job.address.postalCode).toBeNull()
    expect(detail.job.address.municipality).toBeNull()
  })

  test("flags undisclosed deadline as ASAP", () => {
    const detail = mapSearchAdToDetail({
      ...sampleAd,
      applicationDeadlineStatus: "NotDisclosed",
    })

    expect(detail.application.isApplicationDeadlineASAP).toBe(true)
  })
})

describe("detail command handler external ad fallback path", () => {
  const sampleAd: JobAdRaw & { jobAdUrl?: string; jobAnnouncementTypeName?: string } = {
    jobAdId: "ext-123",
    title: "AI Technical Artist",
    hiringOrgName: "Tactile Games",
    occupation: "Programmør og systemudvikler",
    conceptUriDa: "http://data.star.dk/esco/occupation/8b6456a3-ae9a-45a0-a65b-fed797521753",
    jobAnnouncementTypeName: "Almindelige vilkår",
    workHourPartTime: false,
    jobAdUrl: "https://job-boards.eu.greenhouse.io/tactilegames/jobs/4890782101",
    hasLogo: true,
    logoUrl: "/bff/logo/123",
    workPlaceAddress: "  Trekronergade 26  ",
    cvr: "32319882",
    description: "<p>Great job opening at Tactile.</p>",
    applicationDeadline: "2026-12-05T00:00:00+01:00",
    applicationDeadlineStatus: "ExpirationDate",
    country: "Danmark",
    municipality: "København",
    postalCode: 2500,
    postalDistrictName: "Valby",
    publicationDate: "2026-09-05T00:00:00+02:00",
    isExternal: true,
    isSeen: false,
    isFavorite: false,
  }

  test("emits source: 'search-fallback' on stdout and keeps stderr empty on 404 fallback", async () => {
    throwingExit()
    const out = captureOutput()

    globalThis.fetch = (async (input: string | URL | Request) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url
      if (url.includes("/JobAdDetails/ext-123")) {
        return new Response("", { status: 404, statusText: "Not Found" })
      }
      if (url.includes("/FindJob/Search")) {
        return new Response(
          JSON.stringify({
            jobAds: [sampleAd],
            searchFacets: {},
            totalJobAdCount: 1,
            searchString: "ext-123",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      }
      return new Response("", { status: 500, statusText: "Unexpected URL" })
    }) as unknown as typeof fetch

    const result = await runDetail("ext-123")

    expect(result.exit).toBeNull()
    expect(out.stderr.join("")).toBe("")

    const parsed = JSON.parse(out.stdout.join("\n"))
    expect(parsed.id).toBe("ext-123")
    expect(parsed.title).toBe("AI Technical Artist")
    expect(parsed.isExternal).toBe(true)
    expect(parsed.source).toBe("search-fallback")
  })

  test("reports NOT_FOUND on stderr when fallback search returns no matching ad", async () => {
    throwingExit()
    const out = captureOutput()

    globalThis.fetch = (async (input: string | URL | Request) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url
      if (url.includes("/JobAdDetails/ext-999")) {
        return new Response("", { status: 404, statusText: "Not Found" })
      }
      if (url.includes("/FindJob/Search")) {
        return new Response(
          JSON.stringify({
            jobAds: [],
            searchFacets: {},
            totalJobAdCount: 0,
            searchString: "ext-999",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      }
      return new Response("", { status: 500, statusText: "Unexpected URL" })
    }) as unknown as typeof fetch

    const result = await runDetail("ext-999")

    expect(result.exit).toBe(1)
    expect(out.stdout.join("")).toBe("")

    const err = JSON.parse(out.stderr.join("").trim())
    expect(err).toEqual({
      error: "Job ad not found",
      code: "NOT_FOUND",
    })
  })

  test("formats plain output cleanly without stderr note on 404 fallback", async () => {
    throwingExit()
    const out = captureOutput()

    globalThis.fetch = (async (input: string | URL | Request) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url
      if (url.includes("/JobAdDetails/ext-123")) {
        return new Response("", { status: 404, statusText: "Not Found" })
      }
      if (url.includes("/FindJob/Search")) {
        return new Response(
          JSON.stringify({
            jobAds: [sampleAd],
            searchFacets: {},
            totalJobAdCount: 1,
            searchString: "ext-123",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      }
      return new Response("", { status: 500, statusText: "Unexpected URL" })
    }) as unknown as typeof fetch

    const result = await runDetail("ext-123", "plain")

    expect(result.exit).toBeNull()
    expect(out.stderr.join("")).toBe("")
    const stdout = out.stdout.join("\n")
    expect(stdout).toContain("Title: AI Technical Artist")
    expect(stdout).toContain("Employer: Tactile Games")
    expect(stdout).toContain("Source: search-fallback (detail unavailable, synthesised from search)")
  })

  test("formats table output cleanly with source line on 404 fallback", async () => {
    throwingExit()
    const out = captureOutput()

    globalThis.fetch = (async (input: string | URL | Request) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url
      if (url.includes("/JobAdDetails/ext-123")) {
        return new Response("", { status: 404, statusText: "Not Found" })
      }
      if (url.includes("/FindJob/Search")) {
        return new Response(
          JSON.stringify({
            jobAds: [sampleAd],
            searchFacets: {},
            totalJobAdCount: 1,
            searchString: "ext-123",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        )
      }
      return new Response("", { status: 500, statusText: "Unexpected URL" })
    }) as unknown as typeof fetch

    const result = await runDetail("ext-123", "table")

    expect(result.exit).toBeNull()
    expect(out.stderr.join("")).toBe("")
    const stdout = out.stdout.join("\n")
    expect(stdout).toContain("Title:       AI Technical Artist")
    expect(stdout).toContain("Employer:    Tactile Games")
    expect(stdout).toContain("Source:      search-fallback (detail unavailable, synthesised from search)")
  })

  test("a standard ad from JobAdDetails does not set source: 'search-fallback'", async () => {
    throwingExit()
    const out = captureOutput()

    const standardDetail = {
      id: "std-456",
      title: "Senior Backend Developer",
      body: "<p>Work at standard employer.</p>",
      publicationDateTime: "2026-09-01T00:00:00+02:00",
      unpublicationDateTime: null,
      approvalStatus: null,
      views: 12,
      createdDateTime: "2026-09-01T00:00:00+02:00",
      updatedDateTime: "2026-09-01T00:00:00+02:00",
      isAnonymousEmployer: false,
      hasLogo: false,
      logoUrl: null,
      employer: {
        cvrNumber: "12345678",
        pNumber: null,
        name: "Standard Corp",
        hasCompanyLogo: false,
      },
      job: {
        type: "FullTime",
        address: {
          streetName: "Main St",
          city: "Aarhus",
          postalCode: "8000",
          municipality: "Aarhus",
          countryCode: "DK",
          countryName: "Danmark",
        },
        noFixedWorkplace: null,
        isLimitedPeriod: null,
        isDisabilityFriendly: null,
        isPartTime: false,
        employmentDate: null,
        conceptUriDa: null,
        preferredLabelDa: null,
        driversLicenses: [],
        classifications: [],
        shifts: [],
        isFavorite: false,
      },
      application: {
        deadlineDate: "2026-10-01T00:00:00+02:00",
        availablePositions: 1,
        contactPersons: [],
        url: null,
        urlText: null,
        isApplicationDeadlineASAP: false,
      },
      organisationTypeId: null,
      user: null,
    }

    globalThis.fetch = (async (input: string | URL | Request) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url
      if (url.includes("/JobAdDetails/std-456")) {
        return new Response(JSON.stringify(standardDetail), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      return new Response("", { status: 500, statusText: "Unexpected URL" })
    }) as unknown as typeof fetch

    const result = await runDetail("std-456")

    expect(result.exit).toBeNull()
    expect(out.stderr.join("")).toBe("")

    const parsed = JSON.parse(out.stdout.join("\n"))
    expect(parsed.id).toBe("std-456")
    expect(parsed.source).toBeUndefined()
  })
})
