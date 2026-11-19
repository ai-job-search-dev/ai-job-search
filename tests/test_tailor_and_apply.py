#!/usr/bin/env python3
"""Tests for Sprint 4 (tailor) and Sprint 5 (HITL browser apply) plus review fixes."""
import os
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from tools import browser_apply, cover_letter, tailor
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

    def test_skill_groups_follow_the_job_title_and_in_progress_degrees_read_as_present(self):
        from tools.profile_schema import SkillItem
        profile = make_profile()
        profile.skills += [SkillItem(name="Angular", category="Frontend & UI"), SkillItem(name="Qiskit2", category="Quantum Computing")]
        profile.education[0].completed = False
        profile.education[0].period = "2025-03 - 2026-09"
        ui = tailor.build_cv(profile, ["Python"], job_title="Full Stack Engineer")
        quantum = tailor.build_cv(profile, ["Python"], job_title="Quantum Software Engineer")
        self.assertEqual(ui.skill_groups[0][0], "Frontend & UI")
        self.assertEqual(quantum.skill_groups[0][0], "Quantum Computing")
        self.assertEqual(ui.education[0]["period"], "2025-03 - Present")

    def test_headline_and_summary_follow_the_job_and_fall_back_to_the_defaults(self):
        from tools.profile_schema import SkillItem
        profile = make_profile()
        profile.skills += [SkillItem(name="Angular", category="Frontend & UI")]
        profile.headlines = {"default": "Software Engineer", "Quantum Computing": "Software Engineer | Quantum", "Frontend & UI": "Frontend Engineer"}
        profile.summary_variants = {"Quantum Computing": "Quantum-first summary."}
        quantum = tailor.build_cv(profile, ["Qiskit"], job_title="Quantum Software Engineer")
        ui = tailor.build_cv(profile, ["Angular"], job_title="Front End Developer")
        other = tailor.build_cv(profile, [], job_title="Office Assistant")
        self.assertEqual((quantum.title, quantum.summary), ("Software Engineer | Quantum", "Quantum-first summary."))
        self.assertEqual((ui.title, ui.summary), ("Frontend Engineer", profile.summary))
        self.assertEqual((other.title, other.summary), ("Software Engineer", profile.summary))

    def test_letter_wording_varies_by_job_but_never_between_runs(self):
        profile = make_profile()
        cv = tailor.build_cv(profile, ["Python"])
        once = tailor.build_cover_letter(profile, "Acme", "QSE", cv.matched, [], cv)
        again = tailor.build_cover_letter(profile, "Acme", "QSE", cv.matched, [], cv)
        self.assertEqual(once, again)
        variants = {tuple(tailor.build_cover_letter(profile, f"Co{i}", "Engineer", cv.matched, [], cv)[1:2]) for i in range(12)}
        self.assertGreater(len(variants), 1)

    def test_short_title_drops_team_suffixes_and_location_notes(self):
        short = cover_letter.short_title
        self.assertEqual(short("Fullstack Engineer (TypeScript), AI Engineering: Duo Client SDK"), "Fullstack Engineer (TypeScript)")
        self.assertEqual(short("Intern, Quantum Algorithms and Compilation"), "Intern, Quantum Algorithms and Compilation")
        self.assertEqual(short("Senior Full Stack Engineer | Observability | Germany | Remote"), "Senior Full Stack Engineer")
        self.assertEqual(short("Full Stack Engineer (Remote Ireland)"), "Full Stack Engineer")

    def test_cover_letter_does_not_advertise_gaps_or_repeat_the_summary(self):
        profile = make_profile()
        cv = tailor.build_cv(profile, ["Python"])
        text = " ".join(tailor.build_cover_letter(profile, "Acme", "QSE", cv.matched, ["Python", "C++"], cv))
        self.assertNotIn("C++", text)
        self.assertNotIn(profile.summary, text)
        self.assertTrue(any(o.format(title="QSE", company="Acme") in text for o in cover_letter.OPENINGS))

    def test_cover_letter_uses_approved_lines_and_only_claims_profile_skills(self):
        profile = make_profile()
        profile.projects[1].letter_line = "I built Quantum Bench in Qiskit to compare noise models on small VQE problems."
        profile.experience[0].letter_line = "At Acme, I build the REST APIs behind a quantum workflow tool."
        paragraphs = tailor.build_cover_letter(profile, "Acme", "QSE", ["Qiskit", "Python", "Rust"], [], tailor.build_cv(profile, ["Qiskit"]))
        text = " ".join(paragraphs)
        self.assertIn("I built Quantum Bench in Qiskit", text)
        self.assertIn("I build the REST APIs", text)
        self.assertNotIn("Rust", text)
        self.assertTrue(any(b.format(skills="Qiskit and Python") in text for b in cover_letter.BRIDGES))

    def test_cover_letter_offers_relocation_only_for_on_site_roles_when_enabled(self):
        profile = make_profile()
        cv = tailor.build_cv(profile, ["Python"])
        profile.preferences.relocation = True
        on_site = " ".join(tailor.build_cover_letter(profile, "Acme", "QSE", cv.matched, [], cv, remote=False))
        remote = " ".join(tailor.build_cover_letter(profile, "Acme", "QSE", cv.matched, [], cv, remote=True))
        self.assertIn("ready to relocate", on_site)
        self.assertNotIn("relocate", remote)

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
    @mock.patch.dict(os.environ)
    def test_tailor_job_produces_verified_pdfs(self):
        os.environ.pop("OPENAI_API_KEY", None)
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
            should_veto=True, veto_reason="Requires a security clearance", phd_requirement_analysis="generic_hr_boilerplate"))
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


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.profile = make_profile()

    def tearDown(self):
        self.tmp.cleanup()

    def letter(self, company="Acme", extra=""):
        return (f"Dear {company} hiring team, I am applying for the QSE position at {company}. " + "I build quantum software with Python and Qiskit every day. " * 12 + extra + " Sincerely, Ada Example")

    def test_exact_checks_catch_wrong_company_placeholders_length_and_recycled_letters(self):
        from tools import preflight
        folder = self.root / "acme"
        folder.mkdir()
        other = self.root / "beta"
        other.mkdir()
        with mock.patch.object(preflight, "sibling_letters", return_value={"beta": self.letter("Betacorp")}):
            problems = preflight.deterministic_checks("Acme", "QSE", self.letter("Betacorp") + " [COMPANY]", "cv", folder, self.profile, ["Betacorp", "Gammacorp"])
        text = " | ".join(problems)
        self.assertIn("never names the company", text)
        self.assertIn("mentions another company: Betacorp", text)
        self.assertIn("placeholder", text)
        self.assertIn("same as the one for beta", text)
        short = preflight.deterministic_checks("Acme", "QSE", "Dear Acme, Ada Example", "cv", folder, self.profile)
        self.assertTrue(any("words" in p for p in short))
        with mock.patch.object(preflight, "sibling_letters", return_value={}):
            self.assertEqual(preflight.deterministic_checks("Acme", "QSE", self.letter(), "cv", folder, self.profile, ["Betacorp"]), [])

    def test_region_locked_roles_are_flagged_as_probably_ineligible(self):
        from tools import preflight
        self.profile.preferences.work_authorization.current_country = "AR"
        self.profile.preferences.relocation = False
        with mock.patch.object(preflight, "sibling_letters", return_value={}):
            locked = preflight.deterministic_checks("Acme", "QSE", self.letter(), "cv", self.root, self.profile, location="Remote, Canada", is_remote=True,
                                                    left=["This role is open to candidates in the US or Canada. Do you currently live in this region?"])
            open_role = preflight.deterministic_checks("Acme", "QSE", self.letter(), "cv", self.root, self.profile, location="Remote, Global", is_remote=True)
        self.assertTrue(any(p.startswith("ELIGIBILITY") for p in locked))
        self.assertTrue(any("asks where you live" in p for p in locked))
        self.assertEqual(open_role, [])

    def test_model_form_issues_that_only_repeat_the_fields_left_for_the_human_are_dropped(self):
        from tools import preflight
        review = {"document_issues": [], "unsupported_claims": [], "form_issues": [
            "Will you now or in the future require sponsorship for a visa", "The phone number lacks a country code for the form"]}
        out = preflight.clean_review(review, has_form=True, left=["Will you now or in the future require sponsorship for a visa to remain"])
        self.assertEqual(out["form_issues"], ["The phone number lacks a country code for the form"])

    def test_form_values_must_come_from_the_profile(self):
        from tools import preflight
        fills = [{"key": "country", "kind": "combo", "value": "Argentina"}, {"key": "email", "kind": "text", "value": "wrong@example.com"},
                 {"key": "resume_file", "kind": "file", "value": "/tmp/cv.pdf"}]
        with mock.patch.object(preflight, "sibling_letters", return_value={}):
            problems = preflight.deterministic_checks("Acme", "QSE", self.letter(), "cv", self.root, self.profile, fills=fills,
                                                      answers={"country": "Argentina", "email": "ada@example.com"})
        self.assertEqual(len(problems), 1)
        self.assertIn("email", problems[0])

    def test_context_has_only_this_application_and_the_model_verdict_can_only_tighten(self):
        from types import SimpleNamespace
        from tools import preflight
        job = {"job_id": 1, "company": "Acme", "title": "Quantum Software Engineer", "location": "Remote", "is_remote": True,
               "description": "We build quantum compilers in Python. " * 5, "matched_skills": ["Python", "Qiskit"]}
        fills = [{"key": "country", "label": "Country", "kind": "combo", "value": "Argentina"}, {"key": "resume_file", "label": "Attach", "kind": "file", "value": "/x/cv.pdf"}]
        context = preflight.build_context(job, self.profile, "CV TEXT", self.letter(), fills, ["Gender"])
        for expected in ("Company: Acme", "Python, Qiskit", "CV TEXT", "Country: Argentina", "<file cv.pdf>", "LEFT FOR THE CANDIDATE: Gender", "will not relocate", "salary floor"):
            self.assertIn(expected, context)
        review = preflight.PreflightReview(fit="weak", summary="The role stresses storage internals the candidate has not worked on.", reads_like_a_person=False)
        gateway = SimpleNamespace(evaluate=lambda messages, model, job_id=None: (review, None))
        with mock.patch.object(preflight, "sibling_letters", return_value={}):
            result = preflight.run_preflight(job, self.profile, "cv", self.letter(), gateway, folder=self.root)
        self.assertEqual(result["verdict"], "hold")
        noisy = preflight.clean_review({"document_issues": ["location", "The letter never names the product the role builds."], "form_issues": ["salary_floor"], "unsupported_claims": []}, has_form=False)
        self.assertEqual((noisy["document_issues"], noisy["form_issues"]), (["The letter never names the product the role builds."], []))
        with mock.patch.object(preflight, "sibling_letters", return_value={}):
            clean = preflight.run_preflight(job, self.profile, "cv", self.letter(), None, folder=self.root)
        self.assertEqual(clean["verdict"], "send")
        self.assertIn("skipped", preflight.render_preflight(clean))


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

    def test_module_only_clicks_dropdown_helpers_and_never_submits(self):
        import inspect
        source = Path(browser_apply.__file__).read_text(encoding="utf-8")
        code = "\n".join(l for l in source.splitlines() if not l.lstrip().startswith(("#", '"""')))
        for forbidden in (".press(", ".dispatch_event(", ".check(", "requestSubmit", ".submit(", ".tap(", "keyboard."):
            self.assertNotIn(forbidden, code)
        clickers = {name for name, fn in inspect.getmembers(browser_apply, inspect.isfunction) if ".click(" in inspect.getsource(fn)}
        self.assertEqual(clickers, {"_open_combo", "_click_option"})
        self.assertIn("role=\"option\"", inspect.getsource(browser_apply._click_option) + browser_apply.OPTION_SELECTOR)

    def test_education_and_country_fields_are_filled_but_sensitive_questions_never(self):
        answers = {"country": "Argentina", "school": "Universidad Internacional de La Rioja", "degree": "master",
                   "end_month": "February", "end_year": "2025", "city": "Tandil", "location": "Tandil, Buenos Aires, Argentina"}
        fields = [
            {"index": 0, "tag": "input", "type": "text", "descriptor": "Country* Country* country off", "value": "", "role": "combobox"},
            {"index": 1, "tag": "input", "type": "text", "descriptor": "School School school--0 off", "value": "", "role": "combobox"},
            {"index": 2, "tag": "input", "type": "text", "descriptor": "Degree Degree degree--0 off", "value": "", "role": "combobox"},
            {"index": 3, "tag": "input", "type": "number", "descriptor": "End date year End date year end-year--0", "value": ""},
            {"index": 4, "tag": "input", "type": "text", "descriptor": "Are you legally authorized to work in the country for which you are applying?", "value": "", "role": "combobox"},
            {"index": 5, "tag": "input", "type": "text", "descriptor": "Will you now or in the future require sponsorship for employment visa status", "value": "", "role": "combobox"},
            {"index": 6, "tag": "input", "type": "text", "descriptor": "Gender Gender gender off", "value": "", "role": "combobox"},
            {"index": 7, "tag": "select", "type": "select-one", "descriptor": "Country", "value": "", "options": ["Italy", "Argentina"]},
        ]
        fills, left = browser_apply.plan_fills(fields, answers, None, None)
        filled = {f["index"]: (f["kind"], f["key"]) for f in fills}
        self.assertEqual(filled[0], ("combo", "country"))
        self.assertEqual(filled[1], ("combo", "school"))
        self.assertEqual(filled[2], ("combo", "degree"))
        self.assertEqual(filled[3], ("text", "end_year"))
        for index in (4, 5, 6):
            self.assertNotIn(index, filled)
        self.assertEqual(len(left), 4)  # the three sensitive questions and the second Country field, already answered

    def test_labels_lose_duplicated_words_and_ids_and_helper_inputs_are_skipped(self):
        short = browser_apply.short_label
        self.assertEqual(short("First Name* First Name first_name given-name"), "First Name")
        self.assertEqual(short("Country* Country* country off"), "Country")
        self.assertEqual(short("Will you now or in the future require sponsorship?"), "Will you now or in the future require sponsorship?")
        fields = [{"index": 0, "tag": "input", "type": "text", "descriptor": "", "value": ""},
                  {"index": 1, "tag": "input", "type": "text", "descriptor": "Gender Gender gender off", "value": "", "role": "combobox"}]
        fills, left = browser_apply.plan_fills(fields, {}, None, None)
        self.assertEqual(left, ["Gender"])

    def test_native_select_picks_the_matching_option_only(self):
        fields = [{"index": 0, "tag": "select", "type": "select-one", "descriptor": "Country", "value": "", "options": ["Italy", "Argentina"]}]
        fills, _ = browser_apply.plan_fills(fields, {"country": "Argentina"}, None, None)
        self.assertEqual((fills[0]["kind"], fills[0]["value"]), ("select", "Argentina"))
        fills, left = browser_apply.plan_fills(fields, {"country": "Chile"}, None, None)
        self.assertEqual(fills, [])
        self.assertEqual(len(left), 1)

    def test_option_matching_never_guesses(self):
        pick = browser_apply.pick_option
        self.assertEqual(pick(["Tandilt, Morocco", "Tandil, Buenos Aires, Argentina"], "location", "Tandil, Buenos Aires, Argentina"), 1)
        self.assertIsNone(pick(["Tandilt, Morocco", "Tandil, Jharkhand, India"], "location", "Tandil, Buenos Aires, Argentina"))
        self.assertEqual(pick(["Bachelor's Degree", "Master's Degree"], "degree", "master"), 1)
        self.assertIsNone(pick(["Aberystwyth University", "Oxford University"], "school", "Universidad Internacional de La Rioja"))
        self.assertEqual(pick(["Universidad Internacional de La Rioja (UNIR)"], "school", "Universidad Internacional de La Rioja"), 0)
        self.assertEqual(pick(["January", "February"], "end_month", "February"), 1)

    def test_education_uses_the_highest_completed_degree_never_one_in_progress(self):
        from datetime import date
        profile = make_profile()
        profile.education = [
            EducationEntry(degree="Master's Degree, Quantum Computing", institution="UNIR", period="2025-03 - 2026-09", completed=False),
            EducationEntry(degree="Master's Degree, Computer Software Engineering", institution="UNIR", period="2025-02"),
            EducationEntry(degree="Software Systems Engineer, Computer Science", institution="UNCPBA", period="2018-03 - 2023-05"),
        ]
        picked = browser_apply.pick_education(profile, today=date(2026, 11, 18))
        self.assertEqual((picked["degree"], picked["end_year"], picked["end_month"]), ("master", "2025", "February"))
        self.assertEqual(picked["discipline"], "Computer Software Engineering")

    def test_combo_selection_clicks_only_an_option_and_leaves_no_match_empty(self):
        class Page:
            def __init__(self, options):
                self.options, self.clicked, self.typed = options, [], []
            def click(self, selector): self.clicked.append(selector)
            def fill(self, selector, text): self.typed.append(text)
            def wait_for_timeout(self, ms): pass
            def get_attribute(self, selector, name): return "listbox-1"
            def eval_on_selector_all(self, selector, script): return self.options
            def evaluate(self, script): pass
            class _L:
                def __init__(self, page, selector): self.page, self.selector = page, selector
                def nth(self, i): self.page.picked = (self.selector, i); return self
                def click(self): self.page.clicked.append("option")
            def locator(self, selector): return Page._L(self, selector)
        page = Page(["Italy", "Argentina"])
        self.assertTrue(browser_apply.fill_combo(page, "#country", "country", "Argentina"))
        self.assertEqual(page.picked, ('[id="listbox-1"] [role="option"]', 1))
        empty = Page(["Italy"])
        self.assertFalse(browser_apply.fill_combo(empty, "#country", "country", "Argentina"))
        self.assertEqual(empty.typed[-1], "")

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

    def test_record_applied_after_a_manual_submit_starts_the_cooldown_once(self):
        self.assertEqual(browser_apply.record_applied(self.job_id, db_path=self.db), "applied")
        self.assertEqual(self._status(), STATUS_APPLIED)
        with get_db(self.db) as conn:
            self.assertTrue(check_company_cooldown(conn, "Acme Quantum")[0])
        self.assertEqual(browser_apply.record_applied(self.job_id, db_path=self.db), "blocked")

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
