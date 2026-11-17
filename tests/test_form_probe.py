#!/usr/bin/env python3
"""Tests for the read-only form probe and the form-handling fixes it led to.

The field lists below were captured from live Greenhouse, Lever and Ashby application forms.
"""
import contextlib
import io
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from tools import browser_apply, form_probe
from tools.browser_apply import application_url, classify_field, form_recognised, plan_fills
from tools.db import get_db, init_db, record_evaluation, record_tailored_documents, upsert_job
from tools.form_probe import ProbeResult, analyse_fields, render

ANSWERS = {"first_name": "Ada", "last_name": "Example", "full_name": "Ada Example", "email": "ada@example.com",
           "phone": "+1 555 0100", "linkedin": "https://linkedin.com/in/ada", "github": "https://github.com/ada",
           "portfolio": "https://github.com/ada", "location": "Remote"}


def field(descriptor, tag="input", type_="text", value=""):
    return {"index": 0, "tag": tag, "type": type_, "descriptor": descriptor, "value": value}


def numbered(fields):
    return [{**f, "index": i} for i, f in enumerate(fields)]


GREENHOUSE = numbered([
    field("First Name* First Name first_name given-name"),
    field("Last Name* Last Name last_name family-name"),
    field("Preferred First Name* Preferred First Name preferred_name"),
    field("Email* Email email email"),
    field("Country* Country* country off"),
    field("Phone* Phone phone off", type_="tel"),
    field("Location (City)* Location (City)* candidate-location off"),
    field("Attach resume", type_="file"),
    field("Attach cover_letter", type_="file"),
    field("LinkedIn Profile* LinkedIn Profile question_20328533004"),
    field("Website Website question_20328534004"),
    field("Are you currently authorized to work on a full-time, unrestricted basis for any employer in the country in which you are located?* Are you"),
    field("Do you now, or will you in the future, require visa sponsorship from an employer to work or remain in the country where you are located?*"),
    field("Are you a U.S. citizen or lawful U.S. permanent resident (i.e., current Green Card holder)?* Are you"),
    field("Do you currently or have you in the past two years held an active Top Secret clearance?*"),
    field("What are your OTE expectations?* What are your OTE expectations? question_20344552004"),
    field("Gender Gender gender off"),
])

LEVER = numbered([
    field("Resume/CV \u2731 ATTACH RESUME/CV resume resume-upload-input", type_="file"),
    field("Full name \u2731 name"),
    field("Email \u2731 email", type_="email"),
    field("Phone \u2731 phone"),
    field("Current location location location-input"),
    field("Current company \u2731 org"),
    field("LinkedIn URL urls[LinkedIn]"),
    field("GitHub URL urls[GitHub]"),
    field("Portfolio URL urls[Portfolio]"),
    field("Other website urls[Other]"),
    field("cards[052d2626][field0]", tag="textarea", type_="textarea"),
    field("Gender Select ... Male Female Decline to self-identify eeo[gender]", tag="select", type_="select-one"),
])

ASHBY = numbered([
    field("Name Type here... 67b6c629-c78f-4624-8d81-ae0423580627 67b6c629-c78f-4624-8d81-ae0423580627_name"),
    field("Email Type here... b361e146-6144-4c15-b13a-d1e8cedc8bb5 b361e146-6144-4c15-b13a-d1e8cedc8bb5 _email", type_="email"),
    field("Resume file", type_="file"),
    field("LinkedIn URL https://example.com... 9beedb06-8f12-40da-a5e1-04d18f71b3bf 9beedb06-8f12-40da-a5e1-04d18f71b3bf"),
    field("Why are you interested in joining SandboxAQ? Type here...", tag="textarea", type_="textarea"),
])


class UrlTests(unittest.TestCase):
    def test_lever_and_ashby_postings_map_to_their_form_pages(self):
        self.assertEqual(application_url("https://jobs.lever.co/acme/abc-1?lever-source=x", "lever:acme"),
                         "https://jobs.lever.co/acme/abc-1/apply")
        self.assertEqual(application_url("https://jobs.lever.co/acme/abc-1/apply"), "https://jobs.lever.co/acme/abc-1/apply")
        self.assertEqual(application_url("https://jobs.ashbyhq.com/acme/9f", "ashby:acme"), "https://jobs.ashbyhq.com/acme/9f/application")
        self.assertEqual(application_url("https://jobs.ashbyhq.com/acme/9f/application"), "https://jobs.ashbyhq.com/acme/9f/application")

    def test_greenhouse_postings_on_company_sites_map_to_the_embedded_form(self):
        expected = "https://boards.greenhouse.io/embed/job_app?for=ionq&token=6228533004"
        self.assertEqual(application_url("https://ionq.com/job?gh_jid=6228533004", "greenhouse:ionq"), expected)
        self.assertEqual(application_url("https://job-boards.greenhouse.io/ionq/jobs/6228533004", "greenhouse:ionq"), expected)

    def test_everything_else_is_left_alone(self):
        for url, portal in (("https://www.linkedin.com/jobs/view/1", "jobspy:linkedin"),
                            ("https://careers.example.com/apply", "greenhouse:acme"),
                            ("https://x.io/1", ""), ("https://boards.example.com/1", "lever:acme")):
            self.assertEqual(application_url(url, portal), url)


class ClassifierGuardTests(unittest.TestCase):
    def test_questions_are_never_profile_fields(self):
        for text in ("Do you now require visa sponsorship from an employer?* in the country where you are located",
                     "Are you currently authorized to work in the country in which you are located?",
                     "What is your current location and are you willing to relocate?",
                     "Where are you based? " + "x" * 120):
            self.assertIsNone(classify_field(text), text)

    def test_ids_and_urls_in_the_descriptor_do_not_hide_a_simple_label(self):
        self.assertEqual(classify_field(ASHBY[3]["descriptor"]), "linkedin")
        self.assertEqual(classify_field(ASHBY[1]["descriptor"], "input", "email"), "email")

    def test_simple_labels_still_classify(self):
        self.assertEqual(classify_field("Location (City)* Location (City)* candidate-location off"), "location")
        self.assertEqual(classify_field("Current location location location-input"), "location")


class RealFormTests(unittest.TestCase):
    def plan(self, fields):
        return plan_fills(fields, ANSWERS, "cv.pdf", "letter.pdf")

    def test_greenhouse_fills_only_profile_fields_once_each(self):
        fills, left = self.plan(GREENHOUSE)
        keys = [f["key"] for f in fills]
        self.assertEqual(keys, ["first_name", "last_name", "email", "phone", "location", "resume_file", "cover_letter_file", "linkedin", "portfolio"])
        self.assertEqual(len(keys), len(set(keys)))
        joined = " | ".join(left)
        for question in ("authorized to work", "visa sponsorship", "U.S. citizen", "Top Secret", "OTE expectations"):
            self.assertIn(question, joined)

    def test_lever_leaves_the_second_website_and_the_free_text_to_the_user(self):
        fills, left = self.plan(LEVER)
        keys = [f["key"] for f in fills]
        self.assertEqual(keys, ["resume_file", "full_name", "email", "phone", "location", "linkedin", "github", "portfolio"])
        self.assertTrue(any("Other website" in x for x in left))
        self.assertTrue(any("Current company" in x for x in left))

    def test_ashby_with_noisy_descriptors_still_finds_the_profile_fields(self):
        fills, left = self.plan(ASHBY)
        self.assertEqual([f["key"] for f in fills], ["full_name", "email", "resume_file", "linkedin"])
        self.assertEqual(len(left), 1)

    def test_form_recognition(self):
        for fields in (GREENHOUSE, LEVER, ASHBY):
            self.assertTrue(form_recognised(fields))
        self.assertFalse(form_recognised([field("Search jobs"), field("Subscribe to our newsletter topics")]))
        self.assertFalse(form_recognised([]))

    def test_probe_analysis_matches_the_plan(self):
        analysis = analyse_fields(GREENHOUSE, ANSWERS)
        self.assertEqual(analysis["would_upload"], ["resume_file", "cover_letter_file"])
        self.assertNotIn("visa", " ".join(analysis["would_fill"]))


class ProbeReadOnlyTests(unittest.TestCase):
    def test_probe_and_apply_modules_never_act_on_the_page(self):
        forbidden = (".click(", ".type(", ".press(", ".check(", ".uncheck(", ".submit(", ".dispatch_event(", ".select_option(", ".set_input_files(", ".fill(")
        source = Path(form_probe.__file__).read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith(("#", '"""')))
        for token in forbidden:
            self.assertNotIn(token, code, f"form_probe must stay read-only but contains {token}")

    def test_render_summarises_a_result(self):
        result = ProbeResult(url="https://x", final_url="https://y", title="Apply", field_count=5, form_found=True,
                             would_fill=["email"], would_upload=["resume_file"], left_for_you=["Why us?"])
        text = render(result)
        self.assertIn("Landed on:  https://y", text)
        self.assertIn("form recognised: True", text)
        self.assertIn("Left to you (1): Why us?", text)
        self.assertIn("Error:", render(ProbeResult(url="https://x", error="Timeout")))


class FakePage:
    def __init__(self, fields):
        self.fields = fields
        self.filled, self.uploaded = {}, {}

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, script):
        return self.fields

    def fill(self, selector, value):
        self.filled[selector] = value

    def set_input_files(self, selector, path):
        self.uploaded[selector] = path

    def content(self):
        return "<html></html>"


class RunApplyUrlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "t.db"
        init_db(self.db)
        from tools.profile_manager import save_profile
        from tests.test_tailor_and_apply import make_profile

        self.profile_path = root / "profile.yaml"
        save_profile(make_profile(), self.profile_path)
        self.cv = root / "cv.pdf"
        self.cv.write_bytes(b"%PDF-1.4")
        self.opened = []
        self.page = FakePage([])

        @contextmanager
        def factory(url, cdp_url=None):
            self.opened.append(url)
            self.page.url = url
            yield self.page

        self.factory = factory

    def tearDown(self):
        self.tmp.cleanup()

    def ready_job(self, url, portal):
        with get_db(self.db) as conn:
            job_id, _ = upsert_job(conn, "QSE", "Acme", url, portal)
            record_evaluation(conn, job_id, 9.0, matched_skills=["Python"])
            record_tailored_documents(conn, job_id, str(self.cv), None, True, True)
        return job_id

    def apply(self, job_id, answer="skip"):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = browser_apply.run_apply(job_id, db_path=self.db, profile_path=self.profile_path,
                                             input_fn=lambda _: answer, page_factory=self.factory)
        return result, out.getvalue()

    def test_the_form_page_is_opened_not_the_posting(self):
        job_id = self.ready_job("https://acme.com/job?gh_jid=555", "greenhouse:acme")
        self.apply(job_id)
        self.assertEqual(self.opened, ["https://boards.greenhouse.io/embed/job_app?for=acme&token=555"])

    def test_lever_postings_open_the_apply_page(self):
        job_id = self.ready_job("https://jobs.lever.co/acme/abc", "lever:acme")
        self.apply(job_id)
        self.assertEqual(self.opened, ["https://jobs.lever.co/acme/abc/apply"])

    def test_a_page_without_an_application_form_triggers_a_warning(self):
        self.page.fields = [field("Search jobs"), field("Newsletter signup topics")]
        job_id = self.ready_job("https://job-boards.greenhouse.io/acme/jobs/9", "jobspy:indeed")
        result, out = self.apply(job_id)
        self.assertEqual(result, "pending")
        self.assertIn("No application form was recognised", out)

    def test_a_real_form_does_not_trigger_the_warning(self):
        self.page.fields = LEVER
        job_id = self.ready_job("https://jobs.lever.co/acme/abc", "lever:acme")
        _, out = self.apply(job_id)
        self.assertNotIn("No application form was recognised", out)
        self.assertIn("Filled:   8 field(s)", out)
        self.assertEqual(self.page.filled['[data-aja-idx="1"]'], "Ada Example")


@unittest.skipUnless(os.environ.get("AI_JOB_SEARCH_LIVE") == "1", "set AI_JOB_SEARCH_LIVE=1 to probe live application forms")
class LiveFormTests(unittest.TestCase):
    """Opens real forms in a headless browser and checks the classification. Read-only; needs network and Chrome."""

    def setUp(self):
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("playwright is not installed")

    def first_posting(self, provider):
        import yaml

        from tools import ingest_ats

        config = yaml.safe_load((Path(__file__).resolve().parent.parent / "config" / "companies.yaml").read_text(encoding="utf-8"))
        fetchers = {"greenhouse": ingest_ats.fetch_greenhouse_jobs, "lever": ingest_ats.fetch_lever_jobs, "ashby": ingest_ats.fetch_ashby_jobs}
        for company in config["companies"]:
            if company.get("enabled", True) and company["ats_provider"] == provider:
                try:
                    postings = fetchers[provider](company["slug"])
                except Exception:
                    continue
                if postings:
                    return postings[0]["url"], f"{provider}:{company['slug']}"
        self.skipTest(f"no live {provider} posting available")

    def test_each_ats_form_is_recognised_and_questions_are_left_alone(self):
        for provider in ("greenhouse", "lever", "ashby"):
            with self.subTest(provider=provider):
                url, portal = self.first_posting(provider)
                result = form_probe.probe_form(application_url(url, portal))
                self.assertIsNone(result.error, result.error)
                self.assertTrue(result.form_found, f"{provider} form not recognised at {result.final_url}")
                self.assertIn("resume_file", result.would_upload)
                self.assertEqual(len(result.would_fill), len(set(result.would_fill)))
                for text in result.left_for_you:
                    self.assertNotIn(text.strip(), result.would_fill)


if __name__ == "__main__":
    unittest.main()
