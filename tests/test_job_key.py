"""Tests for tools/job_key.py - the canonical seen_jobs.json key function.

/scrape's key rule was prose only, so runs slugified inconsistently and the
state file accumulated two failures: keys carrying "/", "," and "&" that break
the archive-folder path `/apply`/`/outcome` derive from company+role, and the
same job stored twice under two different truncations of a long title. These
pin the fix - a pure, deterministic function of company+title(+url) - and the
audit that finds both failure classes in an existing file.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from job_key import _LATIN_FOLD, is_canonical, is_legacy_shape, make_key, slugify  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "job_key.py"


class Slugify(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slugify("Acme Corp"), "acme-corp")

    def test_strips_punctuation_that_breaks_paths(self):
        self.assertEqual(slugify("Ops Consulting, LLC"), "ops-consulting-llc")
        self.assertEqual(slugify("Penetration Tester / Red Teamer"), "penetration-tester-red-teamer")
        self.assertEqual(slugify("Junior Cybersecurity Analyst (OT/IoT)"), "junior-cybersecurity-analyst-ot-iot")

    def test_non_latin_script_reduces_to_empty(self):
        self.assertEqual(slugify("시큐리온"), "")
        self.assertEqual(slugify("Код Безопасности"), "")


class MakeKey(unittest.TestCase):
    def test_shape(self):
        key = make_key("Acme Corp", "SOC Analyst (L2)")
        self.assertEqual(key, "acme-corp_soc-analyst-l2")
        self.assertTrue(is_canonical(key))

    def test_deterministic_across_calls(self):
        title = "Cyber Intelligence Center Security Analyst with an unusually long title"
        self.assertEqual(make_key("Deloitte", title), make_key("Deloitte", title))

    def test_long_titles_never_collide_after_truncation(self):
        """The bug that produced two Deloitte entries for one posting: two
        runs truncated the same long title at different points. A hash of the
        full slug makes truncation deterministic instead of lossy."""
        a = make_key("Deloitte", "Cyber Intelligence Center Security Analyst with trailing text A")
        b = make_key("Deloitte", "Cyber Intelligence Center Security Analyst with trailing text B")
        self.assertNotEqual(a, b)

    def test_non_latin_title_falls_back_to_the_portal_job_id(self):
        key = make_key(
            "SecuriON",
            "안드로이드 앱(악성코드) 분석가 채용",
            url="https://kr.linkedin.com/jobs/view/x-4461771225",
        )
        self.assertEqual(key, "securion_4461771225")

    def test_non_latin_title_with_no_url_id_still_produces_a_canonical_key(self):
        key = make_key("SecuriON", "안드로이드 앱 분석가", url="")
        self.assertTrue(is_canonical(key))
        self.assertNotEqual(key, "securion_")

    def test_non_latin_company_falls_back_without_producing_a_bare_prefix(self):
        key = make_key("Код Безопасности", "Malware Analytic", url="")
        self.assertTrue(is_canonical(key))
        self.assertFalse(key.startswith("_"))

    def test_one_url_keeps_one_key_when_a_non_latin_title_is_re_listed(self):
        # freehire, the community multi-market portal, has a public API that
        # returns Cyrillic and Greek titles. Its real slugs carry no run of six
        # digits, so the numeric-id branch above never fires for them and the
        # hash is the only key half left. Hashing the title alongside the URL
        # made that hash move whenever a portal re-listed the same posting with
        # the title altered - including a mere case change, which this portal
        # really does emit ("Инженер" and "инженер" both appear).
        #
        # URL and slug are real values from freehire's public API, not
        # constructed: https://freehire.me/api/v1/agent/jobs/search?q=инженер
        url = "https://freehire.me/jobs/inzhener-ooo-chen-hlk3qjfg"
        company = "ООО Чен"
        first = make_key(company, "Инженер", url=url)
        recased = make_key(company, "инженер", url=url)
        retitled = make_key(company, "Инженер-механик", url=url)
        self.assertTrue(is_canonical(first))
        self.assertEqual(first, recased)
        self.assertEqual(first, retitled)

    def test_distinct_urls_still_get_distinct_keys(self):
        # The counterpart to the above: collapsing title variants must not
        # collapse two genuinely different postings from one company. Both
        # slugs are real freehire values.
        company = "ООО Чен"
        a = make_key(company, "Инженер", url="https://freehire.me/jobs/inzhener-ooo-chen-hlk3qjfg")
        b = make_key(company, "Инженер", url="https://freehire.me/jobs/inzhener-mup-g-khabarovska-tep")
        self.assertNotEqual(a, b)


class MixedScriptNamesKeepTheirIdentity(unittest.TestCase):
    """A name that folds to a fragment is as lossy as one that folds to nothing.

    #487 and #502 added the hash fallbacks for titles and companies that
    slugify to '', but a mixed-script name keeps its Latin or digit fragment and
    skipped them: "Программист 1С" and "Аналитик 1С" both became "1", "Сбер AI"
    and "Яндекс AI" both became "ai", so two postings shared one key and
    `/scrape` Step 4 dropped the second as already seen. "1С" titles and
    "N категории" grade suffixes are everyday Russian listings on freehire.
    """

    def test_titles_sharing_a_digit_fragment_get_distinct_keys(self):
        company = "Яндекс"
        a = make_key(company, "Программист 1С", url="https://freehire.me/jobs/programmist-1s-aaa")
        b = make_key(company, "Аналитик 1С", url="https://freehire.me/jobs/analitik-1s-bbb")
        self.assertNotEqual(a, b)
        self.assertTrue(is_canonical(a) and is_canonical(b))

    def test_titles_sharing_a_latin_word_get_distinct_keys(self):
        company = "ООО Чен"
        a = make_key(company, "Python-разработчик", url="https://freehire.me/jobs/python-razrabotchik-x1")
        b = make_key(company, "Python-аналитик", url="https://freehire.me/jobs/python-analitik-x2")
        self.assertNotEqual(a, b)
        # The fragment survives as a readable prefix; the URL carries the identity.
        self.assertTrue(a.split("_", 1)[1].startswith("python-"))

    def test_lossy_title_prefers_the_portal_numeric_id(self):
        key = make_key("Acme", "Инженер 1 категории", url="https://kr.linkedin.com/jobs/view/x-4461771225")
        self.assertEqual(key, "acme_1-4461771225")

    def test_companies_sharing_a_latin_word_get_distinct_keys(self):
        a = make_key("Сбер AI", "ML Engineer", url="https://example.com/1")
        b = make_key("Яндекс AI", "ML Engineer", url="https://example.com/2")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("ai-") and b.startswith("ai-"))
        self.assertTrue(is_canonical(a) and is_canonical(b))

    def test_lossy_company_is_stable_across_case_and_urls(self):
        a = make_key("Сбер AI", "ML Engineer", url="https://example.com/1")
        b = make_key("сбер ai", "ML Engineer", url="https://example.com/2")
        self.assertEqual(a.split("_", 1)[0], b.split("_", 1)[0])

    def test_empty_title_with_a_non_numeric_url_keeps_its_pre_fragment_key(self):
        # The fragment rule must not reach past its own case: an empty title
        # with a URL that carries no six-digit run keyed on the URL-derived
        # basis alone before #514, and the first cut of the rule had started
        # appending the digest there too. Unreachable from /scrape, which always
        # passes a title, but the key is a pure function of the posting and
        # should not move for a case the rule was never about.
        self.assertEqual(
            make_key("Acme", "", url="https://example.com/jobs/abc-xyz"),
            "acme_https-example-com-jobs-abc-xyz",
        )
        self.assertTrue(make_key("Acme", "", url="").startswith("acme_untitled-"))

    def test_latin_names_with_accents_and_ligatures_are_not_lossy(self):
        # NFKD folds these without dropping a letter, so existing keys stay put.
        self.assertEqual(make_key("Zürich Versicherung", "Ingénieur ﬁnance"), "zurich-versicherung_ingenieur-finance")

    def test_latin_letters_without_a_decomposition_are_folded(self):
        # "ø" and "æ" have no NFKD decomposition, so the ASCII fold used to drop
        # them ("Mærsk" -> "mrsk"). #537 folds them through a table instead; a
        # live seen_jobs.json is moved by `--audit --migrate`.
        self.assertEqual(make_key("Ørsted A/S", "Senior Engineer"), "orsted-a-s_senior-engineer")
        self.assertEqual(make_key("Mærsk", "Søfarende"), "maersk_sofarende")  # was mrsk_sfarende before #537

    def test_ao_and_ro_no_longer_collide(self):
        self.assertNotEqual(make_key("Ærø", "Pilot"), make_key("Rø", "Pilot"))
        self.assertEqual(slugify("Ærø"), "aero")

    def test_unchanged_letters_keep_their_keys(self):
        self.assertEqual(slugify("Århus"), "arhus")  # å stays a, not aa
        self.assertEqual(slugify("é"), "e")

    def test_every_table_character_folds_including_capitals(self):
        for ch, folded in _LATIN_FOLD.items():
            with self.subTest(ch=ch):
                self.assertEqual(slugify(f"x{ch}y"), f"x{folded.lower()}y")

    def test_latin_extended_a_letters_fold_including_capitals(self):
        cases = {
            "ı": "i",
            "ħ": "h", "Ħ": "h",
            "ĸ": "k",
            "ŋ": "ng", "Ŋ": "ng",
            "ŧ": "t", "Ŧ": "t",
        }
        for ch, folded in cases.items():
            with self.subTest(ch=ch):
                self.assertEqual(slugify(f"x{ch}y"), f"x{folded}y")

    def test_turkish_dotless_i_keeps_its_letters(self):
        self.assertEqual(slugify("Kılıç"), "kilic")

    def test_fold_applies_to_decomposed_input_too(self):
        # "Å" typed as A + combining ring: NFKD keeps the A and the mark is dropped.
        self.assertEqual(slugify("Århus"), "arhus")

    def test_letter_that_decomposes_into_a_table_letter_is_folded(self):
        # "ǿ" (U+01FF) is NFKD "ø" + acute, so the fold must run after NFKD.
        self.assertEqual(slugify("ǿ"), "o")
        self.assertEqual(slugify("Ǿ"), "o")


class CompanyFallbackCLI(unittest.TestCase):
    def key_for(self, company, url="https://example.com/jobs/123456"):
        proc = subprocess.run(
            [sys.executable, str(TOOL), "--company", company,
             "--title", "Software Engineer", "--url", url],
            capture_output=True, text=True, encoding="utf-8", check=True,
        )
        return proc.stdout.strip()

    def test_distinct_non_latin_companies_have_distinct_keys(self):
        keys = [self.key_for(company) for company in ("腾讯", "阿里巴巴", "")]
        self.assertEqual(len(set(keys)), 3)
        self.assertTrue(all(is_canonical(key) for key in keys))

    def test_company_fallback_is_stable_across_case_normalization_and_urls(self):
        for first, second in (("КОМПАНИЯ", "компания"), ("ガンホー", "カ\u3099ンホー")):
            with self.subTest(first=first, second=second):
                self.assertEqual(
                    self.key_for(first),
                    self.key_for(second, "https://example.com/jobs/654321"),
                )

    def test_missing_company_and_existing_ascii_keys_are_unchanged(self):
        self.assertEqual(self.key_for(""), "unknown-company_software-engineer")
        self.assertEqual(self.key_for("  "), "unknown-company_software-engineer")
        self.assertEqual(self.key_for("Acme Corp"), "acme-corp_software-engineer")


class CanonicalAndLegacyShape(unittest.TestCase):
    def test_canonical_accepts_company_underscore_title(self):
        self.assertTrue(is_canonical("acme-corp_soc-analyst"))

    def test_canonical_rejects_path_breaking_characters(self):
        for bad in ("deloitte_junior-cybersecurity-analyst-(ot/iot)",
                    "neverhack-estonia_penetration-tester-/-red-teamer",
                    "ops-consulting,-llc_malware-analyst",
                    "",
                    "securion_"):
            self.assertFalse(is_canonical(bad), f"{bad!r} should not be canonical")

    def test_legacy_three_part_shape_is_flagged_separately_from_malformed(self):
        self.assertTrue(is_legacy_shape("nviso-security_soc-analyst_athens"))
        self.assertFalse(is_canonical("nviso-security_soc-analyst_athens"))
        # A malformed key (bad characters) is never also reported as legacy shape.
        self.assertFalse(is_legacy_shape("deloitte_junior-cybersecurity-analyst-(ot/iot)"))


class AuditCLI(unittest.TestCase):
    def run_audit(self, seen: dict) -> tuple[dict, int]:
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"seen": seen}, fh)
            path = fh.name
        proc = subprocess.run(
            [sys.executable, str(TOOL), "--audit", path], capture_output=True, text=True, encoding="utf-8"
        )
        return json.loads(proc.stdout), proc.returncode

    def test_clean_state_exits_zero(self):
        report, code = self.run_audit({"acme_soc-analyst": {"company": "Acme", "title": "SOC Analyst"}})
        self.assertEqual(code, 0)
        self.assertEqual(report["malformed_keys"], [])
        self.assertEqual(report["duplicate_urls"], {})

    def test_malformed_key_exits_nonzero(self):
        report, code = self.run_audit(
            {"deloitte_junior-cybersecurity-analyst-(ot/iot)": {"company": "Deloitte", "title": "x"}}
        )
        self.assertEqual(code, 1)
        self.assertIn("deloitte_junior-cybersecurity-analyst-(ot/iot)", report["malformed_keys"])

    def test_duplicate_url_exits_nonzero(self):
        report, code = self.run_audit(
            {
                "a": {"company": "Acme", "title": "x", "url": "https://x/1"},
                "b": {"company": "Acme", "title": "y", "url": "https://x/1"},
            }
        )
        self.assertEqual(code, 1)
        self.assertIn("https://x/1", report["duplicate_urls"])

    def test_legacy_shape_alone_does_not_fail_the_audit(self):
        """Harmless drift, not damage - the sweep-worthy rewrite is a decision
        the maintainer makes, not something the audit enforces."""
        report, code = self.run_audit({"acme_soc-analyst_athens": {"company": "Acme", "title": "x"}})
        self.assertEqual(code, 0)
        self.assertIn("acme_soc-analyst_athens", report["legacy_three_part_keys"])

    def run_audit_raw(self, document) -> subprocess.CompletedProcess:
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(document, fh)
            path = fh.name
        return subprocess.run(
            [sys.executable, str(TOOL), "--audit", path], capture_output=True, text=True, encoding="utf-8"
        )

    def test_non_object_state_file_is_reported_not_a_traceback(self):
        proc = self.run_audit_raw([])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("expected an object of job entries", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_non_object_entry_is_reported_not_a_traceback(self):
        proc = self.run_audit_raw({"seen": {"acme_soc-analyst": "oops"}})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("entry is not an object: acme_soc-analyst", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_non_string_url_is_reported_not_a_traceback(self):
        for bad in (123, ["https://example.com/job"], {"href": "x"}):
            with self.subTest(url=bad):
                proc = self.run_audit_raw({"seen": {"acme_soc-analyst": {"company": "Acme", "title": "SOC Analyst", "url": bad}}})
                self.assertEqual(proc.returncode, 1)
                self.assertIn("entry url is not a string: acme_soc-analyst", proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)


class MigrateCLI(unittest.TestCase):
    """`--audit --migrate` rewrites drifted keys in place, safely."""

    OLD = "mrsk_sfarende"
    NEW = "maersk_sofarende"

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "seen_jobs.json"

    def write(self, seen):
        self.path.write_text(json.dumps({"seen": seen}, ensure_ascii=False), encoding="utf-8")

    def migrate(self):
        proc = subprocess.run(
            [sys.executable, str(TOOL), "--audit", str(self.path), "--migrate"],
            capture_output=True, text=True, encoding="utf-8",
        )
        return json.loads(proc.stdout), proc

    def seen(self):
        return json.loads(self.path.read_text(encoding="utf-8"))["seen"]

    def backups(self):
        return list(self.path.parent.glob("seen_jobs.json.bak-*"))

    def test_rewrites_the_key_and_keeps_the_entry(self):
        entry = {"company": "Mærsk", "title": "Søfarende", "status": "ranked"}
        self.write({self.OLD: entry})
        report, proc = self.migrate()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.seen(), {self.NEW: entry})
        self.assertEqual(report["migrated"], [{"old": self.OLD, "new": self.NEW}])
        self.assertEqual(report["keys_not_matching_current_rule"], 0)

    def test_backup_holds_the_original_bytes(self):
        self.write({self.OLD: {"company": "Mærsk", "title": "Søfarende"}})
        original = self.path.read_bytes()
        report, _ = self.migrate()
        backups = self.backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(report["backup"], str(backups[0]))

    def test_existing_new_key_is_reported_not_overwritten(self):
        existing = {"company": "Mærsk", "title": "Søfarende", "status": "applied"}
        stale = {"company": "Mærsk", "title": "Søfarende", "status": "new"}
        self.write({self.NEW: existing, self.OLD: stale})
        report, _ = self.migrate()
        self.assertEqual(self.seen(), {self.NEW: existing, self.OLD: stale})
        self.assertEqual(report["migrate_collisions"], [{"old": self.OLD, "new": self.NEW}])
        self.assertEqual(report["migrated"], [])
        self.assertEqual(self.backups(), [])

    def test_chain_moves_in_one_run_and_the_second_run_is_byte_identical(self):
        # A is keyed "wrong", and today's key for A is x. B sits on x and today
        # belongs on y. Moving B first frees x for A.
        a = {"company": "Mærsk", "title": "Søfarende", "status": "new"}
        b = {"company": "Ørsted", "title": "Engineer", "status": "applied"}
        x = self.NEW
        y = make_key("Ørsted", "Engineer")
        self.write({"stale-a_x": a, x: b})  # file order: A first, so B must not block it
        self.assertEqual(make_key("Mærsk", "Søfarende"), x)
        report, _ = self.migrate()
        self.assertEqual(self.seen(), {x: a, y: b})
        self.assertEqual({m["old"]: m["new"] for m in report["migrated"]}, {"stale-a_x": x, x: y})
        self.assertEqual(report["migrate_collisions"], [])
        after = self.path.read_bytes()
        again, _ = self.migrate()
        self.assertEqual(again["migrated"], [])
        self.assertEqual(self.path.read_bytes(), after)
        self.assertEqual(len(self.backups()), 1)

    def test_migrating_one_key_changes_only_the_lines_that_carry_it(self):
        # Written the way tools/rank_state.save_state writes it.
        seen = {
            self.OLD: {"company": "Mærsk", "title": "Søfarende", "tags": ["a", "b"]},
            "acme_soc": {"company": "Acme", "title": "SOC", "url": "https://x/1"},
        }
        text = json.dumps({"seen": seen}, indent=2, ensure_ascii=False) + chr(10)
        self.path.write_text(text, encoding="utf-8")
        self.migrate()
        before, after = text.splitlines(), self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(before), len(after))
        changed = [(o, n) for o, n in zip(before, after) if o != n]
        self.assertEqual(len(changed), 1)
        self.assertIn(self.OLD, changed[0][0])
        self.assertIn(self.NEW, changed[0][1])

    def test_second_run_changes_nothing(self):
        self.write({self.OLD: {"company": "Mærsk", "title": "Søfarende"}, "acme_soc": {"company": "Acme", "title": "SOC"}})
        self.migrate()
        after_first = self.path.read_bytes()
        report, _ = self.migrate()
        self.assertEqual(report["migrated"], [])
        self.assertEqual(self.path.read_bytes(), after_first)
        self.assertEqual(len(self.backups()), 1)

    def test_no_drift_writes_no_backup(self):
        self.write({"acme_soc": {"company": "Acme", "title": "SOC"}})
        before = self.path.read_bytes()
        report, _ = self.migrate()
        self.assertEqual(report["migrated"], [])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.backups(), [])

    def test_audit_alone_never_rewrites(self):
        self.write({self.OLD: {"company": "Mærsk", "title": "Søfarende"}})
        before = self.path.read_bytes()
        proc = subprocess.run(
            [sys.executable, str(TOOL), "--audit", str(self.path)], capture_output=True, text=True, encoding="utf-8"
        )
        self.assertEqual(json.loads(proc.stdout)["keys_not_matching_current_rule"], 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_migrate_without_audit_is_a_usage_error(self):
        proc = subprocess.run([sys.executable, str(TOOL), "--migrate"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
