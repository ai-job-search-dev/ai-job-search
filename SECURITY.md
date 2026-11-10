# Security Policy

## Reporting a vulnerability

Please report security findings privately via **[GitHub private vulnerability reporting](https://github.com/ai-job-search-dev/ai-job-search/security/advisories/new)** rather than a public issue. You will get a response within a few days, credit in the fix unless you prefer otherwise, and public disclosure coordinated with the patch.

If the private form is unavailable, open a public issue that describes the *class* of problem without a working recipe, and note that you have details to share privately.

## Threat model, honestly stated

This is an agentic workflow: an LLM with file access reads untrusted web content (job postings) alongside your personal data (CV, profile, application history). That combination is the main risk surface, and it cannot be fully eliminated - only narrowed. A posting can carry hidden text written to steer the agent. The framework answers that on two levels: instruction-level rules that tell the agent how to treat postings, and permission-level rules that bound what the agent can do even if it is steered.

### Instruction level

`/apply`, `/rank`, `/scrape`, `/interview` and the skill's own `09-web-research.md` treat posting text as data, never instructions: agents are told not to follow directions embedded in postings and not to fetch URLs found inside posting text (the user-supplied posting URL is the one exception). Reviewer research starts from the company identity the user confirmed, never from links in the posting body. `/apply` Step 1 also classifies the posting's host (installed portal, known ATS apex, or unverified) before any drafting.

### Permission level

Every grant the template ships, where it lives, how long it lasts, and what checks it. "Guard" is `tools/security_guards.py`, which CI runs on every PR and which fails on any entry it does not know.

| Surface | What is granted | Lifetime | Guard |
|---|---|---|---|
| `.claude/settings.json` `permissions.allow` | Each shipped portal CLI by exact path, `salary_lookup.py`, the `tools/` scripts (`rank_state`, `job_key`, `verify_pdf`, `verify_layout`, `robots_check`, `fetch_page`), and `Skill(job-application-assistant)` | Whole session | Exact allowlist; any addition fails CI unless added to `ALLOWED_PERMISSIONS` in the same PR |
| `.claude/settings.json` `permissions.ask` | Always prompt, even over an allow rule or a skill grant: edits to `settings.json`, `settings.local.json`, any `.claude/skills/*/SKILL.md`, any `.claude/commands/*.md`, `.claude/agents/**`, `.github/**`, `tools/security_guards.py`; edits to the files the allow list runs by path (`tools/**`, `.agents/**`, `salary_lookup.py`); and `git push` | Whole session | Each rule is required; removal fails CI |
| `.claude/settings.json` `permissions.deny` | Blocked in every mode, `bypassPermissions` included: `curl`/`wget` with data, form or upload flags, `scp`, `sftp`, `ssh`, `nc`; reads of `.env`, `.env.*`, `~/.ssh/**`, `~/.aws/**`, `~/.gnupg/**` (Read denies also cover Bash `cat`/`head`/`tail`/`sed`) | Whole session | Each rule is required; removal fails CI |
| Skill `allowed-tools` (`.claude/skills/*/SKILL.md`) | `job-application-assistant`: Read, Glob, Grep, WebFetch, WebSearch, Edit, Write, AskUserQuestion. `scrape`: the same plus Agent, `bun --version`, `tools/job_key.py`. `upskill`: Read, Write, Glob, Grep, WebFetch, WebSearch. No bare `Bash` anywhere. | The turn that invokes the skill; clears on your next message. Not gated by workspace trust. | Exact allowlist (`ALLOWED_SKILL_TOOLS`); bare tools and wildcards fail CI |
| Portal skill `allowed-tools` (`.agents/skills/<name>/SKILL.md`) | Exactly that portal's own `cli/src/cli.ts` and nothing else | Invoking turn | Checked by path, so a skill that grants another portal's CLI, or a glob, fails CI |
| Slash commands (`.claude/commands/*.md`) | No `allowed-tools` frontmatter; they run under the session's rules above | - | Same frontmatter check as skills |
| Subagents (`.claude/agents/*.md`) | `posting-fetcher` (`/apply` Step 0), `posting-scorer` (`/rank` Step 2) and `application-reviewer` (`/apply` Step 3): Read, Glob/Grep where needed, WebFetch, WebSearch, Bash - never Edit, Write or Agent. Bash inside them is scoped by the allow list above, because an agent's `tools:` list is tool-name granularity. `/scrape`'s portal-search agents still run as `general-purpose`. | While the agent runs | Check 5: every agent must declare `tools:` drawn from `ALLOWED_AGENT_TOOLS`; a missing key (= every tool) or a `Bash(pattern)` entry fails CI, as do a `permissionMode` that removes prompts and agent `hooks` or `mcpServers` |
| The posting integrity gate (`tools/posting_guard.py`) | Runs on every posting before `/apply` Step 1 (URL or pasted), inside each `/rank` scorer, and on the archived posting in `/interview`: removes hidden HTML and invisible characters, quotes instruction-shaped lines and hidden URLs, lists mixed-script words, prints a one-line verdict. Advises; the user decides. The archive keeps counts, never the removed excerpts. | - | Pinned by `tests/test_posting_guard.py` and `tests/test_posting_gate_wiring.py` |
| Hooks (`.claude/settings.json` `hooks`, skill and command frontmatter `hooks`, !`command` lines) | None. The template ships no hooks: a hook runs with no prompt the moment its event fires, which is the vector the Shai-Hulud worm used in August 2026. A !`command` line in a skill or command runs when it loads, the same way. | - | Any of them fails CI |
| Every other `settings.json` key | None. `permissions` (with `allow`, `ask`, `deny`) and `hooks` are the whole file: `env` would reach every pre-approved `python tools/*.py` run, `apiKeyHelper` and a `statusLine` command run a program with no prompt, and `permissions.defaultMode` or `additionalDirectories` would widen what the lists above scope. | - | Any other top-level or `permissions` key fails CI (`ALLOWED_SETTINGS_KEYS`, `ALLOWED_PERMISSIONS_KEYS`) |
| Compiling (`lualatex`, `xelatex`, a custom template's command) | Not pre-approved: a `lualatex:*` grant would also approve `-shell-escape`. In default mode each compile prompts. Answering "don't ask again" would persist exactly that `lualatex` rule to your `settings.local.json`, so answer per call. | - | - |
| Portal CLI manifests (`.agents/**/package.json`) | No lifecycle scripts (`preinstall`, `postinstall`, ...), no `trustedDependencies`, no `workspaces` (whose packages bring their own scripts) and no `bunfig.toml` or `.npmrc` beside the manifest (either can point every dependency at another registry), so `bun install` runs no code from a source the template did not review; the installer passes `--ignore-scripts` as well | - | Fails CI; `tools/install_portal_clis.py` also skips a manifest that carries one |
| Personal-data ignore rules (`.gitignore`) | Every rule in `REQUIRED_IGNORE_RULES` present, read as git reads them (leading whitespace is part of the pattern); no negation outside `ALLOWED_IGNORE_NEGATIONS`, in the root file or in any subdirectory `.gitignore`, which outranks it | - | Fails CI |
| The browser-header retry (`tools/fetch_page.py`) | A GET with a fixed argv, http(s) only, 45 s / 5 redirects / 10 MB, `robots.txt` enforced before each redirect target with no bypass flag, output refused inside the repo | - | Pinned by `tests/test_fetch_page.py`; the grant names the script, not `curl` |

### What this does not do

- **It is not a sandbox.** Claude Code's Bash sandbox does not run on native Windows at all, and on macOS and Linux it is off by default. If you are on macOS or Linux, turning it on in your user settings is the single strongest addition you can make; nothing in this repo conflicts with it.
- **Prefix rules are tripwires, not walls.** `Bash(curl -d:*)` matches a command that starts that way; `curl -sS -d` does not match it. The deny list catches the obvious shapes and makes the rest need deliberate evasion, which is a different bar from preventing it.
- **WebFetch is unrestricted by domain.** Company research needs it, and a GET to an arbitrary host can carry data in the URL. The instruction-level rule (never fetch URLs from the posting body) is what stands between a hidden link and a fetch; the permission layer does not.
- **`/rank` scorers still hold the rubric and the posting in one context.** Each scoring agent fetches and scores a batch, so the candidate rubric and the untrusted posting share a context that also has WebFetch. What the agent definition removes is Edit, Write and unscoped shell. Fully separating fetch from scoring would route every posting through the orchestrator, which `/rank` is built to avoid.
- **The gate is deterministic, not an oracle.** `posting_guard.py` catches the payload classes that leave a trace: hidden markup, invisible characters, agent-addressed phrasing, hidden links, look-alike letters. A manipulation written to read naturally to a human and an agent alike leaves none, and the instruction-level rules remain the only defence for that.
- **Edit and Write are what the skill does.** Tailoring a CV means editing files, so those tools stay granted. What the `ask` list guarantees is that an agent cannot silently turn that into a wider grant: the files that define permissions always prompt, and so do the files the allow list pre-approves to run, since rewriting one would turn its allow rule into whatever was written.

Instruction-level defenses raise the bar; permission-level defenses bound the damage. Neither is a guarantee. If you run this workflow against job boards you do not trust at all, review what the agent fetched and wrote before sending anything out.

## Scope notes

- Portal CLI skills make live requests only when you run them; CI never does.
- Community fork skills listed in the [forks index](https://github.com/ai-job-search-dev/ai-job-search/discussions/78) are **not** covered by this policy - review the code you copy, as the index itself says.
