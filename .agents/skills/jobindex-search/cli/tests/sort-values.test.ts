import { describe, expect, test } from "bun:test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// --sort was a free z.string(), so any value went to jobindex verbatim. The
// portal does not reject an unknown sort: `--sort newest` came back exit 0 in
// an order that is neither relevance nor date (checked live 2026-10-18), with
// nothing telling the caller the documented `date` sort was not applied.
// The CLI runs against a loopback server so each outbound request is counted.
async function search(args: string[]) {
  const requests: URL[] = [];
  const server = Bun.serve({
    hostname: "127.0.0.1",
    port: 0,
    fetch(request) {
      requests.push(new URL(request.url));
      return new Response('<script>var Stash = {"jobsearch":{"result_app":{"storeData":{"searchResponse":{"hitcount":0,"results":[]}}}}};</script>');
    },
  });
  const dir = mkdtempSync(join(tmpdir(), "jobindex-sort-"));
  const preload = join(dir, "fetch.ts");
  writeFileSync(preload, `
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (input, init) => {
      const url = new URL(input instanceof Request ? input.url : String(input));
      if (url.protocol === "data:") return originalFetch(input, init);
      if (url.origin !== "https://www.jobindex.dk" || url.pathname !== "/jobsoegning") {
        throw new Error("Unexpected search endpoint");
      }
      return originalFetch(${JSON.stringify(server.url.origin)} + url.pathname + url.search, init);
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
    return { stdout: stdout.trim(), stderr: stderr.trim(), exitCode, requests };
  } finally {
    server.stop(true);
    rmSync(dir, { recursive: true, force: true });
  }
}

describe("--sort values", () => {
  for (const value of ["newest", "Date", "relevance"]) {
    test(`--sort ${value} is rejected before any request`, async () => {
      const result = await search(["--query", "python", "--sort", value]);
      expect(result.exitCode).toBe(1);
      expect(result.stdout).toBe("");
      const error = JSON.parse(result.stderr);
      expect(error.ok).toBe(false);
      expect(error.error.kind).toBe("validation");
      expect(error.error.option).toBe("sort");
      expect(result.requests).toHaveLength(0);
    });
  }

  for (const [args, expected] of [
    [["--sort", "date"], "date"],
    [["--sort", "score"], "score"],
    [[], "score"],
  ] as const) {
    test(`${args.join(" ") || "no --sort"} sends sort=${expected}`, async () => {
      const result = await search(["--query", "python", ...args]);
      expect(result.exitCode).toBe(0);
      expect(result.stderr).toBe("");
      expect(result.requests).toHaveLength(1);
      expect(result.requests[0].searchParams.get("sort")).toBe(expected);
    });
  }
});
