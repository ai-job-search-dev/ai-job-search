import { describe, expect, test } from "bun:test";
import { runCLI } from "./helpers";

// `detail` listed --description-format among its known flags, but runDetail
// never reads it: the detail endpoint's HTML body is always stripped to plain
// text. So `detail <slug> --description-format markdown` passed the
// unknown-flag guard, exited 0 and printed plain text - the silently ignored
// flag that guard exists to prevent. The CLI runs against a loopback API
// through its supported FREEHIRE_API_URL override; requests are counted.
const JOB = {
  public_slug: "backend-engineer-acme-ab12cd34",
  source: "greenhouse",
  external_id: "1",
  url: "https://boards.example/acme/1",
  title: "Backend Engineer",
  company: "Acme",
  company_slug: "acme",
  location: "Berlin, Germany",
  description: "<p>Build things</p><ul><li>Go</li><li>Postgres</li></ul>",
  skills: ["go"],
  regions: ["eu"],
  countries: ["DE"],
  cities: ["Berlin"],
  posted_at: "2026-10-01T00:00:00Z",
  created_at: "2026-10-01T00:00:00Z",
  enrichment: {},
};

async function detail(args: string[]) {
  const requests: URL[] = [];
  const server = Bun.serve({
    hostname: "127.0.0.1",
    port: 0,
    fetch(request) {
      requests.push(new URL(request.url));
      return Response.json({ data: JOB });
    },
  });
  try {
    const result = await runCLI(["detail", JOB.public_slug, ...args], { FREEHIRE_API_URL: server.url.origin });
    return { result, requests };
  } finally {
    server.stop(true);
  }
}

describe("detail flags", () => {
  for (const value of ["markdown", "text", "html"]) {
    test(`--description-format ${value} is rejected as an unknown detail flag before any request`, async () => {
      const { result, requests } = await detail(["--description-format", value]);
      expect(result.exitCode).toBe(1);
      expect(result.stdout).toBe("");
      const error = JSON.parse(result.stderr);
      expect(error.code).toBe("UNKNOWN_FLAG");
      expect(error.error).toContain("--description-format");
      expect(requests).toHaveLength(0);
    });
  }

  test("--format plain still prints the detail", async () => {
    const { result, requests } = await detail(["--format", "plain"]);
    expect(result.exitCode).toBe(0);
    expect(requests).toHaveLength(1);
    expect(requests[0].pathname).toBe(`/api/v1/jobs/${JOB.public_slug}`);
    expect(result.stdout).toContain("Backend Engineer");
  });

  test("search still validates its own --description-format", async () => {
    const result = await runCLI(["search", "--description-format", "tekst"]);
    expect(JSON.parse(result.stderr).code).toBe("BAD_ARG");
  });
});
