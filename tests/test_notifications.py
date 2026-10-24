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
    def __init__(self, fail_priority=None, fail_calls=None):
        self.calls = []
        self.fail_priority = fail_priority
        self.fail_calls = set(fail_calls or [])
        self.attempts = 0

    def send(self, priority, text):
        self.attempts += 1
        if priority == self.fail_priority or self.attempts in self.fail_calls:
            raise RuntimeError("delivery failed")
        self.calls.append((priority, text))


def result(
    key,
    severity,
    summary="unhealthy",
    incident_id="",
    notification_class="sampled",
    notification_code="",
    notification_context=None,
):
    return SimpleNamespace(
        key=key,
        severity=severity,
        summary=summary,
        incident_id=incident_id,
        notification_class=notification_class,
        notification_code=notification_code,
        notification_context=notification_context,
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

    def test_threshold_and_unit_state_messages_include_safe_details(self):
        registry = PriorityRegistry.load(ROOT / "config/priorities.json")

        load = registry.display(
            "load:5m",
            "5-minute load: 27.42 on 8 CPUs",
            "LOAD_THRESHOLD_EXCEEDED",
            {
                "observedLoad": "27.42",
                "thresholdLoad": "24.00",
                "cpuCount": 8,
            },
        )
        service = registry.display(
            "user-service:gnome-remote-desktop.service",
            "gnome-remote-desktop.service is not healthy",
            "SYSTEMD_UNIT_STATE_INVALID",
            {"activeState": "failed", "failureState": "failed"},
        )

        self.assertEqual(
            load.problem,
            "5-minutowe obciążenie wynosi 27.42; przekroczony próg: 24.00 (8 CPU)",
        )
        self.assertEqual(
            service.problem,
            "stan aktywności: failed; wynik kontroli błędu: failed; wymagany stan: active",
        )

    def test_threshold_context_rejects_untrusted_decimal_text(self):
        registry = PriorityRegistry.load(ROOT / "config/priorities.json")

        with self.assertRaises(ValueError):
            registry.display(
                "load:5m",
                "unhealthy",
                "LOAD_THRESHOLD_EXCEEDED",
                {
                    "observedLoad": "27.42; token",
                    "thresholdLoad": "24.00",
                    "cpuCount": 8,
                },
            )


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

    def test_load_notification_names_observed_value_and_crossed_threshold(self):
        failure = result(
            "load:5m",
            "critical",
            "5-minute load: 27.42 on 8 CPUs",
            notification_code="LOAD_THRESHOLD_EXCEEDED",
            notification_context={
                "observedLoad": "27.42",
                "thresholdLoad": "24.00",
                "cpuCount": 8,
            },
        )

        self.cycle(failure)
        self.cycle(failure)

        self.assertEqual(
            self.sender.calls[0][1],
            "[NEW]\nObciążenie maszyny : 5-minutowe obciążenie wynosi 27.42; "
            "przekroczony próg: 24.00 (8 CPU) : gp",
        )

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

    def test_profile_policy_controls_confirmation_reminder_and_recovery(self):
        key = "filesystem:space:/"
        failure = result(key, "critical", "Disk usage 96%")
        healthy = result(key, "ok", "healthy")
        engine = NotificationEngine(
            self.registry,
            host="gp",
            now=self.now,
            notify=True,
            sender=self.sender,
            default_failure_confirmation_cycles=1,
            recovery_confirmation_cycles=1,
            medium_reminder_seconds=60,
            suppressed_recovery_keys=[key],
        )
        engine.process(self.state, [failure])
        self.assertTrue(self.sender.calls[0][1].startswith("[NEW]\n"))

        self.sender.calls.clear()
        engine = NotificationEngine(
            self.registry,
            host="gp",
            now=self.now + 60,
            notify=True,
            sender=self.sender,
            default_failure_confirmation_cycles=1,
            recovery_confirmation_cycles=1,
            medium_reminder_seconds=60,
            suppressed_recovery_keys=[key],
        )
        engine.process(self.state, [failure])
        self.assertTrue(self.sender.calls[0][1].startswith("[REMINDER]\n"))

        self.sender.calls.clear()
        engine = NotificationEngine(
            self.registry,
            host="gp",
            now=self.now + 120,
            notify=True,
            sender=self.sender,
            recovery_confirmation_cycles=1,
            suppressed_recovery_keys=[key],
        )
        engine.process(self.state, [healthy])
        self.assertEqual(self.sender.calls, [])
        self.assertFalse(self.state["checks"][key]["alert_active"])

    def test_suppressed_prefix_does_not_queue_notifications(self):
        engine = NotificationEngine(
            self.registry,
            host="gp",
            now=self.now,
            notify=True,
            sender=self.sender,
            default_failure_confirmation_cycles=1,
            suppressed_key_prefixes=["service:internal-"],
        )
        engine.process(
            self.state,
            [result("service:internal-worker.service", "critical", "not active")],
        )
        self.assertEqual(self.sender.calls, [])
        self.assertFalse(
            self.state["checks"]["service:internal-worker.service"]["alert_active"]
        )

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

    def test_unsent_new_is_removed_when_recovery_is_confirmed(self):
        self.sender.fail_priority = 2
        key = "filesystem:space:/"
        failure = result(
            key, "warning", "Disk usage 95%",
            incident_id="disk-1", notification_class="durable",
        )
        healthy = result(
            key, "ok", "healthy",
            incident_id="disk-1", notification_class="durable",
        )
        self.cycle(failure)
        self.assertEqual(
            {event["status"] for event in self.state["pending_notifications"].values()},
            {"NEW"},
        )
        self.cycle(healthy)
        self.cycle(healthy)
        self.assertEqual(self.sender.calls, [])
        self.assertEqual(self.state["pending_notifications"], {})

    def test_existing_contradictory_pending_events_follow_current_state(self):
        key = "filesystem:space:/"
        common = {
            "key": key,
            "priority": 2,
            "name": "Dysk systemowy",
            "host": "gp",
        }
        self.state = {
            "checks": {
                key: {
                    "severity": "warning",
                    "alert_active": True,
                    "last_notification": self.now - 60,
                    "priority": 2,
                    "name": "Dysk systemowy",
                    "problem": "wykorzystanie dysku przekroczyło próg",
                    "resolved": "usługa ponownie działa prawidłowo",
                }
            },
            "pending_notifications": {
                f"2:NEW:{key}": {
                    **common,
                    "status": "NEW",
                    "problem": "wykorzystanie dysku przekroczyło próg",
                },
                f"2:RESOLVED:{key}": {
                    **common,
                    "status": "RESOLVED",
                    "problem": "usługa ponownie działa prawidłowo",
                },
            },
        }
        self.cycle(
            result(
                key, "warning", "Disk usage 95%",
                incident_id="disk-1", notification_class="durable",
            ),
            notify=False,
        )
        events = list(self.state["pending_notifications"].values())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], "NEW")

    def test_severity_increase_sends_immediate_reminder(self):
        key = "filesystem:space:/"
        self.cycle(
            result(
                key, "warning", "Disk usage 91%",
                incident_id="disk-1", notification_class="durable",
            )
        )
        self.sender.calls.clear()
        self.cycle(
            result(
                key, "critical", "Disk usage 96%",
                incident_id="disk-1", notification_class="durable",
            )
        )
        self.assertEqual(len(self.sender.calls), 1)
        self.assertTrue(self.sender.calls[0][1].startswith("[REMINDER]\n"))

    def test_unconfirmed_ok_does_not_replay_critical_reminder(self):
        key = "online-compiler-scheduler:next-dev:freshness"
        stale = result(
            key, "critical", "stale",
            incident_id="scheduler-next-dev", notification_class="durable",
            notification_code="ONLINE_COMPILER_STATE_STALE",
        )
        self.cycle(stale)
        self.sender.calls.clear()

        self.cycle(
            result(
                key, "ok", "in progress",
                incident_id="scheduler-next-dev", notification_class="durable",
            )
        )
        self.cycle(stale)

        self.assertEqual(self.sender.calls, [])
        self.assertTrue(self.state["checks"][key]["alert_active"])
        self.assertEqual(
            self.state["checks"][key]["notified_severity"], "critical"
        )

    def test_active_v3_state_migrates_without_replaying_reminder(self):
        key = "online-compiler-scheduler:next-dev:freshness"
        stale = result(
            key, "critical", "stale",
            incident_id="scheduler-next-dev", notification_class="durable",
            notification_code="ONLINE_COMPILER_STATE_STALE",
        )
        self.cycle(stale)
        self.sender.calls.clear()
        self.cycle(
            result(
                key, "ok", "in progress",
                incident_id="scheduler-next-dev", notification_class="durable",
            )
        )
        self.state["checks"][key].pop("notified_severity")
        self.state["checks"][key].pop("notified_code")

        self.cycle(stale)

        self.assertEqual(self.sender.calls, [])
        self.assertEqual(
            self.state["checks"][key]["notified_severity"], "critical"
        )

    def test_active_v3_stale_ok_baseline_migrates_without_reminder(self):
        key = "online-compiler-scheduler:next-dev:freshness"
        stale = result(
            key, "critical", "stale",
            incident_id="scheduler-next-dev", notification_class="durable",
            notification_code="ONLINE_COMPILER_STATE_STALE",
        )
        self.cycle(stale)
        self.sender.calls.clear()
        self.cycle(
            result(
                key, "ok", "in progress",
                incident_id="scheduler-next-dev", notification_class="durable",
            )
        )
        self.state["checks"][key]["notified_severity"] = "ok"
        self.state["checks"][key]["notified_code"] = ""

        self.cycle(stale)

        self.assertEqual(self.sender.calls, [])
        self.assertEqual(
            self.state["checks"][key]["notified_severity"], "critical"
        )
        self.assertEqual(
            self.state["checks"][key]["notified_code"],
            "ONLINE_COMPILER_STATE_STALE",
        )

    def test_true_escalation_survives_one_unconfirmed_ok(self):
        key = "filesystem:space:/"
        self.cycle(
            result(
                key, "warning", "Disk usage 91%",
                incident_id="disk-1", notification_class="durable",
            )
        )
        self.sender.calls.clear()
        self.cycle(
            result(
                key, "ok", "healthy",
                incident_id="disk-1", notification_class="durable",
            )
        )
        self.cycle(
            result(
                key, "critical", "Disk usage 96%",
                incident_id="disk-1", notification_class="durable",
            )
        )

        self.assertEqual(len(self.sender.calls), 1)
        self.assertTrue(self.sender.calls[0][1].startswith("[REMINDER]\n"))

    def test_problem_code_change_sends_update_but_dynamic_context_does_not(self):
        key = "online-compiler-scheduler:production:freshness"
        self.cycle(
            result(
                key, "warning", "slow",
                incident_id="cycle-1", notification_class="durable",
                notification_code="ONLINE_COMPILER_CYCLE_SLOW",
                notification_context={},
            )
        )
        self.sender.calls.clear()
        self.cycle(
            result(
                key, "warning", "overdue",
                incident_id="cycle-1", notification_class="durable",
                notification_code="ONLINE_COMPILER_CYCLE_OVERDUE",
                notification_context={},
            )
        )
        self.assertEqual(len(self.sender.calls), 1)
        self.assertTrue(self.sender.calls[0][1].startswith("[REMINDER]\n"))

        self.sender.calls.clear()
        idle_key = "online-compiler-scheduler:production:idle"
        self.cycle(
            result(
                idle_key, "critical", "idle",
                incident_id="idle-1", notification_class="durable",
                notification_code="ONLINE_COMPILER_IDLE_OVERDUE",
                notification_context={
                    "idleSeconds": 1000,
                    "idleThresholdSeconds": 900,
                },
            )
        )
        self.sender.calls.clear()
        self.cycle(
            result(
                idle_key, "critical", "idle",
                incident_id="idle-1", notification_class="durable",
                notification_code="ONLINE_COMPILER_IDLE_OVERDUE",
                notification_context={
                    "idleSeconds": 1300,
                    "idleThresholdSeconds": 900,
                },
            )
        )
        self.assertEqual(self.sender.calls, [])

    def test_immediate_messages_honor_item_and_telegram_limits(self):
        pending = {}
        for index in range(20):
            key = f"custom:{index}"
            event = {
                "key": key,
                "priority": 1,
                "status": "NEW",
                "name": "N" * 180,
                "problem": "P" * 180,
                "host": "gp",
            }
            pending[f"1:NEW:{key}"] = event
        state = {"checks": {}, "pending_notifications": pending}
        NotificationEngine(
            self.registry,
            host="gp",
            now=self.now,
            notify=True,
            sender=self.sender,
            max_items_per_message=20,
        ).process(state, [])
        self.assertGreater(len(self.sender.calls), 1)
        for _, text in self.sender.calls:
            self.assertLessEqual(len(text), 4096)
            self.assertLessEqual(len(text.splitlines()) - 1, 20)
        self.assertEqual(state["pending_notifications"], {})

    def test_failed_message_chunk_retries_only_that_chunk(self):
        sender = RecordingSender(fail_calls={2})
        pending = {}
        for index in range(5):
            key = f"custom:{index}"
            pending[f"1:NEW:{key}"] = {
                "key": key,
                "priority": 1,
                "status": "NEW",
                "name": f"Kontrola {index}",
                "problem": "wykryto nieprawidłowy stan",
                "host": "gp",
            }
        state = {"checks": {}, "pending_notifications": pending}
        NotificationEngine(
            self.registry,
            host="gp",
            now=self.now,
            notify=True,
            sender=sender,
            max_items_per_message=2,
        ).process(state, [])
        self.assertEqual(len(sender.calls), 2)
        self.assertEqual(len(state["pending_notifications"]), 2)

        NotificationEngine(
            self.registry,
            host="gp",
            now=self.now + 300,
            notify=True,
            sender=sender,
            max_items_per_message=2,
        ).process(state, [])
        self.assertEqual(len(sender.calls), 3)
        self.assertEqual(state["pending_notifications"], {})

    def test_failed_low_digest_chunk_does_not_repeat_sent_chunks(self):
        sender = RecordingSender(fail_calls={2})
        pending = {}
        for index in range(5):
            key = f"daily-success:custom-{index}"
            pending[f"3:NEW:{key}"] = {
                "key": key,
                "priority": 3,
                "status": "NEW",
                "name": f"Kontrola {index}",
                "problem": "ostatni przebieg zakończył się niepowodzeniem",
                "host": "gp",
            }
        state = {"checks": {}, "pending_notifications": pending}
        NotificationEngine(
            self.registry,
            host="gp",
            now=self.now,
            notify=True,
            sender=sender,
            max_items_per_message=2,
        ).process(state, [])
        self.assertEqual(len(sender.calls), 2)
        self.assertEqual(len(state["pending_notifications"]), 2)
        self.assertNotIn("last_low_digest_date", state)

        NotificationEngine(
            self.registry,
            host="gp",
            now=self.now + 300,
            notify=True,
            sender=sender,
            max_items_per_message=2,
        ).process(state, [])
        self.assertEqual(len(sender.calls), 3)
        self.assertEqual(state["pending_notifications"], {})
        self.assertEqual(state["last_low_digest_date"], "2026-10-04")
        delivered = "\n".join(text for _, text in sender.calls)
        for index in range(5):
            self.assertEqual(delivered.count(f"Kontrola {index} :"), 1)

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

    def test_password_reset_uses_one_group_lifecycle(self):
        keys = [
            "daily-success:password-reset-canva1",
            "daily-success:password-reset-canva2",
            "daily-success:password-reset-canva3",
        ]

        def failed(key):
            return result(
                key,
                "critical",
                "last run failed",
                incident_id=f"{key}-failed",
                notification_class="durable",
            )

        def healthy(key):
            return result(
                key,
                "ok",
                "healthy",
                incident_id=f"{key}-healthy",
                notification_class="durable",
            )

        def process(items):
            NotificationEngine(
                self.registry,
                host="devbox",
                now=self.now,
                notify=True,
                sender=self.sender,
            ).process(self.state, items)
            self.now += 300

        process([failed(key) for key in keys])
        self.assertEqual(len(self.sender.calls), 1)
        self.assertEqual(self.sender.calls[0][1].count("Password Reset :"), 1)
        self.assertNotIn("canva", self.sender.calls[0][1].lower())
        self.assertTrue(
            self.state["notification_groups"]["password-reset"]["alert_active"]
        )

        self.sender.calls.clear()
        self.now = datetime(
            2026, 10, 5, 10, 0, tzinfo=ZoneInfo("Europe/Warsaw")
        ).timestamp()
        process([failed(key) for key in keys])
        self.assertEqual(len(self.sender.calls), 1)
        self.assertTrue(self.sender.calls[0][1].startswith("[REMINDER]\n"))
        self.assertEqual(self.sender.calls[0][1].count("Password Reset :"), 1)

        self.sender.calls.clear()
        process([healthy(keys[0]), failed(keys[1]), failed(keys[2])])
        process([healthy(keys[0]), failed(keys[1]), failed(keys[2])])
        self.assertEqual(self.sender.calls, [])
        self.assertTrue(
            self.state["notification_groups"]["password-reset"]["alert_active"]
        )
        self.assertFalse(
            any(
                event.get("status") == "RESOLVED"
                for event in self.state.get("pending_notifications", {}).values()
            )
        )

        process([healthy(key) for key in keys])
        process([healthy(key) for key in keys])
        grouped_pending = list(self.state.get("pending_notifications", {}).values())
        self.assertEqual(len(grouped_pending), 1)
        self.assertEqual(grouped_pending[0]["key"], "notification-group:password-reset")
        self.assertEqual(grouped_pending[0]["status"], "RESOLVED")

        self.now = datetime(
            2026, 10, 6, 10, 0, tzinfo=ZoneInfo("Europe/Warsaw")
        ).timestamp()
        process([healthy(key) for key in keys])
        self.assertEqual(len(self.sender.calls), 1)
        self.assertTrue(self.sender.calls[0][1].startswith("[RESOLVED]\n"))
        self.assertEqual(self.sender.calls[0][1].count("Password Reset :"), 1)

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
            self.assertEqual(profile["notifications"]["max_items_per_message"], 20)

    def test_password_reset_is_monitored_without_pending_contents(self):
        profile = json.loads((ROOT / "profiles/devbox.json").read_text())
        timers = {item["unit"] for item in profile["user_timers"]}
        self.assertIn("password-reset-orchestrator.timer", timers)
        checks = profile["daily_success_checks"]
        self.assertEqual(len(checks), 4)
        for check in checks:
            self.assertEqual(check["last_success_local_date_field"], "last_success_local_date")
            self.assertNotIn("pending_field", check)

    def test_lesson_materials_sync_is_owned_exclusively_by_gp(self):
        devbox = json.loads((ROOT / "profiles/devbox.json").read_text())
        gp = json.loads((ROOT / "profiles/gp.json").read_text())
        self.assertNotIn(
            "lesson-starters-sync.timer",
            {item["unit"] for item in devbox["user_timers"]},
        )
        self.assertIn(
            "lesson-materials-sync.timer",
            {item["unit"] for item in gp["system_timers"]},
        )
        registry = PriorityRegistry.load(ROOT / "config/priorities.json")
        self.assertEqual(
            registry.display("system-timer:lesson-materials-sync.timer", "late").priority,
            3,
        )

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
            "cga-auth-prod", "codinggiants-app", "scratch-cg-", "scratchjr-cg-",
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
            "system-timer:vm-manager-reconcile.timer": 2,
            "system-timer:vm-manager-inventory-sync.timer": 2,
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

    def test_password_reset_checks_share_one_notification_group(self):
        registry = PriorityRegistry.load(ROOT / "config/priorities.json")
        keys = (
            "daily-success:password-reset-primary",
            "daily-success:password-reset-canva1",
            "daily-success:password-reset-canva2",
            "daily-success:password-reset-canva3",
            "user-service:password-reset-orchestrator.service:result",
        )
        for key in keys:
            display = registry.display(key, "last run failed")
            self.assertEqual(display.priority, 3, key)
            self.assertEqual(display.name, "Password Reset", key)
            self.assertEqual(display.group, "password-reset", key)

    def test_online_compiler_signals_identify_environment_and_signal(self):
        registry = PriorityRegistry.load(ROOT / "config/priorities.json")
        cases = {
            "external-heartbeat:online-compiler-production-schedule": (
                "Scheduler Online Compiler PROD"
            ),
            "online-compiler-environment:production": (
                "Środowisko Online Compiler PROD"
            ),
            "online-compiler-scheduler:production:freshness": (
                "Stan schedulera Online Compiler PROD"
            ),
            "external-heartbeat:online-compiler-next-dev-hibernation": (
                "Scheduler Online Compiler Next Dev"
            ),
            "online-compiler-environment:next-dev": (
                "Środowisko Online Compiler Next Dev"
            ),
            "online-compiler-scheduler:next-dev:freshness": (
                "Stan schedulera Online Compiler Next Dev"
            ),
        }
        names = []
        for key, expected_name in cases.items():
            display = registry.display(key, "unhealthy")
            self.assertEqual(display.priority, 2, key)
            self.assertEqual(display.name, expected_name, key)
            names.append(display.name)
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
