import { describe, test, expect } from "bun:test";

// Offline: every case below fails during argument parsing, before any request.
async function run(...args: string[]) {
  const proc = Bun.spawn(["bun", "run", `${import.meta.dir}/../src/cli.ts`, ...args], { stdout: "pipe", stderr: "pipe" });
  const [stderr, code] = await Promise.all([new Response(proc.stderr).text(), proc.exited]);
  return { code, error: stderr.trim() ? JSON.parse(stderr.trim()) : null };
}

describe("CLI errors follow the portal contract (JSON on stderr, exit 1)", () => {
  test("unknown flag", async () => {
    const { code, error } = await run("search", "--bogus");
    expect(code).toBe(1);
    expect(error.code).toBe("BAD_ARG");
  });

  test("flag missing its value", async () => {
    const { code, error } = await run("search", "-q");
    expect(code).toBe(1);
    expect(error.code).toBe("BAD_ARG");
  });

  test("unknown command", async () => {
    const { code, error } = await run("nope");
    expect(code).toBe(1);
    expect(error.code).toBe("BAD_CMD");
  });

  test("detail without an id", async () => {
    const { code, error } = await run("detail");
    expect(code).toBe(1);
    expect(error.code).toBe("NO_ID");
  });
});
