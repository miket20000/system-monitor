import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parents[2]
MODULE_PATH = ROOT / "src/system_monitor/adapters/gp.py"
CONFIG_PATH = ROOT / "profiles/gp.json"
SPEC = importlib.util.spec_from_file_location("gp_monitor", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

INGEST_PATH = ROOT / "src/system_monitor/heartbeat_receiver.py"
INGEST_SPEC = importlib.util.spec_from_file_location("gp_monitor_ingest", INGEST_PATH)
INGEST = importlib.util.module_from_spec(INGEST_SPEC)
assert INGEST_SPEC.loader is not None
sys.modules[INGEST_SPEC.name] = INGEST
INGEST_SPEC.loader.exec_module(INGEST)


class ConfigCoverageTests(unittest.TestCase):
    def test_codinggiants_cdn_import_timer_is_monitored(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        timers = {item["unit"]: item for item in config["system_timers"]}
        self.assertEqual(
            timers["codinggiants-app-cdn-import.timer"],
            {
                "unit": "codinggiants-app-cdn-import.timer",
                "service": "codinggiants-app-cdn-import.service",
                "warning_hours": 2,
                "critical_hours": 3,
                "require_completion_after_trigger": True,
                "completion_grace_seconds": 300,
            },
        )

    def test_network_disk_mirror_timer_is_monitored(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        timers = {item["unit"]: item for item in config["system_timers"]}

        self.assertEqual(
            timers["dysk-sieciowy-mirror.timer"],
            {
                "unit": "dysk-sieciowy-mirror.timer",
                "service": "dysk-sieciowy-mirror.service",
                "warning_hours": 0.25,
                "critical_hours": 0.5,
            },
        )

    def test_notification_policy_uses_persistence_and_aggregation(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

        self.assertEqual(
            config["notifications"],
            {
                "default_failure_confirmation_cycles": 2,
                "network_failure_confirmation_cycles": 3,
                "recovery_confirmation_cycles": 2,
                "reminder_seconds": 21600,
                "max_items_per_message": 20,
                "suppressed_recovery_keys": [
                    "service:canva-pro-keys.service",
                    "endpoint:canva-pro-keys-local",
                ],
                "suppressed_key_prefixes": [],
            },
        )
        self.assertNotIn("http_warning_consecutive_failures", config["thresholds"])
        self.assertNotIn("http_critical_consecutive_failures", config["thresholds"])

    def test_online_compiler_production_scheduler_is_monitored_locally(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        checks = {
            item["name"]: item for item in config["online_compiler_schedulers"]
        }

        self.assertEqual(
            checks["production"],
            {
                "name": "production",
                "incident_key": "external-heartbeat:online-compiler-production-schedule",
                "timer_unit": "online-compiler-production-schedule.timer",
                "service_unit": "online-compiler-production-schedule.service",
                "schedule_path": "/var/lib/online-compiler-scheduler/production/schedule.json",
                "health_path": "/var/lib/online-compiler-scheduler/production/scheduler-health.json",
                "holds_path": "/var/lib/online-compiler-scheduler/production/holds",
                "freshness_warning_seconds": 180,
                "freshness_critical_seconds": 300,
            },
        )

    def test_online_compiler_next_dev_scheduler_is_monitored_locally(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        checks = {
            item["name"]: item for item in config["online_compiler_schedulers"]
        }

        self.assertEqual(
            checks["next-dev"],
            {
                "name": "next-dev",
                "incident_key": "external-heartbeat:online-compiler-next-dev-hibernation",
                "timer_unit": "online-compiler-next-dev-idle.timer",
                "service_unit": "online-compiler-next-dev-idle.service",
                "schedule_path": "/var/lib/online-compiler-scheduler/next-dev/schedule.json",
                "health_path": "/var/lib/online-compiler-scheduler/next-dev/scheduler-health.json",
                "power_state_path": "/var/lib/online-compiler-scheduler/next-dev/state.json",
                "activities_path": "/var/lib/online-compiler-scheduler/next-dev/activities",
                "leases_path": "/var/lib/online-compiler-scheduler/next-dev/leases",
                "holds_path": "/var/lib/online-compiler-scheduler/next-dev/holds",
                "freshness_warning_seconds": 1200,
                "freshness_critical_seconds": 2100,
                "idle_threshold_seconds": 3600,
                "idle_grace_seconds": 900,
            },
        )

    def test_vm_manager_daily_status_notifier_replaces_legacy_cron_check(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        timers = {item["unit"]: item for item in config["system_timers"]}
        cron_jobs = {item["name"] for item in config["cron_jobs"]}

        self.assertEqual(
            timers["vm-manager-status-notifier.timer"],
            {
                "unit": "vm-manager-status-notifier.timer",
                "service": "vm-manager-status-notifier.service",
                "warning_hours": 30,
                "critical_hours": 36,
            },
        )
        self.assertNotIn("check-missing", cron_jobs)

    def test_support_class_reporting_timer_replaces_legacy_cron(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        timers = {item["unit"]: item for item in config["system_timers"]}
        cron_jobs = {item["name"] for item in config["cron_jobs"]}
        services = set(config["system_services"])
        endpoints = {item["name"] for item in config["endpoints"]}

        self.assertEqual(
            timers["support-class-reporting.timer"],
            {
                "unit": "support-class-reporting.timer",
                "service": "support-class-reporting.service",
                "require_completion_after_trigger": True,
                "completion_grace_seconds": 900,
                "warning_hours": 84,
                "critical_hours": 90,
                "calendar_schedule": {
                    "weekdays": [1, 2, 3, 4, 5],
                    "hour": 7,
                    "minute": 0,
                    "timezone": "Europe/Warsaw",
                    "warning_delay_hours": 6,
                    "critical_delay_hours": 12,
                },
            },
        )
        self.assertEqual(
            timers["support-class-reporting-subscription.timer"],
            {
                "unit": "support-class-reporting-subscription.timer",
                "service": "support-class-reporting-subscription.service",
                "warning_hours": 30,
                "critical_hours": 48,
            },
        )
        self.assertIn("support-class-reporting-realtime.service", services)
        self.assertNotIn("klasa-testowa-daily", cron_jobs)
        self.assertNotIn("sprawdz-join", endpoints)
        self.assertNotIn("soporte-es-join", endpoints)
        self.assertNotIn("soporte-mx-join", endpoints)

    def test_support_class_schedule_skips_sunday_and_monday(self) -> None:
        timer = {
            "warning_hours": 84,
            "critical_hours": 90,
            "calendar_schedule": {
                "weekdays": [1, 2, 3, 4, 5],
                "hour": 7,
                "minute": 0,
                "timezone": "Europe/Warsaw",
                "warning_delay_hours": 6,
                "critical_delay_hours": 12,
            },
        }
        monday_noon = datetime.fromisoformat(
            "2026-09-28T12:00:00+02:00"
        ).timestamp()

        warning, critical = MODULE.system_timer_age_thresholds(
            timer, monday_noon
        )

        self.assertEqual(warning, 59)
        self.assertEqual(critical, 65)

    def test_support_class_schedule_detects_a_missed_tuesday_run(self) -> None:
        timer = {
            "warning_hours": 84,
            "critical_hours": 90,
            "calendar_schedule": {
                "weekdays": [1, 2, 3, 4, 5],
                "hour": 7,
                "minute": 0,
                "timezone": "Europe/Warsaw",
                "warning_delay_hours": 6,
                "critical_delay_hours": 12,
            },
        }
        tuesday_evening = datetime.fromisoformat(
            "2026-09-29T20:00:00+02:00"
        ).timestamp()

        warning, critical = MODULE.system_timer_age_thresholds(
            timer, tuesday_evening
        )

        self.assertEqual(warning, 19)
        self.assertEqual(critical, 25)

    def test_gowork_notifier_daily_scan_is_monitored(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        timers = {item["unit"]: item for item in config["system_timers"]}

        self.assertEqual(
            timers["gowork-notifier-scan.timer"],
            {
                "unit": "gowork-notifier-scan.timer",
                "service": "gowork-notifier-scan.service",
                "warning_hours": 30,
                "critical_hours": 36,
            },
        )

    def test_vm_manager_inventory_sync_is_monitored(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        endpoints = {item["name"]: item for item in config["endpoints"]}

        self.assertEqual(
            endpoints["vm-manager-inventory-sync-local"],
            {
                "name": "vm-manager-inventory-sync-local",
                "url": "http://127.0.0.1:8092/internal/inventory-sync-health",
                "allowed_statuses": [200],
                "expected_json": {"ok": True},
            },
        )

    def test_minecraft_codebuilder_is_monitored(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        endpoints = {item["name"]: item for item in config["endpoints"]}
        timers = {item["unit"]: item for item in config["system_timers"]}

        self.assertIn("minecraft-codebuilder-backend.service", config["system_services"])
        self.assertNotIn("minecraft-codebuilder-telemetry.service", config["system_services"])
        self.assertEqual(
            timers["minecraft-codebuilder-retention.timer"],
            {
                "unit": "minecraft-codebuilder-retention.timer",
                "service": "minecraft-codebuilder-retention.service",
                "warning_hours": 30,
                "critical_hours": 48,
            },
        )
        self.assertEqual(
            endpoints["minecraft-codebuilder-public"]["allowed_statuses"],
            [200],
        )
        self.assertEqual(
            endpoints["minecraft-codebuilder-backend-ready"]["url"],
            "http://127.0.0.1:8113/health/ready",
        )
        self.assertEqual(
            endpoints["minecraft-codebuilder-backend-ready"]["expected_json"],
            {"status": "ready"},
        )
        self.assertEqual(
            endpoints["minecraft-codebuilder-project-share-storage"],
            {
                "name": "minecraft-codebuilder-project-share-storage",
                "url": "http://127.0.0.1:8113/health/ops/storage",
                "allowed_statuses": [200],
                "expected_json": {"status": "ok"},
            },
        )

    def test_retired_gp_backup_timers_are_not_monitored(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        timers = {item["unit"] for item in config["system_timers"]}

        self.assertTrue(
            {
                "gp-server-backup.timer",
                "gp-server-backup-check.timer",
                "gp-server-backup-retention.timer",
                "gp-mail-backup-purge.timer",
            }.isdisjoint(timers)
        )

    def test_ai_imaging_v2_and_legacy_redirects_are_monitored(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        endpoints = {item["name"]: item for item in config["endpoints"]}
        timers = {item["unit"]: item for item in config["system_timers"]}

        self.assertIn("ai-imaging.service", config["system_services"])
        self.assertIn("ai-imaging-worker.service", config["system_services"])
        self.assertEqual(
            timers["ai-imaging-cleanup.timer"],
            {
                "unit": "ai-imaging-cleanup.timer",
                "service": "ai-imaging-cleanup.service",
                "warning_hours": 0.25,
                "critical_hours": 0.5,
            },
        )
        self.assertEqual(
            timers["ai-imaging-probe.timer"],
            {
                "unit": "ai-imaging-probe.timer",
                "service": "ai-imaging-probe.service",
                "warning_hours": 0.1,
                "critical_hours": 0.2,
                "require_completion_after_trigger": True,
                "completion_grace_seconds": 30,
            },
        )
        self.assertEqual(
            timers["ai-imaging-legacy-retention.timer"],
            {
                "unit": "ai-imaging-legacy-retention.timer",
                "service": "ai-imaging-legacy-retention.service",
                "warning_hours": 30,
                "critical_hours": 48,
            },
        )

        expected_ready = {
            "ok": True,
            "app_id": "ai-imaging",
            "generation_mode": "flare",
            "database": True,
            "templates": True,
            "moderation": True,
            "openrouter": True,
            "transparency": True,
            "legacy_bria": True,
            "storage": True,
            "auth_gp": True,
            "worker": True,
            "cleanup": True,
            "ledger": True,
            "backlog": True,
        }
        for suffix, host in {
            "gp": "ai.gp.edu.pl",
            "de": "ai.codinggiants.de",
            "es": "ai.codinggiants.es",
            "mx": "ai.codinggiants.mx",
            "com": "ai.codinggiants.com",
        }.items():
            endpoint = endpoints[f"ai-imaging-ready-{suffix}"]
            self.assertEqual(endpoint["url"], f"https://{host}/imaging/health/ready")
            self.assertEqual(endpoint["allowed_statuses"], [200])
            self.assertEqual(endpoint["expected_json"], expected_ready)

        for name, url in {
            "ai-imaging-legacy-gp": "https://grafika.gp.edu.pl/",
            "bilder-de": "https://bilder.codinggiants.de/",
            "imagenes-es": "https://imagenes.codinggiants.es/",
            "imagenes-mx": "https://imagenes.codinggiants.mx/",
            "images-com": "https://images.codinggiants.com/",
        }.items():
            self.assertEqual(endpoints[name]["url"], url)
            self.assertEqual(endpoints[name]["allowed_statuses"], [200])

    def test_ai_music_services_timers_and_health_matrix_are_monitored(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        endpoints = {item["name"]: item for item in config["endpoints"]}
        timers = {item["unit"]: item for item in config["system_timers"]}

        self.assertIn("ai-music.service", config["system_services"])
        self.assertIn("ai-music-worker.service", config["system_services"])
        self.assertEqual(
            timers["ai-music-cleanup.timer"],
            {
                "unit": "ai-music-cleanup.timer",
                "service": "ai-music-cleanup.service",
                "warning_hours": 0.25,
                "critical_hours": 0.5,
            },
        )
        self.assertEqual(
            timers["ai-music-probe.timer"],
            {
                "unit": "ai-music-probe.timer",
                "service": "ai-music-probe.service",
                "warning_hours": 0.1,
                "critical_hours": 0.2,
                "require_completion_after_trigger": True,
                "completion_grace_seconds": 30,
            },
        )

        expected_ready = {
            "ok": True,
            "app_id": "ai-music",
            "database": True,
            "templates": True,
            "moderation": True,
            "openrouter": True,
            "storage": True,
            "auth_gp": True,
            "worker": True,
            "cleanup": True,
            "ledger": True,
            "backlog": True,
        }
        for suffix, host in {
            "gp": "ai.gp.edu.pl",
            "de": "ai.codinggiants.de",
            "es": "ai.codinggiants.es",
            "mx": "ai.codinggiants.mx",
            "com": "ai.codinggiants.com",
        }.items():
            endpoint = endpoints[f"ai-music-ready-{suffix}"]
            self.assertEqual(endpoint["url"], f"https://{host}/music/health/ready")
            self.assertEqual(endpoint["allowed_statuses"], [200])
            self.assertEqual(endpoint["expected_json"], expected_ready)

    def test_ai_audio_services_timers_and_health_matrix_are_monitored(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        endpoints = {item["name"]: item for item in config["endpoints"]}
        timers = {item["unit"]: item for item in config["system_timers"]}

        self.assertIn("ai-audio.service", config["system_services"])
        self.assertIn("ai-audio-worker.service", config["system_services"])
        self.assertEqual(
            timers["ai-audio-cleanup.timer"],
            {
                "unit": "ai-audio-cleanup.timer",
                "service": "ai-audio-cleanup.service",
                "warning_hours": 0.25,
                "critical_hours": 0.5,
            },
        )
        self.assertEqual(
            timers["ai-audio-probe.timer"],
            {
                "unit": "ai-audio-probe.timer",
                "service": "ai-audio-probe.service",
                "warning_hours": 0.1,
                "critical_hours": 0.2,
                "require_completion_after_trigger": True,
                "completion_grace_seconds": 30,
            },
        )

        expected_ready = {
            "ok": True,
            "app_id": "ai-audio",
            "database": True,
            "templates": True,
            "moderation": True,
            "providers": True,
            "audio_exports": True,
            "storage": True,
            "auth_gp": True,
            "worker": True,
            "cleanup": True,
            "ledger": True,
            "backlog": True,
        }
        for suffix, host in {
            "gp": "ai.gp.edu.pl",
            "de": "ai.codinggiants.de",
            "es": "ai.codinggiants.es",
            "mx": "ai.codinggiants.mx",
            "com": "ai.codinggiants.com",
        }.items():
            endpoint = endpoints[f"ai-audio-ready-{suffix}"]
            self.assertEqual(endpoint["url"], f"https://{host}/audio/health/ready")
            self.assertEqual(endpoint["allowed_statuses"], [200])
            self.assertEqual(endpoint["expected_json"], expected_ready)

    def test_ai_video_services_timers_and_health_matrix_are_monitored(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        endpoints = {item["name"]: item for item in config["endpoints"]}
        timers = {item["unit"]: item for item in config["system_timers"]}

        self.assertIn("ai-video.service", config["system_services"])
        self.assertIn("ai-video-worker.service", config["system_services"])
        self.assertEqual(
            timers["ai-video-cleanup.timer"],
            {
                "unit": "ai-video-cleanup.timer",
                "service": "ai-video-cleanup.service",
                "warning_hours": 0.25,
                "critical_hours": 0.5,
            },
        )
        self.assertEqual(
            timers["ai-video-probe.timer"],
            {
                "unit": "ai-video-probe.timer",
                "service": "ai-video-probe.service",
                "warning_hours": 0.1,
                "critical_hours": 0.2,
                "require_completion_after_trigger": True,
                "completion_grace_seconds": 30,
            },
        )

        expected_ready = {
            "ok": True,
            "app_id": "ai-video",
            "database": True,
            "templates": True,
            "moderation": True,
            "openrouter": True,
            "storage": True,
            "auth_gp": True,
            "worker": True,
            "cleanup": True,
            "ledger": True,
            "backlog": True,
        }
        for suffix, host in {
            "gp": "ai.gp.edu.pl",
            "de": "ai.codinggiants.de",
            "es": "ai.codinggiants.es",
            "mx": "ai.codinggiants.mx",
            "com": "ai.codinggiants.com",
        }.items():
            endpoint = endpoints[f"ai-video-ready-{suffix}"]
            self.assertEqual(endpoint["url"], f"https://{host}/video/health/ready")
            self.assertEqual(endpoint["allowed_statuses"], [200])
            self.assertEqual(endpoint["expected_json"], expected_ready)

    def test_ai_text_services_timer_and_health_matrix_are_monitored(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        endpoints = {item["name"]: item for item in config["endpoints"]}
        timers = {item["unit"]: item for item in config["system_timers"]}

        self.assertIn("ai-text.service", config["system_services"])
        self.assertIn("ai-text-worker.service", config["system_services"])
        self.assertEqual(
            timers["ai-text-cleanup.timer"],
            {
                "unit": "ai-text-cleanup.timer",
                "service": "ai-text-cleanup.service",
                "warning_hours": 1.5,
                "critical_hours": 2,
            },
        )

        for suffix, host in {
            "gp": "ai.gp.edu.pl",
            "de": "ai.codinggiants.de",
            "es": "ai.codinggiants.es",
            "mx": "ai.codinggiants.mx",
            "com": "ai.codinggiants.com",
        }.items():
            endpoint = endpoints[f"ai-text-ready-{suffix}"]
            self.assertEqual(endpoint["url"], f"https://{host}/text/health/ready")
            self.assertEqual(endpoint["allowed_statuses"], [200])
            self.assertEqual(
                endpoint["expected_json"],
                {"ok": True, "check": "ready", "app_id": "ai-text"},
            )

        local_checks = {
            "live": "live",
            "ready": "ready",
            "backlog": "backlog",
            "provider": "provider",
            "moderation": "moderation",
            "reconciliation": "reconciliation",
            "ledger": "ledger",
            "cleanup": "cleanup",
            "lifecycle": "lifecycle",
            "storage": "storage",
            "worker": "worker",
            "tools": "tools",
            "web": "web",
        }
        for name, check in local_checks.items():
            endpoint = endpoints[f"ai-text-{name}-local"]
            self.assertEqual(endpoint["host_header"], "ai.gp.edu.pl")
            self.assertEqual(endpoint["allowed_statuses"], [200])
            self.assertEqual(
                endpoint["expected_json"],
                {"ok": True, "check": check, "app_id": "ai-text"},
            )

    def test_password_provide_service_and_safe_health_are_monitored(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertIn("password-provide.service", config["system_services"])
        endpoint = next(item for item in config["endpoints"] if item["name"] == "password-provide-local")
        self.assertEqual(endpoint["url"], "http://127.0.0.1:8106/health")
        self.assertEqual(endpoint["allowed_statuses"], [200])
        self.assertEqual(
            endpoint["expected_json"],
            {"ok": True, "applications": ["canva", "vsconline"]},
        )

    def test_online_compiler_keys_service_and_safe_health_are_monitored(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertIn("online-compiler-keys.service", config["system_services"])
        endpoint = next(item for item in config["endpoints"] if item["name"] == "online-compiler-keys-local")
        self.assertEqual(endpoint["url"], "http://127.0.0.1:8107/health")
        self.assertEqual(endpoint["allowed_statuses"], [200])
        self.assertEqual(endpoint["expected_json"], {"ok": True, "application": "online-compiler"})

    def test_trustpilot_notifier_safe_health_is_monitored_through_tunnel(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        endpoint = next(
            item
            for item in config["endpoints"]
            if item["name"] == "trustpilot-notifier-local"
        )
        self.assertEqual(endpoint["url"], "http://127.0.0.1:8115/health")
        self.assertEqual(endpoint["allowed_statuses"], [200])
        self.assertEqual(
            endpoint["expected_json"],
            {
                "ready": True,
                "processing_enabled": True,
                "auto_reply_enabled": True,
                "auth_required": False,
                "auto_reply_ready": True,
            },
        )
        self.assertNotIn("headers", endpoint)

    def test_canva_pro_keys_service_and_safe_health_are_monitored(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertIn("canva-pro-keys.service", config["system_services"])
        endpoint = next(
            item
            for item in config["endpoints"]
            if item["name"] == "canva-pro-keys-local"
        )
        self.assertEqual(endpoint["url"], "http://127.0.0.1:8116/health")
        self.assertEqual(endpoint["allowed_statuses"], [200])
        self.assertEqual(
            endpoint["expected_json"],
            {
                "ok": True,
                "service": "canva-pro-keys",
                "application": "canva-pro",
                "database": True,
                "account_keys": ["CANVA", "CANVA1", "CANVA2", "CANVA3"],
                "password_source": True,
            },
        )
        self.assertNotIn("headers", endpoint)
        self.assertEqual(
            config["notifications"]["suppressed_recovery_keys"],
            [
                "service:canva-pro-keys.service",
                "endpoint:canva-pro-keys-local",
            ],
        )

    def test_retired_legacy_online_compiler_checks_are_excluded(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        timers = {item["unit"] for item in config["system_timers"]}
        endpoints = {item["name"]: item for item in config["endpoints"]}

        self.assertNotIn("online-compiler.service", config["system_services"])
        self.assertNotIn("online-compiler-executor-tunnel.service", config["system_services"])
        self.assertNotIn("online-compiler-cleanup.timer", timers)
        self.assertNotIn("online-compiler-health-local", endpoints)
        self.assertNotIn("online-compiler-ready-local", endpoints)
        self.assertEqual(endpoints["koduj"]["url"], "https://koduj.gp.edu.pl/")

    def test_audited_p1_and_p2_dependencies_are_monitored(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        timers = {item["unit"]: item for item in config["system_timers"]}
        endpoints = {item["name"]: item for item in config["endpoints"]}

        self.assertEqual(
            timers["auth-gp-expiry.timer"]["service"],
            "auth-gp-expiry.service",
        )
        self.assertEqual(
            endpoints["meta-api-local"]["url"],
            "http://127.0.0.1:8090/health",
        )
        self.assertEqual(
            endpoints["meta-notifier-local"]["expected_json"],
            {"ok": True, "pending_dispatches": 0, "failed_dispatches": 0},
        )
        self.assertEqual(
            endpoints["google-mybusiness-notifier-api-local"]["expected_json"],
            {"status": "ok", "database_backend": "postgresql"},
        )
        self.assertEqual(endpoints["komputery"]["allowed_statuses"], [200])
        self.assertEqual(endpoints["bilder-de"]["url"], "https://bilder.codinggiants.de/")
        self.assertEqual(config["route_checks"][0]["interface_prefix"], "ppp")
        self.assertIn(
            "Fallback scan failed",
            config["journal_error_checks"][0]["patterns"],
        )


class TelegramNotificationTests(unittest.TestCase):
    def test_format_preserves_sanitized_batch_lines(self) -> None:
        self.assertEqual(
            MODULE.format_telegram_notification(
                "[CRITICAL]\nNew: service:example\n\n",
                "gp\nignored",
            ),
            "[CRITICAL]\nNew: service:example\nHost: gp ignored",
        )

    def test_batch_is_bounded_and_reports_omitted_checks(self) -> None:
        body, reason = MODULE.format_notification_batch(
            [("endpoint:a", "critical"), ("endpoint:b", "critical")],
            [("endpoint:a", "critical"), ("endpoint:b", "critical")],
            ["endpoint:c"],
            reminder=False,
            max_items=2,
        )

        self.assertEqual(reason, "transition")
        self.assertEqual(body.splitlines()[0], "[CRITICAL]")
        self.assertNotIn("GP Monitor:", body)
        self.assertNotIn("new/escalated", body)
        self.assertIn("Omitted: 1 additional checks", body)

    def test_all_batch_headers_contain_only_the_status_label(self) -> None:
        cases = [
            (
                [("service:new.service", "critical")],
                [("service:new.service", "critical")],
                [],
                False,
                "[CRITICAL]",
            ),
            (
                [],
                [("service:active.service", "critical")],
                [],
                True,
                "[REMINDER/CRITICAL]",
            ),
            (
                [],
                [("service:active.service", "critical")],
                ["service:resolved.service"],
                False,
                "[UPDATE]",
            ),
            ([], [], ["service:resolved.service"], False, "[RESOLVED]"),
        ]

        for new, active, resolved, reminder, expected_header in cases:
            with self.subTest(expected_header=expected_header):
                body, _reason = MODULE.format_notification_batch(
                    new,
                    active,
                    resolved,
                    reminder=reminder,
                    max_items=20,
                )
                self.assertEqual(body.splitlines()[0], expected_header)
                self.assertNotIn("GP Monitor:", body)
                self.assertNotIn("new/escalated", body)

    def test_send_omits_time_and_details(self) -> None:
        state_path = Path(__file__).with_name(".gp-monitor-test-state.json")
        state_path.unlink(missing_ok=True)
        self.addCleanup(state_path.unlink, missing_ok=True)
        monitor = MODULE.Monitor(
            {"state_file": str(state_path), "host_label": "gp"},
            notify=True,
        )

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self) -> bytes:
                return b'{"ok": true}'

        with patch.dict(
            MODULE.os.environ,
            {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_ID": "chat"},
        ), patch.object(MODULE.urllib.request, "urlopen", return_value=Response()) as urlopen:
            delivered = monitor.send("[CRITICAL] service failed", "private diagnostic")

        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertTrue(delivered)
        self.assertEqual(
            payload["text"],
            "[CRITICAL] service failed\nHost: gp",
        )


class ServiceResultFailedTests(unittest.TestCase):
    def test_explicit_success_ignores_stale_exit_status(self) -> None:
        self.assertFalse(MODULE.service_result_failed("success", "11"))

    def test_explicit_failure_is_failure(self) -> None:
        self.assertTrue(MODULE.service_result_failed("exit-code", "1"))

    def test_missing_result_uses_exit_status(self) -> None:
        self.assertTrue(MODULE.service_result_failed("", "1"))
        self.assertFalse(MODULE.service_result_failed("", "0"))
        self.assertFalse(MODULE.service_result_failed("", ""))


class DnsMonitorTests(unittest.TestCase):
    def test_parse_successful_dig_output(self) -> None:
        output = """;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1
example.com. 60 IN A 192.0.2.1
"""
        self.assertEqual(MODULE.parse_dig_output(output), ("NOERROR", 1))

    def test_check_dns_reports_failed_transport(self) -> None:
        state_path = Path(__file__).with_name(".gp-monitor-test-state.json")
        state_path.unlink(missing_ok=True)
        self.addCleanup(state_path.unlink, missing_ok=True)
        config = {
            "state_file": str(state_path),
            "thresholds": {"dns_warning_seconds": 2},
            "dns_endpoints": [
                {
                    "name": "test-ipv4",
                    "server": "192.0.2.1",
                    "transports": ["udp"],
                }
            ],
        }
        monitor = MODULE.Monitor(config, notify=False)
        monitor.run_command = lambda command, timeout=15: subprocess.CompletedProcess(
            command, 9, "", "timed out"
        )
        monitor.check_dns()
        self.assertEqual(len(monitor.results), 1)
        self.assertEqual(monitor.results[0].severity, "critical")
        self.assertIn("timed out", monitor.results[0].details)


class DatabaseReadinessTests(unittest.TestCase):
    def test_second_consecutive_failure_is_critical_and_success_recovers(self) -> None:
        state_path = Path(__file__).with_name(".gp-monitor-test-state.json")
        state_path.unlink(missing_ok=True)
        self.addCleanup(state_path.unlink, missing_ok=True)
        config = {
            "state_file": str(state_path),
            "database_checks": [{"name": "postgresql", "probe": "postgresql"}],
        }
        monitor = MODULE.Monitor(config, notify=False)
        failed = subprocess.CompletedProcess(["pg_isready"], 2, "rejecting", "")
        monitor.run_command = lambda command, timeout=15: failed

        monitor.check_database_readiness()
        self.assertEqual(monitor.results[-1].severity, "warning")

        monitor.results = []
        monitor.check_database_readiness()
        self.assertEqual(monitor.results[-1].severity, "critical")

        monitor.results = []
        monitor.run_command = lambda command, timeout=15: subprocess.CompletedProcess(
            command, 0, "accepting", ""
        )
        monitor.check_database_readiness()
        self.assertEqual(monitor.results[-1].severity, "ok")


class RouteMonitorTests(unittest.TestCase):
    def test_route_requires_expected_interface_and_recovers(self) -> None:
        state_path = Path(__file__).with_name(".gp-monitor-test-state.json")
        state_path.unlink(missing_ok=True)
        self.addCleanup(state_path.unlink, missing_ok=True)
        config = {
            "state_file": str(state_path),
            "route_checks": [
                {
                    "name": "vpn",
                    "destination": "10.10.200.106",
                    "interface_prefix": "ppp",
                    "critical_consecutive_failures": 2,
                }
            ],
        }
        monitor = MODULE.Monitor(config, notify=False)
        monitor.run_command = lambda command, timeout=15: subprocess.CompletedProcess(
            command, 0, "10.10.200.106 dev eth0 src 192.0.2.10\n", ""
        )

        monitor.check_routes()
        self.assertEqual(monitor.results[-1].severity, "warning")
        monitor.results = []
        monitor.check_routes()
        self.assertEqual(monitor.results[-1].severity, "critical")

        monitor.results = []
        monitor.run_command = lambda command, timeout=15: subprocess.CompletedProcess(
            command, 0, "10.10.200.106 dev ppp0 src 10.10.200.219\n", ""
        )
        monitor.check_routes()
        self.assertEqual(monitor.results[-1].severity, "ok")


class JournalErrorMonitorTests(unittest.TestCase):
    def test_recent_worker_error_escalates_and_clear_window_recovers(self) -> None:
        state_path = Path(__file__).with_name(".gp-monitor-test-state.json")
        state_path.unlink(missing_ok=True)
        self.addCleanup(state_path.unlink, missing_ok=True)
        config = {
            "state_file": str(state_path),
            "journal_error_checks": [
                {
                    "name": "worker",
                    "unit": "worker.service",
                    "patterns": ["Fallback scan failed"],
                    "lookback_minutes": 20,
                    "critical_consecutive_failures": 2,
                }
            ],
        }
        monitor = MODULE.Monitor(config, notify=False)
        error = subprocess.CompletedProcess(
            ["journalctl"], 0, "Fallback scan failed\n", ""
        )
        monitor.run_command = lambda command, timeout=15: error

        monitor.check_journal_errors()
        self.assertEqual(monitor.results[-1].severity, "warning")
        monitor.results = []
        monitor.check_journal_errors()
        self.assertEqual(monitor.results[-1].severity, "critical")

        monitor.results = []
        clear = subprocess.CompletedProcess(["journalctl"], 1, "", "")
        monitor.run_command = lambda command, timeout=15: clear
        monitor.check_journal_errors()
        self.assertEqual(monitor.results[-1].severity, "ok")


class OnlineCompilerAvailabilityTests(unittest.TestCase):
    def projection(self, at="2026-10-02T14:10:00+02:00", status="OPEN"):
        now = datetime.fromisoformat(at).timestamp()
        stamp = datetime.fromtimestamp(now, timezone.utc).isoformat()
        valid = datetime.fromtimestamp(now + 60, timezone.utc).isoformat()
        return now, {"status": status, "policyGeneration": 42,
            "timeZone": "Europe/Warsaw", "availabilityMode": "scheduled",
            "updatedAt": stamp, "validUntil": valid,
            "weeklyWindows": {day: [{"startsAt": "14:00", "endsAt": "22:00"}]
                for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")}}

    def test_open_requires_public_api_and_open_admission(self):
        now, value = self.projection()
        self.assertIsNone(MODULE.online_compiler_availability_problem(value, value, now, 1800))
        self.assertIsNotNone(MODULE.online_compiler_availability_problem(value, None, now, 1800))
        self.assertIsNone(MODULE.online_compiler_availability_problem(value, dict(value, status="STARTING"), now, 1800))
        late, late_value = self.projection("2026-10-02T15:00:00+02:00")
        self.assertIsNotNone(MODULE.online_compiler_availability_problem(late_value, dict(late_value, status="STARTING"), late, 1800))

    def test_closed_hibernation_accepts_unavailable_api(self):
        now, value = self.projection("2026-10-02T10:00:00+02:00", "CLOSED")
        self.assertIsNone(MODULE.online_compiler_availability_problem(value, None, now, 1800))

    def test_transition_deadline_uses_calendar_not_refreshed_timestamp(self):
        for at, status, healthy in (("14:20", "STARTING", True), ("14:31", "STARTING", False),
                                   ("22:20", "STOPPING", True), ("22:31", "STOPPING", False)):
            with self.subTest(at=at):
                now, value = self.projection("2026-10-02T" + at + ":00+02:00", status)
                self.assertEqual(MODULE.online_compiler_availability_problem(value, None, now, 1800) is None, healthy)

    def test_stale_closed_projection_does_not_mask_missed_open(self):
        now, value = self.projection(status="CLOSED")
        self.assertIsNotNone(MODULE.online_compiler_availability_problem(value, None, now, 1800))

    def test_fresh_api_is_independent_of_bad_or_outdated_fallback(self):
        now, value = self.projection()
        for candidate in ({}, [], dict(value, status="FAILED"), dict(value, validUntil=value["updatedAt"]),
                          dict(value, weeklyWindows={}), dict(value, policyGeneration=True)):
            with self.subTest(candidate=candidate):
                self.assertIsNone(MODULE.online_compiler_availability_problem(candidate, value, now, 1800))
                self.assertIsNotNone(MODULE.online_compiler_availability_problem(value, candidate, now, 1800))
        self.assertIsNone(MODULE.online_compiler_availability_problem(value, dict(value, policyGeneration=43), now, 1800))

    def test_warsaw_dst_is_used(self):
        now, value = self.projection("2026-10-25T14:10:00+01:00")
        self.assertEqual(MODULE.online_compiler_availability_phase(value, now), ("OPEN", 600))

    def test_http_json_check_never_accepts_html_redirect_or_unexpected_error(self):
        now, value = self.projection("2026-10-02T10:00:00+02:00", "CLOSED")
        config = {"state_file": "/tmp/gp-monitor-test-unused-state",
                  "online_compiler_availability": {"fallback_url": "https://test.invalid/fallback",
                    "api_url": "https://test.invalid/api", "transition_seconds": 1800}}
        class Response:
            status = 200
            def __init__(self, url, data): self.url, self.data = url, data
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def geturl(self): return self.url
            def read(self, limit): return self.data[:limit]
        for code, expected in ((503, "ok"), (401, "critical"), (200, "critical")):
            with self.subTest(code=code):
                monitor = MODULE.Monitor(config, notify=False); monitor.now = now
                responses = []
                if code == 200:
                    responses.append(Response(config["online_compiler_availability"]["api_url"], b"<html>healthy</html>"))
                else:
                    responses.append(MODULE.urllib.error.HTTPError("https://test.invalid/api", code, "test", {}, None))
                if code == 503:
                    responses.append(Response(config["online_compiler_availability"]["fallback_url"], json.dumps(value).encode()))
                with patch.object(MODULE.urllib.request, "urlopen", side_effect=responses):
                    monitor.check_online_compiler_availability()
                self.assertEqual(monitor.results[-1].severity, expected)
        monitor = MODULE.Monitor(config, notify=False); monitor.now = now
        with patch.object(MODULE.urllib.request, "urlopen", return_value=Response("https://other.invalid/", b"{}")):
            monitor.check_online_compiler_availability()
        self.assertEqual(monitor.results[-1].severity, "critical")

    def test_valid_open_api_does_not_fetch_fallback(self):
        now, value = self.projection()
        with tempfile.TemporaryDirectory() as temp:
            config = {"state_file": str(Path(temp)/"state"), "online_compiler_availability": {
                "api_url": "https://test.invalid/api", "fallback_url": "https://test.invalid/fallback",
                "transition_seconds": 1800}}
            class Response:
                status = 200
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def geturl(self): return "https://test.invalid/api"
                def read(self, limit): return json.dumps(value).encode()
            monitor = MODULE.Monitor(config, notify=False); monitor.now = now
            with patch.object(MODULE.urllib.request, "urlopen", return_value=Response()) as fetch:
                monitor.check_online_compiler_availability()
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(monitor.results[-1].severity, "ok")

    def test_localized_hosts_and_keys_maintenance_are_covered(self):
        config = json.loads(CONFIG_PATH.read_text())
        urls = {row["url"] for row in config["endpoints"]}
        for suffix in ("com", "de", "es", "mx"):
            self.assertIn("https://code.codinggiants." + suffix + "/", urls)
        timer = next(row for row in config["system_timers"] if row["unit"] == "online-compiler-keys-maintenance.timer")
        self.assertEqual(timer["service"], "online-compiler-keys-maintenance.service")
        self.assertEqual(timer["warning_hours"] * 3600, 300)
        self.assertEqual(timer["critical_hours"] * 3600, 600)
        self.assertTrue(timer["require_completion_after_trigger"])


class ProductionHeartbeatV2Tests(unittest.TestCase):
    def payload(self, running=False):
        stamp = "2026-10-02T10:00:00Z"
        return {"schemaVersion": 2, "check": "online-compiler-production-schedule", "sourceHost": "devbox",
                "status": "OPEN", "reconciledAt": stamp, "stateUpdatedAt": stamp,
                "lastCycleResult": "SUCCESS", "timerActive": True, "timerEnabled": True,
                "holdCount": 0, "schedulerHealthy": True, "heartbeatAt": stamp,
                "cycleState": "RUNNING" if running else "IDLE",
                "cycleStartedAt": stamp if running else None,
                "cycleDeadlineAt": "2026-10-02T10:30:00Z" if running else None}

    def test_receiver_accepts_v2_and_rejects_bad_deadline(self):
        value = self.payload(True)
        self.assertEqual(INGEST.validate_payload(json.dumps(value).encode()), value)
        for changes in ({"cycleDeadlineAt": "2026-10-02T11:00:00Z"}, {"cycleStartedAt": None},
                        {"lastCycleResult": "UNKNOWN"}, {"heartbeatAt": "2999-01-01T00:00:00Z"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                INGEST.validate_payload(json.dumps(dict(value, **changes)).encode())

    def test_running_is_bounded_and_does_not_erase_failure(self):
        value = self.payload(True); now = MODULE.timestamp_epoch(value["heartbeatAt"])
        self.assertEqual(MODULE.production_heartbeat_v2_problems(value, now + 1200), [])
        self.assertIn("cycleOverdue", MODULE.production_heartbeat_v2_problems(value, now + 1861))
        for changes, reason in (({"holdCount": 1}, "holdCount"), ({"lastCycleResult": "FAILED"}, "lastCycleResult"),
                               ({"timerActive": False}, "timerActive"), ({"status": "FAILED"}, "status")):
            self.assertIn(reason, MODULE.production_heartbeat_v2_problems(dict(value, **changes), now))

    def test_transition_status_is_healthy_only_during_bounded_running_cycle(self):
        for status in ("STARTING", "STOPPING"):
            value = dict(self.payload(True), status=status)
            now = MODULE.timestamp_epoch(value["heartbeatAt"])
            self.assertEqual(INGEST.validate_payload(json.dumps(value).encode()), value)
            self.assertEqual(MODULE.production_heartbeat_v2_problems(value, now), [])
            value.update(cycleState="IDLE", cycleStartedAt=None, cycleDeadlineAt=None, schedulerHealthy=False)
            self.assertEqual(INGEST.validate_payload(json.dumps(value).encode()), value)
            self.assertIn("status", MODULE.production_heartbeat_v2_problems(value, now))

    def test_initial_unknown_requires_bounded_running_cycle(self):
        value = dict(self.payload(True), lastCycleResult="UNKNOWN", reconciledAt=None, schedulerHealthy=False)
        now = MODULE.timestamp_epoch(value["heartbeatAt"])
        self.assertEqual(INGEST.validate_payload(json.dumps(value).encode()), value)
        self.assertEqual(MODULE.production_heartbeat_v2_problems(value, now), [])
        value.update(cycleState="IDLE", cycleStartedAt=None, cycleDeadlineAt=None)
        self.assertIn("completionStale", MODULE.production_heartbeat_v2_problems(value, now))

    def test_fresh_heartbeat_does_not_hide_idle_stale_completion(self):
        value = self.payload(); now = MODULE.timestamp_epoch(value["heartbeatAt"]) + 301
        value["heartbeatAt"] = "2026-10-02T10:05:01Z"
        self.assertIn("completionStale", MODULE.production_heartbeat_v2_problems(value, now))

    def test_monitor_uses_heartbeat_freshness_and_rejects_future(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "heartbeat.json"
            check = {"name": "online-compiler-production-schedule", "path": str(path)}
            for age, severity in ((0, "ok"), (181, "warning"), (301, "critical"), (-61, "critical")):
                value = self.payload(True); path.write_text(json.dumps(value))
                monitor = MODULE.Monitor({"state_file": str(Path(temp)/"state"), "external_json_heartbeats": [check]}, notify=False)
                monitor.now = MODULE.timestamp_epoch(value["heartbeatAt"]) + age
                monitor.check_external_json_heartbeats()
                self.assertEqual(monitor.results[-1].severity, severity)


class ProductionHeartbeatV3Tests(ProductionHeartbeatV2Tests):
    def payload(self, running=False):
        return dict(super().payload(running), schemaVersion=3,
                    schedulerHealth="HEALTHY", schedulerReasonCode="NONE")

    def test_degraded_open_scheduler_does_not_mean_product_unavailable(self):
        value = dict(self.payload(), schedulerHealth="DEGRADED", schedulerReasonCode="AUTH_OR_CYCLE_FAILED",
                     schedulerHealthy=False, lastCycleResult="FAILED")
        self.assertEqual(INGEST.validate_payload(json.dumps(value).encode()), value)
        now = MODULE.timestamp_epoch(value["heartbeatAt"])
        self.assertIn("schedulerDegraded", MODULE.production_heartbeat_v2_problems(value, now))
        product_now, product = OnlineCompilerAvailabilityTests().projection()
        self.assertIsNone(MODULE.online_compiler_availability_problem({}, product, product_now, 1800))

    def test_v3_rejects_unknown_reason_and_inconsistent_health(self):
        for changes in ({"schedulerReasonCode": "private-provider-response"},
                        {"schedulerHealth": "DEGRADED"}, {"schedulerHealthy": False}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                INGEST.validate_payload(json.dumps(dict(self.payload(), **changes)).encode())

    def test_initial_unknown_requires_bounded_running_cycle(self):
        value = dict(self.payload(True), lastCycleResult="UNKNOWN", reconciledAt=None)
        now = MODULE.timestamp_epoch(value["heartbeatAt"])
        self.assertEqual(INGEST.validate_payload(json.dumps(value).encode()), value)
        self.assertEqual(MODULE.production_heartbeat_v2_problems(value, now), [])
        value.update(cycleState="IDLE", cycleStartedAt=None, cycleDeadlineAt=None,
                     schedulerHealthy=False, schedulerHealth="DEGRADED", schedulerReasonCode="CYCLE_UNKNOWN")
        self.assertIn("completionStale", MODULE.production_heartbeat_v2_problems(value, now))

    def test_transition_status_is_healthy_only_during_bounded_running_cycle(self):
        for status in ("STARTING", "STOPPING"):
            value = dict(self.payload(True), status=status)
            now = MODULE.timestamp_epoch(value["heartbeatAt"])
            self.assertEqual(INGEST.validate_payload(json.dumps(value).encode()), value)
            self.assertEqual(MODULE.production_heartbeat_v2_problems(value, now), [])
            value.update(cycleState="IDLE", cycleStartedAt=None, cycleDeadlineAt=None,
                         schedulerHealthy=False, schedulerHealth="DEGRADED", schedulerReasonCode="STATE_UNVERIFIED")
            self.assertEqual(INGEST.validate_payload(json.dumps(value).encode()), value)
            self.assertIn("status", MODULE.production_heartbeat_v2_problems(value, now))


class HttpMonitorTests(unittest.TestCase):
    def test_http_503_is_a_raw_network_failure_without_legacy_counter(self) -> None:
        state_path = Path(__file__).with_name(".gp-monitor-test-state.json")
        state_path.unlink(missing_ok=True)
        self.addCleanup(state_path.unlink, missing_ok=True)
        config = {
            "state_file": str(state_path),
            "thresholds": {
                "http_warning_seconds": 5,
                "http_critical_seconds": 8,
            },
            "endpoints": [
                {
                    "name": "test",
                    "url": "https://example.invalid/health",
                    "allowed_statuses": [200],
                }
            ],
        }
        monitor = MODULE.Monitor(config, notify=False)
        error = MODULE.urllib.error.HTTPError(
            config["endpoints"][0]["url"], 503, "unavailable", {}, None
        )
        self.addCleanup(error.close)

        with patch.object(MODULE.urllib.request, "urlopen", side_effect=error):
            monitor.check_http()

        self.assertEqual(monitor.results[-1].severity, "critical")
        self.assertEqual(monitor.results[-1].notification_class, "network")
        self.assertNotIn("endpoint_failures", monitor.state)

    def test_optional_host_header_is_sent_only_when_configured(self) -> None:
        state_path = Path(__file__).with_name(".gp-monitor-test-state.json")
        state_path.unlink(missing_ok=True)
        self.addCleanup(state_path.unlink, missing_ok=True)
        config = {
            "state_file": str(state_path),
            "thresholds": {
                "http_warning_seconds": 5,
                "http_critical_seconds": 8,
            },
            "endpoints": [
                {
                    "name": "with-host",
                    "url": "http://127.0.0.1:8108/health/live",
                    "host_header": "ai.gp.edu.pl",
                    "allowed_statuses": [200],
                },
                {
                    "name": "without-host",
                    "url": "http://127.0.0.1:8102/health",
                    "allowed_statuses": [200],
                },
            ],
        }
        monitor = MODULE.Monitor(config, notify=False)

        class Response:
            status = 200

            @staticmethod
            def read(size: int) -> bytes:
                return b"{}"

        requests = []

        def urlopen(request, timeout):
            requests.append(request)
            return Response()

        with patch.object(MODULE.urllib.request, "urlopen", side_effect=urlopen):
            monitor.check_http()

        self.assertEqual(requests[0].get_header("Host"), "ai.gp.edu.pl")
        self.assertIsNone(requests[1].get_header("Host"))

    def test_expected_json_mismatch_is_reported(self) -> None:
        state_path = Path(__file__).with_name(".gp-monitor-test-state.json")
        state_path.unlink(missing_ok=True)
        self.addCleanup(state_path.unlink, missing_ok=True)
        config = {
            "state_file": str(state_path),
            "thresholds": {
                "http_warning_seconds": 5,
                "http_critical_seconds": 8,
            },
            "endpoints": [
                {
                    "name": "mcp",
                    "url": "http://127.0.0.1/health",
                    "allowed_statuses": [200],
                    "expected_json": {"ok": True, "catalog_errors": []},
                }
            ],
        }
        monitor = MODULE.Monitor(config, notify=False)

        class Response:
            status = 200

            @staticmethod
            def read(size: int) -> bytes:
                return b'{"ok": true, "catalog_errors": ["backend"]}'

        with patch.object(MODULE.urllib.request, "urlopen", return_value=Response()):
            monitor.check_http()

        self.assertIn("unhealthy JSON", monitor.results[-1].summary)


class ExternalHeartbeatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "heartbeat.json"
        self.state = Path(self.temporary.name) / "state.json"
        self.config = {
            "state_file": str(self.state),
            "external_json_heartbeats": [
                {
                    "name": "production-schedule",
                    "path": str(self.path),
                    "warning_seconds": 1200,
                    "critical_seconds": 2100,
                    "allowed_statuses": ["OPEN", "CLOSED"],
                    "expected_json": {
                        "schemaVersion": 1,
                        "check": "online-compiler-production-schedule",
                        "sourceHost": "devbox",
                        "timerActive": True,
                        "timerEnabled": True,
                        "holdCount": 0,
                        "lastCycleResult": "SUCCESS",
                        "schedulerHealthy": True,
                    },
                }
            ],
        }

    def payload(self, reconciled_at: str = "2026-09-24T12:00:00Z") -> dict:
        return {
            "schemaVersion": 1,
            "check": "online-compiler-production-schedule",
            "sourceHost": "devbox",
            "status": "OPEN",
            "reconciledAt": reconciled_at,
            "stateUpdatedAt": "2026-09-24T11:59:00Z",
            "lastCycleResult": "SUCCESS",
            "timerActive": True,
            "timerEnabled": True,
            "holdCount": 0,
            "schedulerHealthy": True,
        }

    def check(self, age_seconds: int, payload: dict | None = None):
        if payload is not None:
            self.path.write_text(json.dumps(payload), encoding="utf-8")
        monitor = MODULE.Monitor(self.config, notify=False)
        monitor.now = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc).timestamp()
        if age_seconds:
            observed = datetime.fromtimestamp(
                monitor.now - age_seconds, tz=timezone.utc
            ).isoformat().replace("+00:00", "Z")
            value = self.payload(observed) if payload is None else dict(payload)
            value["reconciledAt"] = observed
            self.path.write_text(json.dumps(value), encoding="utf-8")
        monitor.check_external_json_heartbeats()
        return monitor.results[-1]

    def test_fresh_healthy_heartbeat_is_ok(self) -> None:
        result = self.check(60, self.payload())
        self.assertEqual(result.severity, "ok")

    def test_stale_heartbeat_warns_then_becomes_critical(self) -> None:
        self.assertEqual(self.check(1260).severity, "warning")
        self.assertEqual(self.check(2160).severity, "critical")

    def test_missing_or_malformed_heartbeat_is_critical(self) -> None:
        self.assertEqual(self.check(0).severity, "critical")
        self.path.write_text("not-json", encoding="utf-8")
        self.assertEqual(self.check(0).severity, "critical")

    def test_unhealthy_fields_are_critical(self) -> None:
        for field, value in (
            ("timerActive", False),
            ("timerEnabled", False),
            ("holdCount", 1),
            ("status", "FAILED"),
            ("lastCycleResult", "FAILED"),
            ("schedulerHealthy", False),
        ):
            with self.subTest(field=field):
                payload = self.payload()
                payload[field] = value
                self.assertEqual(self.check(0, payload).severity, "critical")

    def test_future_timestamp_is_critical(self) -> None:
        self.assertEqual(self.check(-120, self.payload("2026-09-24T12:02:00Z")).severity, "critical")


class NextDevHibernationHeartbeatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "heartbeat.json"
        self.config = {
            "state_file": str(Path(self.temporary.name) / "state.json"),
            "external_json_heartbeats": [
                {
                    "name": "next-dev-hibernation",
                    "path": str(self.path),
                    "warning_seconds": 1200,
                    "critical_seconds": 2100,
                    "allowed_statuses": ["ACTIVE", "HIBERNATED", "FAILED"],
                    "semantic_policy": "online-compiler-next-dev-hibernation-v1",
                    "overdue_grace_seconds": 900,
                    "expected_json": {
                        "schemaVersion": 1,
                        "check": "online-compiler-next-dev-hibernation",
                        "sourceHost": "devbox",
                        "idleThresholdSeconds": 3600,
                    },
                }
            ],
        }

    def payload(self, **changes) -> dict:
        value = {
            "schemaVersion": 1,
            "check": "online-compiler-next-dev-hibernation",
            "sourceHost": "devbox",
            "status": "ACTIVE",
            "reconciledAt": "2026-09-24T12:00:00Z",
            "lastCycleResult": "SUCCESS",
            "timerActive": True,
            "timerEnabled": True,
            "holdCount": 0,
            "activityCount": 0,
            "leaseCount": 0,
            "idleSeconds": 300,
            "idleThresholdSeconds": 3600,
            "hibernationHealthy": True,
        }
        value.update(changes)
        return value

    def check(self, payload: dict):
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        monitor = MODULE.Monitor(self.config, notify=False)
        monitor.now = datetime(2026, 9, 24, 12, 1, tzinfo=timezone.utc).timestamp()
        monitor.check_external_json_heartbeats()
        return monitor.results[-1]

    def test_active_and_hibernated_states_are_healthy(self) -> None:
        self.assertEqual(self.check(self.payload()).severity, "ok")
        self.assertEqual(
            self.check(self.payload(status="HIBERNATED", idleSeconds=9000)).severity,
            "ok",
        )

    def test_failed_state_timer_and_cycle_are_critical(self) -> None:
        for changes in (
            {"status": "FAILED", "hibernationHealthy": False},
            {"timerActive": False, "hibernationHealthy": False},
            {"timerEnabled": False, "hibernationHealthy": False},
            {"lastCycleResult": "FAILED", "hibernationHealthy": False},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(self.check(self.payload(**changes)).severity, "critical")

    def test_active_overdue_without_blockers_is_critical(self) -> None:
        result = self.check(
            self.payload(idleSeconds=4500, hibernationHealthy=False)
        )
        self.assertEqual(result.severity, "critical")
        self.assertIn("overdueActive", result.details)

    def test_active_overdue_with_a_hold_is_healthy(self) -> None:
        result = self.check(
            self.payload(
                idleSeconds=4500,
                holdCount=1,
                lastCycleResult="BLOCKED",
            )
        )
        self.assertEqual(result.severity, "ok")

    def test_healthy_claim_must_match_semantics(self) -> None:
        result = self.check(self.payload(idleSeconds=4500))
        self.assertEqual(result.severity, "critical")
        self.assertIn("hibernationHealthyMismatch", result.details)


class HeartbeatIngestTests(unittest.TestCase):
    def payload(self) -> bytes:
        return json.dumps(
            {
                "schemaVersion": 1,
                "check": "online-compiler-production-schedule",
                "sourceHost": "devbox",
                "status": "CLOSED",
                "reconciledAt": "2026-09-24T12:00:00Z",
                "stateUpdatedAt": "2026-09-24T11:59:00Z",
                "lastCycleResult": "SUCCESS",
                "timerActive": True,
                "timerEnabled": True,
                "holdCount": 0,
                "schedulerHealthy": True,
            }
        ).encode("ascii")

    def test_validate_and_atomic_store(self) -> None:
        value = INGEST.validate_payload(self.payload())
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "heartbeat.json"
            INGEST.store_payload(self.payload(), target)
            self.assertEqual(json.loads(target.read_text()), value)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_rejects_extra_fields_and_invalid_types(self) -> None:
        value = json.loads(self.payload())
        value["extra"] = True
        with self.assertRaises(ValueError):
            INGEST.validate_payload(json.dumps(value).encode("ascii"))
        value.pop("extra")
        value["holdCount"] = True
        with self.assertRaises(ValueError):
            INGEST.validate_payload(json.dumps(value).encode("ascii"))

    def test_accepts_next_dev_hibernation_payload(self) -> None:
        payload = {
            "schemaVersion": 1,
            "check": "online-compiler-next-dev-hibernation",
            "sourceHost": "devbox",
            "status": "HIBERNATED",
            "reconciledAt": "2026-09-24T12:00:00Z",
            "lastCycleResult": "SUCCESS",
            "timerActive": True,
            "timerEnabled": True,
            "holdCount": 0,
            "activityCount": 0,
            "leaseCount": 0,
            "idleSeconds": 4500,
            "idleThresholdSeconds": 3600,
            "hibernationHealthy": True,
        }
        self.assertEqual(
            INGEST.validate_payload(json.dumps(payload).encode("ascii")), payload
        )

    def test_rejects_stale_next_dev_idle_threshold(self) -> None:
        payload = {
            "schemaVersion": 1,
            "check": "online-compiler-next-dev-hibernation",
            "sourceHost": "devbox",
            "status": "ACTIVE",
            "reconciledAt": "2026-09-24T12:00:00Z",
            "lastCycleResult": "SUCCESS",
            "timerActive": True,
            "timerEnabled": True,
            "holdCount": 0,
            "activityCount": 0,
            "leaseCount": 0,
            "idleSeconds": 300,
            "idleThresholdSeconds": 7200,
            "hibernationHealthy": True,
        }
        with self.assertRaises(ValueError):
            INGEST.validate_payload(json.dumps(payload).encode("ascii"))

    def test_rejects_unknown_check_and_bad_next_dev_types(self) -> None:
        unknown = json.loads(self.payload())
        unknown["check"] = "unknown"
        with self.assertRaises(ValueError):
            INGEST.validate_payload(json.dumps(unknown).encode("ascii"))

        payload = {
            "schemaVersion": 1,
            "check": "online-compiler-next-dev-hibernation",
            "sourceHost": "devbox",
            "status": "ACTIVE",
            "reconciledAt": "2026-09-24T12:00:00Z",
            "lastCycleResult": "SUCCESS",
            "timerActive": True,
            "timerEnabled": True,
            "holdCount": 0,
            "activityCount": 0,
            "leaseCount": 0,
            "idleSeconds": True,
            "idleThresholdSeconds": 3600,
            "hibernationHealthy": True,
        }
        with self.assertRaises(ValueError):
            INGEST.validate_payload(json.dumps(payload).encode("ascii"))


class CompletionMarkerTests(unittest.TestCase):
    def test_mark_completed_writes_atomic_marker(self) -> None:
        state_path = Path(__file__).with_name(".gp-monitor-test-state.json")
        completed_path = state_path.parent / "last_completed"
        state_path.unlink(missing_ok=True)
        completed_path.unlink(missing_ok=True)
        self.addCleanup(state_path.unlink, missing_ok=True)
        self.addCleanup(completed_path.unlink, missing_ok=True)
        monitor = MODULE.Monitor({"state_file": str(state_path)}, notify=False)
        monitor.now = 1_800_000_000.0

        monitor.mark_completed()

        self.assertEqual(completed_path.read_text(encoding="utf-8"), "1800000000.000000\n")


class NotificationPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state_path = Path(self.temporary.name) / "state.json"
        self.config = {
            "state_file": str(self.state_path),
            "host_label": "gp",
            "notifications": {
                "default_failure_confirmation_cycles": 2,
                "network_failure_confirmation_cycles": 3,
                "recovery_confirmation_cycles": 2,
                "reminder_seconds": 21600,
                "max_items_per_message": 20,
                "suppressed_recovery_keys": [],
                "suppressed_key_prefixes": [],
            },
        }
        self.monitor = MODULE.Monitor(self.config, notify=True)
        self.monitor.now = 1_800_000_000.0
        self.messages = []
        self.monitor.send = self._capture

    def _capture(self, summary, details=""):
        self.messages.append(summary)
        return True

    def cycle(self, *results, advance=300):
        self.monitor.results = []
        for result in results:
            self.monitor.add(*result)
        with patch("builtins.print"):
            self.monitor.process_alerts()
        self.monitor.now += advance

    def test_single_network_failure_and_recovery_are_silent(self) -> None:
        self.cycle(("endpoint:test", "critical", "HTTP 503", "", "", "network"))
        self.cycle(("endpoint:test", "ok", "healthy", "", "", "network"))

        self.assertEqual(self.messages, [])

    def test_third_network_failure_sends_one_alert(self) -> None:
        failure = ("endpoint:test", "critical", "HTTP 503", "", "", "network")
        self.cycle(failure)
        self.cycle(failure)
        self.cycle(failure)
        self.cycle(failure)

        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.messages[0], "[CRITICAL]\nNew: endpoint:test")

    def test_second_sampled_failure_sends_one_alert(self) -> None:
        failure = ("service:test.service", "critical", "not healthy")
        self.cycle(failure)
        self.cycle(failure)

        self.assertEqual(len(self.messages), 1)

    def test_durable_failed_invocation_alerts_once_per_invocation(self) -> None:
        first = (
            "system-service:test.service:result",
            "critical",
            "test.service failed",
            "",
            "first",
            "durable",
        )
        second = (*first[:4], "second", "durable")
        self.cycle(first)
        self.cycle(first)
        self.cycle(second)

        self.assertEqual(len(self.messages), 2)

    def test_recovery_requires_two_healthy_cycles(self) -> None:
        failure = ("service:test.service", "critical", "not healthy")
        healthy = ("service:test.service", "ok", "active")
        self.cycle(failure)
        self.cycle(failure)
        self.cycle(healthy)
        self.assertEqual(len(self.messages), 1)
        self.cycle(healthy)

        self.assertEqual(len(self.messages), 2)
        self.assertEqual(
            self.messages[-1],
            "[RESOLVED]\nResolved: service:test.service",
        )

    def test_interrupted_recovery_continues_same_incident(self) -> None:
        failure = ("service:test.service", "critical", "not healthy")
        healthy = ("service:test.service", "ok", "active")
        self.cycle(failure)
        self.cycle(failure)
        self.cycle(healthy)
        self.cycle(failure)
        self.cycle(healthy)
        self.cycle(healthy)

        self.assertEqual(len(self.messages), 2)

    def test_escalation_requires_confirmation(self) -> None:
        warning = ("filesystem:space:/", "warning", "80%")
        critical = ("filesystem:space:/", "critical", "90%")
        self.cycle(warning)
        self.cycle(warning)
        self.cycle(critical)
        self.assertEqual(len(self.messages), 1)
        self.cycle(critical)

        self.assertEqual(len(self.messages), 2)
        self.assertIn("CRITICAL", self.messages[-1])

    def test_five_network_checks_are_batched_with_one_six_hour_reminder(self) -> None:
        failures = tuple(
            (f"endpoint:ai-imaging-{index}", "critical", "unhealthy", "", "", "network")
            for index in range(5)
        )
        self.cycle(*failures)
        self.cycle(*failures)
        self.cycle(*failures)
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.messages[0].splitlines()[0], "[CRITICAL]")
        self.assertIn("New: endpoint:ai-imaging-0", self.messages[0])

        self.monitor.now += 21600
        self.cycle(*failures)

        self.assertEqual(len(self.messages), 2)
        self.assertEqual(self.messages[-1].splitlines()[0], "[REMINDER/CRITICAL]")
        self.assertIn("Active: endpoint:ai-imaging-0", self.messages[-1])

    def test_canva_recovery_is_silent_but_later_failure_alerts_again(self) -> None:
        key = "service:canva-pro-keys.service"
        failure = (key, "critical", "not healthy")
        healthy = (key, "ok", "active")
        self.config["notifications"]["suppressed_recovery_keys"] = [key]

        self.cycle(failure)
        self.cycle(failure)
        self.cycle(healthy)
        self.cycle(healthy)

        self.assertEqual(self.messages, [f"[CRITICAL]\nNew: {key}"])
        self.assertFalse(self.monitor.state["checks"][key]["alert_active"])

        self.cycle(failure)
        self.cycle(failure)

        self.assertEqual(
            self.messages,
            [f"[CRITICAL]\nNew: {key}", f"[CRITICAL]\nNew: {key}"],
        )

    def test_mixed_recovery_omits_only_suppressed_canva_key(self) -> None:
        canva_key = "endpoint:canva-pro-keys-local"
        other_key = "service:example.service"
        self.config["notifications"]["suppressed_recovery_keys"] = [canva_key]

        failures = (
            (canva_key, "critical", "HTTP 503", "", "", "network"),
            (other_key, "critical", "not healthy"),
        )
        self.cycle(*failures)
        self.cycle(*failures)
        self.cycle(*failures)
        self.messages.clear()

        healthy = (
            (canva_key, "ok", "HTTP 200", "", "", "network"),
            (other_key, "ok", "active"),
        )
        self.cycle(*healthy)
        self.cycle(*healthy)

        self.assertEqual(
            self.messages,
            [f"[RESOLVED]\nResolved: {other_key}"],
        )
        self.assertFalse(self.monitor.state["checks"][canva_key]["alert_active"])
        self.assertFalse(self.monitor.state["checks"][other_key]["alert_active"])

    def test_legacy_notified_state_migrates_without_duplicate(self) -> None:
        self.state_path.write_text(
            json.dumps(
                {
                    "checks": {
                        "service:legacy.service": {
                            "severity": "critical",
                            "last_seen": self.monitor.now - 300,
                            "last_notification": self.monitor.now - 300,
                            "summary": "not healthy",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        self.monitor = MODULE.Monitor(self.config, notify=True)
        self.monitor.now = 1_800_000_000.0
        self.monitor.send = self._capture
        self.cycle(("service:legacy.service", "critical", "not healthy"))

        self.assertEqual(self.messages, [])
        self.assertTrue(
            self.monitor.state["checks"]["service:legacy.service"]["alert_active"]
        )

    def test_suppressed_persistent_problem_alerts_once_after_unsuppress(self) -> None:
        self.config["notifications"]["suppressed_key_prefixes"] = ["dns:"]
        failure = ("dns:test:udp", "critical", "query failed", "", "", "network")
        self.cycle(failure)
        self.cycle(failure)
        self.cycle(failure)
        self.assertEqual(self.messages, [])

        self.config["notifications"]["suppressed_key_prefixes"] = []
        self.cycle(failure)

        self.assertEqual(len(self.messages), 1)

    def test_delivery_failure_does_not_persist_transition(self) -> None:
        self.monitor.send = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("delivery failed")
        )
        self.monitor.add(
            "system-service:test.service:result",
            "critical",
            "test.service failed",
            incident_id="first",
            notification_class="durable",
        )
        with self.assertRaisesRegex(RuntimeError, "delivery failed"):
            self.monitor.process_alerts()

        reloaded = MODULE.Monitor(self.config, notify=False)
        self.assertEqual(reloaded.state, {"checks": {}})

    def test_successful_send_writes_sanitized_audit_record(self) -> None:
        self.monitor.results = []
        self.monitor.add(
            "system-service:test.service:result",
            "critical",
            "test.service failed",
            details="private diagnostic",
            incident_id="first",
            notification_class="durable",
        )

        with patch("builtins.print") as output:
            self.monitor.process_alerts()

        output.assert_called_once_with(
            "NOTIFY_SENT reason=transition severity=critical new=1 active=1 resolved=0",
            file=MODULE.sys.stderr,
        )
        self.assertNotIn("private diagnostic", self.messages[0])


class SystemTimerTests(unittest.TestCase):

    def test_check_user_timer_allows_configured_suspension(self) -> None:
        state_path = Path(__file__).with_name(".gp-monitor-test-state.json")
        state_path.unlink(missing_ok=True)
        self.addCleanup(state_path.unlink, missing_ok=True)
        config = {
            "state_file": str(state_path),
            "user_units": {
                "user": "ubuntu",
                "uid": 1000,
                "timers": [
                    {
                        "unit": "example.timer",
                        "service": "example.service",
                        "suspended_until": "2026-11-01T00:15:00+01:00",
                        "warning_hours": 1,
                        "critical_hours": 2,
                    }
                ],
            },
        }
        monitor = MODULE.Monitor(config, notify=False)
        monitor.now = datetime.fromisoformat("2026-09-29T17:00:00+02:00").timestamp()

        def fail_if_called(*_arguments, **_kwargs):
            raise AssertionError("systemctl should not run during a suspension")

        monitor.user_systemctl = fail_if_called
        monitor.check_user_timers()

        self.assertEqual(len(monitor.results), 2)
        self.assertTrue(all(result.severity == "ok" for result in monitor.results))
        self.assertIn("monitoring is suspended", monitor.results[0].summary)

    def test_check_system_timer_allows_pending_first_run(self) -> None:
        state_path = Path(__file__).with_name(".gp-monitor-test-state.json")
        state_path.unlink(missing_ok=True)
        self.addCleanup(state_path.unlink, missing_ok=True)
        config = {
            "state_file": str(state_path),
            "system_timers": [
                {
                    "unit": "example.timer",
                    "service": "example.service",
                    "warning_hours": 1,
                    "critical_hours": 2,
                }
            ],
        }
        monitor = MODULE.Monitor(config, notify=False)

        def fake_run_command(command, timeout=15):
            if command == ["systemctl", "is-active", "example.timer"]:
                return subprocess.CompletedProcess(command, 0, "active\n", "")
            if command == ["systemctl", "is-enabled", "example.timer"]:
                return subprocess.CompletedProcess(command, 0, "enabled\n", "")
            if command[:3] == ["systemctl", "show", "example.service"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "\n".join(
                        [
                            "Result=success",
                            "ExecMainStatus=0",
                            "ExecMainExitTimestamp=",
                            "ActiveState=inactive",
                            "SubState=dead",
                        ]
                    ),
                    "",
                )
            if command[:3] == ["journalctl", "-u", "example.service"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            if command == [
                "systemctl",
                "show",
                "example.timer",
                "--property=LastTriggerUSec",
            ]:
                return subprocess.CompletedProcess(command, 0, "LastTriggerUSec=\n", "")
            return subprocess.CompletedProcess(command, 1, "", "unexpected command")

        monitor.run_command = fake_run_command
        monitor.check_system_timers()

        service_results = [
            result
            for result in monitor.results
            if result.key == "system-service:example.service:result"
        ]
        self.assertEqual(len(service_results), 1)
        self.assertEqual(service_results[0].severity, "ok")
        self.assertIn("awaiting its first scheduled run", service_results[0].summary)

    def test_check_system_timer_uses_journal_when_systemd_timestamp_is_empty(self) -> None:
        state_path = Path(__file__).with_name(".gp-monitor-test-state.json")
        state_path.unlink(missing_ok=True)
        self.addCleanup(state_path.unlink, missing_ok=True)
        config = {
            "state_file": str(state_path),
            "system_timers": [
                {
                    "unit": "example.timer",
                    "service": "example.service",
                    "warning_hours": 1,
                    "critical_hours": 2,
                }
            ],
        }
        monitor = MODULE.Monitor(config, notify=False)
        monitor.now = 1_800_000_000.0
        finished_at = int((monitor.now - 30 * 60) * 1_000_000)

        def fake_run_command(command, timeout=15):
            if command == ["systemctl", "is-active", "example.timer"]:
                return subprocess.CompletedProcess(command, 0, "active\n", "")
            if command == ["systemctl", "is-enabled", "example.timer"]:
                return subprocess.CompletedProcess(command, 0, "enabled\n", "")
            if command[:3] == ["systemctl", "show", "example.service"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "\n".join(
                        [
                            "Result=success",
                            "ExecMainStatus=0",
                            "ExecMainExitTimestamp=",
                            "ActiveState=inactive",
                            "SubState=dead",
                        ]
                    ),
                    "",
                )
            if command[:3] == ["journalctl", "-u", "example.service"]:
                event = {
                    "UNIT": "example.service",
                    "MESSAGE": "Finished example.service - Example job.",
                    "__REALTIME_TIMESTAMP": str(finished_at),
                }
                return subprocess.CompletedProcess(command, 0, json.dumps(event), "")
            return subprocess.CompletedProcess(command, 1, "", "unexpected command")

        monitor.run_command = fake_run_command
        monitor.check_system_timers()

        service_results = [
            result
            for result in monitor.results
            if result.key == "system-service:example.service:result"
        ]
        self.assertEqual(len(service_results), 1)
        self.assertEqual(service_results[0].severity, "ok")
        self.assertIn("last completed 30m ago", service_results[0].summary)

    def test_require_completion_after_latest_trigger_detects_skipped_job(self) -> None:
        state_path = Path(__file__).with_name(".gp-monitor-test-state.json")
        state_path.unlink(missing_ok=True)
        self.addCleanup(state_path.unlink, missing_ok=True)
        config = {
            "state_file": str(state_path),
            "system_timers": [
                {
                    "unit": "example.timer",
                    "service": "example.service",
                    "require_completion_after_trigger": True,
                    "completion_grace_seconds": 60,
                    "warning_hours": 1,
                    "critical_hours": 2,
                }
            ],
        }
        monitor = MODULE.Monitor(config, notify=False)
        monitor.now = datetime.fromisoformat("2030-01-01T00:02:00+00:00").timestamp()

        def fake_run_command(command, timeout=15):
            if command == ["systemctl", "is-active", "example.timer"]:
                return subprocess.CompletedProcess(command, 0, "active\n", "")
            if command == ["systemctl", "is-enabled", "example.timer"]:
                return subprocess.CompletedProcess(command, 0, "enabled\n", "")
            if command[:3] == ["systemctl", "show", "example.service"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "\n".join(
                        [
                            "Result=success",
                            "ExecMainStatus=0",
                            "ExecMainExitTimestamp=2025-12-31T23:00:00+00:00",
                            "ActiveState=inactive",
                            "SubState=dead",
                        ]
                    ),
                    "",
                )
            if command == [
                "systemctl",
                "show",
                "example.timer",
                "--property=LastTriggerUSec",
            ]:
                return subprocess.CompletedProcess(
                    command, 0, "LastTriggerUSec=2030-01-01T00:00:00+00:00\n", ""
                )
            return subprocess.CompletedProcess(command, 1, "", "unexpected command")

        monitor.run_command = fake_run_command
        monitor.check_system_timers()

        service_results = [
            result
            for result in monitor.results
            if result.key == "system-service:example.service:result"
        ]
        self.assertEqual(service_results[0].severity, "critical")
        self.assertIn("did not complete", service_results[0].summary)

    def test_check_system_timer_allows_configured_suspension(self) -> None:
        state_path = Path(__file__).with_name(".gp-monitor-test-state.json")
        state_path.unlink(missing_ok=True)
        self.addCleanup(state_path.unlink, missing_ok=True)
        config = {
            "state_file": str(state_path),
            "system_timers": [
                {
                    "unit": "example.timer",
                    "service": "example.service",
                    "suspended_until": "2026-08-28T00:05:00+02:00",
                    "warning_hours": 1,
                    "critical_hours": 2,
                }
            ],
        }
        monitor = MODULE.Monitor(config, notify=False)
        monitor.now = datetime.fromisoformat("2026-07-04T12:02:36+02:00").timestamp()

        def fail_if_called(command, timeout=15):
            raise AssertionError(f"unexpected command: {command}")

        monitor.run_command = fail_if_called
        monitor.check_system_timers()

        self.assertEqual(len(monitor.results), 2)
        self.assertTrue(all(result.severity == "ok" for result in monitor.results))
        self.assertIn("monitoring is suspended", monitor.results[0].summary)

    def test_check_system_timer_requires_active_after_suspension_expires(self) -> None:
        state_path = Path(__file__).with_name(".gp-monitor-test-state.json")
        state_path.unlink(missing_ok=True)
        self.addCleanup(state_path.unlink, missing_ok=True)
        config = {
            "state_file": str(state_path),
            "system_timers": [
                {
                    "unit": "example.timer",
                    "service": "example.service",
                    "suspended_until": "2026-08-28T00:05:00+02:00",
                    "warning_hours": 1,
                    "critical_hours": 2,
                }
            ],
        }
        monitor = MODULE.Monitor(config, notify=False)
        monitor.now = datetime.fromisoformat("2026-08-28T00:06:00+02:00").timestamp()

        def fake_run_command(command, timeout=15):
            if command == ["systemctl", "is-active", "example.timer"]:
                return subprocess.CompletedProcess(command, 3, "inactive\n", "")
            if command == ["systemctl", "is-enabled", "example.timer"]:
                return subprocess.CompletedProcess(command, 1, "disabled\n", "")
            if command[:3] == ["systemctl", "show", "example.service"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "\n".join(
                        [
                            "Result=success",
                            "ExecMainStatus=0",
                            "ExecMainExitTimestamp=",
                            "ActiveState=inactive",
                            "SubState=dead",
                        ]
                    ),
                    "",
                )
            if command[:3] == ["journalctl", "-u", "example.service"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            if command == [
                "systemctl",
                "show",
                "example.timer",
                "--property=LastTriggerUSec",
            ]:
                return subprocess.CompletedProcess(command, 0, "LastTriggerUSec=\n", "")
            return subprocess.CompletedProcess(command, 1, "", "unexpected command")

        monitor.run_command = fake_run_command
        monitor.check_system_timers()

        timer_results = [
            result for result in monitor.results if result.key == "system-timer:example.timer"
        ]
        self.assertEqual(len(timer_results), 1)
        self.assertEqual(timer_results[0].severity, "critical")


if __name__ == "__main__":
    unittest.main()
