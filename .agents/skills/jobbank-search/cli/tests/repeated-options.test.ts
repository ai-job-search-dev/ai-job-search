import { afterAll, expect, test } from "bun:test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const dir = mkdtempSync(join(tmpdir(), "portal-repeated-options-"));
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

// bunli keeps the last value of a single-value option, so a repeat used to
// search with one of the values and say nothing. The preload records and
// denies every fetch so a silently dropped value is observable offline.
const repeatedCases: string[][] = [
  ["search", "--key", "python", "--key", "java"],
  ["search", "--key=python", "--key", "java"],
  ["search", "--key", "python", "--remote", "helt", "--remote", "delvist"],
];
for (const args of repeatedCases) {
  test(`repeated single-value option: ${args.join(" ")}`, async () => {
    const result = await cli(args);
    expect(result.exitCode).toBe(1);
    expect(result.stdout).toBe("");
    expect(result.stderr).not.toContain("FIXTURE_FETCH_ATTEMPT");
    expect(JSON.parse(result.stderr).code).toBe("BAD_ARG");
    expect(JSON.parse(result.stderr).error).toContain("given 2 times");
  });
}

// A `repeatable` option still collects every value.
const repeatableCases: Array<[string[], string[]]> = [
  [["search", "--key", "python", "--type", "3", "--type", "6"], ["cvtype=3", "cvtype=6"]],
];
for (const [args, values] of repeatableCases) {
  test(`repeatable option keeps every value: ${args.join(" ")}`, async () => {
    const result = await cli(args);
    expect(result.exitCode).toBe(1);
    expect(result.stderr).toContain("FIXTURE_FETCH_ATTEMPT");
    for (const value of values) expect(result.stderr).toContain(value);
    expect(JSON.parse(result.stderr).code).toBe("API_ERROR");
  });
}
