import { describe, expect, test } from "bun:test";
import { runSearchFixture } from "./helpers";

const LOCATION = "Copenhagen, Denmark";

// workTypeFlag() maps a mode it does not recognise to null, and the search
// URL then carries no f_WT at all. So `--remote remote,hybrid`, a misspelt
// mode, or a bare `--remote` searched every workplace type - onsite included -
// and exited 0 as if the filter had been applied. These run the real CLI
// against the saved search fixture and count the outbound requests.
describe("--remote mode", () => {
  for (const args of [
    ["--remote", "remote,hybrid"],
    ["--remote", "remotely"],
    ["--remote", "--limit", "1"],
    ["--remote"],
  ]) {
    test(`${args.join(" ")} exits 1 with BAD_ARG before any request`, async () => {
      const { result, requests } = await runSearchFixture(["search", "-l", LOCATION, "-q", "engineer", ...args]);
      expect(result.exitCode).toBe(1);
      expect(result.stdout).toBe("");
      const error = JSON.parse(result.stderr);
      expect(error.code).toBe("BAD_ARG");
      expect(error.error).toContain("--remote");
      expect(requests).toHaveLength(0);
    });
  }

  for (const [mode, wt] of [["remote", "2"], ["hybrid", "3"], ["onsite", "1"], ["on-site", "1"], ["Remote", "2"]]) {
    test(`--remote ${mode} sends f_WT=${wt}`, async () => {
      const { result, requests } = await runSearchFixture(["search", "-l", LOCATION, "--remote", mode]);
      expect(result.exitCode).toBe(0);
      expect(requests).toHaveLength(1);
      expect(requests[0].searchParams.get("f_WT")).toBe(wt);
    });
  }

  test("omitting --remote still searches every workplace type", async () => {
    const { result, requests } = await runSearchFixture(["search", "-l", LOCATION]);
    expect(result.exitCode).toBe(0);
    expect(requests).toHaveLength(1);
    expect(requests[0].searchParams.has("f_WT")).toBe(false);
  });
});
