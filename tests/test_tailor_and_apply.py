#!/usr/bin/env python3
"""Tests for Sprint 4 (tailor) and Sprint 5 (HITL browser apply) plus review fixes."""
import os
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from tools import browser_apply, tailor
from tools.db import (
    STATUS_APPLIED,
    STATUS_IGNORED,
    STATUS_READY_TO_APPLY,
    STATUS_SHORTLISTED,
    STATUS_TAILORED,
    check_company_cooldown,
    confirm_application,
    get_db,
    get_ready_application,
    get_shortlisted_for_tailoring,
    init_db,
    record_evaluation,
    record_tailored_documents,
    upsert_job,
)
from tools.profile_schema import (
    CandidateIdentity,
    CareerPreferences,
    EducationEntry,
    MasterProfile,
    ProjectEntry,
    SkillItem,
    WorkExperienceEntry,
)
from tools.ranker import normalize_evaluation
from tools.ranker_schema import QuantumJobEvaluation

HAS_TYPST = shutil.which("typst") is not None


def make_profile(**identity_overrides) -> MasterProfile:
    identity = dict(
        name="Ada Example", title="Software Engineer", email="ada@example.com",
        phone="+1 555 0100", location="Remote", linkedin="https://linkedin.com/in/ada",
        github="https://github.com/ada",
    )
    identity.update(identity_overrides)
    return MasterProfile(
        identity=CandidateIdentity(**identity),
        summary="Engineer building quantum software with Python and Qiskit.",
        education=[EducationEntry(degree="BSc Computer Science", institution="Uni", period="2018 - 2022",
                                  key_topics=["Algorithms"])],
        experience=[WorkExperienceEntry(
            company="Acme", role="Engineer", location="Remote", start_date="2022-01", is_current=True,
            achievements=["Built REST APIs with FastAPI", "Wrote Qiskit circuit simulators in Python, cutting runtime 20%"],
            skills_used=["Python", "FastAPI", "Qiskit"],
        )],
        projects=[
            ProjectEntry(name="Web Thing", description="A CRUD site", technologies=["Django"]),
            ProjectEntry(name="Quantum Bench", description="VQE benchmarks with Qiskit",
                         technologies=["Python", "Qiskit"], impact_highlights=["Compared noise models"]),
        ],
        skills=[SkillItem(name="Python", category="Backend & APIs"),
                SkillItem(name="Qiskit", category="Quantum Computing"),
                SkillItem(name="FastAPI", category="Backend & APIs")],
        preferences=CareerPreferences(target_roles=["Quantum Software Engineer"]),
    )


class TailorContentTests(unittest.TestCase):
    def test_unverified_skills_are_never_claimed(self):
        cv = tailor.build_cv(make_profile(), ["Qiskit", "Rust", "python"])
        self.assertEqual(cv.matched, ["Qiskit", "Python"])
        self.assertEqual(cv.unverified, ["Rust"])
        rendered = tailor.render_cv(cv)
        self.assertNotIn("Rust", rendered)

    def test_content_is_reordered_around_matched_skills(self):
        cv = tailor.build_cv(make_profile(), ["Qiskit"])
        self.assertEqual(cv.skill_groups[0][0], "Quantum Computing")
        self.assertEqual(cv.projects[0]["name"], "Quantum Bench")
        self.assertIn("Qiskit", cv.experience[0]["bullets"][0])

    def test_typst_string_escaping(self):
        self.assertEqual(tailor.typst_str('say "hi" \\ #x $y'), '"say \\"hi\\" \\\\ #x $y"')
        self.assertEqual(tailor.typst_str("a\n\n b"), '"a b"')

    def test_slugify_is_path_safe(self):
        self.assertEqual(tailor.slugify("../../Evil Co/ Ltd."), "evil-co-ltd")
        self.assertEqual(tailor.slugify("***"), "x")

    def test_cover_letter_omits_skills_already_in_profile_from_gaps(self):
        profile = make_profile()
        cv = tailor.build_cv(profile, ["Python"])
        text = " ".join(tailor.build_cover_letter(profile, "Acme", "QSE", cv.matched, ["Python", "C++"], cv))
        self.assertIn("C++", text)
        self.assertNotIn("deepen my experience with Python", text)

    def test_parse_json_list(self):
        self.assertEqual(tailor.parse_json_list('["a", "b"]'), ["a", "b"])
        self.assertEqual(tailor.parse_json_list(None), [])
        self.assertEqual(tailor.parse_json_list("not json"), [])


class DbLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        init_db(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def _shortlisted(self, conn, score=8.5, veto=False, title="Quantum SWE"):
        job_id, _ = upsert_job(conn, title, "Acme Quantum", f"https://x.io/{title}", "greenhouse:acme")
        record_evaluation(conn, job_id, score, veto_triggered=veto, matched_skills=["Python"])
        return job_id

    def test_selection_excludes_low_score_and_vetoed(self):
        with get_db(self.db) as conn:
            good = self._shortlisted(conn, 8.5, title="Good")
            self._shortlisted(conn, 6.0, title="Low")
            self._shortlisted(conn, 9.0, veto=True, title="Vetoed")
            rows = get_shortlisted_for_tailoring(conn)
        self.assertEqual([r["id"] for r in rows], [good])

    def test_full_lifecycle_to_applied_with_cooldown(self):
        with get_db(self.db) as conn:
            job_id = self._shortlisted(conn)
            record_tailored_documents(conn, job_id, "cv.pdf", "cl.pdf", ats_verified=True, layout_verified=False)
            self.assertEqual(conn.execute("SELECT status FROM jobs").fetchone()[0], STATUS_TAILORED)
            record_tailored_documents(conn, job_id, "cv.pdf", "cl.pdf", ats_verified=True, layout_verified=True)
            self.assertEqual(conn.execute("SELECT status FROM jobs").fetchone()[0], STATUS_READY_TO_APPLY)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0], 1)
            self.assertFalse(check_company_cooldown(conn, "Acme Quantum")[0])

            confirm_application(conn, job_id)
            self.assertEqual(conn.execute("SELECT status FROM jobs").fetchone()[0], STATUS_APPLIED)
            self.assertTrue(check_company_cooldown(conn, "Acme Quantum")[0])
            row = conn.execute("SELECT stage, applied_at FROM applications").fetchone()
            self.assertEqual(row["stage"], "SUBMITTED")
            self.assertIsNotNone(row["applied_at"])

    def test_unverified_documents_are_never_offered_for_apply(self):
        with get_db(self.db) as conn:
            job_id = self._shortlisted(conn)
            record_tailored_documents(conn, job_id, "cv.pdf", "cl.pdf", ats_verified=True, layout_verified=False)
            self.assertEqual(conn.execute("SELECT stage FROM applications").fetchone()[0], "TAILORED")
            self.assertIsNone(get_ready_application(conn, job_id))
            with self.assertRaises(ValueError):
                confirm_application(conn, job_id)
            record_tailored_documents(conn, job_id, "cv.pdf", "cl.pdf", ats_verified=True, layout_verified=True)
            self.assertEqual(conn.execute("SELECT COUNT(*), MAX(stage) FROM applications").fetchone()[:], (1, "READY_TO_APPLY"))
            self.assertIsNotNone(get_ready_application(conn, job_id))

    def test_legacy_unverified_row_marked_ready_is_not_offered(self):
        with get_db(self.db) as conn:
            job_id = self._shortlisted(conn)
            record_tailored_documents(conn, job_id, "cv.pdf", "cl.pdf", ats_verified=True, layout_verified=True)
            conn.execute("UPDATE applications SET layout_verified = 0")
            self.assertIsNone(get_ready_application(conn, job_id))

    def test_company_names_in_non_latin_scripts_stay_distinct(self):
        from tools.db import normalize_company_name
        self.assertNotEqual(normalize_company_name("量子科技"), normalize_company_name("光子研究"))
        self.assertNotEqual(normalize_company_name("Квант"), normalize_company_name("Фотон"))
        self.assertEqual(normalize_company_name("Café Quantum Inc."), "cafe")
        self.assertEqual(normalize_company_name("Acme, Inc."), "acme")

    def test_confirm_requires_ready_application(self):
        with get_db(self.db) as conn:
            job_id = self._shortlisted(conn)
            with self.assertRaises(ValueError):
                confirm_application(conn, job_id)
            self.assertIsNone(get_ready_application(conn, job_id))

    def test_re_evaluating_applied_job_does_not_change_status(self):
        from tools import ranker

        with get_db(self.db) as conn:
            job_id = self._shortlisted(conn)
            record_tailored_documents(conn, job_id, "cv.pdf", None, True, True)
            confirm_application(conn, job_id)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            ranker.run_ranker(specific_job_id=job_id, db_path=self.db)
        with get_db(self.db) as conn:
            self.assertEqual(conn.execute("SELECT status FROM jobs").fetchone()[0], STATUS_APPLIED)


@unittest.skipUnless(HAS_TYPST, "typst not installed")
class TailorEndToEndTests(unittest.TestCase):
    def test_tailor_job_produces_verified_pdfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            db = tmp / "t.db"
            init_db(db)
            profile = make_profile()
            with get_db(db) as conn:
                job_id, _ = upsert_job(conn, "Quantum SWE", "Acme Quantum", "https://x.io/1", "ashby:acme")
                record_evaluation(conn, job_id, 8.8, matched_skills=["Qiskit", "Python", "Rust"],
                                  missing_keywords=["C++"])
                job = get_shortlisted_for_tailoring(conn)[0]
                result = tailor.tailor_job(conn, job, profile, out_root=tmp / "apps")
                self.assertEqual(result["status"], STATUS_READY_TO_APPLY, result["messages"])
                app = get_ready_application(conn, job_id)
                self.assertTrue(Path(app["cv_path"]).is_file())
                self.assertTrue(Path(app["cover_letter_path"]).is_file())
                self.assertEqual((app["ats_verified"], app["layout_verified"]), (1, 1))

    def test_hostile_text_cannot_break_typst_source(self):
        profile = make_profile(name='Ada "Evil" #[x]')
        cv = tailor.build_cv(profile, ["Python"])
        with tempfile.TemporaryDirectory() as tmp:
            src, pdf = Path(tmp) / "cv.typ", Path(tmp) / "cv.pdf"
            shutil.copy(tailor.TEMPLATES_DIR / "cv_lib.typ", Path(tmp) / "cv_lib.typ")
            src.write_text(tailor.render_cv(cv), encoding="utf-8")
            tailor.compile_typst(src, pdf)
            self.assertTrue(pdf.is_file())


class RankerNormalizationTests(unittest.TestCase):
    def _ev(self, **kw):
        base = dict(
            overall_score=8.0, software_fit_score=8.0, quantum_fit_score=8.0, is_software_role=True,
            is_experimental_hardware=False, phd_requirement_analysis="not_required", should_veto=False,
            veto_reason=None, reasoning="x",
        )
        base.update(kw)
        return QuantumJobEvaluation(**base)

    def test_hardware_role_is_always_vetoed(self):
        ev = normalize_evaluation(self._ev(is_experimental_hardware=True))
        self.assertTrue(ev.should_veto)
        self.assertTrue(ev.veto_reason)

    def test_phd_boilerplate_veto_on_software_role_is_lifted(self):
        ev = normalize_evaluation(self._ev(
            should_veto=True, veto_reason="Requires PhD", phd_requirement_analysis="generic_hr_boilerplate"))
        self.assertFalse(ev.should_veto)
        self.assertIsNone(ev.veto_reason)

    def test_non_phd_veto_is_kept(self):
        ev = normalize_evaluation(self._ev(
            should_veto=True, veto_reason="On-site in Zurich only", phd_requirement_analysis="generic_hr_boilerplate"))
        self.assertTrue(ev.should_veto)

    def test_strict_research_non_software_is_vetoed(self):
        ev = normalize_evaluation(self._ev(is_software_role=False, phd_requirement_analysis="strict_scientific_research"))
        self.assertTrue(ev.should_veto)


class JobspyNanTests(unittest.TestCase):
    def test_nan_is_cleaned(self):
        from tools.ingest_jobspy import _clean, _to_float

        nan = float("nan")
        self.assertIsNone(_clean(nan))
        self.assertIsNone(_to_float(nan))
        self.assertEqual(_to_float("120000.5"), 120000.5)
        self.assertIsNone(_to_float("n/a"))
        self.assertFalse(bool(_clean(nan)))


class FieldClassificationTests(unittest.TestCase):
    def test_identity_fields(self):
        c = browser_apply.classify_field
        self.assertEqual(c("First Name"), "first_name")
        self.assertEqual(c("Last name *"), "last_name")
        self.assertEqual(c("Email address", input_type="email"), "email")
        self.assertEqual(c("Phone"), "phone")
        self.assertEqual(c("LinkedIn Profile URL"), "linkedin")
        self.assertEqual(c("GitHub"), "github")
        self.assertEqual(c("Full name"), "full_name")

    def test_hotel_is_not_a_phone_field(self):
        self.assertIsNone(browser_apply.classify_field("Hotel preference"))

    def test_questions_are_left_to_the_human(self):
        c = browser_apply.classify_field
        self.assertIsNone(c("Will you require visa sponsorship?", tag="select"))
        self.assertIsNone(c("Why do you want to work here?", tag="textarea"))
        self.assertIsNone(c("I agree", input_type="checkbox"))
        self.assertIsNone(c("Password", input_type="password"))

    def test_file_inputs(self):
        c = browser_apply.classify_field
        self.assertEqual(c("Resume/CV", input_type="file"), "resume_file")
        self.assertEqual(c("Cover letter", input_type="file"), "cover_letter_file")
        self.assertIsNone(c("Transcript", input_type="file"))

    def test_plan_fills_skips_prefilled_and_unknown(self):
        answers = browser_apply.build_answers(make_profile())
        fields = [
            {"index": 0, "tag": "input", "type": "text", "descriptor": "First name", "value": ""},
            {"index": 1, "tag": "input", "type": "text", "descriptor": "Last name", "value": "Already"},
            {"index": 2, "tag": "input", "type": "text", "descriptor": "Favorite color", "value": ""},
            {"index": 3, "tag": "input", "type": "file", "descriptor": "Resume", "value": ""},
            {"index": 4, "tag": "input", "type": "file", "descriptor": "Cover letter", "value": ""},
            {"index": 5, "tag": "select", "type": "select-one", "descriptor": "Visa", "value": "Yes"},
        ]
        fills, left = browser_apply.plan_fills(fields, answers, "/tmp/cv.pdf", None)
        self.assertEqual([(f["index"], f["value"]) for f in fills], [(0, "Ada"), (3, "/tmp/cv.pdf")])
        self.assertEqual(left, ["Favorite color", "Cover letter", "Visa"])

    def test_module_never_clicks_or_presses_keys(self):
        source = Path(browser_apply.__file__).read_text(encoding="utf-8")
        code = "\n".join(l for l in source.splitlines() if not l.lstrip().startswith(("#", '"""')))
        for forbidden in (".click(", ".press(", ".dispatch_event(", ".check(", "requestSubmit", ".submit("):
            self.assertNotIn(forbidden, code)


class FakePage:
    def __init__(self, fields, html="<html></html>"):
        self.fields, self.html, self.filled, self.uploaded = fields, html, {}, {}

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, script):
        return self.fields

    def fill(self, selector, value):
        self.filled[selector] = value

    def set_input_files(self, selector, path):
        self.uploaded[selector] = path

    def content(self):
        return self.html


class RunApplyTests(unittest.TestCase):
    final_url = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "t.db"
        self.profile_path = root / "profile.yaml"
        from tools.profile_manager import save_profile

        save_profile(make_profile(), self.profile_path)
        self.cv = root / "cv.pdf"
        self.cv.write_bytes(b"%PDF-1.4")
        init_db(self.db)
        with get_db(self.db) as conn:
            self.job_id, _ = upsert_job(conn, "QSE", "Acme Quantum", "https://job-boards.greenhouse.io/acme/jobs/1", "ashby:acme")
            record_evaluation(conn, self.job_id, 9.0, matched_skills=["Python"])
            record_tailored_documents(conn, self.job_id, str(self.cv), None, True, True)
        self.page = FakePage([
            {"index": 0, "tag": "input", "type": "text", "descriptor": "First name", "value": ""},
            {"index": 1, "tag": "input", "type": "file", "descriptor": "Resume", "value": ""},
        ])

        @contextmanager
        def factory(url, cdp_url=None):
            self.opened = url
            self.page.url = self.final_url or url
            yield self.page

        self.factory = factory

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, answer):
        return browser_apply.run_apply(
            self.job_id, db_path=self.db, profile_path=self.profile_path,
            input_fn=lambda _: answer, page_factory=self.factory,
        )

    def _status(self):
        with get_db(self.db) as conn:
            return conn.execute("SELECT status FROM jobs").fetchone()[0]

    def test_applied_requires_explicit_confirmation(self):
        self.assertEqual(self._run("applied"), "applied")
        self.assertEqual(self.page.filled['[data-aja-idx="0"]'], "Ada")
        self.assertEqual(self.page.uploaded['[data-aja-idx="1"]'], str(self.cv))
        self.assertEqual(self._status(), STATUS_APPLIED)
        with get_db(self.db) as conn:
            self.assertTrue(check_company_cooldown(conn, "Acme Quantum")[0])

    def test_anything_else_leaves_job_pending(self):
        for answer in ("", "yes", "skip", "done"):
            self.assertEqual(self._run(answer), "pending")
        self.assertEqual(self._status(), STATUS_READY_TO_APPLY)

    def test_eof_leaves_job_pending(self):
        def eof(_):
            raise EOFError

        out = browser_apply.run_apply(self.job_id, db_path=self.db, profile_path=self.profile_path,
                                      input_fn=eof, page_factory=self.factory)
        self.assertEqual(out, "pending")
        self.assertEqual(self._status(), STATUS_READY_TO_APPLY)

    def test_blocked_when_not_ready(self):
        with get_db(self.db) as conn:
            other, _ = upsert_job(conn, "Other", "Other Co", "https://x.io/2", "ashby:o")
        out = browser_apply.run_apply(other, db_path=self.db, profile_path=self.profile_path,
                                      input_fn=lambda _: "applied", page_factory=self.factory)
        self.assertEqual(out, "blocked")

    def test_blocked_for_non_http_url(self):
        with get_db(self.db) as conn:
            conn.execute("UPDATE jobs SET url = 'file:///etc/passwd'")
        self.assertEqual(self._run("applied"), "blocked")

    def test_blocked_for_unapproved_or_insecure_host(self):
        for url in ("http://job-boards.greenhouse.io/a/1", "https://evil.example.com/1",
                    "https://greenhouse.io.evil.com/1", "https://greenhouse.io@evil.com/1"):
            with get_db(self.db) as conn:
                conn.execute("UPDATE jobs SET url = ?", (url,))
            self.assertEqual(self._run("applied"), "blocked", url)
            self.assertEqual(self.page.filled, {})

    def test_extra_host_is_allowed_explicitly(self):
        with get_db(self.db) as conn:
            conn.execute("UPDATE jobs SET url = 'https://careers.acme.com/1'")
        out = browser_apply.run_apply(self.job_id, db_path=self.db, profile_path=self.profile_path, input_fn=lambda _: "x",
                                      page_factory=self.factory, extra_hosts=("acme.com",))
        self.assertEqual(out, "pending")

    def test_blocked_when_page_redirects_off_host_before_any_fill(self):
        self.final_url = "https://evil.example.com/collect"
        self.assertEqual(self._run("applied"), "blocked")
        self.assertEqual(self.page.filled, {})
        self.assertEqual(self.page.uploaded, {})

    def test_blocked_when_documents_missing(self):
        self.cv.unlink()
        self.assertEqual(self._run("applied"), "blocked")
        self.assertEqual(self._status(), STATUS_READY_TO_APPLY)


if __name__ == "__main__":
    unittest.main()
