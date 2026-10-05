from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from system_monitor.adapters import devbox, gp
from system_monitor.notifications import NotificationEngine, PriorityRegistry


ROOT = Path(__file__).parents[1]


def completed(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


class Sender:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    def send(self, priority: int, text: str) -> None:
        self.calls.append((priority, text))


class LocalSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.now = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc).timestamp()

    def write_json(self, path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def base_check(self, name: str) -> dict:
        state = self.root / name
        for directory in ("holds", "activities", "leases"):
            (state / directory).mkdir(parents=True, exist_ok=True)
        check = {
            "name": name,
            "incident_key": (
                "external-heartbeat:online-compiler-production-schedule"
                if name == "production"
                else "external-heartbeat:online-compiler-next-dev-hibernation"
            ),
            "timer_unit": f"online-compiler-{name}.timer",
            "service_unit": f"online-compiler-{name}.service",
            "schedule_path": str(state / "schedule.json"),
            "health_path": str(state / "scheduler-health.json"),
            "holds_path": str(state / "holds"),
            "freshness_warning_seconds": 180 if name == "production" else 1200,
            "freshness_critical_seconds": 300 if name == "production" else 2100,
        }
        self.write_json(
            state / "schedule.json",
            {"schemaVersion": 1, "status": "CLOSED", "updatedAt": "2026-10-05T10:00:00Z"},
        )
        self.write_json(
            state / "scheduler-health.json",
            {
                "schemaVersion": 1,
                "status": "HEALTHY",
                "reasonCode": "NONE",
                "updatedAt": "2026-10-05T10:00:00Z",
            },
        )
        if name == "next-dev":
            check.update(
                {
                    "power_state_path": str(state / "state.json"),
                    "activities_path": str(state / "activities"),
                    "leases_path": str(state / "leases"),
                    "idle_threshold_seconds": 3600,
                    "idle_grace_seconds": 900,
                }
            )
            self.write_json(
                state / "state.json",
                {
                    "schemaVersion": 1,
                    "mode": "HIBERNATED",
                    "updatedAt": "2026-10-05T10:00:00Z",
                    "lastActivityAt": "2026-10-05T08:00:00Z",
                    "hibernation": None,
                },
            )
        return check

    @staticmethod
    def healthy_systemd(command, timeout=15):
        if "is-active" in command:
            return completed(command, stdout="active\n")
        if "is-enabled" in command:
            return completed(command, stdout="enabled\n")
        return completed(
            command,
            stdout="ActiveState=inactive\nResult=success\nExecMainStatus=0\n",
        )

    def monitor(self, check: dict) -> gp.Monitor:
        monitor = gp.Monitor(
            {
                "state_file": str(self.root / "monitor-state.json"),
                "online_compiler_schedulers": [check],
            },
            notify=False,
        )
        monitor.now = self.now
        monitor.run_command = self.healthy_systemd
        return monitor

    def test_production_reports_environment_scheduler_and_freshness_independently(self):
        monitor = self.monitor(self.base_check("production"))
        monitor.check_online_compiler_schedulers()
        results = {result.key: result for result in monitor.results}
        self.assertEqual(results["online-compiler-environment:production"].severity, "ok")
        self.assertEqual(
            results["external-heartbeat:online-compiler-production-schedule"].severity,
            "ok",
        )
        self.assertEqual(results["online-compiler-scheduler:production:freshness"].severity, "ok")

    def test_stale_state_has_a_separate_key_without_changing_scheduler_health(self):
        check = self.base_check("production")
        health = Path(check["health_path"])
        value = json.loads(health.read_text(encoding="utf-8"))
        value["updatedAt"] = "2026-10-05T09:50:00Z"
        self.write_json(health, value)
        monitor = self.monitor(check)
        monitor.check_online_compiler_schedulers()
        results = {result.key: result for result in monitor.results}
        stale = results["online-compiler-scheduler:production:freshness"]
        self.assertEqual(stale.severity, "critical")
        self.assertEqual(stale.notification_code, "ONLINE_COMPILER_STATE_STALE")
        self.assertEqual(
            results["external-heartbeat:online-compiler-production-schedule"].severity,
            "ok",
        )

    def test_next_dev_blocked_preserves_legacy_key_and_safe_context(self):
        check = self.base_check("next-dev")
        self.write_json(Path(check["holds_path"]) / "hold.json", {"schemaVersion": 1})
        monitor = self.monitor(check)
        monitor.check_online_compiler_schedulers()
        result = next(
            item for item in monitor.results
            if item.key == "external-heartbeat:online-compiler-next-dev-hibernation"
        )
        self.assertEqual(result.severity, "critical")
        self.assertEqual(result.notification_code, "ONLINE_COMPILER_SCHEDULER_BLOCKED")
        self.assertEqual(
            result.notification_context,
            {"environmentStatus": "HIBERNATED", "holdCount": 1},
        )

    def test_active_next_dev_over_idle_deadline_has_a_separate_key(self):
        check = self.base_check("next-dev")
        self.write_json(
            Path(check["schedule_path"]),
            {"schemaVersion": 1, "status": "OPEN", "updatedAt": "2026-10-05T10:00:00Z"},
        )
        power_path = Path(check["power_state_path"])
        power = json.loads(power_path.read_text(encoding="utf-8"))
        power.update(mode="ACTIVE", lastActivityAt="2026-10-05T08:00:00Z")
        self.write_json(power_path, power)
        monitor = self.monitor(check)
        monitor.check_online_compiler_schedulers()
        result = next(
            item for item in monitor.results
            if item.key == "online-compiler-scheduler:next-dev:idle-overdue"
        )
        self.assertEqual(result.severity, "critical")
        self.assertEqual(result.notification_code, "ONLINE_COMPILER_IDLE_OVERDUE")
        self.assertEqual(result.notification_context["idleThresholdSeconds"], 3600)

    def test_consistent_next_dev_transition_is_not_an_environment_failure(self):
        check = self.base_check("next-dev")
        self.write_json(
            Path(check["schedule_path"]),
            {"schemaVersion": 1, "status": "STOPPING", "updatedAt": "2026-10-05T10:00:00Z"},
        )
        power_path = Path(check["power_state_path"])
        power = json.loads(power_path.read_text(encoding="utf-8"))
        power["mode"] = "HIBERNATING"
        self.write_json(power_path, power)
        monitor = self.monitor(check)
        monitor.check_online_compiler_schedulers()
        result = next(
            item for item in monitor.results
            if item.key == "online-compiler-environment:next-dev"
        )
        self.assertEqual(result.severity, "ok")
        self.assertIn("STOPPING", result.summary)


class DeadManTests(unittest.TestCase):
    def monitor(self) -> devbox.Monitor:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        monitor = devbox.Monitor(
            {
                "state_file": str(Path(temporary.name) / "state.json"),
                "gp_dead_man": {
                    "host": "gp",
                    "ssh_config": "/home/miket/.ssh/config",
                    "max_age_seconds": 900,
                },
            },
            notify=False,
        )
        monitor.now = 1_800_000_000
        return monitor

    def test_dead_man_uses_one_fixed_read_only_ssh_command(self):
        monitor = self.monitor()
        calls = []

        def run(command, timeout=15):
            calls.append((command, timeout))
            return completed(
                command,
                stdout="Result=success\nExecMainStatus=0\nExecMainExitTimestamp=@1799999700\n",
            )

        monitor.run_command = run
        monitor.check_gp_dead_man()
        self.assertEqual(monitor.results[-1].severity, "ok")
        self.assertEqual(monitor.results[-1].notification_class, "network")
        self.assertEqual(calls[0][0][-13:], [
            "gp", "/usr/bin/env", "LC_ALL=C", "TZ=UTC", "/usr/bin/systemctl",
            "show", "gp-monitor.service", "--property=Result",
            "--property=ExecMainStatus", "--property=ExecMainExitTimestamp",
            "--property=ActiveState", "--property=ExecMainStartTimestamp", "--no-pager",
        ])

    def test_running_gp_cycle_is_fresh_dead_man_evidence(self):
        monitor = self.monitor()
        monitor.run_command = lambda command, timeout=15: completed(
            command,
            stdout=(
                "Result=success\nExecMainStatus=0\nExecMainExitTimestamp=\n"
                "ActiveState=activating\nExecMainStartTimestamp=@1799999998\n"
            ),
        )
        monitor.check_gp_dead_man()
        self.assertEqual(monitor.results[-1].severity, "ok")

    def test_systemd_utc_timestamp_is_not_reinterpreted_as_local_time(self):
        expected = datetime(2026, 10, 5, 12, 50, 17, tzinfo=timezone.utc).timestamp()
        self.assertEqual(
            devbox.timestamp_epoch("Mon 2026-10-05 12:50:17 UTC"), expected,
        )

    def test_dead_man_failure_requires_three_cycles_before_new_alert(self):
        monitor = self.monitor()
        monitor.run_command = lambda command, timeout=15: completed(command, 255)
        monitor.check_gp_dead_man()
        result = monitor.results[-1]
        self.assertEqual(result.notification_code, "GP_MONITOR_DEAD_MAN")
        registry = PriorityRegistry.load(ROOT / "config/priorities.json")
        sender = Sender()
        state: dict = {}
        for cycle in range(3):
            engine = NotificationEngine(
                registry, host="devbox", now=1_800_000_000 + cycle * 300,
                notify=True, sender=sender,
            )
            engine.process(state, [result])
        self.assertEqual(len(sender.calls), 1)
        self.assertIn("brak świeżego potwierdzenia pracy monitora GP", sender.calls[0][1])


class SchedulerNotificationTests(unittest.TestCase):
    def test_allowlisted_blocked_context_is_formatted_without_changing_key(self):
        registry = PriorityRegistry.load(ROOT / "config/priorities.json")
        display = registry.display(
            "external-heartbeat:online-compiler-next-dev-hibernation",
            "ignored",
            "ONLINE_COMPILER_SCHEDULER_BLOCKED",
            {"environmentStatus": "HIBERNATED", "holdCount": 1},
        )
        self.assertEqual(display.priority, 2)
        self.assertEqual(
            display.problem,
            "scheduler zablokowany; HOLD=1; ostatni stan środowiska: HIBERNATED",
        )

    def test_unknown_or_unsafe_context_is_rejected(self):
        registry = PriorityRegistry.load(ROOT / "config/priorities.json")
        with self.assertRaises(ValueError):
            registry.display("key", "summary", "PRIVATE_PROVIDER_TEXT", {})
        with self.assertRaises(ValueError):
            registry.display(
                "key", "summary", "ONLINE_COMPILER_SCHEDULER_BLOCKED",
                {"environmentStatus": "provider response", "holdCount": 1},
            )

    def test_existing_active_incident_does_not_replay_new(self):
        registry = PriorityRegistry.load(ROOT / "config/priorities.json")
        sender = Sender()
        now = 1_800_000_000
        key = "external-heartbeat:online-compiler-next-dev-hibernation"
        state = {
            "checks": {
                key: {
                    "severity": "critical", "alert_active": True,
                    "last_notification": now, "priority": 2,
                    "name": "Scheduler Online Compiler",
                    "problem": "poprzedni alarm", "resolved": "przywrócono",
                }
            },
            "pending_notifications": {},
            "notification_state_version": 3,
        }
        result = gp.Result(
            key, "critical", "Online Compiler scheduler is degraded",
            notification_class="durable",
            notification_code="ONLINE_COMPILER_SCHEDULER_BLOCKED",
            notification_context={"environmentStatus": "HIBERNATED", "holdCount": 1},
        )
        NotificationEngine(
            registry, host="gp", now=now + 300, notify=True, sender=sender,
        ).process(state, [result])
        self.assertEqual(sender.calls, [])
        self.assertTrue(state["checks"][key]["alert_active"])


class ProfileContractTests(unittest.TestCase):
    def test_gp_uses_local_scheduler_state_and_preserves_incident_keys(self):
        profile = json.loads((ROOT / "profiles/gp.json").read_text(encoding="utf-8"))
        self.assertNotIn("external_json_heartbeats", profile)
        checks = {item["name"]: item for item in profile["online_compiler_schedulers"]}
        self.assertEqual(
            checks["production"]["incident_key"],
            "external-heartbeat:online-compiler-production-schedule",
        )
        self.assertEqual(
            checks["next-dev"]["incident_key"],
            "external-heartbeat:online-compiler-next-dev-hibernation",
        )
        for check in checks.values():
            self.assertTrue(check["schedule_path"].startswith("/var/lib/online-compiler-scheduler/"))

    def test_devbox_dead_man_profile_is_bounded(self):
        profile = json.loads((ROOT / "profiles/devbox.json").read_text(encoding="utf-8"))
        self.assertEqual(
            profile["gp_dead_man"],
            {
                "host": "gp",
                "ssh_config": "/home/miket/.ssh/config",
                "max_age_seconds": 900,
            },
        )


if __name__ == "__main__":
    unittest.main()
