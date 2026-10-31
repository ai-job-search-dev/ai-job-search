import { describe, expect, test } from "bun:test";
import { formatDetailPlain, prepareDetail, type DetailApiResponse } from "../src/commands/detail";

function detail(overrides: Partial<DetailApiResponse> = {}): DetailApiResponse {
  return {
    id: "job-1",
    title: "Data Engineer",
    body: "<p>Build ETL &amp; analytics flows.</p><p>Use SQL&nbsp;daily.</p>",
    publicationDateTime: "2026-07-01T09:00:00+02:00",
    unpublicationDateTime: "2026-08-01T23:59:00+02:00",
    approvalStatus: "Godkendt",
    views: 10,
    createdDateTime: "2026-07-01T08:00:00+02:00",
    updatedDateTime: "2026-07-01T08:30:00+02:00",
    isAnonymousEmployer: false,
    hasLogo: true,
    logoUrl: "/logo/job-1",
    employer: {
      cvrNumber: "12345678",
      pNumber: "87654321",
      name: "Acme",
      hasCompanyLogo: true,
    },
    job: {
      type: "FullTime",
      address: {
        streetName: "Examplevej 1",
        city: "København",
        postalCode: "2100",
        municipality: "København",
        countryCode: "DK",
        countryName: "Danmark",
      },
      noFixedWorkplace: false,
      isLimitedPeriod: false,
      isDisabilityFriendly: false,
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
      deadlineDate: "2026-08-01T23:59:00+02:00",
      availablePositions: 2,
      contactPersons: [],
      url: "https://example.test/apply",
      urlText: "Apply",
      isApplicationDeadlineASAP: false,
    },
    organisationTypeId: null,
    user: null,
    ...overrides,
  };
}

describe("formatDetailPlain", () => {
  test("renders the plain view with cleaned body text and apply URL", () => {
    const formatted = formatDetailPlain(detail());

    expect(formatted).toContain("Title: Data Engineer");
    expect(formatted).toContain("Employer: Acme");
    expect(formatted).toContain("Location: København, Danmark");
    expect(formatted).toContain("Apply: https://example.test/apply");
    expect(formatted).toContain("Build ETL & analytics flows. Use SQL daily.");
    expect(formatted).not.toContain("<p>");
    expect(formatted).not.toContain("&amp;");
  });

  test("uses placeholders when optional city, deadline, and apply URL are absent", () => {
    const formatted = formatDetailPlain(
      detail({
        job: {
          ...detail().job,
          address: {
            ...detail().job.address,
            city: null,
          },
        },
        application: {
          ...detail().application,
          deadlineDate: null,
          url: null,
        },
      }),
    );

    expect(formatted).toContain("Location: -, Danmark");
    expect(formatted).toContain("Deadline: -");
    expect(formatted).not.toContain("Apply:");
  });

  test("renders source fallback warning line when source is present and omits it when absent", () => {
    const directFormatted = formatDetailPlain(detail());
    expect(directFormatted).not.toContain("Source:");

    const fallbackFormatted = formatDetailPlain(detail({ source: "search-fallback" }));
    expect(fallbackFormatted).toContain("Source: search-fallback (detail unavailable, synthesised from search)");
  });
});


describe("prepareDetail deadline sentinel", () => {
  // The API's "deadline not disclosed" sentinel is 1900-01-01 (paired with
  // isApplicationDeadlineASAP / applicationDeadlineStatus). search maps it to
  // null and has a test pinning that; detail dumped the raw response, so an
  // undisclosed deadline read as 126 years expired and /rank's sweep would
  // retire the job instantly (review finding F33, 2026-08-19).
  test("maps the 1900-01-01 undisclosed sentinel to null", () => {
    const data = detail();
    data.application.deadlineDate = "1900-01-01T00:00:00+01:00";
    expect(prepareDetail(data).application.deadlineDate).toBeNull();
  });

  test("keeps a real deadline unchanged", () => {
    const data = detail();
    data.application.deadlineDate = "2026-09-01T00:00:00+02:00";
    expect(prepareDetail(data).application.deadlineDate).toBe("2026-09-01T00:00:00+02:00");
  });

  test("keeps a null deadline null", () => {
    const data = detail();
    data.application.deadlineDate = null;
    expect(prepareDetail(data).application.deadlineDate).toBeNull();
  });
});
