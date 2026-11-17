import unittest
from pathlib import Path
import tempfile

from tools.profile_schema import (
    CandidateIdentity,
    CareerPreferences,
    MasterProfile,
    SkillItem,
    WorkExperienceEntry,
    ProjectEntry,
)
from tools.profile_manager import save_profile, load_profile


class ProfileEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".yaml", delete=False)
        self.profile_path = Path(self.tmp.name)

    def tearDown(self):
        try:
            self.profile_path.unlink()
        except Exception:
            pass

    def test_profile_serialization_and_audit(self):
        profile = MasterProfile(
            identity=CandidateIdentity(
                name="Franco Raineri",
                email="franco.raineri@example.com",
                location="Argentina",
            ),
            summary="Quantum software developer with Python and Qiskit.",
            experience=[
                WorkExperienceEntry(
                    company="Quantum Lab",
                    role="Developer",
                    location="Remote",
                    start_date="2023-01",
                    achievements=["Developed Qiskit circuits and improved runtime by 20%."],
                    skills_used=["Python", "Qiskit"],
                )
            ],
            projects=[
                ProjectEntry(
                    name="VQE Simulator",
                    description="Variational quantum eigensolver in PennyLane and NumPy.",
                    technologies=["PennyLane", "NumPy"],
                )
            ],
            skills=[
                SkillItem(name="Python", category="Backend & APIs"),
                SkillItem(name="Qiskit", category="Quantum Computing"),
                SkillItem(name="PennyLane", category="Quantum Computing"),
                # Unbacked skill to test anti-hallucination audit:
                SkillItem(name="Kubernetes", category="Cloud & DevOps"),
            ],
            preferences=CareerPreferences(target_roles=["Quantum Software Engineer"]),
        )

        save_profile(profile, self.profile_path)
        loaded = load_profile(self.profile_path)
        self.assertEqual(loaded.identity.name, "Franco Raineri")
        self.assertEqual(len(loaded.skills), 4)

        report = loaded.audit_consistency()
        self.assertTrue(report.is_valid)
        self.assertIn("Kubernetes", report.unbacked_skills)
        self.assertTrue(any("Kubernetes" in w for w in report.warnings))


if __name__ == "__main__":
    unittest.main()
