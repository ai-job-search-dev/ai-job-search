#!/usr/bin/env bun
// hn-hiring-search: Hacker News "Ask HN: Who is hiring?" threads and YC startup
// job posts, through HN's public APIs. No key, no dependencies.
import { detail } from "./commands/detail";
import { search } from "./commands/search";
import { parseArgs } from "./helpers";

const USAGE = `Usage:
  bun run .agents/skills/hn-hiring-search/cli/src/cli.ts search [-q "a OR b"] [--title-only] [--remote] [-l <place>] [--jobage <days>] [--months <n>] [--source all|hn|yc] [-n <limit>] [--no-description] [--format json|table|plain]
  bun run .agents/skills/hn-hiring-search/cli/src/cli.ts detail <hn-123|yc-123|123|HN URL> [--format json|plain]`;

const { command, flags, positional } = parseArgs(process.argv.slice(2));
const format = String(flags.format ?? "json");
if (command === "search") await search(flags, format);
else if (command === "detail") await detail(positional[0], format);
else {
  if (command && command !== "help" && command !== "--help") {
    process.stderr.write(JSON.stringify({ error: `Unknown command "${command}"`, code: "BAD_CMD" }) + "\n");
    process.exit(1);
  }
  console.log(USAGE);
}
