import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from system_monitor.notifications import (
    NotificationEngine,
    PriorityRegistry,
    format_low_digest,
    format_message,
)


ROOT = Path(__file__).parents[1]


class RecordingSender:
    def __init__(self, fail_priority=None):
        self.calls = []
        self.fail_priority = fail_priority

    def send(self, priority, text):
        if priority == self.fail_priority:
            raise RuntimeError("delivery failed")
        self.calls.append((priority, text))


def result(key, severity, summary="unhealthy", incident_id="", notification_class="sampled"):
    return SimpleNamespace(
        key=key,
        severity=severity,
        summary=summary,
        incident_id=incident_id,
        notification_class=notification_class,
    )


class FormatTests(unittest.TestCase):
    def test_each_event_is_a_separate_plain_line(self):
        text = format_message(
            "NEW",
            [
                {"name": "Online Compiler", "problem": "brak poprawnej odpowiedzi publicznej", "host": "gp"},
                {"name": "AI Text", "problem": "proces aplikacji nie działa", "host": "gp"},
            ],
        )
        self.assertEqual(
            text.splitlines(),
            [
                "[NEW]",
                "Online Compiler : brak poprawnej odpowiedzi publicznej : gp",
                "AI Text : proces aplikacji nie działa : gp",
            ],
        )
        lowered = text.lower()
        for forbidden in ("critical", "warning", "endpoint", "service", "timer", "cron"):
            self.assertNotIn(forbidden, lowered)

    def test_low_digest_has_only_supported_status_sections(self):
        text = format_low_digest(
            [
                {"status": "NEW", "name": "Backup", "problem": "backup nie działa", "host": "devbox"},
                {"status": "RESOLVED", "name": "Poczta", "problem": "zadanie ponownie działa", "host": "gp"},
            ]
        )
        self.assertEqual(text.count("[NEW]"), 1)
        self.assertEqual(text.count("[RESOLVED]"), 1)
        self.assertNotIn("[REMINDER]", text)


class NotificationEngineTests(unittest.TestCase):
    def setUp(self):
        self.registry = PriorityRegistry.load(ROOT / "config/priorities.json")
        self.sender = RecordingSender()
        self.state = {"checks": {}}
        self.now = datetime(2026, 10, 4, 10, 0, tzinfo=ZoneInfo("Europe/Warsaw")).timestamp()

    def cycle(self, item, seconds=300, notify=True):
        engine = NotificationEngine(
            self.registry,
            host="gp",
            now=self.now,
            notify=notify,
            sender=self.sender,
        )
        engine.process(self.state, [item])
        self.now += seconds

    def test_third_transient_failure_opens_incident(self):
        failure = result("endpoint:online-compiler-com", "critical", "HTTP 503", notification_class="network")
        self.cycle(failure)
        self.cycle(failure)
        self.assertEqual(self.sender.calls, [])
        self.cycle(failure)
        self.assertEqual(len(self.sender.calls), 1)
        self.assertEqual(self.sender.calls[0][0], 1)
        self.assertTrue(self.sender.calls[0][1].startswith("[NEW]\n"))

    def test_sampled_failure_requires_two_cycles(self):
        failure = result("filesystem:space:/", "warning", "Disk usage 95%")
        self.cycle(failure)
        self.assertEqual(self.sender.calls, [])
        self.cycle(failure)
        self.assertEqual(self.sender.calls[0][0], 2)

    def test_durable_failure_opens_incident_after_one_cycle(self):
        failure = result(
            "system-service:backup.service:result",
            "critical",
            "last run failed",
            incident_id="invocation-1",
            notification_class="durable",
        )
        self.cycle(failure)
        self.assertEqual(self.sender.calls[0][0], 3)
        self.assertTrue(self.sender.calls[0][1].startswith("[NEW]\n"))

    def test_changing_durable_evidence_stays_one_incident_until_recovery(self):
        def failed(invocation_id):
            return result(
                "system-service:meta-api-status-watcher.service:result",
                "critical",
                "last run failed",
                incident_id=invocation_id,
                notification_class="durable",
            )

        self.cycle(failed("invocation-1"))
        self.assertEqual(len(self.sender.calls), 1)
        self.assertTrue(self.sender.calls[0][1].startswith("[NEW]\n"))
        self.sender.calls.clear()

        self.cycle(failed("invocation-2"))
        self.cycle(failed("invocation-3"))
        self.assertEqual(self.sender.calls, [])
        self.assertEqual(
            self.state["checks"]["system-service:meta-api-status-watcher.service:result"]["incident_id"],
            "invocation-3",
        )

        last_notification = self.state["checks"][
            "system-service:meta-api-status-watcher.service:result"
        ]["last_notification"]
        self.now = last_notification + 21600
        self.cycle(failed("invocation-4"))
        self.assertEqual(len(self.sender.calls), 1)
        self.assertTrue(self.sender.calls[0][1].startswith("[REMINDER]\n"))

        self.sender.calls.clear()
        healthy = result(
            "system-service:meta-api-status-watcher.service:result",
            "ok",
            "healthy",
            incident_id="invocation-4",
            notification_class="durable",
        )
        self.cycle(healthy)
        self.assertEqual(self.sender.calls, [])
        self.cycle(healthy)
        self.assertTrue(self.sender.calls[0][1].startswith("[RESOLVED]\n"))

        self.sender.calls.clear()
        self.cycle(failed("invocation-5"))
        self.assertTrue(self.sender.calls[0][1].startswith("[NEW]\n"))

    def test_recovery_requires_two_healthy_cycles(self):
        failure = result("service:ai-text.service", "critical", "not active", notification_class="durable", incident_id="one")
        self.cycle(failure)
        self.sender.calls.clear()
        healthy = result("service:ai-text.service", "ok", "healthy", notification_class="durable", incident_id="one")
        self.cycle(healthy)
        self.assertEqual(self.sender.calls, [])
        self.cycle(healthy)
        self.assertEqual(self.sender.calls[0][1].splitlines()[0], "[RESOLVED]")

    def test_high_reminder_after_fifteen_minutes(self):
        failure = result("service:ai-text.service", "critical", "not active", notification_class="durable", incident_id="one")
        self.cycle(failure)
        self.sender.calls.clear()
        self.cycle(failure, seconds=900)
        self.cycle(failure)
        self.assertEqual(self.sender.calls[0][1].splitlines()[0], "[REMINDER]")

    def test_medium_reminder_after_six_hours(self):
        failure = result(
            "filesystem:space:/",
            "warning",
            "Disk usage 95%",
            incident_id="disk-1",
            notification_class="durable",
        )
        self.cycle(failure)
        self.sender.calls.clear()
        self.cycle(failure, seconds=21600)
        self.cycle(failure)
        self.assertEqual(self.sender.calls[0][0], 2)
        self.assertTrue(self.sender.calls[0][1].startswith("[REMINDER]\n"))

    def test_failed_channel_keeps_pending_event(self):
        self.sender.fail_priority = 1
        failure = result("service:ai-text.service", "critical", "not active", notification_class="durable", incident_id="one")
        self.cycle(failure)
        self.assertTrue(self.state["pending_notifications"])
        self.sender.fail_priority = None
        self.cycle(failure)
        self.assertFalse(self.state["pending_notifications"])

    def test_failed_high_channel_does_not_block_medium_delivery(self):
        self.sender.fail_priority = 1
        high = result(
            "service:ai-text.service", "critical", "not active",
            incident_id="app-1", notification_class="durable",
        )
        medium = result(
            "filesystem:space:/", "warning", "Disk usage 95%",
            incident_id="disk-1", notification_class="durable",
        )
        engine = NotificationEngine(
            self.registry, host="gp", now=self.now, notify=True, sender=self.sender
        )
        engine.process(self.state, [high, medium])
        self.assertEqual([priority for priority, _ in self.sender.calls], [2])
        self.assertTrue(any(event["priority"] == 1 for event in self.state["pending_notifications"].values()))

    def test_legacy_active_incident_is_not_replayed(self):
        self.state = {
            "checks": {
                "filesystem:space:/": {
                    "severity": "warning",
                    "last_notification": self.now - 60,
                    "last_seen": self.now - 60,
                }
            }
        }
        self.cycle(result("filesystem:space:/", "warning", "Disk usage 91%"))
        self.assertEqual(self.sender.calls, [])
        self.assertTrue(self.state["checks"]["filesystem:space:/"]["alert_active"])
        self.assertEqual(self.state["notification_state_version"], 3)

    def test_gp_v2_active_incident_migrates_to_v3_without_replay(self):
        self.state = {
            "notification_state_version": 2,
            "checks": {
                "cron:dysk-sieciowy-sync:freshness": {
                    "severity": "critical",
                    "last_notification": self.now - 300,
                    "last_seen": self.now - 300,
                    "alert_active": True,
                    "failure_streak": 0,
                    "recovery_streak": 0,
                }
            },
            "notification_audit": [{"status": "sent", "count": 1}],
        }
        self.cycle(result("cron:dysk-sieciowy-sync:freshness", "critical", "last success stale"))
        self.assertEqual(len(self.sender.calls), 1)
        self.assertEqual(self.sender.calls[0][0], 3)
        self.assertTrue(self.sender.calls[0][1].startswith("[REMINDER]\n"))
        self.assertNotIn("[NEW]", self.sender.calls[0][1])
        self.assertEqual(self.state["notification_state_version"], 3)
        self.assertEqual(self.state["notification_audit"], [{"status": "sent", "count": 1}])

    def test_low_digest_after_eight_and_not_when_empty(self):
        low = result(
            "user-service:vps-restic-backup.service:result",
            "critical",
            "last run failed",
            notification_class="durable",
            incident_id="one",
        )
        self.cycle(low)
        self.assertEqual(self.sender.calls[0][0], 3)
        self.assertIn("[NEW]", self.sender.calls[0][1])
        self.sender.calls.clear()
        self.cycle(result("user-service:vps-restic-backup.service:result", "ok", "healthy", notification_class="durable", incident_id="one"))
        self.assertEqual(self.sender.calls, [])

    def test_empty_low_digest_does_not_hide_later_event(self):
        self.cycle(result("filesystem:space:/", "ok", "healthy"))
        self.assertNotIn("last_low_digest_date", self.state)
        low = result(
            "daily-success:password-reset-primary",
            "critical",
            "last run failed",
            incident_id="run-1",
            notification_class="durable",
        )
        self.cycle(low)
        self.assertEqual(self.sender.calls[0][0], 3)

    def test_pending_low_event_uses_current_configured_name(self):
        key = "user-service:vps-restic-gp-mail-purge.service:result"
        self.state["pending_notifications"] = {
            f"3:RESOLVED:{key}": {
                "key": key,
                "priority": 3,
                "status": "RESOLVED",
                "name": "Backup",
                "problem": "backup ponownie zakończył się pomyślnie",
                "host": "devbox",
            }
        }
        self.cycle(
            result(
                key,
                "ok",
                "healthy",
                incident_id="mail-archive-1",
                notification_class="durable",
            ),
            notify=False,
        )
        event = next(iter(self.state["pending_notifications"].values()))
        self.assertEqual(event["name"], "Archiwizacja poczty GP")
        self.assertEqual(event["problem"], "zadanie ponownie zakończyło się pomyślnie")

    def test_low_digest_uses_warsaw_date_across_dst(self):
        self.now = datetime(2026, 3, 29, 7, 30, tzinfo=ZoneInfo("Europe/Warsaw")).timestamp()
        low = result(
            "daily-success:password-reset-primary",
            "critical",
            "last run failed",
            incident_id="spring-run",
            notification_class="durable",
        )
        self.cycle(low)
        self.assertEqual(self.sender.calls, [])
        self.now = datetime(2026, 3, 29, 8, 1, tzinfo=ZoneInfo("Europe/Warsaw")).timestamp()
        self.cycle(low)
        self.assertEqual(self.sender.calls[0][0], 3)
        self.assertEqual(self.state["last_low_digest_date"], "2026-03-29")


class ProfileTests(unittest.TestCase):
    def test_profiles_share_versioned_envelope(self):
        for name, adapter in (("devbox", "devbox"), ("gp", "gp")):
            profile = json.loads((ROOT / f"profiles/{name}.json").read_text())
            self.assertEqual(profile["schema_version"], 1)
            self.assertEqual(profile["adapter"], adapter)
            self.assertIn("priorities_file", profile)

    def test_password_reset_is_monitored_without_pending_contents(self):
        profile = json.loads((ROOT / "profiles/devbox.json").read_text())
        timers = {item["unit"] for item in profile["user_timers"]}
        self.assertIn("password-reset-orchestrator.timer", timers)
        checks = profile["daily_success_checks"]
        self.assertEqual(len(checks), 4)
        for check in checks:
            self.assertEqual(check["last_success_local_date_field"], "last_success_local_date")
            self.assertNotIn("pending_field", check)

    def test_every_configured_endpoint_has_an_explicit_expected_priority(self):
        registry = PriorityRegistry.load(ROOT / "config/priorities.json")
        devbox = json.loads((ROOT / "profiles/devbox.json").read_text())
        gp = json.loads((ROOT / "profiles/gp.json").read_text())
        for endpoint in devbox["endpoints"]:
            display = registry.display(f"endpoint:{endpoint['name']}", "HTTP 503")
            self.assertEqual(display.priority, 2, endpoint["name"])
            format_message("NEW", [{"name": display.name, "problem": display.problem, "host": "devbox"}])

        medium_gp = {
            "vm-manager-local",
            "vm-manager-inventory-sync-local",
            "trustpilot-notifier-local",
        }
        for endpoint in gp["endpoints"]:
            display = registry.display(f"endpoint:{endpoint['name']}", "HTTP 503")
            expected = 2 if endpoint["name"] in medium_gp else 1
            self.assertEqual(display.priority, expected, endpoint["name"])
            format_message("NEW", [{"name": display.name, "problem": display.problem, "host": "gp"}])

    def test_public_product_runtimes_are_high_and_infrastructure_is_medium(self):
        registry = PriorityRegistry.load(ROOT / "config/priorities.json")
        gp = json.loads((ROOT / "profiles/gp.json").read_text())
        high_prefixes = (
            "vsconline-", "meta-api", "google-api", "mail-handler", "meta-notifier",
            "designmodo-api", "schedule-publisher-api", "openai-teacher-keys",
            "auth-gp", "password-provide", "online-compiler-keys", "canva-pro-keys",
            "ai-", "minecraft-codebuilder-", "support-class-reporting-realtime",
        )
        for unit in gp["system_services"]:
            expected = 1 if unit == "apache2.service" or unit.startswith(high_prefixes) else 2
            self.assertEqual(registry.display(f"service:{unit}", "not active").priority, expected, unit)

    def test_daily_and_frequent_jobs_have_distinct_priorities(self):
        registry = PriorityRegistry.load(ROOT / "config/priorities.json")
        cases = {
            "user-timer:vps-restic-backup.timer": 3,
            "daily-success:password-reset-primary": 3,
            "system-timer:meta-api-daily-sync.timer": 3,
            "system-timer:ai-imaging-probe.timer": 2,
            "system-timer:openai-key-monitor.timer": 2,
            "user-timer:trustpilot-notifier-process.timer": 2,
        }
        for key, expected in cases.items():
            self.assertEqual(registry.display(key, "last run failed").priority, expected, key)

    def test_backup_and_mail_archive_jobs_have_unambiguous_names(self):
        registry = PriorityRegistry.load(ROOT / "config/priorities.json")
        cases = {
            "system-service:devbox-system-backup.service:result": "Backup Devbox",
            "system-service:devbox-system-backup-retention.service:result": "Retencja backupu Devbox",
            "system-service:devbox-system-backup-check.service:result": "Weryfikacja backupu Devbox",
            "system-service:devbox-system-backup-data-check.service:result": "Kontrola danych backupu Devbox",
            "system-service:devbox-system-backup-restore-test.service:result": "Test odtwarzania backupu Devbox",
            "user-service:vps-restic-backup.service:result": "Backup GP",
            "user-service:vps-restic-check.service:result": "Weryfikacja backupu GP",
            "user-service:vps-restic-retention.service:result": "Retencja backupu GP",
            "user-service:vps-restic-data-check.service:result": "Kontrola danych backupu GP",
            "user-service:vps-restic-gp-mail-purge.service:result": "Archiwizacja poczty GP",
        }
        names = []
        for key, expected_name in cases.items():
            display = registry.display(key, "last run failed")
            self.assertEqual(display.priority, 3, key)
            self.assertEqual(display.name, expected_name, key)
            names.append(display.name)
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
