import { afterAll, expect, test } from "bun:test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const dir = mkdtempSync(join(tmpdir(), "portal-enum-values-"));
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

// add-portal.md: errors are `{ "error": "...", "code": "..." }` on stderr. bunli's
// own enum validation printed `{"ok":false,"error":{...}}` instead, so the exit
// was right and the shape was not.
const outOfEnumCases: string[][] = [
  ["search", "--format", "csv"],
  ["search", "--format=csv"],
  ["categories", "--format", "xml"],
];
for (const args of outOfEnumCases) {
  test(`value outside the enum: ${args.join(" ")}`, async () => {
    const result = await cli(args);
    expect(result.exitCode).toBe(1);
    expect(result.stdout).toBe("");
    expect(result.stderr).not.toContain("FIXTURE_FETCH_ATTEMPT");
    expect(JSON.parse(result.stderr).code).toBe("BAD_ARG");
    expect(JSON.parse(result.stderr).error).toContain("must be one of");
  });
}

const [validArgs, expected] = [["search", "--format", "plain"], "jobsearch"];
test(`a value inside the enum still reaches the request: ${validArgs.join(" ")}`, async () => {
  const result = await cli(validArgs);
  expect(result.exitCode).toBe(1);
  expect(result.stderr).toContain("FIXTURE_FETCH_ATTEMPT");
  expect(result.stderr).toContain(expected);
  expect(JSON.parse(result.stderr).code).toBe("API_ERROR");
});
