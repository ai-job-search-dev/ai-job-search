# /html-report - Generate Application Tracker Dashboard

Generate a self-contained HTML dashboard from `job_search_tracker.csv` and the application archives under `documents/applications/`. The output is a single `.html` file — no server, no dependencies — that can be opened directly in a browser.

## Step 0: Parse Arguments

- No argument → output to `reports/application-dashboard.html`
- A path argument (e.g. `/html-report ~/Desktop/report.html`) → use that path
- `--open` flag → after writing, tell the user to open the file (cannot open a browser directly)

Create `reports/` if it does not exist.

### Application records (mandatory)

> **Windows:** run these tools as `python`, not `python3`.

```bash
python3 tools/application_state.py list
```

Use the returned records and per-attempt `archive` paths throughout this command,
following **Application attempt identity** in `documents/README.md`. Keep
`application_id` when joining records. A null archive means ambiguous legacy
ownership: skip that archive and report missing history. Never attach one shared
archive to multiple attempts. This command never writes the tracker.

---

## Step 1: Collect Data

Read in parallel:

1. **`job_search_tracker.csv`** — the primary source. Parse every row into a record with fields:
   `date`, `company`, `sector`, `role`, `role_type`, `channel`, `status`, `contact_person`, `fit_rating`, `notes`, `cv_file`, `cover_letter_file`, `source`, `deadline`

   Rows written before `deadline` existed have thirteen fields and no fourteenth value. Treat the missing field as empty - never drop the row, and never infer a deadline from its `date`.

2. **`documents/applications/*/outcome.md`** — read only the `outcome.md` in each selected record's resolved `archive`. Merge interview-stage checkboxes and notes into that exact attempt. Never use company+role fuzzy matching for ID-bearing rows, and never merge an ambiguous shared legacy archive into multiple rows. Skip unresolved legacy archives and report the missing history once.


Status normalisation — map tracker values to six canonical buckets before computing stats:
- `drafted` → **Drafted** (documents written by `/apply`, not yet submitted)
- `applied` → **Active** (resume submitted, no further signal)
- `interview` → **Interview**
- `offer` → **Offer**
- `hired` → **Hired**
- `rejected` / `no_response` / `no response` / `offer_declined` / `offer declined` / `withdrawn` → **Rejected/Closed**
- anything else → **Rejected/Closed**, and name the unrecognised value once in the status breakdown — matching is case-insensitive

   The bucket map tolerates the legacy space spellings on read so nothing written before
   the canonical forms were locked drops out of the stats; the **Tracker status vocabulary**
   in `/outcome` is the authoritative set.

---

## Step 2: Compute Summary Stats

From the normalised data compute:

**Drafted rows are excluded from every statistic below** — they were never submitted. Report the Drafted count on its own, and include it only in the status breakdown.

- **Total applications**
- **By status bucket:** count per bucket
- **By sector:** count per unique sector value
- **By channel:** portal vs online vs referral vs other
- **By year/season:** group by the `date` field (which may be a year like `2025` or a full date)
- **Funnel rates:** what % progressed past resume screen (reached Interview or beyond). Compute stage-reached from history, not current status: an application counts as having reached a stage when its current status implies it **or** its merged `outcome.md` stage checkboxes (Step 1.2) show the stage was reached - a `rejected` row whose outcome file ticks an interview stage reached Interview, and a `hired` row reached every stage before Hired. Current status alone structurally undercounts every earlier stage: a finished search would read as though nobody ever interviewed.
- **Rejection rate:** true rejections (`rejected`, `no_response`) ÷ applications with a final outcome. `offer_declined` (the candidate turned the offer down - a success) and `withdrawn` (candidate-initiated) are not rejections and stay out of the numerator; Interview and Offer rows are still unresolved, so they stay out of the denominator along with Active. The Rejected/Closed status *bucket* still groups all closed rows for the doughnut - the rate just must not reuse the bucket blindly.

---

## Step 3: Generate the HTML

Write a single self-contained HTML file. All CSS is inline in a `<style>` block. All JS is inline in a `<script>` block. Draw the doughnut and bar charts as hand-generated inline SVG — no Chart.js, no CDN, no external dependencies of any kind. The report must render fully offline on every open.

**Escaping (required):** HTML-escape every CSV/outcome-file value (`&` `<` `>` `"` `'`) before interpolating it into the page — this includes table cells, `title` attributes on truncated notes, and any text placed inside SVG (`<text>` labels, chart tooltips). Notes and company names copied from job postings routinely contain these characters; unescaped, they break the layout or inject markup into a page the user opens routinely.

### Layout

```
┌─────────────────────────────────────────────┐
│  🔍 Job Search Dashboard    Generated: DATE  │
├──────┬──────┬──────┬──────┬──────┬───────────┤
│Sent  │Draft │Active│Inter-│Offer │Rejected/  │  ← stat cards
│  N   │  N   │  N   │view N│  N   │Closed   N │
├──────┴──────┴──────┴──────┴──────┴───────────┤
│  Status breakdown (doughnut) │ By sector (bar)│  ← charts row
├─────────────────────────────────────────────  ┤
│  By channel (bar)  │  Funnel (horizontal bar) │  ← charts row
├──────────────────────────────────────────────  ┤
│  Applications  [Status ▾] [Sector ▾] [🔍 ...]│  ← table with filters
│  date │ company │ sector │ role │ status │ ... │
│  ...                                          │
└───────────────────────────────────────────────┘
```

### Design spec

- **Colour palette:** CSS custom properties. Status colours:
  - Drafted: `#64748b` (slate)
  - Active: `#3b82f6` (blue)
  - Interview: `#f59e0b` (amber)
  - Offer: `#8b5cf6` (purple)
  - Hired: `#22c55e` (green)
  - Rejected/Closed: `#ef4444` (red)
- **Font:** system-ui stack, no web fonts
- **Stat cards:** white background, subtle shadow, large bold number, label below, left border in status colour
- **Charts:** contained in a 2-column grid on wide screens, stacked on narrow
- **Table:**
  - Alternating row shading
  - Status column uses a coloured pill/badge
  - `source` column renders as a hyperlink if the value is a URL (starts with `http`)
  - Empty cells render as `—`
  - Client-side filter: a text search input filters rows across company + role + sector; the status and sector dropdowns filter independently; all three combine (AND)
  - Rows are sorted newest-first by default (by `date` descending, then alphabetically by company)
- **Responsive:** usable at 900px+, not broken below that
- **Footer:** "Generated by Claude Code · ai-job-search · {ISO date}"

### Charts (inline SVG)

1. **Status doughnut** — slices for each status bucket, colours from the palette above
2. **By sector bar** (horizontal) — company count per sector, sorted descending
3. **By channel bar** — online / referral / other
4. **Application funnel** (horizontal bar) — Applied → Interview → Offer → Hired, each bar = count reaching that stage, derived per Step 2's funnel rule (current status **plus** the merged `outcome.md` stage checkboxes), so a candidate who interviewed and was later rejected still counts in the Interview bar

Build each chart as a hand-written `<svg>` element: compute bar lengths/doughnut arc angles from the stats in Step 2 and emit the `<rect>`/`<path>`/`<circle>` and `<text>` elements directly — no charting library, no `<canvas>`. Each `<svg>` has `role="img"` and an `aria-label` summarizing the chart (e.g. "Status breakdown: 3 Active, 2 Interview, 1 Offer"). Wrap each in a `<div class="chart-card">` with an `<h3>` title above. Remember to escape any label/value text drawn into `<text>` nodes per the escaping rule above.

### Table: columns to include

`Date` · `Deadline` · `Company` · `Role` · `Sector` · `Channel` · `Status` · `Notes` (truncated to 80 chars with `title` tooltip for full text) · `Source` (link or `—`)

Columns with only empty values across all rows may be omitted.

---

## Step 4: Write and Confirm

Write the complete HTML to the output path using the Write tool.

Then present:

> **Dashboard generated:** `<output path>`
>
> Open it in any browser — no server needed.
>
> **Summary:**
> - Applications sent: N · drafted, not yet sent: N
> - Active: N · Interview: N · Hired: N · Rejected/Closed: N
> - Funnel: N% progressed past resume screen
>
> Re-run `/html-report` any time after adding new entries via `/apply` or `/outcome` to refresh the dashboard.

---

## Design Principles

- **Self-contained.** One file, fully offline — charts are inline SVG, no CDN or external requests of any kind.
- **Data-only.** This command reads and renders; it never writes to the tracker or archive.
- **Idempotent.** Re-running overwrites the previous report at the same path — no accumulation.
- **Graceful on sparse data.** With only a few rows (as now), charts render correctly for small N; the table is the primary value. Do not suppress charts just because N is small.
- **No fabrication.** Every number in the report comes directly from the CSV or outcome files. Do not infer or estimate missing fields.
