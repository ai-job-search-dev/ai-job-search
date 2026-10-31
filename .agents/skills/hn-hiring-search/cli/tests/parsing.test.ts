import { describe, test, expect } from "bun:test";
import { matchesQuery, parseArgs, parseHiringComment, toText, ycCompany, type Job } from "../src/helpers";

// 2026-10-01T15:00:00Z
const TIME = 1790866800;

describe("toText - HN item HTML to plain text", () => {
  test("turns paragraphs and links into text and decodes entities", () => {
    const html = 'Acme | PM | Remote<p>Apply: <a href="https:&#x2F;&#x2F;acme.example&#x2F;jobs" rel="nofollow">https:&#x2F;&#x2F;acme.example&#x2F;jobs</a> &amp; say hi';
    expect(toText(html)).toBe("Acme | PM | Remote\n\nApply: https://acme.example/jobs & say hi");
  });

  test("returns an empty string for missing text", () => {
    expect(toText(undefined)).toBe("");
  });
});

describe("parseHiringComment - 'Company | Role | Location' header convention", () => {
  test("splits company, role and location, and flags remote", () => {
    const job = parseHiringComment(1, "Acme | Senior Product Manager | REMOTE (Europe) | Full-time<p>We build things.", TIME)!;
    expect(job.company).toBe("Acme");
    expect(job.title).toBe("Senior Product Manager");
    expect(job.location).toBe("REMOTE (Europe)");
    expect(job.remote).toBe(true);
    expect(job.date).toBe("2026-10-01");
    expect(job.url).toBe("https://news.ycombinator.com/item?id=1");
  });

  test("skips employment-type and salary parts when picking the title", () => {
    const job = parseHiringComment(2, "Acme | Full-Time | $150k | Growth Lead | Berlin, DE", TIME)!;
    expect(job.title).toBe("Growth Lead");
  });

  test("'no remote' in the header is not remote", () => {
    expect(parseHiringComment(3, "Acme | Engineer | London, UK | No remote", TIME)!.remote).toBe(false);
  });

  test("a reply without the pipe header is not a job post", () => {
    expect(parseHiringComment(4, "Is this role open to contractors?", TIME)).toBeNull();
  });

  test("picks up the first URL in the post as applyUrl", () => {
    expect(parseHiringComment(5, "Acme | PM | Remote<p>Apply at https://acme.example/jobs.", TIME)!.applyUrl).toBe("https://acme.example/jobs");
  });
});

describe("ycCompany - company name from a YC job title", () => {
  test("reads the name before the batch tag", () => {
    expect(ycCompany("Acme (YC W24) Is Hiring a Senior PM")).toBe("Acme");
  });

  test("falls back to the text before 'is hiring'", () => {
    expect(ycCompany("Acme is hiring engineers")).toBe("Acme");
  });
});

describe("matchesQuery", () => {
  const job: Job = {
    id: "hn-1", source: "hn-whoishiring", title: "Senior Product Manager", company: "Acme", location: "Remote",
    remote: true, date: "2026-10-01", url: "https://news.ycombinator.com/item?id=1", applyUrl: null,
    description: "Acme | Senior Product Manager | Remote\n\nWe also hire a chief of staff later.",
  };

  test("an uppercase OR matches any phrase, case-insensitive", () => {
    expect(matchesQuery(job, "designer OR product manager", false)).toBe(true);
  });

  test("--title-only ignores mentions in the body", () => {
    expect(matchesQuery(job, "chief of staff", false)).toBe(true);
    expect(matchesQuery(job, "chief of staff", true)).toBe(false);
  });
});

describe("parseArgs", () => {
  test("resolves aliases and boolean flags", () => {
    const { command, flags } = parseArgs(["search", "-q", "pm", "-n", "5", "--remote", "-l", "berlin"]);
    expect(command).toBe("search");
    expect(flags).toEqual({ query: "pm", limit: "5", remote: true, location: "berlin" });
  });
});
