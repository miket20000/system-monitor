#!/usr/bin/env python3
"""Monitor critical devbox resources and services with stateful Telegram alerts."""

from __future__ import annotations

import argparse
import fnmatch
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SEVERITY_ORDER = {"ok": 0, "warning": 1, "critical": 2}


def format_telegram_notification(summary: str, host_label: str) -> str:
    clean_summary = " ".join(summary.splitlines()).strip()
    clean_host = " ".join(host_label.splitlines()).strip()
    return f"{clean_summary}\nHost: {clean_host}"


@dataclass
class Result:
    key: str
    severity: str
    summary: str
    details: str = ""
    incident_id: str = ""
    notification_class: str = "sampled"
    notification_code: str = ""
    notification_context: dict[str, Any] | None = None


@dataclass
class ServiceOutcome:
    status: str
    observed_at: float
    result: str = ""
    exit_status: str = ""
    incident_id: str = ""


class Monitor:
    def __init__(self, config: dict[str, Any], notify: bool = True) -> None:
        self.config = config
        self.notify = notify
        self.now = time.time()
        self.state_path = Path(config["state_file"])
        self.state_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.state = self._load_state()
        self.results: list[Result] = []

    def _load_state(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {"checks": {}}

    def save_state(self) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.state, indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.chmod(0o600)
        temporary.replace(self.state_path)

    def run_command(
        self, command: list[str], timeout: int = 15
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    @staticmethod
    def parse_properties(output: str) -> dict[str, str]:
        properties: dict[str, str] = {}
        for line in output.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                properties[key] = value.strip()
        return properties

    def add(
        self,
        key: str,
        severity: str,
        summary: str,
        details: str = "",
        incident_id: str = "",
        notification_class: str = "sampled",
        notification_code: str = "",
        notification_context: dict[str, Any] | None = None,
    ) -> None:
        self.results.append(
            Result(
                key, severity, summary, details, incident_id, notification_class,
                notification_code, notification_context,
            )
        )

    def check_gp_dead_man(self) -> None:
        check = self.config.get("gp_dead_man")
        if not check:
            return
        command = [
            "/usr/bin/ssh", "-F", check["ssh_config"], "-o", "BatchMode=yes",
            "-o", "ClearAllForwardings=yes", "-o", "ConnectTimeout=10", "-T",
            check["host"], "/usr/bin/env", "LC_ALL=C", "TZ=UTC",
            "/usr/bin/systemctl", "show", "gp-monitor.service",
            "--property=Result", "--property=ExecMainStatus",
            "--property=ExecMainExitTimestamp", "--no-pager",
        ]
        key = "remote-dead-man:gp-monitor"
        try:
            result = self.run_command(command, timeout=15)
            values = self.parse_properties(result.stdout)
            completed = timestamp_epoch(values.get("ExecMainExitTimestamp", ""))
            age = self.now - completed if completed is not None else float("inf")
            healthy = (
                result.returncode == 0
                and values.get("Result") == "success"
                and values.get("ExecMainStatus") == "0"
                and -60 <= age <= float(check["max_age_seconds"])
            )
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
            healthy = False
        self.add(
            key, "ok" if healthy else "critical",
            "GP monitor completed recently" if healthy else "GP monitor has no recent successful cycle",
            notification_class="network",
            notification_code="GP_MONITOR_DEAD_MAN" if not healthy else "",
        )

    def check_filesystems(self) -> None:
        mounts: list[str] = []
        with open("/proc/1/mounts", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) < 4:
                    continue
                device, mount, fs_type, options = parts[:4]
                if mount == "/" and "ro" in options.split(","):
                    self.add(
                        "filesystem:root-read-only",
                        "critical",
                        "Root filesystem is mounted read-only",
                    )
                if (
                    not device.startswith("/dev/")
                    or fs_type in {"iso9660", "squashfs"}
                    or fs_type.startswith("fuse")
                    or mount.startswith(("/snap/", "/boot/efi"))
                ):
                    continue
                mounts.append(mount)

        if not any(result.key == "filesystem:root-read-only" for result in self.results):
            self.add(
                "filesystem:root-read-only",
                "ok",
                "Root filesystem is writable",
            )

        for mount in sorted(set(mounts)):
            usage = shutil.disk_usage(mount)
            used_percent = round((usage.used / usage.total) * 100)
            self.add(
                f"filesystem:space:{mount}",
                threshold_severity(
                    used_percent,
                    self.config["thresholds"]["disk_warning_percent"],
                    self.config["thresholds"]["disk_critical_percent"],
                ),
                f"Disk usage on {mount}: {used_percent}%",
            )
            stats = os.statvfs(mount)
            if stats.f_files:
                inode_percent = round(
                    ((stats.f_files - stats.f_ffree) / stats.f_files) * 100
                )
                self.add(
                    f"filesystem:inodes:{mount}",
                    threshold_severity(
                        inode_percent,
                        self.config["thresholds"]["inode_warning_percent"],
                        self.config["thresholds"]["inode_critical_percent"],
                    ),
                    f"Inode usage on {mount}: {inode_percent}%",
                )

    def check_mounts(self) -> None:
        for check in self.config.get("mount_checks", []):
            path = check["path"]
            key = f"mount:{path}"
            result = self.run_command(
                ["findmnt", "-J", "-T", path, "-o", "SOURCE,TARGET,FSTYPE,OPTIONS,UUID"]
            )
            if result.returncode != 0:
                self.add(key, "critical", f"Required mount {path} is missing", result.stderr.strip())
                continue
            try:
                filesystems = json.loads(result.stdout).get("filesystems", [])
                mounted = filesystems[0]
            except (json.JSONDecodeError, IndexError, KeyError, TypeError):
                self.add(key, "critical", f"Unable to identify required mount {path}")
                continue

            options = set(str(mounted.get("options", "")).split(","))
            mismatches: list[str] = []
            if mounted.get("target") != path:
                mismatches.append(f"target={mounted.get('target', '')}")
            if mounted.get("uuid") != check["uuid"]:
                mismatches.append(f"uuid={mounted.get('uuid', '')}")
            if mounted.get("fstype") != check["fstype"]:
                mismatches.append(f"fstype={mounted.get('fstype', '')}")
            if check.get("writable", True) and "rw" not in options:
                mismatches.append("not writable")
            if mismatches:
                self.add(
                    key,
                    "critical",
                    f"Required mount {path} does not match configuration",
                    ", ".join(mismatches),
                )
            else:
                self.add(key, "ok", f"Required mount {path} is healthy")

    def check_memory(self) -> None:
        values: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                key, value = line.split(":", 1)
                values[key] = int(value.strip().split()[0])
        memory_percent = round(
            ((values["MemTotal"] - values["MemAvailable"]) / values["MemTotal"]) * 100
        )
        memory_severity = threshold_severity(
            memory_percent,
            self.config["thresholds"]["memory_warning_percent"],
            self.config["thresholds"]["memory_critical_percent"],
        )
        self.add(
            "memory:used",
            memory_severity,
            f"Memory usage: {memory_percent}%",
        )

    def check_load(self) -> None:
        load_5m = os.getloadavg()[1]
        cpus = os.cpu_count() or 1
        self.add(
            "load:5m",
            threshold_severity(
                load_5m,
                cpus * self.config["thresholds"]["load_warning_multiplier"],
                cpus * self.config["thresholds"]["load_critical_multiplier"],
            ),
            f"5-minute load: {load_5m:.2f} on {cpus} CPUs",
        )

    def check_reboot(self) -> None:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="utf-8"
        ).strip()
        previous_boot_id = self.state.get("boot_id")
        severity = "warning" if previous_boot_id and previous_boot_id != boot_id else "ok"
        summary = "Server reboot detected" if severity == "warning" else "No new reboot detected"
        self.add("host:reboot", severity, summary)
        self.state["boot_id"] = boot_id

    def _check_units(self, items: list[dict[str, Any]], user: bool) -> None:
        prefix = ["systemctl", "--user"] if user else ["systemctl"]
        key_prefix = "user-service" if user else "system-unit"
        for item in items:
            unit = item["unit"]
            active = self.run_command([*prefix, "is-active", unit])
            failed = self.run_command([*prefix, "is-failed", unit])
            if active.stdout.strip() != "active" or failed.stdout.strip() == "failed":
                self.add(
                    f"{key_prefix}:{unit}",
                    "critical",
                    f"{unit} is not healthy",
                    f"active={active.stdout.strip()}, failed={failed.stdout.strip()}",
                )
                continue
            if item.get("require_enabled", True):
                enabled = self.run_command([*prefix, "is-enabled", unit])
                if enabled.stdout.strip() not in {"enabled", "indirect"}:
                    self.add(
                        f"{key_prefix}:{unit}",
                        "warning",
                        f"{unit} is active but not enabled",
                        enabled.stdout.strip(),
                    )
                    continue
            self.add(f"{key_prefix}:{unit}", "ok", f"{unit} is active")

    def check_system_units(self) -> None:
        self._check_units(self.config.get("system_units", []), user=False)

    def check_user_services(self) -> None:
        self._check_units(self.config.get("user_services", []), user=True)

    def check_vpns(self) -> None:
        for check in self.config.get("vpn_checks", []):
            name = check["name"]
            key = f"vpn:{name}"
            unit = check["unit"]
            interface_pattern = check["interface_pattern"]
            problems: list[str] = []

            active = self.run_command(["systemctl", "is-active", unit])
            enabled = self.run_command(["systemctl", "is-enabled", unit])
            if active.stdout.strip() != "active":
                problems.append(f"{unit} is not active")
            if enabled.stdout.strip() not in {"enabled", "indirect"}:
                problems.append(f"{unit} is not enabled")

            routes: dict[str, dict[str, Any]] = {}
            for route_name, destination in (
                ("target", check["target"]),
                ("server", check["server"]),
            ):
                result = self.run_command(["ip", "-json", "route", "get", destination])
                try:
                    payload = json.loads(result.stdout)
                    if result.returncode != 0 or not isinstance(payload, list) or not payload:
                        raise ValueError
                    routes[route_name] = payload[0]
                except (json.JSONDecodeError, TypeError, ValueError):
                    problems.append(f"unable to resolve {route_name} route")

            target_route = routes.get("target", {})
            vpn_interface = str(target_route.get("dev", ""))
            if target_route and not fnmatch.fnmatchcase(vpn_interface, interface_pattern):
                problems.append(
                    f"{check['target']} is routed through {vpn_interface or 'no interface'}"
                )

            if vpn_interface and fnmatch.fnmatchcase(vpn_interface, interface_pattern):
                address = self.run_command(
                    ["ip", "-json", "address", "show", "dev", vpn_interface]
                )
                try:
                    interfaces = json.loads(address.stdout)
                    addresses = interfaces[0].get("addr_info", [])
                    client_network = ipaddress.ip_network(check["client_subnet"])
                    has_client_address = any(
                        item.get("family") == "inet"
                        and ipaddress.ip_address(item.get("local", "")) in client_network
                        for item in addresses
                    )
                    if address.returncode != 0 or not has_client_address:
                        problems.append(
                            f"{vpn_interface} has no address in {check['client_subnet']}"
                        )
                except (json.JSONDecodeError, IndexError, TypeError, ValueError):
                    problems.append(f"unable to inspect {vpn_interface} address")

            server_route = routes.get("server", {})
            server_interface = str(server_route.get("dev", ""))
            if server_route and fnmatch.fnmatchcase(server_interface, interface_pattern):
                problems.append(f"VPN server {check['server']} is routed through VPN")

            default_routes = self.run_command(["ip", "-json", "route", "show", "default"])
            try:
                defaults = json.loads(default_routes.stdout)
                if default_routes.returncode != 0 or not isinstance(defaults, list) or not defaults:
                    raise ValueError
                if any(
                    fnmatch.fnmatchcase(str(route.get("dev", "")), interface_pattern)
                    for route in defaults
                ):
                    problems.append("default route uses the VPN interface")
            except (json.JSONDecodeError, TypeError, ValueError):
                problems.append("unable to inspect the default route")

            if not problems:
                try:
                    connection = socket.create_connection(
                        (check["target"], int(check["target_port"])),
                        timeout=check.get("timeout_seconds", 5),
                    )
                    connection.close()
                except OSError as error:
                    problems.append(
                        f"TCP {check['target']}:{check['target_port']} failed: {error}"
                    )

            if problems:
                self.add(
                    key,
                    "critical",
                    f"{name} is not healthy",
                    "; ".join(problems),
                )
            else:
                self.add(
                    key,
                    "ok",
                    f"{name} is healthy via {vpn_interface}; "
                    f"TCP {check['target']}:{check['target_port']} is reachable",
                )

    def service_journal_outcome(
        self, service: str, *, user: bool
    ) -> ServiceOutcome | None:
        command = ["journalctl"]
        if user:
            command.append("--user")
        command.extend(
            [
                "-u",
                service,
                "_COMM=systemd",
                "--output=json",
                "--reverse",
                "--lines=200",
                "--no-pager",
            ]
        )
        journal = self.run_command(command)
        for line in journal.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            timestamp = event.get("__REALTIME_TIMESTAMP")
            message = str(event.get("MESSAGE", ""))
            if not timestamp:
                continue
            observed_at = int(timestamp) / 1_000_000
            incident_id = safe_status_value(
                event.get("OBJECT_SYSTEMD_INVOCATION_ID", "")
                or event.get("_SYSTEMD_INVOCATION_ID", "")
            )
            if not incident_id:
                incident_id = f"journal:{int(observed_at)}"
            if message.startswith(f"Finished {service} ") or message == (
                f"{service}: Deactivated successfully."
            ):
                return ServiceOutcome("success", observed_at, incident_id=incident_id)
            lowered = message.lower()
            if service in message and (
                "skipped because of an unmet condition check" in lowered
                or "skipped due to 'exec-condition'" in lowered
                or "condition check resulted" in lowered
            ):
                return ServiceOutcome(
                    "blocked",
                    observed_at,
                    result="condition",
                    incident_id=incident_id,
                )
            if message.startswith(f"Stopped {service} "):
                return ServiceOutcome(
                    "interrupted",
                    observed_at,
                    result="stopped",
                    incident_id=incident_id,
                )
            if (
                message.startswith(f"Failed to start {service} ")
                or message.startswith(f"Dependency failed for {service} ")
                or message.startswith(f"{service}: Failed with result ")
                or message.startswith(f"{service}: Main process exited")
            ):
                return ServiceOutcome(
                    "failed",
                    observed_at,
                    result="journal-failure",
                    incident_id=incident_id,
                )
        return None

    def service_result_file_outcome(
        self, timer: dict[str, Any]
    ) -> ServiceOutcome | None:
        result_file = timer.get("result_file")
        if not result_file:
            return None
        try:
            payload = json.loads(Path(result_file).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        status = str(payload.get("status", "")).upper()
        observed_at = payload.get("completed_at")
        if status not in {"PASS", "FAIL", "BLOCKED"} or not isinstance(
            observed_at, (int, float)
        ):
            return None
        return ServiceOutcome(
            {"PASS": "success", "FAIL": "failed", "BLOCKED": "blocked"}[status],
            float(observed_at),
            result=f"result-file-{status.lower()}",
            incident_id=f"result-file:{int(observed_at)}:{status.lower()}",
        )

    def timer_service_history(self) -> dict[str, Any]:
        history = self.state.setdefault("timer_services", {})
        if not isinstance(history, dict):
            history = {}
            self.state["timer_services"] = history
        return history

    def _check_timers(self, timers: list[dict[str, Any]], *, user: bool) -> None:
        prefix = ["systemctl", "--user"] if user else ["systemctl"]
        timer_key_prefix = "user-timer" if user else "system-timer"
        service_key_prefix = "user-service" if user else "system-service"
        for timer in timers:
            unit = timer["unit"]
            active = self.run_command([*prefix, "is-active", unit])
            enabled = self.run_command([*prefix, "is-enabled", unit])
            if active.stdout.strip() != "active":
                self.add(
                    f"{timer_key_prefix}:{unit}",
                    "critical",
                    f"{unit} is not active",
                    active.stderr.strip(),
                )
            elif enabled.stdout.strip() not in {"enabled", "indirect"}:
                self.add(
                    f"{timer_key_prefix}:{unit}",
                    "warning" if user else "critical",
                    f"{unit} is not enabled",
                    enabled.stdout.strip(),
                )
            else:
                self.add(f"{timer_key_prefix}:{unit}", "ok", f"{unit} is active")

            if not user:
                trigger_properties = self.run_command(
                    [
                        *prefix,
                        "show",
                        unit,
                        "--timestamp=unix",
                        "--property=LastTriggerUSec",
                    ]
                )
                trigger_values = self.parse_properties(trigger_properties.stdout)
                triggered_at = timestamp_epoch(trigger_values.get("LastTriggerUSec", ""))
                trigger_key = f"{timer_key_prefix}:{unit}:freshness"
                if triggered_at is None:
                    deadline = timestamp_epoch(str(timer.get("first_success_due", "")))
                    if deadline is not None and self.now < deadline:
                        self.add(
                            trigger_key,
                            "ok",
                            f"{unit} is awaiting its first natural run",
                        )
                    else:
                        self.add(
                            trigger_key,
                            "critical",
                            f"{unit} has no recorded natural run",
                        )
                else:
                    age_hours = max(0.0, (self.now - triggered_at) / 3600)
                    self.add(
                        trigger_key,
                        threshold_severity(
                            age_hours, timer["warning_hours"], timer["critical_hours"]
                        ),
                        f"{unit} last triggered naturally {format_age(age_hours)} ago",
                    )

            service = timer["service"]
            service_key = f"{service_key_prefix}:{service}:result"
            history = self.timer_service_history()
            cached = history.get(service_key, {})
            if not isinstance(cached, dict):
                cached = {}
            properties = self.run_command(
                [
                    *prefix,
                    "show",
                    service,
                    "--timestamp=unix",
                    "--property=Result",
                    "--property=ExecMainStatus",
                    "--property=ExecMainExitTimestamp",
                    "--property=InactiveExitTimestamp",
                    "--property=ActiveState",
                    "--property=SubState",
                    "--property=InvocationID",
                ]
            )
            values = self.parse_properties(properties.stdout)
            if values.get("ActiveState") in {"active", "activating"}:
                self.add(
                    service_key,
                    "ok",
                    f"{service} is currently running",
                )
                continue
            outcomes: list[ServiceOutcome] = []
            exit_status = safe_status_value(values.get("ExecMainStatus", ""))
            current_completed_at = timestamp_epoch(
                values.get("ExecMainExitTimestamp", "")
                or values.get("InactiveExitTimestamp", "")
            )
            if service_result_failed(values.get("Result", ""), exit_status):
                blocked_exit_statuses = {
                    str(status) for status in timer.get("blocked_exit_statuses", [])
                }
                outcomes.append(
                    ServiceOutcome(
                        "blocked" if exit_status in blocked_exit_statuses else "failed",
                        current_completed_at or self.now,
                        safe_status_value(values.get("Result", "")),
                        exit_status,
                        safe_status_value(values.get("InvocationID", "")),
                    )
                )
            elif current_completed_at is not None:
                outcomes.append(
                    ServiceOutcome(
                        "success",
                        current_completed_at,
                        incident_id=safe_status_value(values.get("InvocationID", "")),
                    )
                )

            cache_recorded_at = cached.get("recorded_at")
            journal_outcome = self.service_journal_outcome(service, user=user)
            first_success_due = timestamp_epoch(str(timer.get("first_success_due", "")))
            backfill_due = first_success_due is None or self.now >= first_success_due
            if journal_outcome is not None and (
                (not cached.get("status") and backfill_due)
                or (
                    isinstance(cache_recorded_at, (int, float))
                    and journal_outcome.observed_at > float(cache_recorded_at)
                )
            ):
                outcomes.append(journal_outcome)
            result_file_outcome = self.service_result_file_outcome(timer)
            if result_file_outcome is not None:
                outcomes.append(result_file_outcome)
            cached_at = cached.get("observed_at", cached.get("completed_at"))
            if isinstance(cached_at, (int, float)) and cached.get("status") in {
                "success",
                "failed",
                "blocked",
                "interrupted",
            }:
                outcomes.append(
                    ServiceOutcome(
                        str(cached["status"]),
                        float(cached_at),
                        safe_status_value(cached.get("result", "")),
                        safe_status_value(cached.get("exit", "")),
                        safe_status_value(cached.get("incident_id", "")),
                    )
                )

            if not outcomes:
                deadline = timestamp_epoch(str(timer.get("first_success_due", "")))
                if deadline is not None and self.now < deadline:
                    self.add(
                        service_key,
                        "ok",
                        f"{service} is awaiting its first scheduled run",
                    )
                else:
                    self.add(
                        service_key,
                        "warning" if user else "critical",
                        f"{service} has no recorded successful run",
                    )
                continue

            latest = max(outcomes, key=lambda outcome: outcome.observed_at)
            history[service_key] = {
                "status": latest.status,
                "observed_at": latest.observed_at,
                "result": latest.result,
                "exit": latest.exit_status,
                "incident_id": latest.incident_id,
                "recorded_at": self.now,
            }
            if latest.status == "failed":
                self.add(
                    service_key,
                    "critical",
                    f"{service} last known run failed",
                    f"result={latest.result}, exit={latest.exit_status}",
                    latest.incident_id,
                )
                continue
            if latest.status in {"blocked", "interrupted"}:
                label = "was blocked" if latest.status == "blocked" else "was interrupted"
                self.add(
                    service_key,
                    "warning" if user else "critical",
                    f"{service} last run {label}",
                    f"result={latest.result}, exit={latest.exit_status}",
                    latest.incident_id,
                )
                continue

            history[service_key]["completed_at"] = latest.observed_at
            age_hours = max(0.0, (self.now - latest.observed_at) / 3600)
            self.add(
                service_key,
                threshold_severity(
                    age_hours, timer["warning_hours"], timer["critical_hours"]
                ),
                f"{service} last completed {format_age(age_hours)} ago",
            )

    def check_user_timers(self) -> None:
        self._check_timers(self.config.get("user_timers", []), user=True)

    def check_system_timers(self) -> None:
        self._check_timers(self.config.get("system_timers", []), user=False)

    def check_daily_successes(self) -> None:
        for check in self.config.get("daily_success_checks", []):
            name = check["name"]
            label = check.get("label", name)
            key = f"daily-success:{name}"
            pending_path = Path(check["pending_file"])
            if pending_path.exists():
                self.add(
                    key,
                    "critical",
                    f"{label} has an unresolved pending operation",
                )
                continue

            state_path = Path(check["state_file"])
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                self.add(key, "critical", f"{label} state file is missing")
                continue
            except (OSError, UnicodeError, json.JSONDecodeError):
                self.add(key, "critical", f"{label} state file is unreadable")
                continue
            if not isinstance(payload, dict):
                self.add(key, "critical", f"{label} state file is invalid")
                continue

            success_field = check["last_success_local_date_field"]
            result_field = check.get("last_result_field")
            try:
                last_success = date.fromisoformat(str(payload.get(success_field, "")))
                zone = ZoneInfo(check["time_zone"])
                window_end = datetime.strptime(check["window_end"], "%H:%M").time()
            except (TypeError, ValueError, ZoneInfoNotFoundError):
                self.add(key, "critical", f"{label} state or configuration is invalid")
                continue

            local_now = datetime.fromtimestamp(self.now, zone)
            if last_success > local_now.date():
                self.add(
                    key,
                    "critical",
                    f"{label} reports a future success date {last_success.isoformat()}",
                )
                continue
            required_date = local_now.date()
            if local_now.time() < window_end:
                required_date -= timedelta(days=1)
            missed_windows = (required_date - last_success).days
            if missed_windows <= 0:
                self.add(
                    key,
                    "ok",
                    f"{label} last succeeded on {last_success.isoformat()}",
                )
                continue

            severity = threshold_severity(
                missed_windows,
                check.get("warning_missed_windows", 1),
                check.get("critical_missed_windows", 2),
            )
            result_suffix = ""
            if result_field:
                last_result = safe_status_value(payload.get(result_field, ""))
                if last_result:
                    result_suffix = f"; last result {last_result}"
            self.add(
                key,
                severity,
                f"{label} missed {missed_windows} daily success "
                f"window{'s' if missed_windows != 1 else ''}; last success "
                f"{last_success.isoformat()}{result_suffix}",
            )

    def add_endpoint_issue(
        self,
        endpoint: dict[str, Any],
        summary: str,
        details: str,
        *,
        immediate_critical: bool = False,
    ) -> None:
        key = f"endpoint:{endpoint['name']}"
        failures = self.state.setdefault("endpoint_failures", {})
        count = int(failures.get(key, 0)) + 1
        failures[key] = count
        if immediate_critical:
            severity = "critical"
        elif count >= self.config["thresholds"]["http_critical_consecutive_failures"]:
            severity = "critical"
        elif count >= self.config["thresholds"]["http_warning_consecutive_failures"]:
            severity = "warning"
        else:
            severity = "ok"
        self.add(key, severity, f"{summary} ({count} consecutive failures)", details)

    def check_http(self) -> None:
        for endpoint in self.config.get("endpoints", []):
            started = time.monotonic()
            request = urllib.request.Request(
                endpoint["url"],
                headers={"User-Agent": "devbox-monitor/1.0"},
                method="GET",
            )
            body = b""
            try:
                response = urllib.request.urlopen(
                    request, timeout=endpoint.get("timeout_seconds", 8)
                )
                status = response.status
                body = response.read(1024)
            except urllib.error.HTTPError as error:
                status = error.code
                body = error.read(1024)
            except Exception as error:
                self.add_endpoint_issue(
                    endpoint, f"{endpoint['name']} is unreachable", str(error)
                )
                continue

            elapsed = time.monotonic() - started
            allowed = endpoint.get("allowed_statuses", [])
            if status not in allowed:
                self.add_endpoint_issue(
                    endpoint,
                    f"{endpoint['name']} returned HTTP {status}",
                    endpoint["url"],
                    immediate_critical=status >= 500,
                )
                continue
            if "expected_json" in endpoint:
                try:
                    payload = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self.add_endpoint_issue(
                        endpoint,
                        f"{endpoint['name']} returned invalid JSON",
                        endpoint["url"],
                    )
                    continue
                mismatches = [
                    key
                    for key, expected in endpoint["expected_json"].items()
                    if payload.get(key) != expected
                ]
                if mismatches:
                    self.add_endpoint_issue(
                        endpoint,
                        f"{endpoint['name']} returned unhealthy JSON",
                        ", ".join(mismatches),
                    )
                    continue
            if elapsed > self.config["thresholds"]["http_critical_seconds"]:
                self.add_endpoint_issue(
                    endpoint,
                    f"{endpoint['name']} response took {elapsed:.2f}s",
                    endpoint["url"],
                )
                continue
            if elapsed > self.config["thresholds"]["http_warning_seconds"]:
                self.add_endpoint_issue(
                    endpoint,
                    f"{endpoint['name']} response took {elapsed:.2f}s",
                    endpoint["url"],
                )
                continue
            self.state.setdefault("endpoint_failures", {})[
                f"endpoint:{endpoint['name']}"
            ] = 0
            self.add(
                f"endpoint:{endpoint['name']}",
                "ok",
                f"{endpoint['name']} is healthy ({status}, {elapsed:.2f}s)",
            )

    def process_alerts(self) -> None:
        checks = self.state.setdefault("checks", {})
        current_keys = {result.key for result in self.results}
        for result in self.results:
            previous = checks.get(result.key, {})
            previous_severity = previous.get("severity", "ok")
            last_notification = previous.get("last_notification", 0)
            previous_incident_id = previous.get("incident_id", "")
            if result.severity == "ok":
                if previous_severity != "ok":
                    self.send(f"[RESOLVED] {result.summary}")
                checks[result.key] = {
                    "severity": "ok",
                    "last_seen": self.now,
                    "summary": result.summary,
                }
                continue

            cooldown_key = f"{result.severity}_cooldown_seconds"
            notification_policy = self.config.get("notification_overrides", {}).get(
                result.key, {}
            )
            cooldown = notification_policy.get(
                cooldown_key, self.config["notifications"][cooldown_key]
            )
            repeat_same_incident = bool(
                notification_policy.get("repeat_same_incident", False)
            )
            changed = result.severity != previous_severity
            escalated = SEVERITY_ORDER[result.severity] > SEVERITY_ORDER.get(
                previous_severity, 0
            )
            due = self.now - last_notification >= cooldown
            same_incident = (
                bool(result.incident_id)
                and result.incident_id == previous_incident_id
            )
            new_incident = bool(result.incident_id) and not same_incident
            if (
                changed
                or escalated
                or new_incident
                or (due and (repeat_same_incident or not same_incident))
            ):
                self.send(f"[{result.severity.upper()}] {result.summary}")
                last_notification = self.now
            checks[result.key] = {
                "severity": result.severity,
                "last_seen": self.now,
                "last_notification": last_notification,
                "summary": result.summary,
                "incident_id": result.incident_id,
            }

        for key in list(checks):
            if key not in current_keys and self.now - checks[key].get("last_seen", 0) > 86400:
                del checks[key]

    def send(self, summary: str) -> None:
        if not self.notify:
            return
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing")
        payload = json.dumps(
            {
                "chat_id": chat_id,
                "text": format_telegram_notification(
                    summary, self.config["host_label"]
                ),
            }
        ).encode()
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = json.loads(response.read().decode("utf-8"))
                if not body.get("ok"):
                    raise RuntimeError("Telegram rejected the notification")
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"Telegram HTTP {error.code}") from error

    def run_all(self) -> None:
        self.check_filesystems()
        self.check_mounts()
        self.check_memory()
        self.check_load()
        self.check_reboot()
        self.check_system_units()
        self.check_user_services()
        self.check_vpns()
        self.check_user_timers()
        self.check_system_timers()
        self.check_daily_successes()
        self.check_http()
        self.check_gp_dead_man()


def threshold_severity(value: float, warning: float, critical: float) -> str:
    if value >= critical:
        return "critical"
    if value >= warning:
        return "warning"
    return "ok"


def service_result_failed(result: str, exit_status: str) -> bool:
    return (bool(result) and result != "success") or exit_status not in {"0", ""}


def format_age(hours: float) -> str:
    if hours < 1:
        return f"{round(hours * 60)}m"
    return f"{hours:.1f}h"


def safe_status_value(value: Any) -> str:
    return " ".join(str(value).split())[:80]


def timestamp_epoch(timestamp: str) -> float | None:
    if not timestamp or timestamp == "n/a":
        return None
    if timestamp.startswith("@"):
        try:
            return float(timestamp[1:])
        except ValueError:
            return None
    normalized = timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = datetime.strptime(timestamp, "%a %Y-%m-%d %H:%M:%S %Z")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.timestamp()


def load_config(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        config = json.load(handle)
    required = {
        "host_label",
        "state_file",
        "thresholds",
        "notifications",
        "system_units",
        "user_services",
        "vpn_checks",
        "user_timers",
        "system_timers",
        "daily_success_checks",
        "mount_checks",
        "endpoints",
    }
    missing = sorted(required - config.keys())
    if missing:
        raise ValueError(f"Missing config keys: {', '.join(missing)}")
    return config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default="/home/miket/.config/devbox-monitor/config.json",
        help="Monitor config",
    )
    parser.add_argument("--state-file", help="Use an isolated state file")
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--test-notification", action="store_true")
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()

    config = load_config(arguments.config)
    if arguments.state_file:
        config = dict(config)
        config["state_file"] = arguments.state_file
    monitor = Monitor(config, notify=not arguments.no_notify)
    if arguments.test_notification:
        monitor.send("[OK] devbox-monitor.service test notification")
        return 0
    monitor.run_all()
    monitor.process_alerts()
    monitor.save_state()
    if arguments.json:
        print(json.dumps([result.__dict__ for result in monitor.results], indent=2))
    else:
        for result in monitor.results:
            print(f"{result.severity.upper():8} {result.key}: {result.summary}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"devbox-monitor failed: {exc}", file=sys.stderr)
        raise
