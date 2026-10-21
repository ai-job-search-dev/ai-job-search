import { createCLI } from "@bunli/core"
import { writeError } from "./helpers.js"
import { search } from "./commands/search.js"
import { detail } from "./commands/detail.js"
import { categories } from "./commands/categories.js"
import { autocomplete } from "./commands/autocomplete.js"
import { locations } from "./commands/locations.js"

const cli = await createCLI({
  name: "jobdanmark-cli",
  version: "1.0.0",
  description: "CLI for the Jobdanmark.dk public job search API",
})

const commands = [search, detail, categories, autocomplete, locations]
for (const command of commands) {
  cli.command(command)
}

// Reject unknown flags before dispatch. bunli silently discards them, and a
// silently discarded filter changes what the search returns without any error
// (a wrong flag name once returned an entire portal's database as if it
// matched the query). add-portal.md's contract requires a bogus flag to exit 1
// with a JSON error on stderr; this enforces it for the reference CLIs too.
//
// Both dash forms are checked. This loop inspected only `--long` tokens until
// #426, so an undefined short flag was discarded in silence: `-q "..."` on a
// portal whose keyword flag is `--search-string` returned the whole database
// as a successful, unfiltered search. Declared shorts and bunli's built-in
// -h/-v stay valid; every other single-dash token is rejected, including a
// negative number. bunli does not consume a `-`-prefixed token as the previous
// flag's value - it discards it - so `--radius -5` silently fell back to the
// default radius rather than failing its own `min(1)` schema. Erroring on it
// is the same trade linkedin-search already makes. A value that must begin
// with a dash uses the `--flag=value` form, which is checked as a long flag.
const argv = process.argv.slice(2)
const invoked = commands.find((c) => (c as { name?: string }).name === argv[0])
if (invoked) {
  const options =
    (invoked as { options?: Record<string, { short?: string; repeatable?: boolean } | undefined> }).options ?? {}
  const known = new Set([...Object.keys(options), "help", "version"])
  const knownShorts = new Set(
    Object.values(options)
      .map((o) => o?.short)
      .filter((s): s is string => typeof s === "string")
      .concat("h", "v"),
  )
  const rejectFlag = (rendered: string): never => {
    writeError(
      `unknown flag ${rendered} for '${argv[0]}' - flags are never silently ignored, because a discarded filter changes what the search returns; see --help for the supported flags`,
      "UNKNOWN_FLAG",
    )
    process.exit(1)
  }
  for (const token of argv.slice(1)) {
    if (token === "--") break
    if (token.startsWith("--")) {
      const flag = token.slice(2).split("=")[0]
      if (!known.has(flag)) rejectFlag(`--${flag}`)
    } else if (token.startsWith("-") && token !== "-") {
      const flag = token.slice(1).split("=")[0]
      if (!knownShorts.has(flag)) rejectFlag(`-${flag}`)
    }
  }
  // A declared option that is not `repeatable` keeps only its last value in
  // bunli, so `--region A --region B` searched B alone and said nothing.
  // Count every occurrence in both spellings and refuse a repeat; a
  // repeatable option (jobbank's `--type 3 --type 6`) is unaffected.
  const optionName = (token: string): string | undefined => {
    const flag = token.replace(/^-+/, "").split("=")[0]
    if (token.startsWith("--")) return options[flag] !== undefined ? flag : undefined
    return Object.entries(options).find(([, o]) => o?.short === flag)?.[0]
  }
  const occurrences = new Map<string, number>()
  for (const token of argv.slice(1)) {
    if (token === "--") break
    if (!token.startsWith("-")) continue
    const name = optionName(token)
    if (name === undefined || options[name]?.repeatable) continue
    occurrences.set(name, (occurrences.get(name) ?? 0) + 1)
  }
  for (const [name, count] of occurrences) {
    if (count > 1) {
      writeError(
        `option --${name} is given ${count} times - it takes one value, and keeping only the last would silently drop the others`,
        "BAD_ARG",
      )
      process.exit(1)
    }
  }
  // Every declared portal option takes a value; only the built-in help and
  // version flags are switches. Bunli drops a bare known option before schema
  // validation, so an optional filter disappears or a default is used instead.
  // Check after unknown flags so negative tokens keep their existing error.
  for (let i = 1; i < argv.length; i++) {
    const token = argv[i]
    if (token === "--") break
    if (!token.startsWith("-") || token.includes("=")) continue
    const name = token.replace(/^-+/, "")
    const isOption = token.startsWith("--")
      ? options[name] !== undefined
      : Object.values(options).some((o) => o?.short === name)
    if (!isOption) continue
    const next = argv[i + 1]
    if (next === undefined || next.startsWith("-")) {
      writeError(`option ${token} requires a value`, "BAD_ARG")
      process.exit(1)
    }
  }

}

await cli.run()
