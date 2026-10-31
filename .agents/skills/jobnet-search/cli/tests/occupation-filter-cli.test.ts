import { expect, test } from "bun:test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { buildSearchParams, occupationId } from "../src/commands/search";

// The identifier `occupations` documents and prints, copied unchanged.
async function documentedConceptUri(): Promise<string> {
  const readme = (await Bun.file(join(import.meta.dir, "../README.md")).text()).replace(/\r\n/g, "\n");
  const section = readme.split("## `occupations`")[1].split("\n---")[0];
  return JSON.parse(section.match(/```json\n([\s\S]*?)```/)![1])[0].conceptUriDa;
}

async function searchRequest(args: string[]) {
  const requests: URL[] = [];
  const server = Bun.serve({
    hostname: "127.0.0.1",
    port: 0,
    fetch(request) {
      requests.push(new URL(request.url));
      return Response.json({ jobAds: [], searchFacets: {}, totalJobAdCount: 0, searchString: null });
    },
  });
  const dir = mkdtempSync(join(tmpdir(), "jobnet-occupation-filter-"));
  const preload = join(dir, "fetch.ts");
  writeFileSync(preload, `
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (input, init) => {
      const url = new URL(input instanceof Request ? input.url : String(input));
      if (url.protocol === "data:") return originalFetch(input, init);
      if (url.origin !== "https://jobnet.dk" || url.pathname !== "/bff/FindJob/Search") {
        throw new Error("Unexpected search fixture request");
      }
      return originalFetch(${JSON.stringify(server.url.origin)} + url.pathname + url.search, init);
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
    expect(requests).toHaveLength(1);
    return requests[0].searchParams;
  } finally {
    server.stop(true);
    rmSync(dir, { recursive: true, force: true });
  }
}

test("search --occupation sends the conceptUriDa printed by occupations as the occupations filter", async () => {
  const uri = await documentedConceptUri();
  const uuid = uri.split("/").at(-1)!;
  const params = await searchRequest(["--occupation", uri]);
  expect(params.get("occupations")).toBe(uuid);
  // Never the area/group filters, which take numeric codes and match no ad for a UUID.
  expect(params.get("occupationAreas")).toBeNull();
  expect(params.get("occupationGroups")).toBeNull();
});

test("search --occupation accepts the bare UUID", async () => {
  const uuid = (await documentedConceptUri()).split("/").at(-1)!;
  expect((await searchRequest(["--occupation", uuid])).get("occupations")).toBe(uuid);
});

test("occupationId keeps a UUID and takes the last segment of a URI", () => {
  const uuid = "56f5d45c-ce48-43ac-9720-34831a1c8302";
  expect(occupationId(uuid)).toBe(uuid);
  expect(occupationId(`http://data.star.dk/esco/occupation/${uuid}`)).toBe(uuid);
  expect(occupationId(` http://data.star.dk/esco/occupation/${uuid}/ `)).toBe(uuid);
  expect(
    buildSearchParams({ page: 1, "per-page": 10, order: "PublicationDate", radius: 50, occupation: uuid }).occupations,
  ).toBe(uuid);
});
