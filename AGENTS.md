---
framework_version: 1.0.1
---

# Agent Guidelines: AI Job Search

This workspace is structured to manage job search activities, scraper tools, CVs, cover letters, and interview preparation.

## Thin-Pointer Design (Single Source of Truth)

To prevent duplication and configuration drift across different AI agent frameworks (Claude Code, Google Antigravity, Codex, Cursor, Gemini CLI, etc.), this workspace uses a unified thin-pointer design. All agent runtimes should load the canonical specifications and candidate profiles from the files and directories below:

1. **Personal Candidate Profile:**
   - The candidate profile, contact details, education, and target preferences are defined in [CLAUDE.md](CLAUDE.md) and the individual profile methodology files under [.claude/skills/job-application-assistant/](.claude/skills/job-application-assistant/) (specifically `01-*.md` etc.).
2. **Canonical Workflow Specifications:**
   - The step-by-step instructions and triggers for tasks (setup, scrape, rank, apply, upskill, interview) are defined in the [.claude/](.claude/) directory (specifically under `.claude/skills/` and `.claude/commands/`).
   - Do not duplicate these rules or specifications. Treat `.claude/` files as the single source of truth.
3. **Portal Search Skills:**
   - Job-portal search CLIs are installed under `.agents/skills/` in the portable Agent Skills format (with a `SKILL.md` per portal). None ship with this repo: copy them in from [community-portals](https://github.com/ai-job-search-dev/community-portals). Codex and Antigravity discover these automatically; the `/scrape` workflow in [.claude/skills/job-scraper/](.claude/skills/job-scraper/) orchestrates them.
4. **Automated Pipeline (`tools/`):**
   - Ingestion, ranking, tailoring and the human-in-the-loop apply assistant (`tools/pipeline.py`, `ranker.py`, `tailor.py`, `browser_apply.py`, `form_probe.py`) are operated through [.claude/skills/quantum-pipeline/](.claude/skills/quantum-pipeline/SKILL.md). Read it before running or changing any of them: it holds the commands, the status lifecycle, the configuration map and the hard rules (never click Submit, never bypass the approved-host check, never write to the database by hand).
   - Screening rules live only in the `preferences:` block of `profile/master_profile.yaml`; see [config/PREFERENCES.md](config/PREFERENCES.md).
