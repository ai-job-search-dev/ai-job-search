"""Tests for the /html-report command and its gitignore rule.

Mirrors the pattern in test_security_guards.py: one class that verifies
properties of the real repo, testing the things CI would catch if the
command file or gitignore rule were wrong.
"""

import csv
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

try:
    import yaml  # noqa: F401 - only probing availability for the lint integration test
    _HAVE_YAML = True
except ImportError:
    _HAVE_YAML = False

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMAND_FILE = REPO_ROOT / ".claude" / "commands" / "html-report.md"
LINT_SCRIPT = REPO_ROOT / "tools" / "lint_skills.py"
GITIGNORE = REPO_ROOT / ".gitignore"


class HtmlReportCommandFileTests(unittest.TestCase):
    """Structural checks on the command file itself."""

    def test_command_file_exists(self):
        self.assertTrue(COMMAND_FILE.exists(), f"{COMMAND_FILE} not found")

    def test_command_file_starts_with_correct_header(self):
        """lint_skills.py rejects command files that don't start with '# /<name>'."""
        text = COMMAND_FILE.read_text(encoding="utf-8")
        first_line = text.lstrip().splitlines()[0]
        self.assertTrue(
            first_line.startswith("# /html-report"),
            f"Command file must start with '# /html-report', got: {first_line!r}",
        )

    def test_command_file_is_non_empty(self):
        text = COMMAND_FILE.read_text(encoding="utf-8").strip()
        self.assertGreater(len(text), 100, "Command file appears suspiciously short")


class HtmlReportTrackerFieldTests(unittest.TestCase):
    """The dashboard is a consumer of every tracker column: the Step 1 field
    enumeration and the Step 3 table columns must stay in phase with the
    canonical 14-column header (apply.md /outcome.md Step 1.1), so a future
    column addition cannot silently vanish from the dashboard the way
    `deadline` did."""

    # Derived, never copied: a header literal repeated in this file drifts in
    # lockstep with the spec it polices - add a 15th column to apply.md and a
    # stale hardcoded 14-column list still passes every comparison here (a
    # 14-column string is a substring of a 15-column header). Reading the
    # canonical line back from apply.md makes the simulated drift fail with a
    # clean list diff naming the missing column instead.
    CANONICAL_HEADER = re.search(
        r"^\s*(date,company,[a-z_,]+)$",
        (REPO_ROOT / ".claude" / "commands" / "apply.md").read_text(encoding="utf-8"),
        re.M,
    ).group(1).split(",")

    def test_step1_parses_every_canonical_tracker_column(self):
        text = COMMAND_FILE.read_text(encoding="utf-8")
        match = re.search(
            r"Parse every row into a record with fields:\n\s+((?:`[^`]+`,?\s*)+)",
            text,
        )
        self.assertIsNotNone(match, "Step 1 field enumeration not found")
        fields = [f.strip() for f in re.findall(r"`([^`]+)`", match.group(1))]
        self.assertEqual(fields, self.CANONICAL_HEADER)

    def test_step3_table_columns_include_deadline_after_date(self):
        """Date · Deadline order is the whole point of the change: the dashboard
        must surface the clock that drives `/rank`'s urgency next to the date.
        A membership pair (both `Date` and `Deadline` present somewhere) cannot
        tell a swapped order from the correct one, and the order is what the
        table shows the reader."""
        text = COMMAND_FILE.read_text(encoding="utf-8")
        match = re.search(r"### Table: columns to include\n\n(.+)\n", text)
        self.assertIsNotNone(match, "Step 3 table column list not found")
        line = match.group(1)
        self.assertIn(
            "`Date` · `Deadline` · `Company`",
            line,
            "Step 3 must offer the Deadline column directly after Date - the "
            "list defines the dashboard's column order, and a swapped order "
            "reads as a different table",
        )


class HtmlReportGitignoreTests(unittest.TestCase):
    """reports/ must be gitignored — it holds personal generated output."""

    def test_funnel_is_computed_from_stage_history_not_current_status(self):
        """status is a current state, not a history: an application that
        interviewed and was then rejected carries status `rejected` and would
        never count as having reached Interview, so a finished search reads
        as though nobody ever interviewed (review finding F10, 2026-08-19).
        The stage checkboxes merged from outcome.md in Step 1.2 are the
        history; the funnel must be told to use them."""
        text = COMMAND_FILE.read_text(encoding="utf-8")
        self.assertIn(
            "stage checkboxes",
            text.split("## Step 3")[0].split("## Step 2")[1],
            "Step 2's funnel definition must derive stage-reached from the "
            "merged outcome.md stage checkboxes",
        )
        self.assertIn(
            "not current status",
            text,
            "the funnel rule must say explicitly that current status alone undercounts",
        )

    def test_rejection_rate_excludes_declined_offers_and_withdrawals(self):
        """offer_declined is the candidate turning an offer down (a success)
        and withdrawn is candidate-initiated; counting either as a rejection
        inflates the rejection rate on a self-assessment dashboard (review
        finding F11, 2026-08-19)."""
        text = COMMAND_FILE.read_text(encoding="utf-8")
        self.assertIn(
            "`offer_declined`",
            text.split("## Step 3")[0].split("## Step 2")[1],
            "the rejection-rate definition must address offer_declined",
        )
        self.assertIn(
            "not rejections",
            text,
            "the rate must exclude candidate-initiated outcomes explicitly",
        )

    def test_reports_folder_is_gitignored(self):
        rules = {line.strip() for line in GITIGNORE.read_text(encoding="utf-8").splitlines()}
        self.assertIn(
            "reports/",
            rules,
            "reports/ must be listed in .gitignore — generated dashboards are personal output",
        )


class HtmlReportScriptTests(unittest.TestCase):
    """tools/html_report.py renders a fixture tracker safely and offline."""

    SCRIPT = REPO_ROOT / "tools" / "html_report.py"
    HEADER = HtmlReportTrackerFieldTests.CANONICAL_HEADER

    def render(self, rows, archives=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with open(root / "job_search_tracker.csv", "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows([self.HEADER] + rows)
            for name, outcome in (archives or {}).items():
                d = root / "documents" / "applications" / name
                d.mkdir(parents=True)
                if outcome is not None:
                    (d / "outcome.md").write_text(outcome, encoding="utf-8")
            out = root / "out.html"
            result = subprocess.run([sys.executable, str(self.SCRIPT), "--root", str(root), "--out", str(out)],
                                    check=True, capture_output=True, text=True, cwd=REPO_ROOT)
            return out.read_text(encoding="utf-8"), result.stdout

    def row(self, company, role, status, source=""):
        values = {"date": "2026-10-01", "company": company, "role": role, "status": status,
                  "sector": "AI", "channel": "online", "source": source}
        return [values.get(h, "") for h in self.HEADER]

    PAYLOAD = "x' onmouseover='alert(1)' \" onfocus=\"alert(2)\" <img src=x onerror=alert(3)>"

    def test_fixture_tracker_pins_every_figure(self):
        """Every status, a drafted row, the legacy `no response` spelling, an unknown status and a
        13-field row. Counting withdrawn/offer_declined as rejections, drafted rows as sent, or
        dropping the legacy spelling each changes this line."""
        statuses = ["drafted", "applied", "interview", "offer", "hired", "rejected", "no_response",
                    "no response", "offer_declined", "offer declined", "withdrawn", "ghosted"]
        rows = [self.row(f"Co{i}", "Dev", st) for i, st in enumerate(statuses)]
        rows.append(self.row("Legacy", "Dev", "applied")[:13])
        rows[1][self.HEADER.index("notes")] = self.PAYLOAD
        html, summary = self.render(rows)
        self.assertEqual(
            summary.split(": ", 1)[1].strip(),
            "sent 12, drafted 1, active 2, interview 1, offer 1, hired 1, closed 7, "
            "past CV screen 42%, rejection rate 43%; unrecognised status values: ghosted")
        self.assertNotIn("onmouseover='alert", html)
        self.assertNotIn('onfocus="alert', html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("x&#x27; onmouseover=&#x27;alert(1)&#x27; &quot; onfocus=", html)

    def test_empty_cells_do_not_print_none_and_the_rate_reading_is_stated(self):
        row = self.row("Acme", "Dev", "ghosted")
        row[self.HEADER.index("sector")] = ""
        html, _ = self.render([row])
        self.assertNotIn(">None<", html)
        self.assertIn("(ghosted) are counted under Closed but left out of the rate", html)

    def test_values_are_escaped_and_page_is_offline(self):
        html, _ = self.render([
            self.row("<script>alert(1)</script>", "Engineer", "applied", "https://example.com/job"),
            self.row("Acme", "Frontend", "rejected", "javascript:alert(1)"),
            self.row("Draftco", "Dev", "drafted"),
        ])
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("href='javascript:", html)
        external = re.findall(r'(?:src|href)=["\'](https?://[^"\']+)', html)
        self.assertEqual(external, ["https://example.com/job"])
        self.assertNotIn("@font-face", html)

    def test_out_must_be_an_html_file(self):
        """settings.json pre-approves the script, so a non-HTML --out, or an out.html symlink to a
        non-HTML file, must be refused and left untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "job_search_tracker.csv").write_text(",".join(self.HEADER) + "\n", encoding="utf-8")
            notes = root / "notes.txt"
            notes.write_text("keep me", encoding="utf-8")
            link = root / "out.html"
            try:
                link.symlink_to(notes)
                targets = [notes, link]
            except OSError:  # Windows without symlink rights
                targets = [notes]
            for out in targets:
                result = subprocess.run([sys.executable, str(self.SCRIPT), "--root", tmp, "--out", str(out)],
                                        capture_output=True, text=True, cwd=REPO_ROOT)
                self.assertNotEqual(result.returncode, 0, out)
                self.assertIn("--out must end in .html", result.stderr)
                self.assertEqual(notes.read_text(encoding="utf-8"), "keep me")
            hard = root / "hard.html"
            os.link(notes, hard)  # realpath cannot see a hard link; the write must still not reach notes.txt
            subprocess.run([sys.executable, str(self.SCRIPT), "--root", tmp, "--out", str(hard)],
                           check=True, capture_output=True, cwd=REPO_ROOT)
            self.assertEqual(notes.read_text(encoding="utf-8"), "keep me")
            self.assertIn("<!doctype html>", hard.read_text(encoding="utf-8"))

    def test_out_expands_a_quoted_home_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "job_search_tracker.csv").write_text(",".join(self.HEADER) + "\n", encoding="utf-8")
            env = dict(os.environ, HOME=tmp, USERPROFILE=tmp)
            subprocess.run([sys.executable, str(self.SCRIPT), "--root", tmp, "--out", "~/report.html"],
                           check=True, capture_output=True, cwd=tmp, env=env)
            self.assertTrue((home / "report.html").is_file())
            self.assertFalse((home / "~").exists())

    def test_settings_and_guards_allow_the_script(self):
        sys.path.insert(0, str(REPO_ROOT / "tools"))
        import security_guards
        allow = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))["permissions"]["allow"]
        for entry in ("Bash(python tools/html_report.py:*)", "Bash(python3 tools/html_report.py:*)"):
            self.assertIn(entry, allow, f"{entry} missing from .claude/settings.json")
            self.assertIn(entry, security_guards.ALLOWED_PERMISSIONS,
                          f"{entry} missing from security_guards.py's reviewed allowlist")
        result = subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "security_guards.py")],
                                capture_output=True, text=True, cwd=REPO_ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_new_archive_without_outcome_is_not_matched_to_older_same_company_archive(self):
        """A second application at one company must not inherit the first one's interview."""
        _, summary = self.render(
            [self.row("Revolut", "Frontend Software Engineer", "rejected"),
             self.row("Revolut", "Software Engineer (Frontend)", "applied")],
            {"revolut_frontend_software_engineer": "- [x] Phone screen\n- [x] Technical interview\n",
             "revolut_software_engineer_frontend": None},
        )
        self.assertIn("past CV screen 50%", summary)

    def run_raw(self, tracker_bytes):
        """Run the script on a tracker written as raw bytes (None: no tracker at all)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            if tracker_bytes is not None:
                (root / "job_search_tracker.csv").write_bytes(tracker_bytes)
            return subprocess.run([sys.executable, str(self.SCRIPT), "--root", str(root),
                                   "--out", str(root / "out.html")], capture_output=True, text=True, cwd=REPO_ROOT)

    def csv_text(self, rows):
        return "\n".join([",".join(self.HEADER)] + [",".join(r) for r in rows]) + "\n"

    def assert_clean_exit(self, result, code, needle):
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.returncode, code, result.stderr)
        self.assertIn(needle, result.stdout + result.stderr)

    def test_excel_csv_utf8_with_bom_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "job_search_tracker.csv").write_bytes(
                self.csv_text([self.row("Acme", "Dev", "applied")]).encode("utf-8-sig"))
            result = subprocess.run([sys.executable, str(self.SCRIPT), "--root", tmp, "--out", str(root / "o.html")],
                                    capture_output=True, text=True, cwd=REPO_ROOT)
            self.assert_clean_exit(result, 0, "sent 1,")
            self.assertIn("data-v='2026-10-01'", (root / "o.html").read_text(encoding="utf-8"),
                          "the BOM must not swallow the date header")

    def test_row_with_fewer_fields_than_the_header_is_kept(self):
        short = self.row("Acme", "Dev", "applied")[:13]
        result = self.run_raw(self.csv_text([short, self.row("Beta", "Dev", "applied")[:5]]).encode())
        self.assert_clean_exit(result, 0, "sent 2,")

    def test_cp1252_tracker_asks_for_a_utf8_resave(self):
        result = self.run_raw(self.csv_text([self.row("Café Ünïon", "Dev", "applied")]).encode("cp1252"))
        self.assert_clean_exit(result, 1, "re-save it as CSV UTF-8")

    def test_semicolon_tracker_asks_for_a_comma_separated_resave(self):
        text = self.csv_text([self.row("Acme", "Dev", "applied")]).replace(",", ";")
        result = self.run_raw(text.encode("utf-8-sig"))
        self.assert_clean_exit(result, 1, "re-save it as comma-separated CSV UTF-8")

    def test_empty_tracker_exits_with_a_message(self):
        self.assert_clean_exit(self.run_raw(b""), 1, "re-save it as comma-separated CSV UTF-8")

    def test_capitalised_header_is_read(self):
        result = self.run_raw(b"Date,Company,Role,Status\n2026-10-01,Acme,Dev,applied\n")
        self.assert_clean_exit(result, 0, "sent 1,")

    def test_missing_tracker_exits_with_a_message(self):
        self.assert_clean_exit(self.run_raw(None), 1, "job_search_tracker.csv not found")

    def test_sent_rows_dated_in_the_future_render(self):
        row = self.row("Acme", "Dev", "applied")
        row[self.HEADER.index("date")] = "2999-01-04"
        self.assert_clean_exit(self.run_raw(self.csv_text([row]).encode()), 0, "sent 1,")

    def test_summary_names_hired_and_unrecognised_statuses(self):
        html, summary = self.render([self.row("Acme", "Dev", "hired"), self.row("Beta", "Dev", "ghosted")])
        self.assertIn("hired 1,", summary)
        self.assertIn("; unrecognised status values: ghosted", summary)
        self.assertRegex(html, r"Hired</span><span class='kpi-value num'>1<")

    def dated(self, date, status="applied", company="Acme"):
        row = self.row(company, "Dev", status)
        row[self.HEADER.index("date")] = date
        return row

    def test_a_finished_search_is_charted_by_month_up_to_its_last_application(self):
        html, _ = self.render([self.dated("2024-01-05"), self.dated("2024-03-20", "rejected", "Beta")])
        self.assertIn("Sent, per month", html)
        self.assertEqual(self.table(html, "Applications over time"),
                         {"Jan 2024": "1", "Feb 2024": "0", "Mar 2024": "1"})

    def test_a_recent_search_is_charted_by_week(self):
        html, _ = self.render([self.dated(datetime.date.today().isoformat())])
        self.assertIn("Sent, per week", html)

    def test_active_rows_without_a_usable_date_are_counted_in_the_waiting_table(self):
        html, _ = self.render([self.dated("01-10-2026"), self.dated("02-10-2026", company="Beta")])
        self.assertIn("2 active applications have no usable date", html)
        self.assertNotIn("Nothing is waiting on a reply", html)

    def test_a_year_only_date_is_charted_and_waits(self):
        html, _ = self.render([self.dated("2025"), self.dated("2026-02-11", "rejected", "Beta")])
        self.assertEqual(self.table(html, "Applications over time"), {"2025": "1", "2026": "1"})
        self.assertIn("since 2025", html)

    def test_folders_named_under_the_older_rule_still_match(self):
        """Before #329 the rule was only lowercase + underscores, so hyphens and dots stayed."""
        rows = [("Coca-Cola Europacific Partners", "Data Analyst", "rejected"),
                ("Acme Inc.", "Sr. Engineer", "rejected")]
        _, summary = self.render([self.row(*r) for r in rows],
                                 {"coca-cola_europacific_partners_data_analyst": "- [x] Phone screen\n",
                                  "acme_inc._sr._engineer": "- [x] Phone screen\n"})
        self.assertIn("past CV screen 100%", summary)

    def test_folders_sharing_a_key_merge_their_stages(self):
        """An archive from before #329 next to the folder /outcome writes now: the later-sorted
        folder must not drop the earlier one's ticks."""
        html, summary = self.render([self.row("Acme Inc.", "Dev", "rejected")],
                                    {"acme_inc._dev": "- [x] Phone screen\n- [x] Final round\n",
                                     "acme_inc_dev": "- [ ] Phone screen\n"})
        self.assertIn("past CV screen 100%", summary)
        self.assertEqual(self.table(html, "Where closed applications stopped")["Final round"], "1")

    def test_names_with_vowel_signs_match_a_folder_that_keeps_them(self):
        company, role = "टाटा", "इंजीनियर"  # Devanagari vowel signs are marks, not isalnum()
        _, summary = self.render([self.row(company, role, "rejected")],
                                 {f"{company}_{role}": "- [x] Phone screen\n"})
        self.assertIn("past CV screen 100%", summary)

    def test_names_that_differ_only_in_underscores_or_accent_encoding_match_their_own_folder(self):
        self.assertEqual(self.past_screen([("Ab", "C", "rejected"), ("A", "Bc", "rejected")],
                                          {("Ab", "C"): "- [x] Final round\n", ("A", "Bc"): None}), "50%")
        nfd = unicodedata.normalize("NFD", "café_müller_développeur")
        _, summary = self.render([self.row("Café Müller", "Développeur", "rejected")], {nfd: "- [x] Phone screen\n"})
        self.assertIn("past CV screen 100%", summary)
        # and the other way round: decomposed accents in the tracker row, precomposed folder
        nfd_row = [unicodedata.normalize("NFD", v) for v in ("Café Müller", "Développeur")]
        _, summary = self.render([self.row(*nfd_row, "rejected")],
                                 {unicodedata.normalize("NFC", "café_müller_développeur"): "- [x] Phone screen\n"})
        self.assertIn("past CV screen 100%", summary)

    def test_outcome_that_is_not_utf8_asks_for_a_resave(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "job_search_tracker.csv").write_text(self.csv_text([self.row("Acme", "Dev", "applied")]),
                                                         encoding="utf-8")
            (root / "documents" / "applications" / "acme_dev").mkdir(parents=True)
            (root / "documents" / "applications" / "acme_dev" / "outcome.md").write_bytes(b"- [x] Phone screen caf\xe9\n")
            result = subprocess.run([sys.executable, str(self.SCRIPT), "--root", tmp, "--out", str(root / "o.html")],
                                    capture_output=True, text=True, cwd=REPO_ROOT)
            self.assert_clean_exit(result, 1, "re-save it as UTF-8")

    def test_typo_years_and_slash_dates_do_not_distort_the_chart(self):
        html, _ = self.render([self.dated("2999"), self.dated("9999-12-31", company="B"), self.dated("0001-01-01", company="C"),
                               self.dated("2026/02/03", company="D"), self.dated("2026-03-04", company="E")])
        self.assertEqual(self.table(html, "Applications over time"), {"Feb 2026": "1", "Mar 2026": "1"})
        self.assertIn("3 without a usable date, left out", html)

    def test_rows_of_bare_commas_are_not_applications(self):
        _, summary = self.render([self.row("Acme", "Dev", "applied"), [""] * len(self.HEADER)])
        self.assertIn("sent 1,", summary)
        self.assertNotIn("unrecognised", summary)

    def test_only_the_stages_checklist_counts_and_uppercase_ticks_count(self):
        outcome = ("# Outcome: Acme — Dev\n\n## Interview stages reached\n- [X] Phone screen\n- [ ] Final round\n\n"
                   "## Notes\n- [x] Offer received from someone else\n")
        html, summary = self.render([self.row("Acme", "Dev", "rejected")], {"acme_dev": outcome})
        self.assertIn("past CV screen 100%", summary)
        self.assertEqual(self.table(html, "Application funnel")["Offer"], "0")

    def test_malformed_dates_are_not_charted_and_dates_sort_chronologically(self):
        html, _ = self.render([self.dated("2026-foo"), self.dated("2026-02-03foo", company="B"),
                               self.dated("2026-9-30", company="C"), self.dated("2026-10-01", company="D")])
        self.assertIn("2 without a usable date, left out", html)
        order = re.findall(r"<td class='num' data-v='([^']*)'>", html)
        self.assertEqual(order[:2], ["2026-10-01", "2026-09-30"])

    def test_uppercase_scheme_links_and_blank_sector_falls_back(self):
        row = self.row("Acme", "Dev", "applied", "HTTPS://example.com/job")
        row[self.HEADER.index("sector")] = "   "
        html, _ = self.render([row])
        self.assertIn("href='HTTPS://example.com/job'", html)
        self.assertIn("Unspecified", self.table(html, "Sent by sector"))

    @staticmethod
    def table(html, caption):
        """The screen-reader table behind a chart, as {label: value}."""
        body = re.search(rf"<caption>{re.escape(caption)}</caption>(.*?)</table>", html).group(1)
        return dict(re.findall(r"<th scope='row'>(.*?)</th><td>(.*?)</td>", body))

    def test_offer_declined_reached_offer_in_the_funnel_and_the_stopped_chart(self):
        html, _ = self.render([self.row("Acme", "Dev", "offer_declined")])
        funnel = self.table(html, "Application funnel")
        self.assertEqual((funnel["Interview"], funnel["Offer"], funnel["Hired"]), ("1", "1", "0"))
        self.assertEqual(self.table(html, "Where closed applications stopped")["Offer"], "1")

    def test_stopped_chart_agrees_with_the_funnel_on_ticked_stages(self):
        html, _ = self.render([self.row("Acme", "Dev", "rejected")],
                              {"acme_dev": "- [x] Phone screen\n- [x] Technical interview\n- [ ] Final round\n"})
        self.assertEqual(self.table(html, "Application funnel")["Interview"], "1")
        stopped = self.table(html, "Where closed applications stopped")
        self.assertEqual((stopped["Technical round"], stopped["Application screen"]), ("1", "0"))

    @staticmethod
    def folder(company, role):
        """documents/README.md Subfolder naming: lowercase, spaces to underscores, drop the rest."""
        name = re.sub(r"[^\w]", "", f"{company} {role}".lower().replace(" ", "_"))
        return re.sub(r"_+", "_", name).strip("_")

    def past_screen(self, rows, archives):
        _, summary = self.render([self.row(*r) for r in rows],
                                 {self.folder(c, r): o for (c, r), o in archives.items()})
        return re.search(r"past CV screen (\S+),", summary).group(1)

    INTERVIEWED = "- [x] Phone screen\n- [x] Technical interview\n"

    def test_shorter_role_does_not_match_a_longer_roles_folder(self):
        rows = [("Acme", "Engineering Manager", "rejected"), ("Acme", "Engineer", "applied")]
        both = {("Acme", "Engineering Manager"): self.INTERVIEWED, ("Acme", "Engineer"): None}
        self.assertEqual(self.past_screen(rows, both), "50%")
        first_only = {("Acme", "Engineering Manager"): self.INTERVIEWED}
        self.assertEqual(self.past_screen(rows, first_only), "50%")

    def test_company_prefix_does_not_match_another_company(self):
        rows = [("Metabase", "Software Engineer", "rejected"), ("Meta", "Software Engineer", "applied")]
        archives = {("Metabase", "Software Engineer"): "- [x] Phone screen\n"}
        self.assertEqual(self.past_screen(rows, archives), "50%")

    def test_non_latin_names_match_their_own_folder(self):
        names = [("Яндекс", "Инженер"), ("Сбер", "Аналитик"), ("Τράπεζα", "Μηχανικός"),
                 ("Εταιρεία", "Αναλυτής"), ("楽天", "エンジニア"), ("任天堂", "開発者")]
        archives = {n: None for n in names}
        archives[names[3]] = "- [x] Phone screen\n"
        self.assertEqual(self.past_screen([(c, r, "rejected") for c, r in names], archives), "17%")


@unittest.skipUnless(
    _HAVE_YAML,
    "PyYAML not installed (the CI Python-test job omits it; the lint job runs lint_skills.py directly)",
)
class HtmlReportLintIntegrationTests(unittest.TestCase):
    """lint_skills.py must pass after the command is added."""

    def test_lint_passes_on_real_repo(self):
        result = subprocess.run(
            [sys.executable, str(LINT_SCRIPT)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"lint_skills.py failed:\n{result.stdout}{result.stderr}",
        )
        self.assertIn("OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
