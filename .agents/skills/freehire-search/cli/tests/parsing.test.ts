import { describe, test, expect } from "bun:test";
import { cleanHtml, contractDate, normalizeSlug, toResult, toDetail, type FreehireJob } from "../src/helpers";

function job(overrides: Partial<FreehireJob> = {}): FreehireJob {
  return {
    public_slug: "backend-engineer-acme-ab12cd34",
    source: "greenhouse",
    external_id: "acme:1",
    url: "https://boards.greenhouse.io/acme/jobs/1",
    title: "Backend Engineer",
    company: "Acme",
    company_slug: "acme",
    location: "Berlin, Germany",
    description: "<ul><li>Build APIs</li><li>Ship &amp; iterate</li></ul>",
    skills: ["go", "kubernetes"],
    work_mode: "remote",
    regions: ["eu"],
    countries: ["de"],
    cities: ["Berlin"],
    posted_at: "2026-07-06T00:00:00Z",
    created_at: "2026-07-06T15:00:00Z",
    enrichment: {},
    ...overrides,
  };
}

describe("toResult — reshape into the portal-skill contract", () => {
  test("maps public_slug -> id and posted_at -> date", () => {
    const r = toResult(job());
    expect(r.id).toBe("backend-engineer-acme-ab12cd34");
    // The contract shape is the date part only, like every other portal CLI.
    expect(r.date).toBe("2026-07-06");
  });

  test("carries the required contract fields", () => {
    const r = toResult(job());
    expect(r).toMatchObject({
      title: "Backend Engineer",
      company: "Acme",
      company_slug: "acme",
      location: "Berlin, Germany",
      url: "https://boards.greenhouse.io/acme/jobs/1",
    });
  });

  test("missing values are null, not omitted", () => {
    const r = toResult(job({ company: "", location: "", posted_at: null, work_mode: undefined }));
    expect(r.company).toBeNull();
    expect(r.location).toBeNull();
    expect(r.date).toBeNull();
    expect(r.work_mode).toBeNull();
  });

  test("decodes HTML entities and trims whitespace in the title", () => {
    // Real title from the index (freehire slug intern-fullstack-amp-ai-innovation-noise-edaawqq2).
    expect(toResult(job({ title: "Intern - Fullstack &amp; AI Innovation " })).title).toBe(
      "Intern - Fullstack & AI Innovation",
    );
    expect(toResult(job({ title: "R&#38;D Engineer" })).title).toBe("R&D Engineer");
    expect(toResult(job({ title: "SDE Intern - Frontend   " })).title).toBe("SDE Intern - Frontend");
  });

  test("a whitespace-only title still falls back to (untitled)", () => {
    expect(toResult(job({ title: "   " })).title).toBe("(untitled)");
    expect(toResult(job({ title: "" })).title).toBe("(untitled)");
  });

  test("detail inherits the cleaned title", () => {
    expect(toDetail(job({ title: "Data &amp; Analytics Intern" })).title).toBe("Data & Analytics Intern");
  });
});

describe("toDetail — adds cleaned description + enrichment", () => {
  test("strips HTML and decodes entities in the description", () => {
    const d = toDetail(job());
    expect(d.description).toBe("Build APIs\nShip & iterate");
  });

  test("surfaces enrichment fields and a formatted salary", () => {
    const d = toDetail(
      job({
        enrichment: { seniority: "senior", category: "backend", employment_type: "full_time", salary_min: 90000, salary_max: 120000, salary_currency: "EUR" },
      }),
    );
    expect(d.seniority).toBe("senior");
    expect(d.category).toBe("backend");
    expect(d.salary).toBe("EUR 90000–120000");
  });

  test("keeps the salary period, so a yearly figure is not read as monthly", () => {
    const d = toDetail(
      job({ enrichment: { salary_min: 300000, salary_max: 300000, salary_currency: "INR", salary_period: "year" } }),
    );
    expect(d.salary).toBe("INR 300000–300000/year");
  });

  test("a single-bound salary keeps its period too", () => {
    const d = toDetail(job({ enrichment: { salary_min: 12000, salary_currency: "INR", salary_period: "month" } }));
    expect(d.salary).toBe("INR 12000/month");
  });

  test("null enrichment fields when the enrichment object is empty", () => {
    const d = toDetail(job({ enrichment: {} }));
    expect(d.seniority).toBeNull();
    expect(d.salary).toBeNull();
  });
});

describe("cleanHtml", () => {
  test("preserves paragraph breaks between blocks", () => {
    expect(cleanHtml("<p>One</p><p>Two</p>")).toBe("One\nTwo");
  });
  test("decodes hex numeric entities", () => {
    expect(cleanHtml("Caf&#xE9;")).toBe("Café");
  });
  test("returns null for empty input", () => {
    expect(cleanHtml("")).toBeNull();
    expect(cleanHtml(null)).toBeNull();
  });
});

describe("contractDate", () => {
  test("cuts the API's ISO timestamp to YYYY-MM-DD", () => {
    expect(contractDate("2026-07-06T00:00:00Z")).toBe("2026-07-06");
    expect(contractDate("2026-07-06T15:30:00+02:00")).toBe("2026-07-06");
    expect(contractDate("2026-07-06")).toBe("2026-07-06");
  });
  test("is null for an absent or non-date value", () => {
    expect(contractDate(null)).toBeNull();
    expect(contractDate(undefined)).toBeNull();
    expect(contractDate("")).toBeNull();
    expect(contractDate("soon")).toBeNull();
  });
  test("detail carries the same shape as search", () => {
    expect(toDetail(job()).date).toBe("2026-07-06");
  });
});

describe("normalizeSlug", () => {
  test("accepts a bare slug", () => {
    expect(normalizeSlug("golang-zensar-2bxu6dxm")).toBe("golang-zensar-2bxu6dxm");
  });
  test("extracts the slug from a /jobs/<slug> URL", () => {
    expect(normalizeSlug("https://freehire.me/jobs/golang-zensar-2bxu6dxm")).toBe("golang-zensar-2bxu6dxm");
  });
  test("rejects a non-slug string", () => {
    expect(normalizeSlug("not a slug!")).toBeNull();
    expect(normalizeSlug("")).toBeNull();
  });
  test("accepts subdomains, a scheme-less host, a trailing slash and a query string", () => {
    expect(normalizeSlug("https://www.freehire.me/jobs/golang-zensar-2bxu6dxm/")).toBe("golang-zensar-2bxu6dxm");
    expect(normalizeSlug("freehire.me/jobs/golang-zensar-2bxu6dxm?utm_source=x")).toBe("golang-zensar-2bxu6dxm");
  });
  test("rejects a /jobs/ URL on another host instead of asking the API for its last segment", () => {
    // The search fixture's own `url` is a Greenhouse apply link; pasting it
    // back into detail used to request /api/v1/jobs/1 and report NOT_FOUND.
    expect(normalizeSlug("https://boards.greenhouse.io/acme/jobs/1")).toBeNull();
    expect(normalizeSlug("https://jobs.lever.co/acme/jobs/abc")).toBeNull();
  });
  test("rejects look-alike and userinfo hosts", () => {
    expect(normalizeSlug("https://freehire.me.evil.io/jobs/x")).toBeNull();
    expect(normalizeSlug("https://notfreehire.me/jobs/x")).toBeNull();
    expect(normalizeSlug("https://freehire.me@evil.io/jobs/x")).toBeNull();
  });
  test("rejects a freehire.me URL that is not a job page", () => {
    expect(normalizeSlug("https://freehire.me/companies/acme")).toBeNull();
    expect(normalizeSlug("https://freehire.me/jobs/")).toBeNull();
  });
  test("requires the path to be exactly /jobs/<slug>", () => {
    // The slug is the whole path after /jobs/: a nested path, a prefix before
    // /jobs/, or a slug with characters outside the slug alphabet is not a
    // freehire job page, even on the right host.
    expect(normalizeSlug("https://freehire.me/jobs/my-slug/apply")).toBeNull();
    expect(normalizeSlug("https://freehire.me/en/jobs/my-slug")).toBeNull();
    expect(normalizeSlug("https://freehire.me/jobs/my_slug")).toBeNull();
    expect(normalizeSlug("https://freehire.me/jobs/my-slug#details")).toBe("my-slug");
  });

  test("accepts the self-hosted base URL's host", () => {
    const saved = process.env.FREEHIRE_API_URL;
    process.env.FREEHIRE_API_URL = "http://localhost:8080";
    try {
      expect(normalizeSlug("http://localhost:8080/jobs/my-slug")).toBe("my-slug");
      expect(normalizeSlug("https://boards.greenhouse.io/acme/jobs/1")).toBeNull();
    } finally {
      if (saved === undefined) delete process.env.FREEHIRE_API_URL;
      else process.env.FREEHIRE_API_URL = saved;
    }
  });
});
