import { expect, test } from "bun:test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Runs the real CLI against a local HTTP server and records its actual request.
// Unknown type strings return a successful empty search on the real API.
async function search(args: string[]) {
  const bodies: Array<{ jobTypes: string[] }> = [];
  const server = Bun.serve({
    hostname: "127.0.0.1",
    port: 0,
    async fetch(request) {
      bodies.push((await request.json()) as { jobTypes: string[] });
      return Response.json({ items: [], currentPage: 1, totalItems: 0, itemsPrPage: 30, totalPages: 0 });
    },
  });
  const dir = mkdtempSync(join(tmpdir(), "jobdanmark-job-type-"));
  const preload = join(dir, "fetch.ts");
  writeFileSync(preload, `
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (input, init) => {
      const url = new URL(input instanceof Request ? input.url : String(input));
      if (url.protocol === "data:") return originalFetch(input, init);
      if (url.origin !== "https://jobdanmark.dk" || url.pathname !== "/api/jobsearch/search/1") {
        throw new Error("Unexpected search fixture request");
      }
      return originalFetch(${JSON.stringify(server.url.origin)} + url.pathname, init);
    };
  `);
  try {
    const proc = Bun.spawn([
      process.execPath, "run", "--preload", preload,
      join(import.meta.dir, "../src/cli.ts"), "search", ...args,
    ], { stdout: "pipe", stderr: "pipe" });
    const [stdout, stderr, exitCode] = await Promise.all([
      new Response(proc.stdout).text(), new Response(proc.stderr).text(), proc.exited,
    ]);
    return { stdout, stderr, exitCode, bodies };
  } finally {
    server.stop(true);
    rmSync(dir, { recursive: true, force: true });
  }
}

test.each(["fulltime", "student", "fuldtid,typo"])(
  "--job-type %s exits 1 before searching instead of returning an empty result",
  async (value) => {
    const result = await search(["--job-type", value]);
    expect(result.exitCode).toBe(1);
    expect(result.stdout).toBe("");
    expect(result.bodies).toHaveLength(0);
    const error = JSON.parse(result.stderr);
    expect(error.code).toBe("BAD_ARG");
    expect(error.error).toContain("fuldtid, deltid");
  },
);

test("--job-type fuldtid,deltid is sent as the jobTypes array", async () => {
  const result = await search(["--job-type", "fuldtid, deltid"]);
  expect(result.stderr).toBe("");
  expect(result.exitCode).toBe(0);
  expect(result.bodies.map((b) => b.jobTypes)).toEqual([["fuldtid", "deltid"]]);
});

// All six are still listed by /api/jobsearch/settings. No live ad carries
// fleksjob, elev, studiejob, or praktik, but each remains a valid filter.
test.each(["fleksjob", "elev", "studiejob", "praktik"])(
  "settings-offered --job-type %s remains usable even when currently empty",
  async (value) => {
    const result = await search(["--job-type", value]);
    expect(result.exitCode).toBe(0);
    expect(result.stderr).toBe("");
    expect(result.bodies.map((b) => b.jobTypes)).toEqual([[value]]);
  },
);
