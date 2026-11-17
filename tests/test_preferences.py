#!/usr/bin/env python3
"""Tests for preference-driven screening: schema, region logic, rule modes and both ranker paths."""
import tempfile
import unittest
from pathlib import Path

import yaml
from pydantic import ValidationError

from tools import preferences as prefmod
from tools.profile_schema import (
    CareerPreferences,
    EmploymentPreferences,
    RuleSetting,
    ScoringPreferences,
    ScreeningRules,
    WorkAuthorization,
)
from tools.ranker import (
    CITIZENSHIP_VETO_REASON,
    HARDWARE_VETO_REASON,
    PHD_VETO_REASON,
    deterministic_screen,
    heuristic_quantum_eval,
    normalize_evaluation,
    veto_metric,
)
from tools.ranker_schema import QuantumJobEvaluation

DESC = (
    "Build the quantum SDK in Python and C++. Compiler passes, testing and CI/CD. "
    "Work with Qiskit on qubits and error mitigation."
)


def italian_in_argentina(**overrides) -> CareerPreferences:
    base = dict(
        work_authorization=WorkAuthorization(
            citizenships=["IT"], current_country="AR",
            authorized_regions=["EU", "EEA", "AR"], visa_possible_regions=["US", "CA", "AU", "UK", "NZ"],
        ),
        employment=EmploymentPreferences(penalties={"internship": 0.0, "part_time": 0.0, "contract": 0.0}),
        rules=ScreeningRules(
            research_phd=RuleSetting(mode="off"), non_engineering=RuleSetting(mode="off"),
            management=RuleSetting(mode="penalty", penalty=2.0),
        ),
    )
    base.update(overrides)
    return CareerPreferences(**base)


def evaluate(title, description=DESC, location="Remote", prefs=None):
    return heuristic_quantum_eval(title, "Acme", description, location, True, prefs)


class SchemaTests(unittest.TestCase):
    def test_defaults_are_valid_and_strict(self):
        prefs = CareerPreferences()
        self.assertEqual(prefs.shortlist_threshold, 7.5)
        self.assertEqual(prefs.rules.research_phd.mode, "veto")
        self.assertEqual(prefs.rules.work_authorization.mode, "penalty")
        self.assertIn("internship", prefs.employment.accepted_types)

    def test_old_profiles_without_the_new_blocks_still_load(self):
        old = {"target_roles": ["QSE"], "remote_only": True, "deal_breakers": [], "preferred_locations": []}
        self.assertEqual(CareerPreferences.model_validate(old).rules.hardware_lab.mode, "veto")

    def test_invalid_values_are_rejected(self):
        with self.assertRaises(ValidationError):
            RuleSetting(mode="maybe")
        with self.assertRaises(ValidationError):
            CareerPreferences(shortlist_threshold=11)
        with self.assertRaises(ValidationError):
            EmploymentPreferences(accepted_types=["weekends"])

    def test_load_preferences_reads_only_the_block_and_tolerates_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(prefmod.load_preferences(Path(tmp) / "nope.yaml").shortlist_threshold, 7.5)
            path = Path(tmp) / "p.yaml"
            path.write_text(yaml.safe_dump({"summary": "x", "preferences": {"shortlist_threshold": 6.5,
                                                                              "rules": {"unpaid": {"mode": "off"}}}}))
            loaded = prefmod.load_preferences(path)
            self.assertEqual(loaded.shortlist_threshold, 6.5)
            self.assertEqual(loaded.rules.unpaid.mode, "off")
            self.assertEqual(loaded.rules.hardware_lab.mode, "veto")


class RegionTests(unittest.TestCase):
    def test_groups_expand(self):
        self.assertIn("IE", prefmod.expand_regions(["EU"]))
        self.assertIn("NO", prefmod.expand_regions(["EEA"]))
        self.assertNotIn("NO", prefmod.expand_regions(["EU"]))
        self.assertEqual(prefmod.expand_regions(["ar"]), {"AR"})

    def test_regions_in_locations_and_clauses(self):
        self.assertEqual(prefmod.regions_in("Dublin, County Dublin, Ireland"), {"IE"})
        self.assertEqual(prefmod.regions_in("Belfast, Northern Ireland"), set())
        self.assertEqual(prefmod.regions_in("Remote, USA"), {"US"})
        self.assertEqual(prefmod.regions_in("Sydney, Australia"), {"AU"})
        self.assertIn("DE", prefmod.regions_in("Remote within the European Union"))
        self.assertEqual(prefmod.regions_in("Remote"), set())
        self.assertEqual(
            prefmod.posting_regions("Remote", "You must be legally authorized to work in the United States."), {"US"})

    def test_pronoun_us_is_not_a_country(self):
        self.assertEqual(prefmod.regions_in("Join us for a great time"), set())


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.prefs = italian_in_argentina()

    def assess(self, location, description=""):
        return prefmod.assess_work_authorization(self.prefs, location, description)

    def test_eu_postings_need_no_visa_for_an_eu_citizen(self):
        self.assertEqual(self.assess("Dublin, Ireland", "We will not sponsor visas.").penalty, 0.0)
        self.assertEqual(self.assess("Remote", "Remote within the EU only.").penalty, 0.0)

    def test_home_country_is_authorized(self):
        self.assertEqual(self.assess("Buenos Aires, Argentina").penalty, 0.0)

    def test_visa_regions_get_a_light_penalty_unless_sponsorship_is_refused(self):
        self.assertEqual(self.assess("Sydney, Australia").penalty, 1.0)
        self.assertEqual(self.assess("Remote, USA", "We do not sponsor visas.").penalty, 2.0)

    def test_unlisted_regions_get_the_unauthorized_penalty(self):
        self.assertEqual(self.assess("Bangalore, India").penalty, 2.0)

    def test_open_remote_postings_are_not_penalised(self):
        self.assertEqual(self.assess("Remote", "Work from anywhere.").penalty, 0.0)


class EmploymentTests(unittest.TestCase):
    def test_detection(self):
        f = prefmod.detect_employment_types
        self.assertEqual(f("Quantum Software Intern", ""), {"internship"})
        self.assertEqual(f("Engineer", "Part-time, 20 hours a week"), {"part_time"})
        self.assertEqual(f("Engineer (Contract)", ""), {"contract"})
        self.assertEqual(f("Engineer", "We build things."), {"full_time"})
        self.assertEqual(f("International Business Developer", ""), {"full_time"})

    def test_unpaid_detection_ignores_benefit_boilerplate(self):
        self.assertTrue(prefmod.is_unpaid("Intern", "This is an unpaid internship."))
        self.assertTrue(prefmod.is_unpaid("Research Assistant", "Volunteer position, no stipend."))
        self.assertFalse(prefmod.is_unpaid("Engineer", "Benefits include unpaid leave and flexible hours."))

    def test_unaccepted_type_is_vetoed_and_accepted_internship_is_scored(self):
        only_full_time = CareerPreferences(employment=EmploymentPreferences(accepted_types=["full_time"]))
        veto, _, _ = deterministic_screen(only_full_time, "Quantum Software Intern", "Remote", DESC)
        self.assertIn("not accepted", veto)
        accepting = italian_in_argentina()
        veto, penalty, _ = deterministic_screen(accepting, "Quantum Software Intern", "Remote", DESC)
        self.assertIsNone(veto)
        self.assertEqual(penalty, 0.0)


class RuleModeTests(unittest.TestCase):
    def test_research_and_non_engineering_rules_can_be_switched_off(self):
        strict = CareerPreferences()
        relaxed = italian_in_argentina()
        research = evaluate("Quantum Algorithms Research Scientist", DESC + " A Ph.D. in physics is required.", prefs=relaxed)
        self.assertFalse(research.should_veto)
        self.assertTrue(evaluate("Quantum Algorithms Research Scientist", DESC + " A Ph.D. is required.", prefs=strict).should_veto)
        self.assertFalse(evaluate("Sales Engineer", prefs=relaxed).should_veto)
        self.assertTrue(evaluate("Sales Engineer", prefs=strict).should_veto)

    def test_penalty_mode_lowers_the_score_without_vetoing(self):
        base = evaluate("Quantum Software Engineer", prefs=CareerPreferences()).overall_score
        penalised = CareerPreferences(rules=ScreeningRules(non_engineering=RuleSetting(mode="penalty", penalty=1.5)))
        ev = evaluate("Quantum Sales Engineer", prefs=penalised)
        self.assertFalse(ev.should_veto)
        self.assertLess(ev.overall_score, base)

    def test_management_penalty_for_this_user(self):
        prefs = italian_in_argentina()
        manager = evaluate("Engineering Manager, Quantum Software", prefs=prefs)
        engineer = evaluate("Quantum Software Engineer", prefs=prefs)
        self.assertFalse(manager.should_veto)
        self.assertLess(manager.overall_score, engineer.overall_score)

    def test_hardware_stays_vetoed_for_this_user(self):
        ev = evaluate("Cryogenic Test Engineer", "Operate a dilution refrigerator for qubit tests.", prefs=italian_in_argentina())
        self.assertTrue(ev.should_veto)

    def test_unpaid_roles_are_vetoed_only_when_paid_only(self):
        desc = DESC + " This is an unpaid internship."
        self.assertTrue(evaluate("Quantum Software Intern", desc, prefs=italian_in_argentina()).should_veto)
        paid_not_required = italian_in_argentina(employment=EmploymentPreferences(paid_only=False))
        self.assertFalse(evaluate("Quantum Software Intern", desc, prefs=paid_not_required).should_veto)

    def test_internships_are_not_penalised_for_this_user(self):
        prefs = italian_in_argentina()
        self.assertEqual(
            evaluate("Quantum Software Intern", prefs=prefs).overall_score,
            evaluate("Quantum Software Engineer", prefs=prefs).overall_score,
        )

    def test_citizenship_rule_is_skipped_for_someone_who_holds_it(self):
        desc = DESC + " Only U.S. citizens are eligible."
        self.assertTrue(evaluate("Quantum Software Engineer", desc, prefs=CareerPreferences()).should_veto)
        us_holder = CareerPreferences(work_authorization=WorkAuthorization(citizenships=["US"]))
        self.assertFalse(evaluate("Quantum Software Engineer", desc, prefs=us_holder).should_veto)
        self.assertEqual(evaluate("Quantum Software Engineer", desc).veto_reason, CITIZENSHIP_VETO_REASON)

    def test_eu_posting_outscores_the_same_us_posting_for_an_eu_citizen(self):
        prefs = italian_in_argentina()
        eu = evaluate("Quantum Software Engineer", location="Dublin, Ireland", prefs=prefs).overall_score
        us = evaluate("Quantum Software Engineer", location="Austin, USA", prefs=prefs).overall_score
        self.assertGreater(eu, us)

    def test_work_authorization_can_be_set_to_veto(self):
        prefs = italian_in_argentina(rules=ScreeningRules(work_authorization=RuleSetting(mode="veto")))
        self.assertTrue(evaluate("Quantum Software Engineer", location="Bangalore, India", prefs=prefs).should_veto)
        self.assertFalse(evaluate("Quantum Software Engineer", location="Dublin, Ireland", prefs=prefs).should_veto)


class ScoringTests(unittest.TestCase):
    GENERIC = "Build backend services in Go and Kubernetes. Testing and CI/CD."

    def test_domain_weight_zero_scores_engineering_skills_alone(self):
        quantum_only = CareerPreferences()
        generalist = CareerPreferences(scoring=ScoringPreferences(
            domain_weight=0.0, skill_keywords=["go", "kubernetes", "testing", "ci/cd"]))
        low = evaluate("Backend Software Engineer", self.GENERIC, prefs=quantum_only).overall_score
        high = evaluate("Backend Software Engineer", self.GENERIC, prefs=generalist).overall_score
        self.assertGreater(high, low)
        self.assertGreaterEqual(high, 7.5)

    def test_custom_domain_keywords_replace_the_quantum_default(self):
        prefs = CareerPreferences(scoring=ScoringPreferences(domain_keywords=["fintech", "payments"]))
        fintech = evaluate("Payments Software Engineer", "Python payments and fintech platform. Testing, API, git.", prefs=prefs)
        self.assertGreater(fintech.quantum_fit_score, 5.0)
        quantum = evaluate("Quantum Software Engineer", prefs=prefs)
        self.assertLess(quantum.quantum_fit_score, fintech.quantum_fit_score)

    def test_matched_skills_follow_the_candidates_own_keywords(self):
        prefs = CareerPreferences(scoring=ScoringPreferences(skill_keywords=["python", "qiskit"]))
        ev = evaluate("Quantum Software Engineer", prefs=prefs)
        self.assertEqual(sorted(ev.matched_skills), ["Python", "Qiskit"])

    def test_remote_bonus_is_configurable(self):
        with_bonus = evaluate("Quantum Software Engineer", prefs=CareerPreferences()).overall_score
        none = evaluate("Quantum Software Engineer", prefs=CareerPreferences(scoring=ScoringPreferences(remote_bonus=0.0)))
        self.assertGreater(with_bonus, none.overall_score)


class FunnelMetricTests(unittest.TestCase):
    def _ev(self, reason, **kw):
        base = dict(overall_score=2.0, software_fit_score=1.0, quantum_fit_score=1.0, is_software_role=True,
                    is_experimental_hardware=False, phd_requirement_analysis="strict_scientific_research",
                    should_veto=True, veto_reason=reason, reasoning="x")
        base.update(kw)
        return QuantumJobEvaluation(**base)

    def test_veto_is_classified_by_its_reason_not_by_flags(self):
        self.assertEqual(veto_metric(self._ev(CITIZENSHIP_VETO_REASON)), "vetoed_location")
        self.assertEqual(veto_metric(self._ev(HARDWARE_VETO_REASON, is_experimental_hardware=True)), "vetoed_hardware")
        self.assertEqual(veto_metric(self._ev(PHD_VETO_REASON)), "vetoed_phd_strict")
        self.assertEqual(veto_metric(self._ev("VETO: Management role.")), "vetoed_role")
        self.assertEqual(veto_metric(self._ev("VETO: Work authorization (tied to ['IN'])")), "vetoed_location")


class LlmNormalizationTests(unittest.TestCase):
    def _ev(self, **kw):
        base = dict(overall_score=8.0, software_fit_score=8.0, quantum_fit_score=8.0, is_software_role=True,
                    is_experimental_hardware=False, phd_requirement_analysis="not_required", should_veto=False,
                    veto_reason=None, reasoning="x")
        base.update(kw)
        return QuantumJobEvaluation(**base)

    def test_a_veto_for_a_rule_the_user_switched_off_is_lifted(self):
        prefs = italian_in_argentina()
        ev = normalize_evaluation(self._ev(should_veto=True, veto_reason="Research role requiring a PhD",
                                           is_software_role=False, phd_requirement_analysis="strict_scientific_research"), prefs)
        self.assertFalse(ev.should_veto)
        self.assertIsNone(ev.veto_reason)

    def test_a_phd_boilerplate_veto_is_lifted_only_when_phd_is_the_sole_cause(self):
        prefs = CareerPreferences()
        boilerplate = dict(should_veto=True, phd_requirement_analysis="generic_hr_boilerplate")
        lifted = normalize_evaluation(self._ev(veto_reason="Requires a PhD", **boilerplate), prefs)
        self.assertFalse(lifted.should_veto)
        for reason in ("Requires a PhD and US citizenship", "PhD required; on-site in Boston only",
                       "PhD and security clearance required", "Needs a PhD, unpaid internship"):
            kept = normalize_evaluation(self._ev(veto_reason=reason, **boilerplate), prefs)
            self.assertTrue(kept.should_veto, reason)

    def test_a_veto_for_a_penalty_rule_becomes_a_score_penalty(self):
        ev = normalize_evaluation(self._ev(should_veto=True, veto_reason="Management role: director"), italian_in_argentina())
        self.assertFalse(ev.should_veto)
        self.assertEqual(ev.overall_score, 6.0)

    def test_vetoes_for_rules_still_on_are_kept(self):
        ev = normalize_evaluation(self._ev(should_veto=True, veto_reason="Needs a US security clearance"), italian_in_argentina())
        self.assertTrue(ev.should_veto)

    def test_hardware_flag_is_enforced_and_not_lifted_by_the_research_switch(self):
        prefs = italian_in_argentina()
        ev = normalize_evaluation(self._ev(is_experimental_hardware=True, should_veto=True,
                                           veto_reason="Experimental hardware research role"), prefs)
        self.assertTrue(ev.should_veto)

    def test_strict_research_is_vetoed_only_when_the_rule_is_on(self):
        raw = self._ev(is_software_role=False, phd_requirement_analysis="strict_scientific_research")
        self.assertTrue(normalize_evaluation(raw, CareerPreferences()).should_veto)
        self.assertFalse(normalize_evaluation(raw, italian_in_argentina()).should_veto)


class PromptTests(unittest.TestCase):
    def test_prompt_reflects_the_configuration(self):
        text = prefmod.describe_preferences(italian_in_argentina())
        self.assertIn("citizenship IT", text)
        self.assertIn("EU, EEA, AR", text)
        self.assertIn("ignore this rule", text)
        self.assertIn("do NOT veto", text)
        self.assertIn("only paid positions", text)
        strict = prefmod.describe_preferences(CareerPreferences())
        self.assertIn("set should_veto=true", strict)

    def test_ranker_prompt_has_no_personal_or_hardcoded_rules(self):
        root = Path(__file__).resolve().parent.parent
        prompt = yaml.safe_load((root / "config" / "prompts.yaml").read_text())["ranker"]["system_prompt"]
        self.assertIn("{{ preferences }}", prompt)
        self.assertIn("{{ candidate }}", prompt)
        for personal in ("Qiskit", "PennyLane", "Franco", "IMMEDIATE VETO"):
            self.assertNotIn(personal, prompt)

    def test_real_profile_is_git_ignored_and_only_the_example_is_committed(self):
        root = Path(__file__).resolve().parent.parent
        self.assertIn("profile/master_profile.yaml", (root / ".gitignore").read_text().splitlines())
        self.assertTrue((root / "profile" / "master_profile.example.yaml").exists())

    def test_prompts_tell_the_model_postings_are_untrusted_data(self):
        root = Path(__file__).resolve().parent.parent
        prompts = yaml.safe_load((root / "config" / "prompts.yaml").read_text())
        for section in ("ranker", "tailor"):
            self.assertIn("untrusted", prompts[section]["system_prompt"].lower())

    def test_candidate_context_comes_from_the_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.yaml"
            path.write_text(yaml.safe_dump({
                "summary": "Backend engineer.", "skills": [{"name": "Go"}],
                "experience": [{"skills_used": ["Kafka", "Go"]}], "preferences": {"target_roles": ["SRE"]}}))
            text = prefmod.load_candidate_context(path)
            self.assertIn("Backend engineer.", text)
            self.assertIn("Go, Kafka", text)
            self.assertIn("SRE", text)


if __name__ == "__main__":
    unittest.main()
