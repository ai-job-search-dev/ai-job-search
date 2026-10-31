import { describe, expect, test } from "bun:test";
import { runSearchFixture } from "./helpers";

const LOCATION = "Copenhagen, Denmark";

// parseFlags stores a value-less flag as `true` and an empty argument as "",
// and the search took `--query` only when it was a non-empty string. So a
// bare `-q`, `-q ""` (an unset shell variable) or a blank query dropped the
// keywords and returned every posting in the location with exit 0. These run
// the real CLI against the saved search fixture and count outbound requests.
describe("--query requires a value", () => {
  for (const args of [
    ["-q"],
    ["-q", "--jobage", "7"],
    ["--query", ""],
    ["-q", "   "],
  ]) {
    test(`${JSON.stringify(args)} exits 1 with BAD_ARG before any request`, async () => {
      const { result, requests } = await runSearchFixture(["search", "-l", LOCATION, ...args]);
      expect(result.exitCode).toBe(1);
      expect(result.stdout).toBe("");
      const error = JSON.parse(result.stderr);
      expect(error.code).toBe("BAD_ARG");
      expect(error.error).toContain("--query");
      expect(requests).toHaveLength(0);
    });
  }

  test("a query is sent as keywords", async () => {
    const { result, requests } = await runSearchFixture(["search", "-l", LOCATION, "-q", "data engineer"]);
    expect(result.exitCode).toBe(0);
    expect(requests).toHaveLength(1);
    expect(requests[0].searchParams.get("keywords")).toBe("data engineer");
  });

  test("omitting --query is still an intentional location-only search", async () => {
    const { result, requests } = await runSearchFixture(["search", "-l", LOCATION]);
    expect(result.exitCode).toBe(0);
    expect(requests).toHaveLength(1);
    expect(requests[0].searchParams.has("keywords")).toBe(false);
  });
});
