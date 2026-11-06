import { describe, expect, test } from "bun:test";
import { runCLI } from "./helpers";

// parseFlags is last-wins, so a repeated flag used to keep only its final
// value with no error - the discarded-filter failure the UNKNOWN_FLAG guard
// exists to prevent. Facet list flags join like their comma form; any other
// repeat fails before a request is made.

function serve(requests: string[]) {
  return Bun.serve({
    hostname: "127.0.0.1",
    port: 0,
    fetch(request) {
      requests.push(request.url);
      return Response.json({ data: [], meta: { total: 0 } });
    },
  });
}

describe("repeated flags", () => {
  test("a repeated facet flag ORs its values like the comma form", async () => {
    const requests: string[] = [];
    const server = serve(requests);
    try {
      const args = ["search", "--country", "dk", "--country", "se", "--limit", "1", "--no-description"];
      const result = await runCLI(args, { FREEHIRE_API_URL: server.url.toString() });
      expect(result.exitCode).toBe(0);
      expect(requests).toHaveLength(1);
      const url = decodeURIComponent(requests[0]);
      expect(url).toMatch(/countries=[^&]*dk/);
      expect(url).toMatch(/countries=[^&]*se/);
    } finally {
      server.stop(true);
    }
  });

  for (const args of [
    ["--query", "python", "--query", "java"],
    ["-q", "python", "--query", "java"],
    ["--limit", "1", "--limit", "2"],
    ["--remote", "remote", "--remote", "hybrid"],
  ]) {
    test(`a repeated single-value flag fails before requesting jobs: ${args.join(" ")}`, async () => {
      const requests: string[] = [];
      const server = serve(requests);
      try {
        const result = await runCLI(["search", ...args], { FREEHIRE_API_URL: server.url.toString() });
        expect(result.exitCode).toBe(1);
        expect(result.stdout).toBe("");
        expect(JSON.parse(result.stderr)).toMatchObject({ code: "BAD_ARG" });
        expect(JSON.parse(result.stderr).error).toContain("more than once");
        expect(requests).toEqual([]);
      } finally {
        server.stop(true);
      }
    });
  }
});
