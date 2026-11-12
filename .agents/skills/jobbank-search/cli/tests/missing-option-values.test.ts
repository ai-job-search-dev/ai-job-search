import { afterAll, expect, test } from "bun:test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const dir = mkdtempSync(join(tmpdir(), "portal-missing-values-"));
const preload = join(dir, "deny-fetch.ts");
writeFileSync(preload, `globalThis.fetch = async (input, init) => {
  throw new Error("FIXTURE_FETCH_ATTEMPT " + JSON.stringify({ url: String(input), body: init?.body }));
};`);
afterAll(() => rmSync(dir, { recursive: true, force: true }));

async function cli(args: string[]) {
  const proc = Bun.spawn([
    process.execPath, "run", "--preload", preload,
    join(import.meta.dir, "../src/cli.ts"), ...args,
  ], { stdout: "pipe", stderr: "pipe" });
  const [stdout, stderr, exitCode] = await Promise.all([
    new Response(proc.stdout).text(), new Response(proc.stderr).text(), proc.exited,
  ]);
  return { stdout, stderr, exitCode };
}

// These are the documented flags sent to the actual CLI, rather than values
// fed directly to its schema. The preload records and denies every fetch so a
// silently dropped filter is observable without contacting the portal.
const missingValueCases: string[][] = [
  ["search", "--key", "python", "--remote"],
  ["search", "--key", "python", "--type"],
  ["search", "--key", "python", "--company"],
  ["search", "--key", "python", "--since", "--limit", "5"],
  // `--flag=` and `--flag ''` carry no value either: bunli kept the
  // empty string and the handler dropped the filter, so the search ran unfiltered.
  ["search", "--key", "python", "--remote="],
  ["search", "--key", "python", "--remote", ""],
  ["search", "--key=", "--remote", "helt"],
];
for (const args of missingValueCases) {
  test(`missing option value: ${args.join(" ")}`, async () => {
    const result = await cli(args);
    expect(result.exitCode).toBe(1);
    expect(result.stdout).toBe("");
    expect(result.stderr).not.toContain("FIXTURE_FETCH_ATTEMPT");
    expect(JSON.parse(result.stderr).code).toBe("BAD_ARG");
    expect(JSON.parse(result.stderr).error).toContain("requires a value");
  });
}

const valuedCases: Array<[string[], string]> = [
  [["search", "--key", "python", "--remote", "helt"], "fjernarbejde=helt"],
  [["search", "--key=python", "--type=3,6"], "cvtype=3&cvtype=6"],
];
for (const [args, value] of valuedCases) {
  test(`valued options still reach the request: ${args.join(" ")}`, async () => {
    const result = await cli(args);
    expect(result.exitCode).toBe(1);
    expect(result.stderr).toContain("FIXTURE_FETCH_ATTEMPT");
    expect(result.stderr).toContain(value);
    expect(JSON.parse(result.stderr).code).toBe("API_ERROR");
  });
}

test("built-in short help remains a switch", async () => {
  const result = await cli(["search", "-h"]);
  expect(result.exitCode).toBe(0);
  expect(result.stderr).toBe("");
});
