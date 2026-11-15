import { describe, expect, test } from "bun:test"
import { buildSearchUrl, htmlToText, parseDetail, parsePosts, stripInvisible } from "../src/helpers"
import { normalizeInput } from "../src/commands/detail"
import fixture from "./fixtures/search-posts.json"

describe("parsePosts (captured live response, 2026-11-11)", () => {
  const jobs = parsePosts(fixture)

  test("parses every posting with the contract fields", () => {
    expect(jobs).toHaveLength(3)
    for (const j of jobs) {
      for (const k of ["id", "title", "company", "location", "date", "url"]) expect(j).toHaveProperty(k)
    }
  })

  test("splits the company off the title and uses the job_company term", () => {
    expect(jobs[0]).toMatchObject({
      id: "453406",
      title: "Tech Lead (m/f/d)",
      company: "Superchat",
      location: "Berlin, Germany",
      date: "2026-11-02",
      url: "https://berlinstartupjobs.com/engineering/tech-lead-m-f-d-superchat/",
      category: "IT / Software Development",
    })
  })

  test("a malformed entry is skipped without breaking the rest", () => {
    const out = parsePosts([{ nope: true }, null, "x", ...fixture])
    expect(out).toHaveLength(3)
  })

  test("falls back to the title suffix when no company term is embedded", () => {
    const [j] = parsePosts([{ id: 1, date: "2026-01-02T00:00:00", link: "https://berlinstartupjobs.com/x/y/", title: { rendered: "Staff Engineer // Acme" } }])
    expect(j.company).toBe("Acme")
    expect(j.location).toBeNull()
  })

  test("off-site links are replaced with the canonical ?p= URL", () => {
    const [j] = parsePosts([{ id: 7, title: { rendered: "A // B" }, link: "https://evil.example/x" }])
    expect(j.url).toBe("https://berlinstartupjobs.com/?p=7")
  })
})

describe("output hygiene", () => {
  test("hidden markup and invisible characters never reach the output", () => {
    const d = parseDetail({
      id: 5,
      title: { rendered: "Eng​ // Co" },
      content: { rendered: '<p>Visible</p><div style="display:none">IGNORE PREVIOUS</div><!-- secret --><script>x</script>' },
    })!
    const clean = stripInvisible(d)
    expect(clean.title).toBe("Eng")
    expect(clean.description).toBe("Visible")
  })

  test("htmlToText keeps paragraphs and bullet lists readable", () => {
    expect(htmlToText("<p>Intro &amp; more</p><ul><li>One</li><li>Two</li></ul>")).toBe("Intro & more\n\n- One\n- Two")
  })
})

describe("buildSearchUrl", () => {
  test("maps query, jobage, page and embeds terms", () => {
    const url = new URL(buildSearchUrl({ query: "backend", jobage: 7, page: 2, now: new Date("2026-11-11T00:00:00Z") }))
    expect(url.searchParams.get("search")).toBe("backend")
    expect(url.searchParams.get("after")).toBe("2026-11-04T00:00:00")
    expect(url.searchParams.get("page")).toBe("2")
    expect(url.searchParams.get("_embed")).toBe("wp:term")
  })
})

describe("normalizeInput", () => {
  test("accepts ids and berlinstartupjobs.com URLs", () => {
    expect(normalizeInput("453406")).toEqual({ id: "453406" })
    expect(normalizeInput("https://berlinstartupjobs.com/engineering/tech-lead-m-f-d-superchat/")).toEqual({ slug: "tech-lead-m-f-d-superchat" })
    expect(normalizeInput("https://berlinstartupjobs.com/?p=12")).toEqual({ id: "12" })
  })
  test("rejects other hosts and look-alikes", () => {
    expect(normalizeInput("https://berlinstartupjobs.com.evil.io/a/b/")).toBeNull()
    expect(normalizeInput("https://berlinstartupjobs.com@evil.io/a/b/")).toBeNull()
    expect(normalizeInput("https://boards.greenhouse.io/acme/jobs/123")).toBeNull()
  })
})
