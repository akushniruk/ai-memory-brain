import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

from brain_doctor import run_doctor


class BrainDoctorTests(unittest.TestCase):
    @patch("brain_doctor.get_brain_health", return_value={"raw_event_count": 1})
    @patch("brain_doctor.get_vault_status", return_value={"ok": True, "queue": {"pending": 0}})
    @patch("brain_doctor._launchctl_loaded", return_value=(True, "loaded"))
    @patch("brain_doctor._http_ok", side_effect=[(True, '{"ok": true}'), (True, '{"models": []}')])
    @patch("brain_doctor.get_postgres_status", return_value={"ok": True, "reason": ""})
    @patch(
        "brain_doctor.load_settings",
        return_value={
            "profile": "power-user",
            "postgres_enabled": False,
            "neo4j_enabled": False,
            "helper_enabled": False,
            "dedupe_similarity_threshold": 0.86,
            "dedupe_window_minutes": 30,
        },
    )
    def test_run_doctor_reports_profile_drift(self, *_mocks) -> None:
        result = run_doctor()
        self.assertIn("drift_checks", result)
        failing = [item for item in result["drift_checks"] if not item["ok"]]
        self.assertTrue(any(item["name"] == "profile_postgres_alignment" for item in failing))
        self.assertTrue(any(item["name"] == "profile_neo4j_alignment" for item in failing))
        self.assertTrue(any(item["name"] == "profile_helper_alignment" for item in failing))
        self.assertTrue(any("profile_postgres_alignment" in hint for hint in result["hints"]))

    @patch("brain_doctor.get_brain_health", return_value={"raw_event_count": 1})
    @patch("brain_doctor.get_vault_status", return_value={"ok": True, "queue": {"pending": 0}})
    @patch("brain_doctor._launchctl_loaded", return_value=(True, "loaded"))
    @patch("brain_doctor._http_ok", side_effect=[(True, '{"ok": true}'), (True, '{"models": []}')])
    @patch("brain_doctor.get_postgres_status", return_value={"ok": True, "reason": ""})
    @patch(
        "brain_doctor.load_settings",
        return_value={
            "profile": "simple",
            "postgres_enabled": False,
            "neo4j_enabled": False,
            "helper_enabled": False,
            "dedupe_similarity_threshold": 1.5,
            "dedupe_window_minutes": 0,
        },
    )
    def test_run_doctor_flags_invalid_dedupe_settings(self, *_mocks) -> None:
        result = run_doctor()
        failing = [item for item in result["drift_checks"] if not item["ok"]]
        self.assertTrue(any(item["name"] == "dedupe_threshold_range" for item in failing))
        self.assertTrue(any(item["name"] == "dedupe_window_minimum" for item in failing))


if __name__ == "__main__":
    unittest.main()
