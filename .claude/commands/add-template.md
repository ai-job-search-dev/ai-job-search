# /add-template - Register a Custom CV or Cover Letter Template

You are helping the user register their own CV or cover letter template with the AI Job Search framework — LaTeX, Typst, or any other toolchain that compiles to PDF from the command line. The framework ships with moderncv (banking style) for CVs and a custom `cover.cls` for cover letters. This command lets the user swap in their own template: store the template files, capture usage instructions (source extension, compile command, fonts, style rules, page limits), verify the template compiles, and wire it into the `/apply` workflow so every future application uses it.

`$ARGUMENTS` may contain a subcommand, a file path, or nothing.

Follow these steps **in order**.

---

## Step 0: Parse Arguments

- If `$ARGUMENTS` contains `--list`: run **List Mode** below and stop.
- If `$ARGUMENTS` contains `--use <name>`: run **Switch Mode** below, then continue to **Step 5: Activate** with the resolved template metadata. `--use default` deactivates any custom template and restores the stock guidance (see Step 5).
- If `$ARGUMENTS` contains a file path or @-mentioned file: treat it as the template source and carry it into Step 1.
- Otherwise: start the registration flow at Step 1.

### List Mode

Use Glob with `templates/**/TEMPLATE.md` to find registered templates. For each, read the manifest and print a table:

```
## Registered Templates

| Name | Type | Source | Toolchain | Fonts | Active |
|------|------|--------|-----------|-------|--------|
| <name> | CV / Cover letter | .tex/.typ/... | lualatex/typst/... | <main font> | yes/no |
```

A template is **active** if `05-cv-templates.md` (CV) or `06-cover-letter-templates.md` (cover letter) contains an `ACTIVE-TEMPLATE` managed block naming it. If no custom templates exist, say so and explain that `/add-template` registers one. Stop here.

### Switch Mode

If `$ARGUMENTS` contains `--use <name>`:

1. If `<name>` is `default`, skip template resolution and continue to Step 5 with `default` as the activation target.
2. Use Glob with `templates/**/TEMPLATE.md` and find manifests whose parent folder name exactly matches `<name>`.
3. If no manifest matches, stop and say the template is not registered. Suggest `/add-template --list` to see available names.
4. If more than one manifest matches, stop and list the matching manifest paths. Ask the user to rename one of the templates; activation must be unambiguous.
5. Read the matching `TEMPLATE.md` and extract:
   - **Type:** `CV` or `Cover letter`
   - **Source extension:** e.g. `.tex`, `.typ`
   - **Compile command:** the full declared command
   - **Engine/toolchain:** e.g. `lualatex`, `typst` (display label)
   - **Page limit:** `<N> page(s)`
   - **Fonts:** the full font summary line
6. Derive the template folder from the manifest path and verify `template<source-extension>` exists in the same folder. If it is missing, stop with an error; the template registration is incomplete.
7. Derive `<type>` for Step 5 from the manifest path:
   - `templates/cv/<name>/TEMPLATE.md` -> `cv`
   - `templates/cover_letters/<name>/TEMPLATE.md` -> `cover_letters`
8. Continue to Step 5 using the resolved `<name>`, `<type>`, `<source-extension>`, `<compile-command>`, engine/toolchain label, font summary, page limit, template skeleton path, and manifest path. Do not re-run Steps 1-4; `--use` switches an already-registered template.

---

## Step 1: Template Type and Source

Ask the user (skip anything already answered by `$ARGUMENTS`):

1. **Type:** Is this a **CV** template or a **cover letter** template?
2. **Source:** Where is the template? Accept any of:
   - A path or @-mention of a source file in any toolchain (`.tex` plus optional `.cls`/`.sty`, `.typ` plus optional local packages, or another compile-to-PDF format)
   - Pasted template content
   - A directory containing the template and its assets (class/package files, fonts, images)

Read every provided file. If the template references an include the declared toolchain doesn't ship by default — a custom `.cls`/`.sty` not part of standard TeX distributions, a Typst package imported via a local `#import`, or an equivalent for another toolchain — confirm the user has the file and ask for it if missing — the template cannot compile without it.

---

## Step 2: Capture Template Instructions

Interview the user for the metadata that `/apply` needs to use the template correctly. Infer as much as possible from the source first (LaTeX: documentclass, `\fontspec` calls, geometry, colors; Typst: `#set`/`#show` rules, `#import`s; other toolchains: whatever the format exposes) and present your inferences for confirmation rather than asking blind questions.

Collect:

1. **Name** - short kebab-case identifier (e.g. `awesome-cv`, `classic-serif`). If a folder with this name already exists under `templates/<type>/`, this is a re-registration: say so and offer to **update that template in place** (re-running Steps 2-4 against the existing folder, replacing its files and manifest) or to pick another name. Never create a second copy beside it - registration is idempotent (see Design Principles).
2. **Source extension** - the main file's extension (`.tex`, `.typ`, ...), inferred from the provided source file.
3. **Compile command** - the full command `/apply` and Step 4's test compile will run, using `<file>` (no extension) as the placeholder for the output basename:
   - **`.tex` source**: infer the engine the same way as before - if the source uses `fontspec` or loads font files by path, it requires `xelatex` or `lualatex`; tell the user this rather than letting them pick `pdflatex`. Render as `rm -f <file>.pdf && mkdir -p build && lualatex -interaction=nonstopmode -output-directory=build <file>.tex && mv build/<file>.pdf ./` (or the appropriate engine).
   - **`.typ` source**: default to `typst compile <file>.typ <file>.pdf` - Typst has a single binary, no engine choice.
   - **Anything else**: no built-in guidance; ask the user for the exact compile command.

   **Build directory rule:** if the toolchain can redirect its intermediate files, the declared command sends them to a `build/` folder beside the source, and still leaves the PDF next to the source, where `/apply` reads it. For LaTeX, use `-output-directory=build`. It also moves the PDF, hence the `mv`. TeX Live does not create the directory, hence the `mkdir -p`. A failed compile skips the `mv`, hence the leading `rm -f`, so a failed LaTeX compile never leaves a stale PDF where `/apply` inspects it. After a failed compile, read the log in `build/`. If Step 4 fails only because of the redirect (e.g. `\include` from a subfolder), drop the redirect but keep the leading `rm -f`, and record why under "Known pitfalls". If the toolchain writes nothing but the PDF (`typst compile`) or has no redirect option, keep the command as it is - never block registration on this rule.
4. **Fonts** - which font(s) the template uses and where they come from:
   - **Bundled font files** (`.ttf`/`.otf` shipped with the template): copy them into the template folder in Step 3 and record the relative path used to load them (LaTeX `\fontspec` `Path`, Typst `#import`/font path, or equivalent).
   - **System / distribution fonts**: record the font name and note that the user's machine must have it installed.
5. **Style rules** - anything the drafter must preserve when filling the template: color scheme, section order, heading style, spacing conventions, bullet formatting, date format.
6. **Page limit** - hard page count for the compiled PDF. Default: **2 pages** for a CV, **1 page** for a cover letter. `/apply`'s compile-and-inspect loop enforces this.
7. **Known pitfalls** (optional) - macros/rules that break with certain content (like the stock template's `\lettercontent{}`/`itemize` interaction), characters that need escaping, sections that must not be reordered.

---

## Step 3: Store the Template

Create the template folder:

- CV: `templates/cv/<name>/`
- Cover letter: `templates/cover_letters/<name>/`

Write into it:

1. **`template<source-extension>`** (e.g. `template.tex`, `template.typ`) - the template skeleton. Replace all personal data in the source with `[PLACEHOLDER]` tokens (`[YOUR_NAME]`, `[YOUR_EMAIL]`, `[YOUR_PHONE]`, `[YOUR_LINKEDIN_URL]`, ...) so the template is shareable and profile-agnostic. Keep the structure, preamble, and styling exactly as provided.
2. **Class/style/package files** - copy any companion files (`.cls`/`.sty` for LaTeX, local Typst packages, or equivalents) alongside the skeleton.
3. **`fonts/`** - copy bundled font files here, preserving any directory layout the toolchain's font-loading mechanism expects (LaTeX `\fontspec` `Path`, Typst font path, ...). Adjust those path values in the skeleton to be relative to the template folder.
4. **`TEMPLATE.md`** - the manifest. Use exactly this format:

```markdown
# Template: <name>

- **Type:** CV | Cover letter
- **Source extension:** .tex | .typ | ...
- **Engine/toolchain:** lualatex | xelatex | pdflatex | typst | <other> (display label only)
- **Page limit:** <N> page(s)
- **Fonts:** <main font> (<bundled in fonts/ | system font - must be installed>)
- **Class/packages:** <documentclass/imports and any non-standard packages, or "standard">

## Compile command

    cd <output dir> && <the full declared command, e.g. rm -f <file>.pdf && mkdir -p build && lualatex -interaction=nonstopmode -output-directory=build <file>.tex && mv build/<file>.pdf ./ or typst compile <file>.typ <file>.pdf>

## Style rules

- <rule 1: colors, section order, heading style, ...>
- <rule 2>

## Known pitfalls

- <pitfall and its fix, or "none recorded">
```

---

## Step 4: Verify the Template Compiles (MANDATORY)

Never register a template without a successful test compile. Templates that "look fine" routinely fail on missing fonts, missing classes/packages, or a wrong compile command.

1. Copy `template<source-extension>` to a scratch file in the same folder (e.g. `_compile_test.tex` or `_compile_test.typ`) and fill every `[PLACEHOLDER]` with realistic dummy data (name, contact line, one education entry, one job entry with 3 bullets — enough content to exercise the layout).
2. Compile with the declared compile command, substituting `_compile_test` for `<file>`:
   ```bash
   cd templates/<type>/<name> && <declared compile command with <file> -> _compile_test>
   ```
3. If the compile fails: show the user the relevant error lines, diagnose (missing font file, wrong engine/command, missing class or package), fix what you can (e.g. font path values), and re-compile. If the fix needs input only the user has (a missing font file, a license-restricted class), ask for it and wait.
4. On success, confirm a PDF was produced and Read it to check the layout renders sensibly (no overlapping text, fonts loaded, page count matches the declared page limit for the dummy content). Record any surprises in the manifest's "Known pitfalls".
5. Delete the scratch source file, the scratch PDF, any `build/` folder a test compile created, and any other `_compile_test.*` byproduct left in the template folder.

Do not proceed to Step 5 until the test compile passes.

---

## Step 5: Activate the Template

Activation wires the template into `/apply` by adding a **managed block** to the top of the relevant guidance file — `05-cv-templates.md` for CVs, `06-cover-letter-templates.md` for cover letters. `/apply` reads these files in both its drafting step and its compile step, so the block is all it takes.

If Step 5 was reached from Switch Mode, use the template metadata resolved from `TEMPLATE.md`. If Step 5 was reached after registering a new template, use the metadata collected and verified in Steps 2-4.

Insert (or replace, if one exists) this block immediately after the file's H1 title:

```markdown
<!-- BEGIN ACTIVE-TEMPLATE (managed by /add-template - do not edit by hand) -->
> **Active template override: `<name>`**
>
> A custom template is active. Where this block conflicts with the stock guidance below, this block wins. Structural advice below (tailoring, page-budget, cutting rules) still applies.
>
> - **Template skeleton:** `templates/<type>/<name>/template<source-extension>` — use this as the structural reference instead of the stock template
> - **Manifest:** `templates/<type>/<name>/TEMPLATE.md` — read this for style rules and known pitfalls before drafting
> - **Source extension:** `<source-extension>` (not `.tex` unless the template's own toolchain is LaTeX)
> - **Compile command:** `<the full declared command>` (not the command named in the stock guidance below — `/apply`'s compile step must use this instead). Run it from the output directory. If it redirects to `build/`, the log is in `build/`, and `/apply`'s Step 5e cleanup must delete that `build/` folder too
> - **Fonts:** <font summary, including any path note for bundled fonts>
> - **Page limit:** exactly <N> page(s)
> - **Output file:** `cv/main_<company>_<role><source-extension>` / `cover_letters/cover_<company>_<role><source-extension>`; copy any class/package/font files the template needs into the output directory, or reference them by relative path
<!-- END ACTIVE-TEMPLATE -->
```

Rules:

- Exactly **one** managed block per guidance file. Replace the whole block between the `BEGIN`/`END` markers when switching templates; never stack blocks.
- **`--use default`**: remove the managed block entirely. The stock moderncv / cover.cls guidance below it is untouched and takes over again.
- Do not modify anything outside the markers.

---

## Step 6: Confirm

Present a summary:

> **Template `<name>` registered and activated.**
>
> - Files: `templates/<type>/<name>/` (skeleton, manifest<, class/package files><, fonts>)
> - Test compile: passed with `<compile command>` (<N> page(s))
> - `/apply` will now draft <CVs | cover letters> from this template.
>
> Useful follow-ups:
> - `/add-template --list` — see all registered templates
> - `/add-template --use <other-name>` — switch templates
> - `/add-template --use default` — go back to the stock <moderncv | cover.cls> template

---

## Design Principles

- Registration is idempotent: re-running with the same name offers to update the existing template rather than duplicating it.
- Templates are stored profile-agnostic (`[PLACEHOLDER]` tokens) so they can be shared or committed without leaking personal data.
- The compile check in Step 4 is non-negotiable — a template that has never compiled will fail mid-`/apply`, which is the worst place to discover it.
- Activation is a small managed block, not a rewrite of the guidance files: `/setup` and manual edits to `05`/`06` survive template switches, and `--use default` is a clean revert.
