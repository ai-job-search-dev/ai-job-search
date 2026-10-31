# Setup Guide

Step-by-step instructions for getting the AI Job Search framework running.

## 1. Prerequisites

### Claude Code

Install Claude Code (Anthropic's CLI for Claude):

```bash
npm install -g @anthropic-ai/claude-code
```

You'll need an Anthropic API key or a Claude Pro/Team subscription. See the [Claude Code docs](https://docs.anthropic.com/en/docs/claude-code) for details.

### Python

Python 3.10+ is required for the salary lookup tool. Check with:

```bash
python3 --version
```

On Windows, `py --version` is often the most reliable check. If your system exposes Python as `python` instead of `python3`, use `python` in the commands below.

### Bun (for job search tools)

The job portal CLIs (four Danish portals plus the country-agnostic `linkedin-search` and `freehire-search` tools) are written in TypeScript and run with Bun.

- macOS/Linux:

```bash
curl -fsSL https://bun.sh/install | bash
```

- Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://bun.sh/install.ps1 | iex"
```

If you prefer a package manager, `winget install Oven-sh.Bun` also works on Windows.

### LaTeX (for compiling CVs and cover letters)

Install a LaTeX distribution to compile the generated `.tex` files to PDF:

- **Windows:** [MiKTeX](https://miktex.org/download)
- **macOS:** [MacTeX](https://tug.org/mactex/)
- **Linux:** `sudo apt install texlive-full` or `sudo dnf install texlive-scheme-full`

The CV compiles with `lualatex` (pdflatex often fails on modern MiKTeX installs with `fontawesome5` font-expansion errors). The cover letter compiles with `xelatex` because `cover.cls` requires `fontspec` for its custom Lato/Raleway fonts.

#### Minimal TeX install: TinyTeX/BasicTeX

Full TeX distributions work out of the box, but minimal distributions need a few extra packages before the stock templates compile.

On macOS, a user-level TinyTeX install avoids a system-wide installer and does not require `sudo`:

```bash
curl -fsSL https://yihui.org/tinytex/install-bin-unix.sh -o /tmp/tinytex-install-bin-unix.sh
sh /tmp/tinytex-install-bin-unix.sh /tmp --no-path
export PATH="$HOME/Library/TinyTeX/bin/universal-darwin:$PATH"
```

The `export` above only lasts for the current terminal window. Persist it, or every new window reports `zsh: command not found: lualatex` (or `xelatex`) even though TinyTeX installed fine:

```bash
echo 'export PATH="$HOME/Library/TinyTeX/bin/universal-darwin:$PATH"' >> ~/.zshrc
source ~/.zshrc
lualatex --version   # should print a version, not "command not found"
```

(Bash users: append to `~/.bash_profile` instead.)

Then install the template dependencies:

```bash
tlmgr install \
  moderncv fontawesome5 fontawesome6 academicons import luatexbase pgf \
  titlesec textpos xltxtra xunicode cite realscripts needspace
```

For BasicTeX/MacTeX, make sure the TeX binary directory is on `PATH` first (for example via `/Library/TeX/texbin`), then run the same `tlmgr install ...` command.

Quick smoke tests after setup:

```bash
cd cv && lualatex -interaction=nonstopmode -halt-on-error main_example.tex && cd ..

SMOKE_DIR="$(mktemp -d /tmp/ai-job-cover-smoke.XXXXXX)"
cp -R cover_letters/cover.cls cover_letters/OpenFonts "$SMOKE_DIR/"
cat >"$SMOKE_DIR/cover_smoke.tex" <<'EOF'
\documentclass[]{cover}
\begin{document}
\namesection{Test}{Candidate}{test@example.com}
\companyname{Example Company}
\companyaddress{123 Hiring Street\\Example City}
\currentdate{\today}
\lettercontent{Dear Hiring Manager,}
\lettercontent{This smoke test verifies that xelatex can load cover.cls and the bundled fonts.}
\closing{Sincerely,}
\signature{Test Candidate}
\end{document}
EOF
(cd "$SMOKE_DIR" && xelatex -interaction=nonstopmode -halt-on-error cover_smoke.tex)
```

#### Windows: Basic MiKTeX

The full MiKTeX installer bundles every CTAN package and works out of the box, but the smaller [Basic MiKTeX](https://miktex.org/download) installer (`basic-miktex-*.exe`) only ships a minimal package set and needs a couple of one-time settings before the stock templates compile.

By default, MiKTeX installs missing packages on demand but pops up a GUI prompt for each one — which blocks non-interactive terminals (including Claude Code's Bash tool). Turn that into a silent auto-install instead:

```powershell
initexmf --admin --set-config-value=[MPM]AutoInstall=1
initexmf --set-config-value=[MPM]AutoInstall=1
```

(Run the first line from an elevated/Admin PowerShell if you installed MiKTeX for all users; the second line covers a per-user install. Only one will apply depending on how you installed it — running both is harmless.)

If you'd rather not rely on on-the-fly installs at all (for example, for a fully offline compile later), pre-install the same package set the macOS TinyTeX section above lists, using MiKTeX's package manager:

```powershell
mpm --admin --install=moderncv --install=fontawesome5 --install=fontawesome6 --install=academicons --install=import --install=luatexbase --install=pgf --install=titlesec --install=textpos --install=xltxtra --install=xunicode --install=cite --install=realscripts --install=needspace
```

Drop `--admin` if MiKTeX is installed for the current user only. If a package name doesn't resolve, `mpm --find=<name>` searches the repository for the correct name.

Quick smoke tests after setup (PowerShell):

```powershell
Set-Location cv; lualatex -interaction=nonstopmode -halt-on-error main_example.tex; Set-Location ..

$SmokeDir = New-Item -ItemType Directory -Path (Join-Path $env:TEMP "ai-job-cover-smoke-$(Get-Random)")
Copy-Item cover_letters\cover.cls, cover_letters\OpenFonts -Destination $SmokeDir -Recurse
@'
\documentclass[]{cover}
\begin{document}
\namesection{Test}{Candidate}{test@example.com}
\companyname{Example Company}
\companyaddress{123 Hiring Street\\Example City}
\currentdate{\today}
\lettercontent{Dear Hiring Manager,}
\lettercontent{This smoke test verifies that xelatex can load cover.cls and the bundled fonts.}
\closing{Sincerely,}
\signature{Test Candidate}
\end{document}
'@ | Set-Content (Join-Path $SmokeDir "cover_smoke.tex")
Push-Location $SmokeDir; xelatex -interaction=nonstopmode -halt-on-error cover_smoke.tex; Pop-Location
```

### Optional: ATS text extraction (pypdf, then pdftotext)

`/apply` runs an ATS parseability check on the compiled CV: it extracts the PDF's text layer and verifies contact details, reading order, and keyword coverage the way an applicant-tracking system sees them.

The default extractor is **pypdf** (BSD, `pip install pypdf`). Poppler `pdftotext` remains an optional fallback:

- **macOS:** `brew install poppler`
- **Debian/Ubuntu:** `sudo apt install poppler-utils`
- **Windows:** `choco install poppler`

If a command still uses `pdftotext -layout`, it must pass `-enc UTF-8` as well. If **neither** extractor is available, `/apply` skips the mechanical check with a warning and falls back to a visual keyword review — everything else works normally.

## 2. Fork and clone

```bash
gh repo fork ai-job-search-dev/ai-job-search --clone
cd ai-job-search
gh repo set-default <your-github-username>/ai-job-search
```

Or manually: fork on GitHub, then clone your fork.

> **The `set-default` line is not optional.** `gh repo fork --clone` sets the
> **upstream** repo as gh's default repository ("The `upstream` remote will be set as
> the default remote repository" — `gh repo fork --help`), and gh uses the default for
> **creating issues and PRs**. Without it, any later `gh issue create` run from this
> clone — by you or by an agent you have asked to track your applications — silently
> files on the upstream **public** tracker, publishing whatever the issue contains
> under your GitHub identity, on a repo where you cannot delete it (#389).

> **Before you go further: forks are public.** GitHub cannot make a fork of a public
> repository private, and `/setup` (section 6) writes your personal data into **tracked**
> files — pushing those commits to a fork publishes them. If this copy is for your own
> job search rather than for contributing, prefer a **private repository** with this repo
> as `upstream`: see section 8, step 1 for the exact commands and why committing your
> personalization there is still the right move. Everything else in this guide works
> identically either way.

## 3. Install job search CLI dependencies
Run these from the repository root.

```bash
python3 tools/install_portal_clis.py   # Windows: python tools\install_portal_clis.py
```

It runs `bun install` in every portal CLI under `.agents/skills/*/cli/` - discovered, not listed, so a portal you add with `/add-portal` or copy from another fork is included on a fresh clone. Every install runs with `--ignore-scripts`, and a CLI whose `package.json` declares an install-time lifecycle script, `trustedDependencies` or `workspaces`, or that has a `bunfig.toml` or `.npmrc` beside it, is skipped and reported rather than run. `--dry-run` shows what it would install.

For `linkedin-search` and `freehire-search` the install is optional: both have zero runtime dependencies and run with plain `bun`; `bun install` only pulls TypeScript dev types.

If you're outside Denmark, you can generate an equivalent search skill for your local job board with `/add-portal` — it scaffolds the same CLI structure for any public portal and test-runs a live query before registering. See the "Job search tools" section in the README.

## 4. Run the setup interview

Start Claude Code in the repository:

```bash
claude
```

Then run the onboarding:

```
/setup
```

Claude will offer three paths:

- **Path A (documents folder):** Add your CV, LinkedIn export, diplomas, references, or past applications under `documents/`. Claude reads and cross-references them before proposing profile updates. This is best when you have several source files.
- **Path B (single CV import):** Share one CV/resume by mentioning the file with `@` or pasting the text. Claude extracts it and asks follow-up questions for anything missing.
- **Path C (interview mode):** Answer structured interview questions section by section.

All three paths produce the same result: fully populated profile files.

### What gets populated

| File | Content |
|------|---------|
| `CLAUDE.md` | Your full candidate profile |
| `01-candidate-profile.md` | Structured education, experience, skills |
| `02-behavioral-profile.md` | Behavioral assessment |
| `04-job-evaluation.md` | Personalized skill match areas and career goals |
| `05-cv-templates.md` | Profile statement templates for your background |
| `07-interview-prep.md` | STAR examples from your experience |
| `cv/main_example.tex` | Your LaTeX CV with actual details |
| `search-queries.md` | Job search queries for `/scrape` |

### Re-running setup

You can update specific sections later:

```
/setup --section skills
/setup --section experience
/setup --section search
```

The `--section search` option is especially useful as your priorities evolve. It re-runs the search configuration interview and suggests role types you may not have considered based on your full profile.

## 5. Optional: Set up salary benchmarking

If you have salary data (from a union, salary survey, Glassdoor, or personal research):

1. **Option A:** Create `salary_data.json` manually in the repo root (see `tools/README_SALARY_TOOL.md` for the format)
2. **Option B:** Convert from Excel:
   ```bash
   pip install openpyxl
   python3 tools/convert_salary_excel.py path/to/salary-data.xlsx --source "My Salary Data 2025"
   ```

This creates `salary_data.json` which the `/apply` workflow uses for salary benchmarking. If you skip this step, salary lookup is simply omitted.

## 6. Test the workflow

Find a job posting you're interested in, then:

```
/apply https://jobindex.dk/job/1234567
```

Or paste the job description directly:

```
/apply [paste job posting text here]
```

Claude will:
1. Evaluate the fit against your profile
2. Ask if you want to proceed
3. Draft a tailored CV and cover letter
4. Have a reviewer agent critique the drafts
5. Revise and present the final output

## 7. Compile your documents

After `/apply` creates the LaTeX files:

```bash
# Bash / zsh / Git Bash
cd cv && lualatex main_<company>_<role>.tex && cd ..
cd cover_letters && xelatex cover_<company>_<role>.tex && cd ..
```

```powershell
# PowerShell
Set-Location cv; lualatex main_<company>_<role>.tex; Set-Location ..
Set-Location cover_letters; xelatex cover_<company>_<role>.tex; Set-Location ..
```

These commands apply to the stock templates (moderncv CV, `cover.cls` cover letter). If you'd rather use your own LaTeX template, run `/add-template` — it captures the template's compile engine, fonts, style rules, and page limit, test-compiles it, and wires it into `/apply`. See the "LaTeX templates" section in the README.

## 8. Pulling upstream updates into your fork

Upstream keeps improving the methodology files your fork has personalized, so plan for updates from day one:

**Prefer releases over raw `master`.** Tagged [releases](../../releases) are weekly checkpoints of `master` that passed CI, each listing the PRs it contains on its release page ([CHANGELOG.md](CHANGELOG.md) covers releases up to 1.7.4). Updating to a tag pulls a stable, documented state instead of whatever `master` happens to be mid-review. Fetch tags with `git fetch upstream --tags` and merge a release (for example `git merge v1.0.0`) when you want stability; pull `master` directly only when you specifically want the latest unreleased changes. The steps below apply either way - substitute the release tag for `upstream/master` where you see it.

1. **Commit your personalization - but know where those commits land.** `/setup` edits CLAUDE.md and the profile skill files in place; those edits are *yours*, and committing them is what lets updates merge cleanly. But a GitHub **fork of this repo is public** - forks of public repositories cannot be made private - so anything you commit *and push to a fork* is visible to anyone. If you want your profile in a remote at all, don't push it to a fork: create a **private** repository, push there, and add this repo as the `upstream` remote (`git remote add upstream https://github.com/ai-job-search-dev/ai-job-search.git`) to keep receiving updates. Committing locally without pushing is also fine. The genuinely sensitive files (tracker, salary data, `documents/`, application archives) are gitignored and never enter git either way. An uncommitted working tree is the most common reason `git pull` refuses to merge at all (`Your local changes ... would be overwritten`).
2. **Preview what changed before pulling:**
   ```bash
   git remote add upstream https://github.com/ai-job-search-dev/ai-job-search.git   # first time only, if you cloned your own fork
   git fetch upstream    # or origin, if you cloned the template directly
   python3 tools/check_upstream_updates.py
   ```
   It compares the `framework_version` markers in your framework files against upstream and lists exactly which methodology files changed, with the diff command for each.

   Two tools answer two different questions, and it's worth running both:
   - **`check_upstream_updates.py`** — *which of my personalized files changed?* It reads the `framework_version` stamp on each methodology file, so it flags exactly the customized files a release touched.
   - **`upstream_triage.py`** — *which upstream commits deserve my attention?* It walks the commits you're behind and sorts them into "worth reviewing" vs "probably skip", dropping anything you've already cherry-picked (matched by `git patch-id`, so ported work falls off with no bookkeeping), commits that only touch files your fork removed, and SHAs you've listed in `.github/upstream-wontport.txt`. It's report-only — it prints ready-to-run `git cherry-pick` lines but never merges, pushes, or opens a PR, because on a fork "applies cleanly" isn't "correct".

     ```bash
     python3 tools/upstream_triage.py --remote upstream
     ```

     Forks also inherit a `.github/workflows/upstream-watch.yml` that runs this weekly and writes the result into a single rolling issue (it no-ops on the upstream template itself, and stays disabled on a fork until you enable Actions).
3. **Merge normally.** `git merge upstream/master` (or `git pull`) three-way-merges upstream's edits around your personalization; because methodology edits rarely touch the lines `/setup` filled in, most updates land cleanly. A conflict in a personalized file is a *feature*, not a failure — it means upstream changed methodology in a section you customized, and the version marker plus its changelog commit tell you why. Resolve by keeping your data and adopting the methodology change around it.

## Troubleshooting

### "salary_data.json not found"
This is expected if you haven't set up salary benchmarking. The `/apply` workflow skips this step automatically.

### Job search CLI tools not working
Make sure Bun is installed and you ran `bun install` in each CLI directory. The tools require network access to fetch job listings.

### LaTeX compilation errors
- CV: uses `lualatex` (pdflatex often fails on modern MiKTeX with `fontawesome5` font-expansion errors; lualatex handles the same sources cleanly)
- Cover letter: uses `xelatex` (for custom fonts in `OpenFonts/fonts/`)
- Make sure your LaTeX distribution includes the `moderncv` package

### Fonts not found in cover letter
The cover letter template expects fonts in `cover_letters/OpenFonts/fonts/`. Make sure this directory exists and contains the Lato and Raleway font files.

### Stale `.claude/settings.local.json` from an older clone
Shared Claude Code permissions now live in `.claude/settings.json`: an `allow` list scoped to the exact commands the workflow runs (each portal CLI by path, the `tools/` scripts and `salary_lookup.py`), an `ask` list that always prompts before the permission-defining files are edited or `git push` runs, and a `deny` list that blocks upload-shaped shell and reads of credential files in every mode. `SECURITY.md` has the full table. Earlier versions of this repo committed a broader `.claude/settings.local.json` that pre-approved `Bash(curl:*)`, `Bash(python:*)` and `Bash(bun:*)`. If you cloned before that change, git leaves the old file behind in your working copy, and its permissions still apply on top of `settings.json`. Delete it (or trim it to your own personal overrides):

```bash
rm .claude/settings.local.json
```
