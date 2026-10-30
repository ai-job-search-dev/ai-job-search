# /html-report - Generate Application Tracker Dashboard

Generate a self-contained HTML dashboard from `job_search_tracker.csv` and the application archives under `documents/applications/`. The output is a single `.html` file that opens directly in a browser: system fonts, light and dark themes, CSS and SVG charts, no network requests.

The work is done by two scripts: `tools/report_data.py` computes every figure and `tools/html_report.py` renders it. Steps 1 and 2 document the rules `report_data.py` implements; do not recompute them in the conversation or edit the generated HTML by hand.

## Step 0: Parse Arguments

- No argument → output to `reports/application-dashboard.html`
- A path argument (e.g. `/html-report ~/Desktop/report.html`) → pass it as `--out`
- `--open` flag → after writing, tell the user to open the file (cannot open a browser directly)

---

## Step 1: Collect Data

The script reads:

1. **`job_search_tracker.csv`** — the primary source. Parse every row into a record with fields:
   `date`, `company`, `sector`, `role`, `role_type`, `channel`, `status`, `contact_person`, `fit_rating`, `notes`, `cv_file`, `cover_letter_file`, `source`, `deadline`

   Rows written before `deadline` existed have thirteen fields and no fourteenth value. Treat the missing field as empty - never drop the row, and never infer a deadline from its `date`.

2. **`documents/applications/*/`** — every archive folder, with the stage checkboxes from its `outcome.md` when one exists. Each tracker row is matched to an archive by company+role fuzzy match (lowercase, ignore punctuation). A folder without `outcome.md` still counts, so a new application matches its own archive instead of an older one at the same company.

Status normalisation — tracker values map to six canonical buckets before stats are computed:
- `drafted` → **Drafted** (documents written by `/apply`, not yet submitted)
- `applied` → **Active** (resume submitted, no further signal)
- `interview` → **Interview**
- `offer` → **Offer**
- `hired` → **Hired**
- `rejected` / `no_response` / `no response` / `offer_declined` / `offer declined` / `withdrawn` → **Rejected/Closed**
- anything else → **Rejected/Closed**, and the unrecognised value is named once on the page — matching is case-insensitive

   The bucket map tolerates the legacy space spellings on read so nothing written before
   the canonical forms were locked drops out of the stats; the **Tracker status vocabulary**
   in `/outcome` is the authoritative set.

---

## Step 2: Compute Summary Stats

**Drafted rows are excluded from every statistic below** — they were never submitted. The Drafted count is reported on its own and appears only in the status breakdown.

- **Totals by status bucket, sector, channel and week**
- **Funnel rates:** what % progressed past resume screen (reached Interview or beyond). Stage-reached comes from history, not current status: an application counts as having reached a stage when its current status implies it **or** its matched `outcome.md` stage checkboxes show the stage was reached. Current status alone structurally undercounts every earlier stage.
- **Rejection rate:** true rejections (`rejected`, `no_response`) ÷ applications with a final outcome. `offer_declined` (the candidate turned the offer down - a success) and `withdrawn` (candidate-initiated) are not rejections and stay out of the numerator; Interview and Offer rows are unresolved, so they stay out of the denominator along with Active.

---

## Step 3: Generate the HTML

> **Windows:** run these tools as `python`, not `python3`.

```bash
python3 tools/html_report.py [--out <path>]
```

The page contains a KPI strip, the funnel, a status mix bar, applications per week, where closed applications stopped, sector and channel bars, a "waiting longest for a reply" table, and a sortable, filterable applications table. Every tracker value is HTML-escaped; source links render only for `http(s)` URLs.

### Table: columns to include

`Date` · `Deadline` · `Company` · `Role` · `Sector` · `Channel` · `Status` · `Fit` · `Notes` (truncated to 90 chars with `title` tooltip for full text) · `Source` (link or None)

The Deadline column is omitted while every row's deadline is empty.

---

## Step 4: Confirm

Relay the script's one-line summary and the output path, say the file opens directly in a browser, and name any unrecognised status values. Suggest re-running `/html-report` after `/apply` or `/outcome` adds entries.

---

## Design Principles

- **Self-contained.** One file, fully offline; it uses the system UI and monospace fonts, so nothing is embedded or fetched.
- **Data-only.** Reads and renders; never writes to the tracker or archive.
- **Idempotent.** Re-running overwrites the previous report at the same path.
- **No fabrication.** Every number comes from the CSV or outcome files.
