import { describe, test, expect } from "bun:test";
import { runCLI } from "./helpers";

function parsedStderr(stderr: string): { error?: string; code?: string } {
  try {
    return JSON.parse(stderr);
  } catch {
    return {};
  }
}

async function searchRemote(args: string[]): Promise<{ result: Awaited<ReturnType<typeof runCLI>>; requests: URL[] }> {
  const requests: URL[] = [];
  const server = Bun.serve({
    hostname: "127.0.0.1",
    port: 0,
    fetch(request) {
      requests.push(new URL(request.url));
      return Response.json({ data: [], meta: { total: 0 } });
    },
  });
  try {
    const result = await runCLI(["search", "-q", "go", "--limit", "1", ...args], {
      FREEHIRE_API_URL: server.url.toString(),
    });
    return { result, requests };
  } finally {
    server.stop(true);
  }
}

describe("--remote work_mode validation", () => {
  test("--remote foo exits 1 with BAD_ARG and does not call the API", async () => {
    const { result, requests } = await searchRemote(["--remote", "foo"]);
    expect(result.exitCode).toBe(1);
    expect(result.stdout).toBe("");
    expect(parsedStderr(result.stderr).code).toBe("BAD_ARG");
    expect(parsedStderr(result.stderr).error).toContain("--remote");
    expect(parsedStderr(result.stderr).error).toContain("foo");
    expect(requests).toEqual([]);
  });

  test("comma-separated modes are normalised and re-joined", async () => {
    for (const [flag, expected] of [
      ["remote,hybrid", "remote,hybrid"],
      ["remote,on-site", "remote,onsite"],
    ] as const) {
      const { result, requests } = await searchRemote(["--remote", flag]);
      expect(result.exitCode).toBe(0);
      expect(result.stderr).toBe("");
      expect(requests).toHaveLength(1);
      expect(requests[0].searchParams.get("work_mode")).toBe(expected);
    }
  });

  test("--remote remote,foo exits 1 with BAD_ARG naming foo and does not call the API", async () => {
    const { result, requests } = await searchRemote(["--remote", "remote,foo"]);
    expect(result.exitCode).toBe(1);
    expect(result.stdout).toBe("");
    expect(parsedStderr(result.stderr).code).toBe("BAD_ARG");
    expect(parsedStderr(result.stderr).error).toContain('"foo"');
    expect(requests).toEqual([]);
  });

  test("documented modes and on-site reach work_mode", async () => {
    for (const [flag, expected] of [
      ["remote", "remote"],
      ["hybrid", "hybrid"],
      ["onsite", "onsite"],
      ["on-site", "onsite"],
      ["Remote", "remote"],
    ] as const) {
      const { result, requests } = await searchRemote(["--remote", flag]);
      expect(result.exitCode).toBe(0);
      expect(result.stderr).toBe("");
      expect(requests).toHaveLength(1);
      expect(requests[0].searchParams.get("work_mode")).toBe(expected);
    }
  });

  test("bare --remote still means work_mode=remote", async () => {
    const { result, requests } = await searchRemote(["--remote"]);
    expect(result.exitCode).toBe(0);
    expect(requests).toHaveLength(1);
    expect(requests[0].searchParams.get("work_mode")).toBe("remote");
  });

  test("omitting --remote does not send work_mode", async () => {
    const { result, requests } = await searchRemote([]);
    expect(result.exitCode).toBe(0);
    expect(requests).toHaveLength(1);
    expect(requests[0].searchParams.has("work_mode")).toBe(false);
  });
});
