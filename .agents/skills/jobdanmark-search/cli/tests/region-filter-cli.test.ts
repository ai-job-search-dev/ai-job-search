import { expect, test } from "bun:test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Both region kinds exactly as GET /api/search/locations?q=jylland returns them.
const LOCATIONS = [
  {
    title: "Landsdel",
    items: [{ id: "region__218546", text: "Østjylland", value: "Østjylland", category: "region", slug: "oestjylland" }],
  },
  {
    title: "Region",
    items: [{
      id: "administrativeRegion__389101",
      text: "Region Midtjylland",
      value: "Region Midtjylland",
      category: "administrativeRegion",
      slug: "region-midtjylland",
    }],
  },
];

async function searchFilters(args: string[]) {
  const bodies: unknown[] = [];
  const server = Bun.serve({
    hostname: "127.0.0.1",
    port: 0,
    async fetch(request) {
      bodies.push(await request.json());
      return Response.json({ items: [], currentPage: 1, totalItems: 0, itemsPrPage: 30, totalPages: 0 });
    },
  });
  const dir = mkdtempSync(join(tmpdir(), "jobdanmark-region-"));
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
    const [stderr, exitCode] = await Promise.all([new Response(proc.stderr).text(), proc.exited]);
    expect(stderr).toBe("");
    expect(exitCode).toBe(0);
    expect(bodies).toHaveLength(1);
    return (bodies[0] as { filters: Array<{ type: string; value: string; displayText: string }> }).filters;
  } finally {
    server.stop(true);
    rmSync(dir, { recursive: true, force: true });
  }
}

const item = (category: string) => LOCATIONS.flatMap((g) => g.items).find((i) => i.category === category)!;

test("an administrativeRegion location is sent with the API's administrativeRegion filter type", async () => {
  const { value } = item("administrativeRegion");
  const filters = await searchFilters(["--administrative-region", value]);
  expect(filters).toEqual([{ type: "administrativeRegion", value, displayText: value }]);
});

test("a region (landsdel) location keeps the region filter type", async () => {
  const { value } = item("region");
  const filters = await searchFilters(["--region", value]);
  expect(filters).toEqual([{ type: "region", value, displayText: value }]);
});
