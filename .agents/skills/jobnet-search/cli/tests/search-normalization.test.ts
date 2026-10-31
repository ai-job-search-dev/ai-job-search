import { describe, expect, test } from "bun:test";
import {
  buildSearchParams,
  createSearchOutput,
  externalAdUrl,
  type SearchApiResponse,
  type SearchFlags,
} from "../src/commands/search";

const flags: SearchFlags = {
  "search-string": "data engineer",
  page: 2,
  "per-page": 25,
  order: "BestMatch",
  region: "HovedstadenOgBornholm",
  "work-hours": "FullTime",
  duration: "Permanent",
  "job-type": "Ordinaert",
  "postal-code": "2100",
  radius: 25,
  "occupation-area": "10000",
  "occupation-group": "10060",
  limit: 1,
};

function apiResponse(): SearchApiResponse {
  return {
    totalJobAdCount: 2,
    searchString: "data engineer",
    searchFacets: {
      regions: [{ type: "HovedstadenOgBornholm", jobAdCount: 2 }],
      workHours: [{ type: "FullTime", jobAdCount: 2 }],
      employmentDurations: [{ type: "Permanent", jobAdCount: 1 }],
      occupationAreas: [{ identifier: "10000", jobAdCount: 2 }],
      countries: [{ label: "Danmark", identifier: "DK", jobAdCount: 2 }],
    },
    jobAds: [
      {
        jobAdId: "job-1",
        title: "Data Engineer",
        hiringOrgName: "Acme",
        occupation: null,
        municipality: null,
        postalCode: null,
        postalDistrictName: null,
        country: "Danmark",
        publicationDate: "2026-07-01T00:00:00+02:00",
        applicationDeadline: null,
        applicationDeadlineStatus: null,
        workHourPartTime: false,
        isExternal: false,
        hasLogo: false,
        logoUrl: null,
        cvr: null,
        workPlaceAddress: "",
        conceptUriDa: "http://example.test/occupation",
        isSeen: false,
        isFavorite: false,
        description: "<p>Search results should not include this bulky HTML.</p>",
      },
      {
        jobAdId: "job-2",
        title: "Analytics Engineer",
        hiringOrgName: "Example Co",
        occupation: "Softwareudvikler",
        municipality: "København",
        postalCode: 2100,
        postalDistrictName: "København Ø",
        country: "Danmark",
        publicationDate: "2026-07-02T00:00:00+02:00",
        applicationDeadline: "2026-08-01T23:59:00+02:00",
        applicationDeadlineStatus: "ExpirationDate",
        workHourPartTime: false,
        isExternal: true,
        jobAdUrl: "https://example.com/jobs/job-2",
        hasLogo: true,
        logoUrl: "/logo/job-2",
        cvr: "12345678",
        workPlaceAddress: "Examplevej 1",
        isSeen: false,
        isFavorite: true,
      },
    ],
  };
}

describe("Jobnet search normalization", () => {
  test("builds the API query with required paging and optional filters", () => {
    expect(buildSearchParams(flags)).toEqual({
      resultsPerPage: "25",
      pageNumber: "2",
      orderType: "BestMatch",
      searchString: "data engineer",
      regions: "HovedstadenOgBornholm",
      workHoursType: "FullTime",
      employmentDurationType: "Permanent",
      jobAnnouncementType: "Ordinaert",
      postalCode: "2100",
      kmRadius: "25",
      occupationAreas: "10000",
      occupationGroups: "10060",
    });
  });

  test("omits radius when postal-code is absent", () => {
    const params = buildSearchParams({ ...flags, "postal-code": undefined });
    expect(params.postalCode).toBeUndefined();
    expect(params.kmRadius).toBeUndefined();
  });

  test("creates the documented output envelope and omits bulky descriptions", () => {
    const output = createSearchOutput(apiResponse(), flags);

    expect(output.meta).toEqual({
      totalJobAdCount: 2,
      pageNumber: 2,
      resultsPerPage: 25,
      searchString: "data engineer",
    });
    expect(output.facets.regions).toEqual([{ type: "HovedstadenOgBornholm", jobAdCount: 2 }]);
    expect(output.results).toHaveLength(1);
    expect(output.results[0]).toMatchObject({
      jobAdId: "job-1",
      occupation: null,
      municipality: null,
      postalCode: null,
      applicationDeadline: null,
      workPlaceAddress: "",
    });
    expect("description" in output.results[0]).toBe(false);
  });

  test("additively emits the /scrape contract fields (id, company, location, date, deadline, url)", () => {
    const output = createSearchOutput(apiResponse(), { ...flags, limit: undefined });

    expect(output.results).toHaveLength(2);
    expect(output.results[0]).toMatchObject({
      id: "job-1",
      company: "Acme",
      location: null,
      date: "2026-07-01",
      deadline: null,
      url: "https://jobnet.dk/find-job/job-1",
    });
    expect(output.results[1]).toMatchObject({
      id: "job-2",
      company: "Example Co",
      location: "København Ø",
      date: "2026-07-02",
      deadline: "2026-08-01",
      url: "https://jobnet.dk/find-job/job-2",
      applyUrl: "https://example.com/jobs/job-2",
      jobAdUrl: "https://example.com/jobs/job-2",
    });
    expect(output.results[0].hiringOrgName).toBe("Acme");
    expect(output.results[1].applicationDeadline).toBe("2026-08-01T23:59:00+02:00");
    // `id` is the contract name for the value `detail <jobAdId>` consumes.
    expect(output.results[0].id).toBe(output.results[0].jobAdId);
  });

  test("external ads keep canonical find-job url and provide destination as applyUrl and jobAdUrl", () => {
    const response = apiResponse();
    // External ad with valid http(s) jobAdUrl emits canonical url and destination applyUrl/jobAdUrl
    response.jobAds[1].isExternal = true;
    response.jobAds[1].jobAdUrl = "https://employer.example/apply";
    const withUrl = createSearchOutput(response, { ...flags, limit: undefined });
    expect(withUrl.results[1].url).toBe("https://jobnet.dk/find-job/job-2");
    expect(withUrl.results[1].applyUrl).toBe("https://employer.example/apply");
    expect(withUrl.results[1].jobAdUrl).toBe("https://employer.example/apply");
    expect(withUrl.results[1].isExternal).toBe(true);

    // External ad without jobAdUrl keeps canonical find-job url and null applyUrl/jobAdUrl
    response.jobAds[1].jobAdUrl = null;
    const withoutUrl = createSearchOutput(response, { ...flags, limit: undefined });
    expect(withoutUrl.results[1].url).toBe("https://jobnet.dk/find-job/job-2");
    expect(withoutUrl.results[1].applyUrl).toBeNull();
    expect(withoutUrl.results[1].jobAdUrl).toBeNull();
    expect(withoutUrl.results[1].isExternal).toBe(true);

    // External ad with non-http(s) scheme keeps canonical find-job url and null applyUrl/jobAdUrl
    response.jobAds[1].jobAdUrl = "javascript:alert(1)";
    const invalidUrl = createSearchOutput(response, { ...flags, limit: undefined });
    expect(invalidUrl.results[1].url).toBe("https://jobnet.dk/find-job/job-2");
    expect(invalidUrl.results[1].applyUrl).toBeNull();
    expect(invalidUrl.results[1].jobAdUrl).toBeNull();

    // Internal ad always keeps find-job link and null applyUrl
    expect(withoutUrl.results[0].url).toBe("https://jobnet.dk/find-job/job-1");
    expect(withoutUrl.results[0].applyUrl).toBeNull();
    expect(withoutUrl.results[0].isExternal).toBe(false);
  });

  test("maps Jobnet's undisclosed-deadline sentinel (1900-01-01) to null", () => {
    const response = apiResponse();
    response.jobAds[0].applicationDeadline = "1900-01-01T00:00:00+01:00";
    response.jobAds[0].applicationDeadlineStatus = "NotDisclosed";

    const output = createSearchOutput(response, { ...flags, limit: undefined });

    expect(output.results[0].deadline).toBeNull();
    expect(output.results[1].deadline).toBe("2026-08-01");
  });
});

describe("Jobnet null publicationDate degradation", () => {
  // publicationDate: string was a TypeScript claim, not runtime validation -
  // apiFetch casts the JSON body, so one ad with a null publication date
  // threw TypeError from .slice() inside the jobAds map and killed the whole
  // search as API_ERROR (#418). The neighboring applicationDeadline field is
  // already guarded (null check + 1900-01-01 sentinel); this pins the same
  // per-item degradation for publicationDate: date null, no throw.
  test("an ad with a null publicationDate yields date: null instead of crashing the search", () => {
    const data = apiResponse();
    data.jobAds[0].publicationDate = null;

    // The shared fixture flags carry limit: 1, which would slice off the
    // second ad; lift the limit so the survives-alongside assertion is real.
    const output = createSearchOutput(data, { ...flags, limit: undefined });

    expect(output.results[0].date).toBeNull();
    expect(output.results[1].date).toBe("2026-07-02");
  });
});

describe("externalAdUrl helper", () => {
  test("returns valid https or http URL when isExternal is true", () => {
    expect(externalAdUrl({ isExternal: true, jobAdUrl: "https://example.com/apply" })).toBe("https://example.com/apply");
    expect(externalAdUrl({ isExternal: true, jobAdUrl: "http://example.com/apply" })).toBe("http://example.com/apply");
  });

  test("returns null if not external, missing, or non-http(s) scheme", () => {
    expect(externalAdUrl({ isExternal: false, jobAdUrl: "https://example.com/apply" })).toBeNull();
    expect(externalAdUrl({ isExternal: true, jobAdUrl: null })).toBeNull();
    expect(externalAdUrl({ isExternal: true, jobAdUrl: "   " })).toBeNull();
    expect(externalAdUrl({ isExternal: true, jobAdUrl: "javascript:void(0)" })).toBeNull();
    expect(externalAdUrl({ isExternal: true, jobAdUrl: "file:///etc/passwd" })).toBeNull();
    expect(externalAdUrl({ isExternal: true, jobAdUrl: "not-a-valid-url" })).toBeNull();
  });
});
