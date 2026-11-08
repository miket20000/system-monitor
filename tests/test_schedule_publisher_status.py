import json
import tempfile
import unittest
from pathlib import Path

from system_monitor.adapters.gp import Monitor
from system_monitor.notifications import PriorityRegistry


ROOT = Path(__file__).parents[1]


class SchedulePublisherStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "status.json"
        self.monitor = Monitor({
            "state_file": str(Path(self.temp.name) / "monitor.json"),
            "schedule_publisher_status_file": str(self.path),
        }, notify=False)

    def check(self, payload):
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        self.monitor.results.clear()
        self.monitor.check_schedule_publisher_status()
        return self.monitor.results[0]

    def test_short_manual_failure_remains_visible_after_success(self):
        prior = "2026-11-07T08:00:00Z"
        marker = "2026-11-08T08:15:00Z"
        first = self.check({"ok": True, "lastFailedAttemptAt": prior})
        self.assertEqual((first.severity, first.incident_id), ("ok", prior))
        self.monitor.state["checks"][first.key] = {"incident_id": prior}
        result = self.check({"ok": True, "lastFailedAttemptAt": marker,
                             "error": "private source content must not appear"})
        self.assertEqual((result.severity, result.incident_id, result.notification_class),
                         ("critical", marker, "durable"))
        self.assertNotIn("private", result.summary)
        display = PriorityRegistry.load(ROOT / "config/priorities.json").display(
            result.key, result.summary)
        self.assertEqual(display.priority, 2)
        self.monitor.state["checks"][result.key] = {"incident_id": marker}
        self.assertEqual(self.check({"ok": True, "lastFailedAttemptAt": marker}).severity, "ok")

    def test_current_failure_and_invalid_status(self):
        self.assertEqual(self.check({"ok": False, "lastFailedAttemptAt": "2026-11-08T08:15:00Z"}).severity,
                         "critical")
        self.assertEqual(self.check({"ok": "true"}).summary,
                         "Publication status is unavailable")


if __name__ == "__main__":
    unittest.main()
