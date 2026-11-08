import json
import tempfile
import unittest
from pathlib import Path

from system_monitor.adapters.gp import Monitor
from system_monitor.notifications import PriorityRegistry


ROOT = Path(__file__).parents[1]


class OpenAIKeyStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.status_path = Path(self.temp.name) / "key-status.json"
        self.monitor = Monitor({
            "state_file": str(Path(self.temp.name) / "monitor-state.json"),
            "openai_key_status_file": str(self.status_path),
        }, notify=False)
        self.monitor.now = 1_800_000_000

    def sample(self, status, seen=None):
        self.status_path.write_text(json.dumps({
            "status": status,
            "last_seen": self.monitor.now if seen is None else seen,
            "last_summary": "must never appear in notification",
        }), encoding="utf-8")
        self.monitor.results.clear()
        self.monitor.check_openai_key_status()
        return self.monitor.results[0]

    def test_exhausted_credit_routes_to_medium_without_provider_payload(self):
        result = self.sample("credit_balance_exhausted")
        self.assertEqual((result.key, result.severity, result.notification_class),
                         ("openai-key:status", "critical", "durable"))
        self.assertNotIn("last_summary", result.summary)
        display = PriorityRegistry.load(ROOT / "config/priorities.json").display(
            result.key, result.summary)
        self.assertEqual((display.priority, display.name), (2, "Klucz OpenAI"))

    def test_transient_api_error_requires_network_confirmation(self):
        result = self.sample("api_error")
        self.assertEqual((result.severity, result.notification_class),
                         ("critical", "network"))

    def test_stale_or_malformed_status_fails_closed(self):
        self.assertEqual(self.sample("ok", self.monitor.now - 7201).severity, "critical")
        self.status_path.write_text("not-json", encoding="utf-8")
        self.monitor.results.clear()
        self.monitor.check_openai_key_status()
        self.assertEqual(self.monitor.results[0].summary,
                         "OpenAI key check status is unavailable")
        self.assertEqual(self.sample("ok").severity, "ok")


if __name__ == "__main__":
    unittest.main()
