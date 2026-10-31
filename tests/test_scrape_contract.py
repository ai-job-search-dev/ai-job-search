"""Tests for the /scrape Step 2 search-output contract across portal CLIs.

Mirrors the pattern of test_html_report_command.py: derive the contract from
the spec itself and compare it against the real portal CLIs, so a drift on
either side fails with a clean diff.

Why this test exists: .claude/skills/job-scraper/SKILL.md Step 2 promises
"Search output already includes title, company, location, date, and URL" for
every portal CLI, and Step 4.75's degraded scan flags "company null or empty
on every result" as a half-working parser. A CLI that quietly stops emitting
those fields flags the portal as degraded on every /scrape run while CI stays
green, breaks the seen_jobs.json dedupe (url_or_company_title_key), and leaves
/rank without a posting URL. That failure class landed for real: jobnet-search
emitted only the raw API schema and jobdanmark-search emitted companyName with
no company/location/date keys until both were normalized.

{helpers.ts, commands/search.ts} are the two files where every registered
CLI's search output currently lives (HTML-parsing portals normalize in
helpers.ts, API portals in commands/search.ts). detail.ts is deliberately
excluded: the contract is about the search output /scrape consumes.
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRAPER_SKILL = REPO_ROOT / ".claude" / "skills" / "job-scraper" / "SKILL.md"
PORTAL_CLIS = sorted((REPO_ROOT / ".agents" / "skills").glob("*-search"))

# Derived, never copied: a hardcoded field list drifts in lockstep with
# nothing - if Step 2's prose drops or adds a field, the known-good portals
# and this pin would keep agreeing forever while the contract changed.
_CONTRACT_SENTENCE = re.compile(r"Search output already includes ([a-zA-Z0-9\s,]+)\.", re.MULTILINE)


def derive_contract_fields() -> frozenset[str]:
    text = SCRAPER_SKILL.read_text(encoding="utf-8")
    match = _CONTRACT_SENTENCE.search(text)
    if match is None:
        raise AssertionError("Step 2 contract sentence not found in job-scraper/SKILL.md")
    fields_text = re.sub(r"\s+and\s+", ",", match.group(1))
    fields = {f.strip().lower() for f in fields_text.split(",") if f.strip()}
    return frozenset(fields)


def search_output_source(search_ts: Path) -> str:
    helpers_ts = search_ts.parent.parent / "helpers.ts"
    files = [search_ts, helpers_ts] if helpers_ts.exists() else [search_ts]
    return "\n".join(f.read_text(encoding="utf-8") for f in files)


class ScrapeSearchOutputContractTests(unittest.TestCase):
    """Every portal CLI's search output must carry the Step 2 contract fields."""

    def test_step2_contract_sentence_is_found_in_the_scraper_skill(self):
        """Guards the anchor the field list is derived from."""
        fields = derive_contract_fields()
        self.assertGreaterEqual(fields, {"title", "company", "location", "date", "url"})

    def test_every_portal_cli_emits_the_step2_contract_fields(self):
        contract = derive_contract_fields()
        failures: list[str] = []
        for portal in PORTAL_CLIS:
            search_ts = portal / "cli" / "src" / "commands" / "search.ts"
            if not search_ts.exists():
                failures.append(f"{portal.name}: no cli/src/commands/search.ts")
                continue
            source = search_output_source(search_ts)
            emitted = set(re.findall(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*):", source, re.MULTILINE))
            missing = sorted(contract - emitted)
            if missing:
                failures.append(f"{portal.name}: missing {missing} in search output")
        self.assertEqual([], failures, "; ".join(failures) or "no portal CLIs checked")



# Step 4's storage schema, derived the same way as the Step 2 contract above:
# the field list lives in the spec, never duplicated here, so a schema change
# fails this test instead of silently agreeing with a stale copy.
_STEP4_SCHEMA_BLOCK = re.compile(r"Add ALL fetched jobs.*?```json(.*?)```", re.DOTALL)


def derive_stored_fields() -> frozenset[str]:
    text = SCRAPER_SKILL.read_text(encoding="utf-8")
    match = _STEP4_SCHEMA_BLOCK.search(text)
    if match is None:
        raise AssertionError("Step 4 seen_jobs.json schema block not found in job-scraper/SKILL.md")
    return frozenset(re.findall(r'"([a-z_]+)":', match.group(1)))


class SeenJobsPostingDateTests(unittest.TestCase):
    """The posting date Step 2 guarantees must survive into Step 4's storage.

    Step 2's contract promises a `date` on every portal CLI's search output and
    the test above keeps every CLI honest about emitting it. Step 1b then uses
    that date to scope the run to the last 14 days - and Step 4's schema drops
    it. `first_seen` records when this scraper first saw an entry, not when the
    employer posted it, so once the run ends nothing can tell a posting
    published yesterday from one published two years ago: the Step 1b window is
    unauditable and /rank has no freshness signal to weigh.

    That failure landed for real: a freehire-search posting dated 2024-05-13 was
    scraped and ranked Strong Fit at position 1 of 133, its own scoring note
    observing the listing "may be long stale" with nothing able to act on it.
    """

    def test_step4_schema_persists_a_posting_date(self):
        stored = derive_stored_fields()
        self.assertIn(
            "posted_date",
            stored,
            "Step 4's seen_jobs.json schema stores no posting-date field, so a "
            "posting's age is unrecoverable after the run that scraped it",
        )

    def test_the_step2_date_field_survives_into_storage(self):
        contract = derive_contract_fields()
        self.assertIn("date", contract, "Step 2 no longer guarantees a posting date")
        stored = derive_stored_fields()
        self.assertIn(
            "posted_date",
            stored,
            "Step 2 guarantees a posting `date` and CI enforces every CLI emits it, "
            "but Step 4 discards it at write time",
        )

    def test_posted_date_semantics_are_documented(self):
        """A stored field the spec never explains gets backfilled by guessing."""
        text = SCRAPER_SKILL.read_text(encoding="utf-8")
        self.assertIn("`posted_date`", text, "posted_date is in the schema but never documented")
        self.assertRegex(
            text,
            r"never infer a posting date",
            "posted_date must carry the same never-backfill rule as `deadline`",
        )


class SeenJobsDedupContinuityTests(unittest.TestCase):
    """The new key rule must not replay jobs stored under legacy keys."""

    def test_existing_urls_are_seen_regardless_of_key(self):
        text = SCRAPER_SKILL.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"URL matches any existing `(?:job_scraper/)?seen_jobs\.json` entry, regardless of\s+that entry's key",
            "legacy seen_jobs entries must be matched by URL during the key-rule transition",
        )

    def test_step4_presentation_mentions_url_deduplication(self):
        text = SCRAPER_SKILL.read_text(encoding="utf-8")
        self.assertRegex(text, r"matched by URL or\s+company\+title")


# Step 1b sends one plain term per CLI call and takes quoting from the portal's
# own SKILL.md. The rule is data each portal carries, so the test derives the
# portal list from the skills tree and names no portal.
_QUERY_SYNTAX_LINE = re.compile(r"^Query syntax:\s*\S", re.MULTILINE)


class PortalQuerySyntaxTests(unittest.TestCase):
    """Every installed portal SKILL.md must state its query syntax."""

    def test_step1b_points_at_the_query_syntax_line(self):
        text = SCRAPER_SKILL.read_text(encoding="utf-8")
        self.assertIn("`Query syntax:`", text, "Step 1b no longer defers quoting to the portal's Query syntax line")

    def test_every_installed_portal_skill_has_a_query_syntax_line(self):
        self.assertTrue(PORTAL_CLIS, "no installed portal skills found")
        missing = [
            portal.name
            for portal in PORTAL_CLIS
            if not _QUERY_SYNTAX_LINE.search((portal / "SKILL.md").read_text(encoding="utf-8"))
        ]
        self.assertEqual(
            [],
            missing,
            "portal SKILL.md files without a `Query syntax:` line: " + ", ".join(missing),
        )


if __name__ == "__main__":
    unittest.main()
