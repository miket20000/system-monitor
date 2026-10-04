import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo


ROOT = Path(__file__).parents[2]
MODULE_PATH = ROOT / "src/system_monitor/adapters/devbox.py"
CONFIG_PATH = ROOT / "profiles/devbox.json"
SPEC = importlib.util.spec_from_file_location("devbox_monitor", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def completed(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


class MonitorTestCase(unittest.TestCase):
    def make_monitor(self, config=None, notify=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = {
            "state_file": str(Path(temporary.name) / "state.json"),
            "host_label": "devbox",
            "thresholds": {
                "http_warning_seconds": 5,
                "http_critical_seconds": 8,
                "http_warning_consecutive_failures": 2,
                "http_critical_consecutive_failures": 3,
            },
            "notifications": {
                "warning_cooldown_seconds": 3600,
                "critical_cooldown_seconds": 900,
            },
        }
        if config:
            base.update(config)
        return MODULE.Monitor(base, notify=notify)


class ConfigCoverageTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def test_curated_system_and_user_services(self):
        system_units = {item["unit"] for item in self.config["system_units"]}
        self.assertEqual(
            system_units,
            {
                "ssh.service",
                "NetworkManager.service",
                "systemd-resolved.service",
                "chrony.service",
                "cron.service",
                "rsyslog.service",
                "docker.service",
                "smbd.service",
                "smartmontools.service",
                "gdm.service",
                "anydesk.service",
            },
        )
        self.assertEqual(
            {item["unit"] for item in self.config["user_services"]},
            {
                "gnome-remote-desktop.service",
                "telegram-daily-reason-receiver.service",
                "trustpilot-notifier.service",
                "trustpilot-notifier-tunnel.service",
                "vps-restic-rest-server.service",
            },
        )

    def test_expected_timers_are_monitored_and_transient_timer_is_excluded(self):
        timers = {item["unit"]: item for item in self.config["user_timers"]}
        self.assertEqual(len(timers), 10)
        self.assertNotIn("online-compiler-dev-retention-audit.timer", timers)
        self.assertNotIn("scripts-windows-git-sync.timer", timers)
        self.assertNotIn("git-sync@scripts-windows.timer", timers)
        self.assertNotIn("git-sync@scripts-wsl.timer", timers)
        self.assertEqual(
            timers["password-reset-orchestrator.timer"],
            {
                "unit": "password-reset-orchestrator.timer",
                "service": "password-reset-orchestrator.service",
                "warning_hours": 1,
                "critical_hours": 2,
            },
        )
        self.assertEqual(
            timers["lesson-starters-sync.timer"],
            {
                "unit": "lesson-starters-sync.timer",
                "service": "lesson-starters-sync.service",
                "warning_hours": 18,
                "critical_hours": 26,
            },
        )
        self.assertEqual(timers["vps-restic-backup.timer"]["critical_hours"], 36)
        self.assertEqual(
            timers["socialmedia-weekly-invitations.timer"]["blocked_exit_statuses"],
            [20],
        )
        self.assertEqual(
            timers["socialmedia-weekly-invitations.timer"]["result_file"],
            "/home/miket/.local/state/socialmedia-weekly-invitations/result.json",
        )
        self.assertTrue(
            self.config["notification_overrides"][
                "user-service:socialmedia-weekly-invitations.service:result"
            ]["repeat_same_incident"]
        )
        self.assertEqual(timers["vps-restic-data-check.timer"]["warning_hours"], 840)
        self.assertEqual(timers["telegram-daily-reason.timer"]["critical_hours"], 36)
        self.assertEqual(
            timers["trustpilot-notifier-process.timer"],
            {
                "unit": "trustpilot-notifier-process.timer",
                "service": "trustpilot-notifier-process.service",
                "warning_hours": 0.08333333333333333,
                "critical_hours": 0.16666666666666666,
            },
        )

        system_timers = {item["unit"]: item for item in self.config["system_timers"]}
        self.assertEqual(
            set(system_timers),
            {
                "devbox-system-backup.timer",
                "devbox-system-backup-retention.timer",
                "devbox-system-backup-check.timer",
                "devbox-system-backup-data-check.timer",
                "devbox-system-backup-restore-test.timer",
                "minecraft-bedrock-start.timer",
                "minecraft-bedrock-stop.timer",
            },
        )
        self.assertEqual(
            system_timers["devbox-system-backup-restore-test.timer"]["critical_hours"],
            2640,
        )
        self.assertEqual(
            system_timers["minecraft-bedrock-start.timer"]["service"],
            "minecraft-bedrock.service",
        )

    def test_password_reset_daily_state_uses_only_safe_fields(self):
        checks = self.config["daily_success_checks"]
        self.assertEqual(len(checks), 4)
        self.assertEqual(
            {item["name"] for item in checks},
            {
                "password-reset-primary",
                "password-reset-canva1",
                "password-reset-canva2",
                "password-reset-canva3",
            },
        )
        for item in checks:
            self.assertEqual(item["last_success_local_date_field"], "last_success_local_date")
            self.assertEqual(item["last_result_field"], "last_result")
            self.assertEqual(item["window_end"], "09:00")

    def test_memory_alert_thresholds_exclude_swap(self):
        thresholds = self.config["thresholds"]
        self.assertEqual(thresholds["memory_warning_percent"], 90)
        self.assertEqual(thresholds["memory_critical_percent"], 95)
        self.assertNotIn("swap_warning_percent", thresholds)
        self.assertNotIn("swap_critical_percent", thresholds)

    def test_disk_space_alert_thresholds(self):
        thresholds = self.config["thresholds"]
        self.assertEqual(thresholds["disk_warning_percent"], 90)
        self.assertEqual(thresholds["disk_critical_percent"], 95)
        self.assertEqual(
            [
                MODULE.threshold_severity(
                    value,
                    thresholds["disk_warning_percent"],
                    thresholds["disk_critical_percent"],
                )
                for value in (89, 90, 95)
            ],
            ["ok", "warning", "critical"],
        )

    def test_vpn_contract_is_end_to_end_and_selective(self):
        self.assertEqual(
            self.config["vpn_checks"],
            [
                {
                    "name": "vpn-cg",
                    "unit": "vpn-cg.service",
                    "interface_pattern": "ppp*",
                    "client_subnet": "10.10.200.0/24",
                    "server": "51.89.41.33",
                    "target": "10.10.200.106",
                    "target_port": 31433,
                    "timeout_seconds": 5,
                }
            ],
        )

    def test_backup_mount_contract_is_exact(self):
        self.assertEqual(
            self.config["mount_checks"],
            [
                {
                    "path": "/mnt/backup-disk",
                    "uuid": "3ccbe9be-62aa-4522-9210-ddafc46dc4ed",
                    "fstype": "ext4",
                    "writable": True,
                },
                {
                    "path": "/mnt/devbox-system-backup",
                    "uuid": "14cf42d3-5e29-44ae-809f-8c91a5f1b3ac",
                    "fstype": "ext4",
                    "writable": True,
                },
            ],
        )

    def test_endpoints_are_safe_unauthenticated_gets(self):
        endpoints = {item["name"]: item for item in self.config["endpoints"]}
        self.assertEqual(endpoints["home-assistant-api"]["allowed_statuses"], [401])
        self.assertEqual(endpoints["vps-restic-rest-server"]["allowed_statuses"], [401])
        self.assertEqual(
            endpoints["trustpilot-notifier-health"],
            {
                "name": "trustpilot-notifier-health",
                "url": "http://127.0.0.1:8115/health",
                "allowed_statuses": [200],
                "expected_json": {
                    "ready": True,
                    "processing_enabled": True,
                    "auto_reply_enabled": True,
                },
            },
        )


class FilesystemTests(MonitorTestCase):
    def test_zero_size_fuse_mount_is_not_treated_as_physical_disk(self):
        monitor = self.make_monitor(
            {
                "thresholds": {
                    "disk_warning_percent": 80,
                    "disk_critical_percent": 90,
                    "inode_warning_percent": 80,
                    "inode_critical_percent": 90,
                }
            }
        )
        mounts = (
            "/dev/nvme0n1p2 / ext4 rw 0 0\n"
            "/dev/fuse /run/user/1000/portal fuse.portal rw 0 0\n"
        )
        with (
            patch("builtins.open", return_value=io.StringIO(mounts)),
            patch.object(MODULE.shutil, "disk_usage", return_value=SimpleNamespace(total=100, used=10)),
            patch.object(MODULE.os, "statvfs", return_value=SimpleNamespace(f_files=0, f_ffree=0)),
        ):
            monitor.check_filesystems()
        self.assertFalse(any("portal" in result.key for result in monitor.results))
        self.assertTrue(any(result.key == "filesystem:space:/" for result in monitor.results))


class TelegramTests(MonitorTestCase):
    def test_format_is_two_sanitized_lines(self):
        self.assertEqual(
            MODULE.format_telegram_notification("[OK] test\nmessage", "devbox\nignored"),
            "[OK] test message\nHost: devbox ignored",
        )

    def test_send_uses_only_summary_and_host(self):
        monitor = self.make_monitor(notify=True)

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            @staticmethod
            def read():
                return b'{"ok": true}'

        with patch.dict(
            MODULE.os.environ,
            {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_ID": "chat"},
        ), patch.object(MODULE.urllib.request, "urlopen", return_value=Response()) as urlopen:
            monitor.send("[CRITICAL] example failed")
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["text"], "[CRITICAL] example failed\nHost: devbox")

    def test_failed_invocation_is_deduplicated_but_new_invocation_alerts(self):
        monitor = self.make_monitor(notify=True)
        monitor.send = unittest.mock.Mock()
        monitor.results = [MODULE.Result("job:x", "critical", "failed", incident_id="one")]
        monitor.process_alerts()
        self.assertEqual(monitor.send.call_count, 1)

        monitor.now += 901
        monitor.results = [MODULE.Result("job:x", "critical", "failed", incident_id="one")]
        monitor.process_alerts()
        self.assertEqual(monitor.send.call_count, 1)

        monitor.results = [MODULE.Result("job:x", "critical", "failed", incident_id="two")]
        monitor.process_alerts()
        self.assertEqual(monitor.send.call_count, 2)

    def test_recovery_is_sent_once(self):
        monitor = self.make_monitor(notify=True)
        monitor.send = unittest.mock.Mock()
        monitor.results = [MODULE.Result("service:x", "critical", "failed")]
        monitor.process_alerts()
        monitor.results = [MODULE.Result("service:x", "ok", "healthy")]
        monitor.process_alerts()
        monitor.process_alerts()
        self.assertEqual(monitor.send.call_count, 2)

    def test_per_key_notification_cooldown_override_is_used(self):
        monitor = self.make_monitor(
            {
                "notification_overrides": {
                    "job:x": {
                        "warning_cooldown_seconds": 21600,
                        "repeat_same_incident": True,
                    }
                }
            },
            notify=True,
        )
        monitor.send = unittest.mock.Mock()
        monitor.results = [
            MODULE.Result("job:x", "warning", "blocked", incident_id="same")
        ]
        monitor.process_alerts()
        monitor.now += 3601
        monitor.process_alerts()
        self.assertEqual(monitor.send.call_count, 1)
        monitor.now += 18000
        monitor.process_alerts()
        self.assertEqual(monitor.send.call_count, 2)


class MountTests(MonitorTestCase):
    def test_expected_uuid_filesystem_and_rw_are_healthy(self):
        monitor = self.make_monitor(
            {
                "mount_checks": [
                    {"path": "/mnt/backup-disk", "uuid": "uuid", "fstype": "ext4", "writable": True}
                ]
            }
        )
        payload = {
            "filesystems": [
                {
                    "source": "/dev/sda1",
                    "target": "/mnt/backup-disk",
                    "fstype": "ext4",
                    "options": "rw,noexec",
                    "uuid": "uuid",
                }
            ]
        }
        monitor.run_command = lambda command, timeout=15: completed(
            command, stdout=json.dumps(payload)
        )
        monitor.check_mounts()
        self.assertEqual(monitor.results[0].severity, "ok")

    def test_wrong_uuid_is_critical(self):
        monitor = self.make_monitor(
            {
                "mount_checks": [
                    {"path": "/mnt/backup-disk", "uuid": "expected", "fstype": "ext4", "writable": True}
                ]
            }
        )
        payload = {
            "filesystems": [
                {
                    "target": "/mnt/backup-disk",
                    "fstype": "ext4",
                    "options": "rw",
                    "uuid": "wrong",
                }
            ]
        }
        monitor.run_command = lambda command, timeout=15: completed(
            command, stdout=json.dumps(payload)
        )
        monitor.check_mounts()
        self.assertEqual(monitor.results[0].severity, "critical")
        self.assertIn("uuid=wrong", monitor.results[0].details)


class MemoryTests(MonitorTestCase):
    def test_ram_below_90_percent_is_healthy_and_swap_is_not_reported(self):
        monitor = self.make_monitor(
            {
                "thresholds": {
                    **self.make_monitor().config["thresholds"],
                    "memory_warning_percent": 90,
                    "memory_critical_percent": 95,
                }
            }
        )
        meminfo = """MemTotal: 1000 kB
MemAvailable: 110 kB
SwapTotal: 1000 kB
SwapFree: 0 kB
"""
        with patch("builtins.open", unittest.mock.mock_open(read_data=meminfo)):
            monitor.check_memory()
        self.assertEqual(
            [(result.key, result.severity) for result in monitor.results],
            [("memory:used", "ok")],
        )

    def test_ram_at_90_percent_is_warning(self):
        monitor = self.make_monitor(
            {
                "thresholds": {
                    **self.make_monitor().config["thresholds"],
                    "memory_warning_percent": 90,
                    "memory_critical_percent": 95,
                }
            }
        )
        meminfo = """MemTotal: 1000 kB
MemAvailable: 100 kB
SwapTotal: 1000 kB
SwapFree: 0 kB
"""
        with patch("builtins.open", unittest.mock.mock_open(read_data=meminfo)):
            monitor.check_memory()
        self.assertEqual(
            [(result.key, result.severity) for result in monitor.results],
            [("memory:used", "warning")],
        )

    def test_ram_at_95_percent_is_critical(self):
        monitor = self.make_monitor(
            {
                "thresholds": {
                    **self.make_monitor().config["thresholds"],
                    "memory_warning_percent": 90,
                    "memory_critical_percent": 95,
                }
            }
        )
        meminfo = """MemTotal: 1000 kB
MemAvailable: 50 kB
SwapTotal: 1000 kB
SwapFree: 0 kB
"""
        with patch("builtins.open", unittest.mock.mock_open(read_data=meminfo)):
            monitor.check_memory()
        self.assertEqual(
            [(result.key, result.severity) for result in monitor.results],
            [("memory:used", "critical")],
        )


class UnitTests(MonitorTestCase):
    def test_active_enabled_system_unit_is_healthy(self):
        monitor = self.make_monitor({"system_units": [{"unit": "ssh.service"}]})

        def run(command, timeout=15):
            values = {"is-active": "active\n", "is-failed": "inactive\n", "is-enabled": "enabled\n"}
            return completed(command, stdout=values[command[1]])

        monitor.run_command = run
        monitor.check_system_units()
        self.assertEqual(monitor.results[0].severity, "ok")

    def test_inactive_user_service_is_critical(self):
        monitor = self.make_monitor({"user_services": [{"unit": "example.service"}]})

        def run(command, timeout=15):
            action = command[2]
            value = "inactive\n" if action == "is-active" else "inactive\n"
            return completed(command, stdout=value)

        monitor.run_command = run
        monitor.check_user_services()
        self.assertEqual(monitor.results[0].severity, "critical")


class VpnTests(MonitorTestCase):
    def vpn_monitor(self):
        return self.make_monitor(
            {
                "vpn_checks": [
                    {
                        "name": "vpn-cg",
                        "unit": "vpn-cg.service",
                        "interface_pattern": "ppp*",
                        "client_subnet": "10.10.200.0/24",
                        "server": "51.89.41.33",
                        "target": "10.10.200.106",
                        "target_port": 31433,
                        "timeout_seconds": 5,
                    }
                ]
            }
        )

    @staticmethod
    def healthy_run(command, timeout=15):
        if command[:2] == ["systemctl", "is-active"]:
            return completed(command, stdout="active\n")
        if command[:2] == ["systemctl", "is-enabled"]:
            return completed(command, stdout="enabled\n")
        if command[:5] == ["ip", "-json", "route", "get", "10.10.200.106"]:
            return completed(
                command,
                stdout=json.dumps(
                    [{"dst": "10.10.200.106", "dev": "ppp0", "prefsrc": "10.10.200.202"}]
                ),
            )
        if command[:5] == ["ip", "-json", "route", "get", "51.89.41.33"]:
            return completed(
                command,
                stdout=json.dumps(
                    [{"dst": "51.89.41.33", "dev": "enp0s31f6", "gateway": "192.168.50.1"}]
                ),
            )
        if command == ["ip", "-json", "route", "show", "default"]:
            return completed(command, stdout=json.dumps([{"dst": "default", "dev": "enp0s31f6"}]))
        if command == ["ip", "-json", "address", "show", "dev", "ppp0"]:
            return completed(
                command,
                stdout=json.dumps(
                    [{"ifname": "ppp0", "addr_info": [{"family": "inet", "local": "10.10.200.202"}]}]
                ),
            )
        return completed(command, returncode=1, stderr="unexpected command")

    def test_healthy_vpn_requires_service_routes_ppp_address_and_tcp(self):
        monitor = self.vpn_monitor()
        monitor.run_command = self.healthy_run
        connection = unittest.mock.Mock()
        with patch.object(MODULE.socket, "create_connection", return_value=connection) as connect:
            monitor.check_vpns()
        self.assertEqual(monitor.results[0].severity, "ok")
        self.assertIn("healthy via ppp0", monitor.results[0].summary)
        connect.assert_called_once_with(("10.10.200.106", 31433), timeout=5)
        connection.close.assert_called_once_with()

    def test_inactive_service_is_critical(self):
        monitor = self.vpn_monitor()

        def run(command, timeout=15):
            result = self.healthy_run(command, timeout)
            if command[:2] == ["systemctl", "is-active"]:
                result.stdout = "inactive\n"
            return result

        monitor.run_command = run
        with patch.object(MODULE.socket, "create_connection") as connect:
            monitor.check_vpns()
        self.assertEqual(monitor.results[0].severity, "critical")
        self.assertIn("not active", monitor.results[0].details)
        connect.assert_not_called()

    def test_non_vpn_target_route_is_critical_without_tcp_probe(self):
        monitor = self.vpn_monitor()

        def run(command, timeout=15):
            if command[:5] == ["ip", "-json", "route", "get", "10.10.200.106"]:
                return completed(
                    command,
                    stdout=json.dumps([{"dst": "10.10.200.106", "dev": "enp0s31f6"}]),
                )
            return self.healthy_run(command, timeout)

        monitor.run_command = run
        with patch.object(MODULE.socket, "create_connection") as connect:
            monitor.check_vpns()
        self.assertEqual(monitor.results[0].severity, "critical")
        self.assertIn("routed through enp0s31f6", monitor.results[0].details)
        connect.assert_not_called()

    def test_server_or_default_route_through_vpn_is_critical(self):
        monitor = self.vpn_monitor()

        def run(command, timeout=15):
            if command[:5] == ["ip", "-json", "route", "get", "51.89.41.33"]:
                return completed(
                    command,
                    stdout=json.dumps([{"dst": "51.89.41.33", "dev": "ppp0"}]),
                )
            if command == ["ip", "-json", "route", "show", "default"]:
                return completed(command, stdout=json.dumps([{"dst": "default", "dev": "ppp0"}]))
            return self.healthy_run(command, timeout)

        monitor.run_command = run
        with patch.object(MODULE.socket, "create_connection") as connect:
            monitor.check_vpns()
        self.assertEqual(monitor.results[0].severity, "critical")
        self.assertIn("VPN server 51.89.41.33 is routed through VPN", monitor.results[0].details)
        self.assertIn("default route uses the VPN interface", monitor.results[0].details)
        connect.assert_not_called()

    def test_tcp_failure_is_critical_without_exposing_credentials(self):
        monitor = self.vpn_monitor()
        monitor.run_command = self.healthy_run
        with patch.object(MODULE.socket, "create_connection", side_effect=TimeoutError("timed out")):
            monitor.check_vpns()
        self.assertEqual(monitor.results[0].severity, "critical")
        self.assertEqual(
            monitor.results[0].details,
            "TCP 10.10.200.106:31433 failed: timed out",
        )


class UserTimerTests(MonitorTestCase):
    def timer_monitor(self, first_success_due=""):
        item = {
            "unit": "job.timer",
            "service": "job.service",
            "warning_hours": 1,
            "critical_hours": 2,
        }
        if first_success_due:
            item["first_success_due"] = first_success_due
        return self.make_monitor({"user_timers": [item]})

    @staticmethod
    def base_run(command, timeout=15):
        if "is-active" in command:
            return completed(command, stdout="active\n")
        if "is-enabled" in command:
            return completed(command, stdout="enabled\n")
        if "show" in command:
            return completed(
                command,
                stdout="Result=success\nExecMainStatus=0\nActiveState=inactive\nSubState=dead\nInvocationID=abc\n",
            )
        return completed(command)

    def test_running_service_is_healthy(self):
        monitor = self.timer_monitor()

        def run(command, timeout=15):
            result = self.base_run(command, timeout)
            if "show" in command:
                result.stdout += "ActiveState=active\n"
            return result

        monitor.run_command = run
        monitor.check_user_timers()
        self.assertEqual(monitor.results[-1].severity, "ok")
        self.assertIn("currently running", monitor.results[-1].summary)

    def test_failed_service_is_critical_with_invocation_id(self):
        monitor = self.timer_monitor()

        def run(command, timeout=15):
            result = self.base_run(command, timeout)
            if "show" in command:
                result.stdout = "Result=exit-code\nExecMainStatus=3\nActiveState=inactive\nInvocationID=failed-id\n"
            return result

        monitor.run_command = run
        monitor.check_user_timers()
        self.assertEqual(monitor.results[-1].severity, "critical")
        self.assertEqual(monitor.results[-1].incident_id, "failed-id")
        self.assertEqual(
            monitor.state["timer_services"]["user-service:job.service:result"]["status"],
            "failed",
        )

    def test_configured_blocked_exit_status_is_warning(self):
        monitor = self.timer_monitor()
        monitor.config["user_timers"][0]["blocked_exit_statuses"] = [20]

        def run(command, timeout=15):
            if "show" in command:
                return completed(
                    command,
                    stdout=(
                        "Result=exit-code\nExecMainStatus=20\nActiveState=inactive\n"
                        "InvocationID=blocked-id\n"
                    ),
                )
            return self.base_run(command, timeout)

        monitor.run_command = run
        monitor.check_user_timers()
        self.assertEqual(monitor.results[-1].severity, "warning")
        self.assertIn("was blocked", monitor.results[-1].summary)
        self.assertEqual(
            monitor.state["timer_services"]["user-service:job.service:result"]["status"],
            "blocked",
        )

    def test_cached_failure_survives_missing_post_reboot_properties(self):
        monitor = self.timer_monitor()
        monitor.state["timer_services"] = {
            "user-service:job.service:result": {
                "status": "failed",
                "observed_at": monitor.now - 60,
                "incident_id": "failed-before-reboot",
                "result": "exit-code",
                "exit": "1",
            }
        }
        monitor.run_command = self.base_run
        monitor.check_user_timers()
        self.assertEqual(monitor.results[-1].severity, "critical")
        self.assertIn("last known run failed", monitor.results[-1].summary)
        self.assertEqual(monitor.results[-1].incident_id, "failed-before-reboot")

    def test_older_journal_success_does_not_clear_cached_failure(self):
        monitor = self.timer_monitor()
        monitor.now = 1_800_000_000
        monitor.state["timer_services"] = {
            "user-service:job.service:result": {
                "status": "failed",
                "observed_at": monitor.now - 60,
                "incident_id": "newer-failure",
                "result": "exit-code",
                "exit": "1",
            }
        }

        def run(command, timeout=15):
            if command[0] == "journalctl":
                event = {
                    "MESSAGE": "Finished job.service - job",
                    "__REALTIME_TIMESTAMP": str(int((monitor.now - 3600) * 1_000_000)),
                }
                return completed(command, stdout=json.dumps(event) + "\n")
            return self.base_run(command, timeout)

        monitor.run_command = run
        monitor.check_user_timers()
        self.assertEqual(monitor.results[-1].severity, "critical")
        self.assertEqual(monitor.results[-1].incident_id, "newer-failure")

    def test_cached_success_survives_missing_post_reboot_timestamp(self):
        monitor = self.timer_monitor()
        monitor.now = 1_800_000_000
        monitor.state["timer_services"] = {
            "user-service:job.service:result": {
                "status": "success",
                "completed_at": monitor.now - 1800,
            }
        }
        monitor.run_command = self.base_run
        monitor.check_user_timers()
        self.assertEqual(monitor.results[-1].severity, "ok")
        self.assertIn("30m", monitor.results[-1].summary)

    def test_legacy_cache_is_migration_baseline_not_overridden_by_old_journal(self):
        monitor = self.timer_monitor()
        monitor.now = 1_800_000_000
        monitor.state["timer_services"] = {
            "user-service:job.service:result": {
                "status": "success",
                "completed_at": monitor.now - 7200,
            }
        }

        def run(command, timeout=15):
            if command[0] == "journalctl":
                event = {
                    "MESSAGE": "Failed to start job.service - job",
                    "__REALTIME_TIMESTAMP": str(int((monitor.now - 3600) * 1_000_000)),
                }
                return completed(command, stdout=json.dumps(event) + "\n")
            return self.base_run(command, timeout)

        monitor.run_command = run
        monitor.check_user_timers()
        self.assertEqual(monitor.results[-1].severity, "critical")
        self.assertIn("last completed 2.0h ago", monitor.results[-1].summary)
        self.assertEqual(
            monitor.state["timer_services"]["user-service:job.service:result"]["recorded_at"],
            monitor.now,
        )

    def test_new_journal_result_after_cache_recording_is_used(self):
        monitor = self.timer_monitor()
        monitor.now = 1_800_000_000
        monitor.state["timer_services"] = {
            "user-service:job.service:result": {
                "status": "success",
                "completed_at": monitor.now - 7200,
                "observed_at": monitor.now - 7200,
                "recorded_at": monitor.now - 120,
            }
        }

        def run(command, timeout=15):
            if command[0] == "journalctl":
                event = {
                    "MESSAGE": "Failed to start job.service - job",
                    "__REALTIME_TIMESTAMP": str(int((monitor.now - 60) * 1_000_000)),
                }
                return completed(command, stdout=json.dumps(event) + "\n")
            return self.base_run(command, timeout)

        monitor.run_command = run
        monitor.check_user_timers()
        self.assertEqual(monitor.results[-1].severity, "critical")
        self.assertIn("last known run failed", monitor.results[-1].summary)

    def test_first_run_before_deadline_is_healthy(self):
        monitor = self.timer_monitor("2099-01-01T00:00:00+00:00")
        monitor.run_command = self.base_run
        monitor.check_user_timers()
        self.assertEqual(monitor.results[-1].severity, "ok")
        self.assertIn("awaiting", monitor.results[-1].summary)

    def test_old_journal_failure_before_first_deadline_does_not_backfill(self):
        monitor = self.timer_monitor("2099-01-01T00:00:00+00:00")

        def run(command, timeout=15):
            if command[0] == "journalctl":
                event = {
                    "MESSAGE": "Failed to start job.service - job",
                    "__REALTIME_TIMESTAMP": str(int((monitor.now - 60) * 1_000_000)),
                }
                return completed(command, stdout=json.dumps(event) + "\n")
            return self.base_run(command, timeout)

        monitor.run_command = run
        monitor.check_user_timers()
        self.assertEqual(monitor.results[-1].severity, "ok")
        self.assertIn("awaiting", monitor.results[-1].summary)

    def test_first_run_after_deadline_warns(self):
        monitor = self.timer_monitor("2020-01-01T00:00:00+00:00")
        monitor.run_command = self.base_run
        monitor.check_user_timers()
        self.assertEqual(monitor.results[-1].severity, "warning")

    def test_journal_timestamp_is_used_when_systemd_timestamp_is_missing(self):
        monitor = self.timer_monitor()
        monitor.now = 1_800_000_000

        def run(command, timeout=15):
            if command[0] == "journalctl":
                event = {
                    "MESSAGE": "Finished job.service - job",
                    "__REALTIME_TIMESTAMP": str(int((monitor.now - 1800) * 1_000_000)),
                }
                return completed(command, stdout=json.dumps(event) + "\n")
            return self.base_run(command, timeout)

        monitor.run_command = run
        monitor.check_user_timers()
        self.assertEqual(monitor.results[-1].severity, "ok")
        self.assertIn("30m", monitor.results[-1].summary)
        self.assertEqual(
            monitor.state["timer_services"]["user-service:job.service:result"]["status"],
            "success",
        )

    def test_manager_only_journal_failure_survives_reboot(self):
        monitor = self.timer_monitor()
        monitor.now = 1_800_000_000

        def run(command, timeout=15):
            if command[0] == "journalctl":
                self.assertIn("_COMM=systemd", command)
                event = {
                    "MESSAGE": "Failed to start job.service - job",
                    "__REALTIME_TIMESTAMP": str(int((monitor.now - 60) * 1_000_000)),
                    "OBJECT_SYSTEMD_INVOCATION_ID": "journal-failure-id",
                }
                return completed(command, stdout=json.dumps(event) + "\n")
            return self.base_run(command, timeout)

        monitor.run_command = run
        monitor.check_user_timers()
        self.assertEqual(monitor.results[-1].severity, "critical")
        self.assertIn("last known run failed", monitor.results[-1].summary)
        self.assertEqual(monitor.results[-1].incident_id, "journal-failure-id")

    def test_interrupted_journal_run_is_warning(self):
        monitor = self.timer_monitor()
        monitor.now = 1_800_000_000

        def run(command, timeout=15):
            if command[0] == "journalctl":
                event = {
                    "MESSAGE": "Stopped job.service - job",
                    "__REALTIME_TIMESTAMP": str(int((monitor.now - 60) * 1_000_000)),
                }
                return completed(command, stdout=json.dumps(event) + "\n")
            return self.base_run(command, timeout)

        monitor.run_command = run
        monitor.check_user_timers()
        self.assertEqual(monitor.results[-1].severity, "warning")
        self.assertIn("was interrupted", monitor.results[-1].summary)

    def test_structured_result_file_is_authoritative_when_newer(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        result_file = Path(temporary.name) / "result.json"
        monitor = self.timer_monitor()
        monitor.now = 1_800_000_000
        monitor.config["user_timers"][0]["result_file"] = str(result_file)
        result_file.write_text(
            json.dumps({"status": "BLOCKED", "completed_at": monitor.now - 30}),
            encoding="utf-8",
        )

        def run(command, timeout=15):
            if command[0] == "journalctl":
                event = {
                    "MESSAGE": "Finished job.service - job",
                    "__REALTIME_TIMESTAMP": str(int((monitor.now - 3600) * 1_000_000)),
                }
                return completed(command, stdout=json.dumps(event) + "\n")
            return self.base_run(command, timeout)

        monitor.run_command = run
        monitor.check_user_timers()
        self.assertEqual(monitor.results[-1].severity, "warning")
        self.assertIn("was blocked", monitor.results[-1].summary)


class SystemTimerTests(MonitorTestCase):
    def timer_monitor(self):
        monitor = self.make_monitor(
            {
                "system_timers": [
                    {
                        "unit": "devbox-system-backup.timer",
                        "service": "devbox-system-backup.service",
                        "warning_hours": 30,
                        "critical_hours": 36,
                        "first_success_due": "2099-01-01T00:00:00+00:00",
                    }
                ]
            }
        )
        monitor.now = 1_800_000_000
        return monitor

    @staticmethod
    def healthy_run(command, timeout=15):
        if "is-active" in command:
            return completed(command, stdout="active\n")
        if "is-enabled" in command:
            return completed(command, stdout="enabled\n")
        if "show" in command and command[2].endswith(".timer"):
            return completed(command, stdout="LastTriggerUSec=@1799998200\n")
        if "show" in command:
            return completed(
                command,
                stdout=(
                    "Result=success\nExecMainStatus=0\n"
                    "ExecMainExitTimestamp=@1799998260\nActiveState=inactive\n"
                    "SubState=dead\nInvocationID=system-ok\n"
                ),
            )
        return completed(command)

    def test_enabled_timer_natural_trigger_and_service_success_are_healthy(self):
        monitor = self.timer_monitor()
        monitor.run_command = self.healthy_run
        monitor.check_system_timers()
        self.assertEqual([result.severity for result in monitor.results], ["ok", "ok", "ok"])
        self.assertIn("naturally 30m ago", monitor.results[1].summary)

    def test_inactive_system_timer_is_critical(self):
        monitor = self.timer_monitor()

        def run(command, timeout=15):
            if "is-active" in command:
                return completed(command, stdout="inactive\n")
            return self.healthy_run(command, timeout)

        monitor.run_command = run
        monitor.check_system_timers()
        self.assertEqual(monitor.results[0].severity, "critical")

    def test_disabled_system_timer_is_critical(self):
        monitor = self.timer_monitor()

        def run(command, timeout=15):
            if "is-enabled" in command:
                return completed(command, stdout="disabled\n")
            return self.healthy_run(command, timeout)

        monitor.run_command = run
        monitor.check_system_timers()
        self.assertEqual(monitor.results[0].severity, "critical")

    def test_missing_natural_run_after_deadline_is_critical(self):
        monitor = self.timer_monitor()
        monitor.config["system_timers"][0]["first_success_due"] = (
            "2020-01-01T00:00:00+00:00"
        )

        def run(command, timeout=15):
            if "show" in command and command[2].endswith(".timer"):
                return completed(command, stdout="LastTriggerUSec=\n")
            return self.healthy_run(command, timeout)

        monitor.run_command = run
        monitor.check_system_timers()
        self.assertEqual(monitor.results[1].severity, "critical")

    def test_exit_three_and_stale_natural_trigger_are_critical(self):
        monitor = self.timer_monitor()

        def run(command, timeout=15):
            if "show" in command and command[2].endswith(".timer"):
                return completed(command, stdout="LastTriggerUSec=@1799100000\n")
            if "show" in command:
                return completed(
                    command,
                    stdout=(
                        "Result=exit-code\nExecMainStatus=3\nActiveState=inactive\n"
                        "InvocationID=incomplete-snapshot\n"
                    ),
                )
            return self.healthy_run(command, timeout)

        monitor.run_command = run
        monitor.check_system_timers()
        self.assertEqual(monitor.results[1].severity, "critical")
        self.assertEqual(monitor.results[2].severity, "critical")
        self.assertEqual(monitor.results[2].incident_id, "incomplete-snapshot")


class DailySuccessTests(MonitorTestCase):
    def daily_monitor(self, last_success="2026-09-06", last_result="FULL_SUCCESS"):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state_path = Path(temporary.name) / "source-state.json"
        pending_path = Path(temporary.name) / "source.pending"
        state_path.write_text(
            json.dumps(
                {
                    "last_success_local_date": last_success,
                    "last_result": last_result,
                    "ignored_sensitive_field": "must-not-appear",
                }
            ),
            encoding="utf-8",
        )
        monitor = self.make_monitor(
            {
                "daily_success_checks": [
                    {
                        "name": "password-reset",
                        "label": "Password reset",
                        "state_file": str(state_path),
                        "pending_file": str(pending_path),
                        "last_success_local_date_field": "last_success_local_date",
                        "last_result_field": "last_result",
                        "time_zone": "Europe/Warsaw",
                        "window_end": "09:00",
                        "warning_missed_windows": 1,
                        "critical_missed_windows": 2,
                    }
                ]
            }
        )
        return monitor, state_path, pending_path

    @staticmethod
    def epoch(local_timestamp):
        return datetime.fromisoformat(local_timestamp).replace(
            tzinfo=ZoneInfo("Europe/Warsaw")
        ).timestamp()

    def test_yesterday_success_is_healthy_before_window_end(self):
        monitor, _state, _pending = self.daily_monitor(last_success="2026-09-06")
        monitor.now = self.epoch("2026-09-07T08:59:59")
        monitor.check_daily_successes()
        self.assertEqual(monitor.results[0].severity, "ok")

    def test_today_success_is_healthy_at_window_end(self):
        monitor, _state, _pending = self.daily_monitor(last_success="2026-09-07")
        monitor.now = self.epoch("2026-09-07T09:00:00")
        monitor.check_daily_successes()
        self.assertEqual(monitor.results[0].severity, "ok")

    def test_one_missed_window_warns(self):
        monitor, _state, _pending = self.daily_monitor(
            last_success="2026-09-06", last_result="SSH_UNREACHABLE"
        )
        monitor.now = self.epoch("2026-09-07T09:00:00")
        monitor.check_daily_successes()
        self.assertEqual(monitor.results[0].severity, "warning")
        self.assertIn("last result SSH_UNREACHABLE", monitor.results[0].summary)

    def test_multiple_missed_windows_are_critical_without_state_dump(self):
        monitor, _state, _pending = self.daily_monitor(
            last_success="2026-09-03", last_result="SSH_UNREACHABLE"
        )
        monitor.now = self.epoch("2026-09-07T12:00:00")
        monitor.check_daily_successes()
        self.assertEqual(monitor.results[0].severity, "critical")
        self.assertIn("missed 4 daily success windows", monitor.results[0].summary)
        self.assertNotIn("must-not-appear", monitor.results[0].summary)
        self.assertEqual(monitor.results[0].details, "")

    def test_pending_is_critical_and_its_contents_are_not_read(self):
        monitor, state_path, pending_path = self.daily_monitor()
        pending_path.write_text("must-not-be-read", encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=AssertionError("read attempted")):
            monitor.check_daily_successes()
        self.assertEqual(monitor.results[0].severity, "critical")
        self.assertIn("unresolved pending operation", monitor.results[0].summary)
        self.assertNotIn(str(state_path), monitor.results[0].summary)

    def test_missing_or_invalid_state_is_critical(self):
        monitor, state_path, _pending = self.daily_monitor()
        state_path.unlink()
        monitor.check_daily_successes()
        self.assertEqual(monitor.results[0].severity, "critical")

        state_path.write_text("not-json", encoding="utf-8")
        monitor.results = []
        monitor.check_daily_successes()
        self.assertEqual(monitor.results[0].severity, "critical")

    def test_future_success_date_is_critical(self):
        monitor, _state, _pending = self.daily_monitor(last_success="2026-09-08")
        monitor.now = self.epoch("2026-09-07T12:00:00")
        monitor.check_daily_successes()
        self.assertEqual(monitor.results[0].severity, "critical")
        self.assertIn("future success date", monitor.results[0].summary)


class HttpTests(MonitorTestCase):
    def test_allowed_401_is_healthy(self):
        monitor = self.make_monitor(
            {
                "endpoints": [
                    {"name": "protected", "url": "http://127.0.0.1/", "allowed_statuses": [401]}
                ]
            }
        )
        error = urllib.error.HTTPError(
            "http://127.0.0.1/", 401, "Unauthorized", {}, io.BytesIO(b"")
        )
        self.addCleanup(error.close)
        with patch.object(MODULE.urllib.request, "urlopen", side_effect=error):
            monitor.check_http()
        self.assertEqual(monitor.results[0].severity, "ok")

    def test_second_connection_failure_warns(self):
        monitor = self.make_monitor(
            {
                "endpoints": [
                    {"name": "down", "url": "http://127.0.0.1/", "allowed_statuses": [200]}
                ]
            }
        )
        with patch.object(MODULE.urllib.request, "urlopen", side_effect=OSError("down")):
            monitor.check_http()
            monitor.results = []
            monitor.check_http()
        self.assertEqual(monitor.results[0].severity, "warning")

    def test_http_500_is_immediately_critical(self):
        monitor = self.make_monitor(
            {
                "endpoints": [
                    {"name": "error", "url": "http://127.0.0.1/", "allowed_statuses": [200]}
                ]
            }
        )
        error = urllib.error.HTTPError(
            "http://127.0.0.1/", 500, "Error", {}, io.BytesIO(b"")
        )
        self.addCleanup(error.close)
        with patch.object(MODULE.urllib.request, "urlopen", side_effect=error):
            monitor.check_http()
        self.assertEqual(monitor.results[0].severity, "critical")

    def test_expected_json_mismatch_uses_consecutive_failure_policy(self):
        monitor = self.make_monitor(
            {
                "endpoints": [
                    {
                        "name": "application",
                        "url": "http://127.0.0.1/health",
                        "allowed_statuses": [200],
                        "expected_json": {"ready": True},
                    }
                ]
            }
        )
        response = unittest.mock.MagicMock()
        response.status = 200
        response.read.return_value = b'{"ready": false}'
        with patch.object(MODULE.urllib.request, "urlopen", return_value=response):
            monitor.check_http()
            self.assertEqual(monitor.results[-1].severity, "ok")
            monitor.results = []
            monitor.check_http()
            self.assertEqual(monitor.results[-1].severity, "warning")
            monitor.results = []
            monitor.check_http()
            self.assertEqual(monitor.results[-1].severity, "critical")


class HelperTests(unittest.TestCase):
    def test_service_result_checks_result_and_exit_status(self):
        self.assertFalse(MODULE.service_result_failed("success", "0"))
        self.assertTrue(MODULE.service_result_failed("success", "3"))
        self.assertTrue(MODULE.service_result_failed("exit-code", "1"))
        self.assertTrue(MODULE.service_result_failed("", "1"))

    def test_systemd_unix_timestamp_is_parsed(self):
        self.assertEqual(MODULE.timestamp_epoch("@1800000000"), 1_800_000_000)

    def test_config_rejects_missing_required_sections(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as handle:
            json.dump({"host_label": "devbox"}, handle)
            handle.flush()
            with self.assertRaises(ValueError):
                MODULE.load_config(handle.name)


if __name__ == "__main__":
    unittest.main()
