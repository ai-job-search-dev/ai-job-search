"""Answer bank: stances by region and language, grounded free-text answers, gaps and consents."""
import sqlite3
import unittest
from types import SimpleNamespace

from tests.test_tailor_and_apply import make_profile
from tools import answer_bank as ab
from tools.profile_schema import AnswerBank, BankEntry, Stance

JOB = {"job_id": 1, "company": "Acme", "title": "Software Engineer", "location": "Remote, Global", "description": "We build developer tools in TypeScript."}


def profile():
    p = make_profile()
    p.answer_bank = AnswerBank(
        workplace_tools=["Slack", "Jira", "GitHub"],
        consents=[r"privacy|tratamiento de datos"],
        stances=[
            Stance(id="sponsorship_visa_region", pattern="sponsor", answer="Yes", regions=["US", "UK", "CA"]),
            Stance(id="sponsorship", pattern="sponsor", answer="No"),
            Stance(id="notice", pattern="notice period|disponibilidad", answer="20 days", translations={"es": "20 días"}),
        ],
        entries=[BankEntry(id="remote", topics=["remote_async"], approved=True,
                           text="At Acme I coordinate in writing with Jira tickets and GitHub pull requests, and post short updates in Slack.")],
    )
    return p


def field(i, label, tag="input", kind="text", **extra):
    return {"index": i, "tag": tag, "type": kind, "descriptor": label, "value": "", **extra}


class Gateway:
    def __init__(self, text):
        self.text = text

    def evaluate(self, messages, model, job_id=None):
        return model(answer=self.text), None


class AnswerBankTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")

    def run_form(self, fields, job=JOB, gateway=None, p=None):
        return ab.answer_form(self.conn, p or profile(), job, fields, gateway)

    def test_stances_depend_on_the_job_region_and_the_form_language(self):
        global_role = self.run_form([field(0, "Will you require visa sponsorship to work here?", role="combobox")])
        us_role = self.run_form([field(0, "Will you require visa sponsorship to work here?", role="combobox")], job={**JOB, "location": "Remote, United States"})
        self.assertEqual(global_role["answers"][0].answer, "No")
        self.assertEqual(us_role["answers"][0].answer, "Yes")
        spanish = self.run_form([field(0, "¿Cuál es tu disponibilidad para empezar el trabajo?"), field(1, "Nombre"), field(2, "Correo electrónico de la persona")])
        self.assertEqual(spanish["language"], "es")
        self.assertEqual(spanish["answers"][0].answer, "20 días")

    def test_a_grounded_composition_is_used_and_an_invented_tool_falls_back_to_the_bank_text(self):
        q = [field(0, "Tell us about your experience working in a remote and async environment", tag="textarea", kind="textarea")]
        good = self.run_form(q, gateway=Gateway("I coordinate in writing at Acme: Jira tickets, GitHub pull requests and short Slack updates for the TypeScript tools."))
        self.assertTrue(good["answers"][0].note.startswith("composed"))
        invented = self.run_form(q, gateway=Gateway("I coordinate with Kubernetes dashboards and Notion pages every single day at Acme."))
        self.assertTrue(invented["answers"][0].note.startswith("verbatim"))
        self.assertIn("Jira tickets", invented["answers"][0].answer)

    def test_a_composition_that_claims_remote_work_the_bank_never_states_is_rejected(self):
        q = [field(0, "Tell us about your experience working in a remote and async environment", tag="textarea", kind="textarea")]
        job = {**JOB, "description": "Fully remote team across time zones, building TypeScript tools."}
        out = self.run_form(q, job=job, gateway=Gateway("Working remotely at Acme across time zones, I coordinate with Jira tickets and GitHub pull requests."))
        self.assertTrue(out["answers"][0].note.startswith("verbatim"))

    def test_questions_the_bank_cannot_answer_become_gaps(self):
        out = self.run_form([field(0, "Describe a time you disagreed with your manager and what happened", tag="textarea", kind="textarea")])
        self.assertEqual(out["answers"], [])
        rows = self.conn.execute("SELECT question, count FROM answer_gaps").fetchall()
        self.assertEqual(rows[0][1], 1)
        self.run_form([field(0, "Describe a time you disagreed with your manager and what happened", tag="textarea", kind="textarea")])
        self.assertEqual(self.conn.execute("SELECT count FROM answer_gaps").fetchone()[0], 2)

    def test_consents_are_ticked_and_own_words_only_when_every_answer_is_bank_text(self):
        fields = [field(0, "I have read the privacy notice", kind="checkbox"), field(1, "I agree to use only my own words in this application", kind="checkbox"),
                  field(2, "Tell us about your remote work experience in a distributed team", tag="textarea", kind="textarea")]
        out = self.run_form(fields, gateway=Gateway("Composed text that the own-words rule must not use at all here."))
        self.assertTrue(out["own_words"])
        self.assertEqual(sorted(out["ticks"]), [0, 1])
        self.assertTrue(out["answers"][0].note.startswith("verbatim"))
        with_gap = self.run_form(fields + [field(3, "Describe a time you disagreed with your manager", tag="textarea", kind="textarea")])
        self.assertEqual(with_gap["ticks"], [0])

    def test_age_and_yes_no_stances_work_as_dropdowns_radio_groups_and_checkboxes(self):
        p = profile()
        p.answer_bank.stances.insert(0, Stance(id="over_18", pattern=r"over (?:the age of )?18|mayor de edad", answer="Yes"))
        combo = self.run_form([field(0, "Are you over the age of 18?", role="combobox")], p=p)
        self.assertEqual((combo["answers"][0].answer, combo["answers"][0].note), ("Yes", "stance:over_18"))
        radios = [field(1, "Yes", kind="radio", name="age", group="Are you over the age of 18?"),
                  field(2, "No", kind="radio", name="age", group="Are you over the age of 18?")]
        self.assertEqual(self.run_form(radios, p=p)["ticks"], [1])
        spanish = [field(1, "Sí", kind="radio", name="edad", group="¿Sos mayor de edad?"), field(2, "No", kind="radio", name="edad", group="¿Sos mayor de edad?")]
        self.assertEqual(self.run_form(spanish, p=p)["ticks"], [1])
        box = self.run_form([field(3, "I confirm I am over the age of 18", kind="checkbox")], p=p)
        self.assertEqual(box["ticks"], [3])

    def test_a_question_naming_a_country_overrides_the_job_region(self):
        p = profile()
        p.answer_bank.stances.insert(0, Stance(id="auth_visa", pattern="authori[sz]ed to work", answer="No", regions=["US"]))
        p.answer_bank.stances.insert(1, Stance(id="auth", pattern="authori[sz]ed to work", answer="Yes"))
        q = [field(0, "Are you legally authorized to work in the United States?", role="combobox")]
        self.assertEqual(self.run_form(q, p=p, job={**JOB, "location": "Boulder, CO"})["answers"][0].answer, "No")
        self.assertEqual(self.run_form(q, p=p)["answers"][0].answer, "No")
        other = [field(0, "Are you legally authorized to work in the country you are applying for?", role="combobox")]
        self.assertEqual(self.run_form(other, p=p)["answers"][0].answer, "Yes")

    def test_consent_dropdowns_are_answered_yes_and_the_earliest_topic_leads(self):
        out = self.run_form([field(0, "Please confirm that you have read and agree to our Privacy Notice", role="combobox")])
        self.assertEqual((out["answers"][0].answer, out["answers"][0].note), ("Yes", "consent"))
        self.assertEqual(ab.topics_of("Describe your involvement in a production project you are proud of, and any open source contributions")[0],
                         "proudest_project")

    def test_specific_topics_lead_and_the_own_words_declaration_is_never_answered(self):
        q = "Tell us about your experience working in an async and/or remote environment. What challenges have you faced?"
        self.assertEqual(ab.topics_of(q)[0], "remote_async")
        out = self.run_form([field(0, "During this application process I agree to use only my own words", role="combobox"),
                             field(1, "During this application process I agree to use only my own words. Explain", tag="textarea", kind="textarea")])
        self.assertEqual(out["answers"], [])

    def test_audit_flags_names_the_profile_does_not_support(self):
        p = profile()
        p.answer_bank.entries.append(BankEntry(id="bad", topics=["challenge"], text="I migrated Kafka clusters for Netflix.", source="linkedin_hr", approved=True))
        problems = ab.audit(p)
        self.assertTrue(any("bad" in x and "Kafka" in x for x in problems))


if __name__ == "__main__":
    unittest.main()
