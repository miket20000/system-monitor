#!/usr/bin/env python3
"""Monitor GP host health and send stateful Telegram alerts."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SEVERITY_ORDER = {"ok": 0, "warning": 1, "critical": 2}
NOTIFICATION_CLASSES = {"sampled", "network", "durable"}


def next_dev_hibernation_problems(
    payload: dict[str, Any], overdue_grace_seconds: int
) -> list[str]:
    """Return content-free reasons why a next-dev heartbeat is unhealthy."""

    problems: list[str] = []
    status = payload.get("status")
    cycle_result = payload.get("lastCycleResult")
    timer_active = payload.get("timerActive")
    timer_enabled = payload.get("timerEnabled")
    integer_fields = (
        "holdCount",
        "activityCount",
        "leaseCount",
        "idleSeconds",
        "idleThresholdSeconds",
    )
    if status not in {"ACTIVE", "HIBERNATED", "FAILED"}:
        problems.append("status")
    if cycle_result not in {"SUCCESS", "BLOCKED", "FAILED"}:
        problems.append("lastCycleResult")
    if type(timer_active) is not bool or not timer_active:
        problems.append("timerActive")
    if type(timer_enabled) is not bool or not timer_enabled:
        problems.append("timerEnabled")
    if any(type(payload.get(field)) is not int or payload[field] < 0 for field in integer_fields):
        problems.append("counters")
        return problems

    blocker_count = sum(
        payload[field] for field in ("holdCount", "activityCount", "leaseCount")
    )
    if status == "FAILED":
        problems.append("statusFailed")
    if cycle_result == "FAILED":
        problems.append("lastCycleFailed")
    if cycle_result == "BLOCKED" and blocker_count == 0:
        problems.append("unexplainedBlock")
    if (
        status == "ACTIVE"
        and blocker_count == 0
        and payload["idleSeconds"]
        >= payload["idleThresholdSeconds"] + overdue_grace_seconds
    ):
        problems.append("overdueActive")

    healthy = not problems
    if type(payload.get("hibernationHealthy")) is not bool:
        problems.append("hibernationHealthy")
    elif payload["hibernationHealthy"] != healthy:
        problems.append("hibernationHealthyMismatch")
    return problems


def online_compiler_availability_phase(payload: object, now: float) -> tuple[str, float]:
    """Validate the public projection and derive its calendar independently of status."""
    if not isinstance(payload, dict):
        raise ValueError("availability schema")
    if (payload.get("timeZone") != "Europe/Warsaw"
            or payload.get("availabilityMode") != "scheduled"
            or type(payload.get("policyGeneration")) is not int
            or payload["policyGeneration"] <= 0):
        raise ValueError("availability policy")
    for field in ("updatedAt", "validUntil"):
        value = payload.get(field)
        if not isinstance(value, str):
            raise ValueError("availability timestamp")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("availability timestamp timezone")
    updated = timestamp_epoch(payload["updatedAt"])
    valid = timestamp_epoch(payload["validUntil"])
    if updated is None or valid is None or updated > now + 60 or valid <= now:
        raise ValueError("availability expired or future")
    days = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    windows = payload.get("weeklyWindows")
    if not isinstance(windows, dict) or set(windows) != set(days):
        raise ValueError("availability calendar")
    for rows in windows.values():
        if not isinstance(rows, list):
            raise ValueError("availability windows")
        previous_end = "00:00"
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"startsAt", "endsAt"}:
                raise ValueError("availability window")
            start, end = row["startsAt"], row["endsAt"]
            if (not isinstance(start, str) or not isinstance(end, str)
                    or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", start)
                    or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", end)
                    or not previous_end <= start < end):
                raise ValueError("availability window bounds")
            previous_end = end
    local = datetime.fromtimestamp(now, ZoneInfo("Europe/Warsaw"))
    last_end = float("-inf")
    for offset in range(-7, 1):
        day = local.date() + timedelta(days=offset)
        for row in windows[days[day.weekday()]]:
            bounds = [datetime.combine(day, datetime.strptime(row[key], "%H:%M").time(),
                                       tzinfo=local.tzinfo).timestamp()
                      for key in ("startsAt", "endsAt")]
            start, end = bounds
            if start <= now < end:
                return "OPEN", now - start
            if end <= now:
                last_end = max(last_end, end)
    return "CLOSED", now - last_end


def online_compiler_availability_problem(
    fallback: object, dynamic: object | None, now: float, transition_seconds: int
) -> str | None:
    # A fresh public API observation describes the product independently of its
    # scheduler and static projection. The fallback is used only while the API
    # is unavailable, as expected during hibernation and bounded transitions.
    projection = dynamic if dynamic is not None else fallback
    source = "public API" if dynamic is not None else "fallback"
    try:
        phase, elapsed = online_compiler_availability_phase(projection, now)
        status = projection.get("status")
        transition = "STARTING" if phase == "OPEN" else "STOPPING"
        allowed = {phase}
        if elapsed <= transition_seconds:
            allowed.add(transition)
        if status not in allowed:
            return source + " status disagrees with calendar or transition deadline"
        if dynamic is None and status == "OPEN":
            return "public API unavailable while OPEN"
        return None
    except (ValueError, TypeError, OverflowError):
        return source + " availability projection invalid or expired"


def production_heartbeat_v2_problems(payload: dict[str, Any], now: float) -> list[str]:
    problems = []
    if payload.get("schemaVersion") == 3:
        if payload.get("schedulerHealth") != "HEALTHY":
            problems.append("schedulerDegraded")
        if payload.get("schedulerReasonCode") != "NONE":
            problems.append("schedulerReasonCode")
        if (payload.get("schedulerHealth") == "HEALTHY") != (payload.get("schedulerHealthy") is True):
            problems.append("schedulerHealthMismatch")
    for field in ("timerActive", "timerEnabled"):
        if payload.get(field) is not True:
            problems.append(field)
    if payload.get("holdCount") != 0 or type(payload.get("holdCount")) is not int:
        problems.append("holdCount")
    allowed_statuses = {"OPEN", "CLOSED"}
    if payload.get("cycleState") == "RUNNING":
        allowed_statuses.update({"STARTING", "STOPPING"})
    if payload.get("status") not in allowed_statuses:
        problems.append("status")
    result = payload.get("lastCycleResult")
    if result not in {"SUCCESS", "UNKNOWN"}:
        problems.append("lastCycleResult")
    observed = {}
    for field in ("heartbeatAt", "stateUpdatedAt", "reconciledAt", "cycleStartedAt", "cycleDeadlineAt"):
        raw = payload.get(field)
        parsed = timestamp_epoch(raw) if isinstance(raw, str) else None
        observed[field] = parsed
        if raw is not None and parsed is None:
            problems.append(field)
        if parsed is not None and field != "cycleDeadlineAt" and parsed > now + 60:
            problems.append(field + "Future")
    if observed["heartbeatAt"] is None or observed["stateUpdatedAt"] is None:
        problems.append("heartbeatTimestamp")
    if (result == "UNKNOWN") != (observed["reconciledAt"] is None):
        problems.append("completionTimestamp")
    if payload.get("cycleState") == "RUNNING":
        start, deadline = observed["cycleStartedAt"], observed["cycleDeadlineAt"]
        if (start is None or deadline is None or deadline - start != 1800
                or observed["heartbeatAt"] is None or start > observed["heartbeatAt"]):
            problems.append("cycleBounds")
        elif now > deadline + 60:
            problems.append("cycleOverdue")
        if result == "SUCCESS" and payload.get("schedulerHealthy") is not True:
            problems.append("schedulerHealthy")
    elif payload.get("cycleState") == "IDLE":
        if observed["cycleStartedAt"] is not None or observed["cycleDeadlineAt"] is not None:
            problems.append("idleCycleBounds")
        if result != "SUCCESS" or payload.get("schedulerHealthy") is not True:
            problems.append("schedulerHealthy")
        if observed["reconciledAt"] is None or now - observed["reconciledAt"] >= 300:
            problems.append("completionStale")
    else:
        problems.append("cycleState")
    return problems


def clean_notification_value(value: str, limit: int = 160) -> str:
    return " ".join(value.split()).strip()[:limit]


def format_telegram_notification(body: str, host_label: str) -> str:
    clean_lines = [
        clean_notification_value(line, 3800)
        for line in body.splitlines()
        if clean_notification_value(line, 3800)
    ]
    clean_host = clean_notification_value(host_label)
    return "\n".join([*clean_lines, f"Host: {clean_host}"])


def format_notification_batch(
    new: list[tuple[str, str]],
    active: list[tuple[str, str]],
    resolved: list[str],
    *,
    reminder: bool,
    max_items: int,
) -> tuple[str, str]:
    if new:
        severity = max((item[1] for item in new), key=SEVERITY_ORDER.get)
        label = severity.upper()
        reason = "transition"
    elif reminder:
        severity = max((item[1] for item in active), key=SEVERITY_ORDER.get)
        label = f"REMINDER/{severity.upper()}"
        reason = "reminder"
    elif active:
        severity = max((item[1] for item in active), key=SEVERITY_ORDER.get)
        label = "UPDATE"
        reason = "recovery"
    else:
        severity = "ok"
        label = "RESOLVED"
        reason = "recovery"

    lines = [f"[{label}]"]
    remaining = max(0, max_items)
    omitted = 0
    new_keys = {key for key, _severity in new}
    sections: list[tuple[str, list[str]]] = [
        ("New", [item[0] for item in new]),
        ("Resolved", resolved),
        ("Active", [item[0] for item in active if item[0] not in new_keys]),
    ]
    for title, keys in sections:
        visible = keys[:remaining]
        if visible:
            lines.append(f"{title}: {', '.join(clean_notification_value(key) for key in visible)}")
        omitted += len(keys) - len(visible)
        remaining -= len(visible)
    if omitted:
        lines.append(f"Omitted: {omitted} additional checks")
    return "\n".join(lines), reason


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


class Monitor:
    def __init__(self, config: dict[str, Any], notify: bool = True) -> None:
        self.config = config
        self.notify = notify
        self.now = time.time()
        self.state_path = Path(config["state_file"])
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
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
        temporary.replace(self.state_path)

    def mark_completed(self) -> None:
        completed_path = self.state_path.parent / "last_completed"
        temporary = completed_path.with_suffix(".tmp")
        temporary.write_text(f"{self.now:.6f}\n", encoding="utf-8")
        temporary.replace(completed_path)

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
        if severity not in SEVERITY_ORDER:
            raise ValueError(f"Unsupported severity: {severity}")
        if notification_class not in NOTIFICATION_CLASSES:
            raise ValueError(f"Unsupported notification class: {notification_class}")
        self.results.append(
            Result(
                key, severity, summary, details, incident_id, notification_class,
                notification_code, notification_context,
            )
        )

    def _scheduler_unit_state(self, timer: str, service: str) -> tuple[bool, str]:
        active = self.run_command(["systemctl", "is-active", timer])
        enabled = self.run_command(["systemctl", "is-enabled", timer])
        properties = self.run_command(
            [
                "systemctl", "show", service, "--property=ActiveState",
                "--property=Result", "--property=ExecMainStatus",
            ]
        )
        values = self.parse_properties(properties.stdout)
        healthy = (
            active.returncode == 0
            and active.stdout.strip() == "active"
            and enabled.returncode == 0
            and enabled.stdout.strip() == "enabled"
            and properties.returncode == 0
            and values.get("ActiveState") in {"active", "activating", "deactivating", "inactive"}
            and values.get("Result") == "success"
            and values.get("ExecMainStatus") == "0"
        )
        if active.stdout.strip() != "active" or enabled.stdout.strip() != "enabled":
            return healthy, "TIMER_INACTIVE"
        if values.get("ExecMainStatus") == "20":
            return False, "CYCLE_BLOCKED"
        if not healthy:
            return False, "AUTH_OR_CYCLE_FAILED"
        return True, "NONE"

    @staticmethod
    def _scheduler_json(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("scheduler state is not an object")
        return value

    @staticmethod
    def _scheduler_records(path: Path) -> int:
        if path.is_symlink() or not path.is_dir():
            raise ValueError("scheduler record directory differs")
        count = 0
        for child in path.iterdir():
            if child.is_symlink() or not child.is_file() or child.suffix != ".json":
                raise ValueError("scheduler record differs")
            count += 1
        return count

    def check_online_compiler_schedulers(self) -> None:
        """Inspect GP-local scheduler units and owner-only state independently."""
        allowed_reasons = {
            "NONE", "READ_UNAVAILABLE", "AUTH_OR_CYCLE_FAILED", "CYCLE_BLOCKED",
            "CYCLE_UNKNOWN", "TIMER_INACTIVE", "HOLD_PRESENT", "CYCLE_OVERDUE",
            "STATE_UNVERIFIED",
        }
        for check in self.config.get("online_compiler_schedulers", []):
            name = check["name"]
            legacy_key = check["incident_key"]
            environment_key = f"online-compiler-environment:{name}"
            freshness_key = f"online-compiler-scheduler:{name}:freshness"
            try:
                schedule = self._scheduler_json(Path(check["schedule_path"]))
                health = self._scheduler_json(Path(check["health_path"]))
                holds = self._scheduler_records(Path(check["holds_path"]))
                health_time = timestamp_epoch(str(health.get("updatedAt", "")))
                if (
                    health.get("schemaVersion") != 1
                    or health.get("status") not in {"HEALTHY", "DEGRADED"}
                    or health.get("reasonCode") not in allowed_reasons
                    or (health["status"] == "HEALTHY") != (health["reasonCode"] == "NONE")
                    or health_time is None
                ):
                    raise ValueError("scheduler health schema differs")
                schedule_status = schedule.get("status")
                if name == "production":
                    if schedule_status not in {"OPEN", "CLOSED", "STARTING", "STOPPING", "FAILED"}:
                        raise ValueError("production environment status differs")
                    environment_status = str(schedule_status)
                elif name == "next-dev":
                    power = self._scheduler_json(Path(check["power_state_path"]))
                    activities = self._scheduler_records(Path(check["activities_path"]))
                    leases = self._scheduler_records(Path(check["leases_path"]))
                    power_mode = power.get("mode")
                    last_activity = timestamp_epoch(str(power.get("lastActivityAt", "")))
                    if schedule_status not in {"OPEN", "CLOSED", "STARTING", "STOPPING", "FAILED"}:
                        raise ValueError("next-dev schedule status differs")
                    if power_mode not in {
                        "UNKNOWN", "ACTIVE", "RESUMING", "HIBERNATING", "HIBERNATED", "FAILED",
                    }:
                        raise ValueError("next-dev power mode differs")
                    if last_activity is None or last_activity > self.now + 60:
                        raise ValueError("next-dev last activity timestamp differs")
                    environment_status = (
                        "ACTIVE" if (schedule_status, power_mode) == ("OPEN", "ACTIVE")
                        else "HIBERNATED" if (schedule_status, power_mode) == ("CLOSED", "HIBERNATED")
                        else "STARTING" if (schedule_status, power_mode) == ("STARTING", "RESUMING")
                        else "STOPPING" if (schedule_status, power_mode) == ("STOPPING", "HIBERNATING")
                        else "FAILED"
                    )
                else:
                    raise ValueError("unknown scheduler profile")
            except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
                self.add(
                    freshness_key, "critical", "Online Compiler scheduler state is unavailable",
                    type(error).__name__, notification_class="durable",
                    notification_code="ONLINE_COMPILER_STATE_STALE",
                )
                self.add(
                    legacy_key, "critical", "Online Compiler scheduler state cannot be verified",
                    type(error).__name__, notification_class="durable",
                    notification_code="ONLINE_COMPILER_SCHEDULER_DEGRADED",
                    notification_context={
                        "environmentStatus": "UNKNOWN", "reasonCode": "STATE_UNVERIFIED",
                    },
                )
                self.add(
                    environment_key, "critical", "Online Compiler environment state cannot be verified",
                    type(error).__name__, notification_class="durable",
                )
                if name == "next-dev":
                    self.add(
                        f"online-compiler-scheduler:{name}:idle-overdue", "ok",
                        "Next Dev idle deadline cannot be evaluated while state is unavailable",
                        notification_class="durable",
                    )
                continue

            age = self.now - health_time
            freshness = (
                "critical" if age < -60
                else threshold_severity(
                    max(0, age), float(check["freshness_warning_seconds"]),
                    float(check["freshness_critical_seconds"]),
                )
            )
            self.add(
                freshness_key, freshness,
                "Online Compiler scheduler state is fresh" if freshness == "ok" else "Online Compiler scheduler state is stale",
                notification_class="durable",
                notification_code="ONLINE_COMPILER_STATE_STALE" if freshness != "ok" else "",
            )

            environment_healthy = environment_status not in {"FAILED", "UNKNOWN"}
            self.add(
                environment_key, "ok" if environment_healthy else "critical",
                f"Online Compiler environment status is {environment_status}",
                notification_class="durable",
            )

            try:
                unit_healthy, unit_reason = self._scheduler_unit_state(
                    check["timer_unit"], check["service_unit"]
                )
            except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
                unit_healthy, unit_reason = False, "STATE_UNVERIFIED"
            blocked = holds > 0 or unit_reason == "CYCLE_BLOCKED" or health.get("reasonCode") in {
                "CYCLE_BLOCKED", "HOLD_PRESENT",
            }
            scheduler_healthy = unit_healthy and health["status"] == "HEALTHY" and not blocked
            if blocked:
                code = "ONLINE_COMPILER_SCHEDULER_BLOCKED"
                context = {"environmentStatus": environment_status, "holdCount": holds}
            elif not scheduler_healthy:
                code = "ONLINE_COMPILER_SCHEDULER_DEGRADED"
                reason = unit_reason if not unit_healthy else str(health["reasonCode"])
                context = {"environmentStatus": environment_status, "reasonCode": reason}
            else:
                code, context = "", None
            self.add(
                legacy_key, "ok" if scheduler_healthy else "critical",
                "Online Compiler scheduler is healthy" if scheduler_healthy else "Online Compiler scheduler is degraded",
                notification_class="durable", notification_code=code,
                notification_context=context,
            )

            if name == "next-dev":
                idle_seconds = max(0, int(self.now - last_activity))
                idle_overdue = (
                    environment_status == "ACTIVE"
                    and holds + activities + leases == 0
                    and idle_seconds >= int(check["idle_threshold_seconds"]) + int(check["idle_grace_seconds"])
                )
                self.add(
                    f"online-compiler-scheduler:{name}:idle-overdue",
                    "critical" if idle_overdue else "ok",
                    "Next Dev is ACTIVE beyond its idle deadline" if idle_overdue else "Next Dev idle deadline is satisfied",
                    notification_class="durable",
                    notification_code="ONLINE_COMPILER_IDLE_OVERDUE" if idle_overdue else "",
                    notification_context={
                        "idleSeconds": idle_seconds,
                        "idleThresholdSeconds": int(check["idle_threshold_seconds"]),
                    } if idle_overdue else None,
                )

    def check_filesystems(self) -> None:
        mounts: list[str] = []
        # PID 1 remains in the host mount namespace even when this service uses
        # ProtectSystem=strict, so its mount table reflects the actual host.
        with open("/proc/1/mounts", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) < 4:
                    continue
                device, mount, fs_type, options = parts[:4]
                if (
                    not device.startswith("/dev/")
                    or fs_type in {"iso9660", "squashfs"}
                    or mount.startswith(("/snap/", "/boot/efi"))
                ):
                    continue
                mounts.append(mount)
                if mount == "/" and "ro" in options.split(","):
                    self.add(
                        "filesystem:root-read-only",
                        "critical",
                        "Root filesystem is mounted read-only",
                        notification_class="durable",
                    )

        for mount in sorted(set(mounts)):
            usage = shutil.disk_usage(mount)
            used_percent = round((usage.used / usage.total) * 100)
            severity = threshold_severity(
                used_percent,
                self.config["thresholds"]["disk_warning_percent"],
                self.config["thresholds"]["disk_critical_percent"],
            )
            self.add(
                f"filesystem:space:{mount}",
                severity,
                f"Disk usage on {mount}: {used_percent}%",
            )

            stats = os.statvfs(mount)
            if stats.f_files:
                inode_percent = round(
                    ((stats.f_files - stats.f_ffree) / stats.f_files) * 100
                )
                severity = threshold_severity(
                    inode_percent,
                    self.config["thresholds"]["inode_warning_percent"],
                    self.config["thresholds"]["inode_critical_percent"],
                )
                self.add(
                    f"filesystem:inodes:{mount}",
                    severity,
                    f"Inode usage on {mount}: {inode_percent}%",
                )

    def check_memory(self) -> None:
        values: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                key, value = line.split(":", 1)
                values[key] = int(value.strip().split()[0])

        memory_percent = round(
            ((values["MemTotal"] - values["MemAvailable"]) / values["MemTotal"])
            * 100
        )
        self.add(
            "memory:used",
            threshold_severity(
                memory_percent,
                self.config["thresholds"]["memory_warning_percent"],
                self.config["thresholds"]["memory_critical_percent"],
            ),
            f"Memory usage: {memory_percent}%",
        )

        if values.get("SwapTotal", 0):
            swap_percent = round(
                ((values["SwapTotal"] - values["SwapFree"]) / values["SwapTotal"])
                * 100
            )
            self.add(
                "memory:swap",
                threshold_severity(
                    swap_percent,
                    self.config["thresholds"]["swap_warning_percent"],
                    self.config["thresholds"]["swap_critical_percent"],
                ),
                f"Swap usage: {swap_percent}%",
            )

    def check_load(self) -> None:
        load_5m = os.getloadavg()[1]
        cpus = os.cpu_count() or 1
        warning = cpus * self.config["thresholds"]["load_warning_multiplier"]
        critical = cpus * self.config["thresholds"]["load_critical_multiplier"]
        self.add(
            "load:5m",
            threshold_severity(load_5m, warning, critical),
            f"5-minute load: {load_5m:.2f} on {cpus} CPUs",
        )

    def check_reboot(self) -> None:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="utf-8"
        ).strip()
        previous_boot_id = self.state.get("boot_id")
        if previous_boot_id and previous_boot_id != boot_id:
            self.add(
                "host:reboot",
                "warning",
                "Server reboot detected",
                notification_class="durable",
            )
        else:
            self.add("host:reboot", "ok", "No new reboot detected")
        self.state["boot_id"] = boot_id

    def check_system_services(self) -> None:
        for unit in self.config["system_services"]:
            active = self.run_command(["systemctl", "is-active", unit])
            failed = self.run_command(["systemctl", "is-failed", unit])
            if active.stdout.strip() != "active" or failed.stdout.strip() == "failed":
                self.add(
                    f"service:{unit}",
                    "critical",
                    f"{unit} is not healthy",
                    f"active={active.stdout.strip()}, failed={failed.stdout.strip()}",
                )
            else:
                self.add(f"service:{unit}", "ok", f"{unit} is active")

    def check_database_readiness(self) -> None:
        for check in self.config.get("database_checks", []):
            name = check["name"]
            probe = check["probe"]
            if probe == "postgresql":
                command = [
                    "pg_isready",
                    "-h",
                    check.get("host", "127.0.0.1"),
                    "-p",
                    str(check.get("port", 5432)),
                ]
            elif probe == "mariadb-socket":
                command = ["mariadb-admin", "--protocol=socket", "ping"]
            else:
                self.add(
                    f"database:{name}",
                    "critical",
                    f"Unsupported database probe for {name}",
                    probe,
                    notification_class="durable",
                )
                continue

            result = self.run_command(command)
            key = f"database:{name}"
            failures = self.state.setdefault("database_failures", {})
            if result.returncode == 0:
                failures[key] = 0
                self.add(key, "ok", f"{name} is accepting connections")
                continue

            count = int(failures.get(key, 0)) + 1
            failures[key] = count
            severity = "warning" if count == 1 else "critical"
            self.add(
                key,
                severity,
                f"{name} readiness probe failed ({count} consecutive failures)",
                result.stderr.strip() or result.stdout.strip(),
            )

    def check_routes(self) -> None:
        failures = self.state.setdefault("route_failures", {})
        for check in self.config.get("route_checks", []):
            name = check["name"]
            destination = check["destination"]
            key = f"route:{name}"
            result = self.run_command(["ip", "route", "get", destination])
            interface = route_interface(result.stdout)
            expected_prefix = check.get("interface_prefix", "")
            healthy = (
                result.returncode == 0
                and bool(interface)
                and interface.startswith(expected_prefix)
            )
            if healthy:
                failures[key] = 0
                self.add(
                    key,
                    "ok",
                    f"Route to {destination} uses {interface}",
                )
                continue

            count = int(failures.get(key, 0)) + 1
            failures[key] = count
            critical_after = int(check.get("critical_consecutive_failures", 2))
            severity = "critical" if count >= critical_after else "warning"
            details = result.stderr.strip() or result.stdout.strip() or "route missing"
            self.add(
                key,
                severity,
                (
                    f"Route to {destination} is not using {expected_prefix}* "
                    f"({count} consecutive failures)"
                ),
                details,
            )

    def check_journal_errors(self) -> None:
        failures = self.state.setdefault("journal_error_failures", {})
        for check in self.config.get("journal_error_checks", []):
            name = check["name"]
            unit = check["unit"]
            key = f"journal-error:{name}"
            patterns = [str(item) for item in check.get("patterns", [])]
            pattern = "|".join(re.escape(item) for item in patterns)
            lookback_minutes = int(check.get("lookback_minutes", 20))
            result = self.run_command(
                [
                    "journalctl",
                    "-u",
                    unit,
                    "--since",
                    f"{lookback_minutes} minutes ago",
                    "--grep",
                    pattern,
                    "--lines=1",
                    "--output=cat",
                    "--no-pager",
                ]
            )

            if result.returncode in {0, 1} and not result.stdout.strip():
                failures[key] = 0
                self.add(
                    key,
                    "ok",
                    f"No monitored errors for {unit} in the last {lookback_minutes}m",
                )
                continue

            if result.returncode not in {0, 1}:
                self.add(
                    key,
                    "warning",
                    f"Unable to inspect monitored errors for {unit}",
                    result.stderr.strip(),
                )
                continue

            count = int(failures.get(key, 0)) + 1
            failures[key] = count
            critical_after = int(check.get("critical_consecutive_failures", 2))
            severity = "critical" if count >= critical_after else "warning"
            self.add(
                key,
                severity,
                f"{unit} reported a monitored worker error",
                result.stdout.strip()[:1000],
            )

    def check_external_json_heartbeats(self) -> None:
        for check in self.config.get("external_json_heartbeats", []):
            name = check["name"]
            key = f"external-heartbeat:{name}"
            path = Path(check["path"])
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                self.add(key, "critical", f"No heartbeat received for {name}")
                continue
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                self.add(
                    key,
                    "critical",
                    f"Heartbeat for {name} is unreadable",
                    type(error).__name__,
                )
                continue

            if not isinstance(payload, dict):
                self.add(key, "critical", f"Heartbeat for {name} is not a JSON object")
                continue
            if name == "online-compiler-production-schedule" and payload.get("schemaVersion") in {2, 3}:
                problems = production_heartbeat_v2_problems(payload, self.now)
                if payload.get("check") != name or payload.get("sourceHost") != "devbox":
                    problems.append("identity")
                if problems:
                    self.add(key, "critical", "Production scheduler heartbeat is unhealthy",
                             ", ".join(problems), notification_class="durable")
                else:
                    age = max(0, self.now - timestamp_epoch(payload["heartbeatAt"]))
                    self.add(key, threshold_severity(age, 180, 300),
                             f"Production scheduler heartbeat received {format_seconds_age(age)} ago",
                             notification_class="durable")
                continue
            mismatches = [
                field
                for field, expected in check.get("expected_json", {}).items()
                if payload.get(field) != expected
            ]
            status = payload.get("status")
            allowed_statuses = check.get("allowed_statuses", [])
            if allowed_statuses and status not in allowed_statuses:
                mismatches.append("status")
            if mismatches:
                self.add(
                    key,
                    "critical",
                    f"Heartbeat for {name} has unexpected fields",
                    ", ".join(sorted(set(mismatches))),
                    notification_class="durable",
                )
                continue

            semantic_policy = check.get("semantic_policy")
            if semantic_policy == "online-compiler-next-dev-hibernation-v1":
                problems = next_dev_hibernation_problems(
                    payload, int(check.get("overdue_grace_seconds", 900))
                )
                if problems:
                    self.add(
                        key,
                        "critical",
                        f"Heartbeat for {name} reports unhealthy hibernation",
                        ", ".join(sorted(set(problems))),
                        notification_class="durable",
                    )
                    continue

            completed_at = timestamp_epoch(str(payload.get("reconciledAt", "")))
            if completed_at is None:
                self.add(
                    key,
                    "critical",
                    f"Heartbeat for {name} has an invalid reconciliation timestamp",
                )
                continue
            age_seconds = self.now - completed_at
            if age_seconds < -60:
                self.add(
                    key,
                    "critical",
                    f"Heartbeat for {name} is dated in the future",
                )
                continue
            age_seconds = max(0.0, age_seconds)
            severity = threshold_severity(
                age_seconds,
                float(check["warning_seconds"]),
                float(check["critical_seconds"]),
            )
            self.add(
                key,
                severity,
                f"{name} reconciled {format_seconds_age(age_seconds)} ago with status {status}",
                notification_class="durable",
            )

    def check_system_timers(self) -> None:
        for timer in self.config.get("system_timers", []):
            unit = timer["unit"]
            suspended_until = active_suspension_until(timer, self.now)
            if suspended_until:
                self.add(
                    f"system-timer:{unit}",
                    "ok",
                    f"{unit} monitoring is suspended until {suspended_until}",
                )
                self.add(
                    f"system-service:{timer['service']}:result",
                    "ok",
                    f"{timer['service']} monitoring is suspended until {suspended_until}",
                )
                continue

            active = self.run_command(["systemctl", "is-active", unit])
            enabled = self.run_command(["systemctl", "is-enabled", unit])
            if active.stdout.strip() != "active":
                self.add(
                    f"system-timer:{unit}",
                    "critical",
                    f"{unit} is not active",
                    active.stderr.strip(),
                )
            elif enabled.stdout.strip() != "enabled":
                self.add(
                    f"system-timer:{unit}",
                    "warning",
                    f"{unit} is not enabled",
                    enabled.stdout.strip(),
                )
            else:
                self.add(f"system-timer:{unit}", "ok", f"{unit} is active")

            service = timer["service"]
            properties = self.run_command(
                [
                    "systemctl",
                    "show",
                    service,
                    "--property=Result",
                    "--property=ExecMainStatus",
                    "--property=ExecMainExitTimestamp",
                    "--property=ActiveState",
                    "--property=SubState",
                    "--property=InvocationID",
                ]
            )
            values = self.parse_properties(properties.stdout)
            active_state = values.get("ActiveState", "")
            sub_state = values.get("SubState", "")
            if active_state in {"active", "activating"}:
                self.add(
                    f"system-service:{service}:result",
                    "ok",
                    f"{service} is currently running",
                    f"active={active_state}, sub={sub_state}",
                )
                continue

            result = values.get("Result", "")
            exit_status = values.get("ExecMainStatus", "")
            timestamp = values.get("ExecMainExitTimestamp", "")
            if service_result_failed(result, exit_status):
                self.add(
                    f"system-service:{service}:result",
                    "critical",
                    f"{service} failed",
                    f"result={result}, exit={exit_status}",
                    incident_id=values.get("InvocationID", ""),
                    notification_class="durable",
                )
                continue

            completed_at = timestamp_epoch(timestamp)
            if completed_at is None:
                completed_at = self.system_service_journal_completed_epoch(service)

            if timer.get("require_completion_after_trigger"):
                timer_properties = self.run_command(
                    ["systemctl", "show", unit, "--property=LastTriggerUSec"]
                )
                trigger_values = self.parse_properties(timer_properties.stdout)
                triggered_at = timestamp_epoch(trigger_values.get("LastTriggerUSec", ""))
                grace_seconds = int(timer.get("completion_grace_seconds", 900))
                if (
                    triggered_at is not None
                    and self.now >= triggered_at + grace_seconds
                    and (completed_at is None or completed_at < triggered_at)
                ):
                    self.add(
                        f"system-service:{service}:result",
                        "critical",
                        f"{service} did not complete after its latest timer trigger",
                        notification_class="durable",
                    )
                    continue

            if completed_at is None:
                timer_properties = self.run_command(
                    ["systemctl", "show", unit, "--property=LastTriggerUSec"]
                )
                timer_values = self.parse_properties(timer_properties.stdout)
                if not timer_values.get("LastTriggerUSec"):
                    self.add(
                        f"system-service:{service}:result",
                        "ok",
                        f"{service} is awaiting its first scheduled run",
                    )
                    continue
                self.add(
                    f"system-service:{service}:result",
                    "warning",
                    f"{service} has no recorded successful run",
                    notification_class="durable",
                )
                continue
            age_hours = max(0.0, (self.now - completed_at) / 3600)
            warning_hours, critical_hours = system_timer_age_thresholds(
                timer, self.now
            )
            severity = threshold_severity(
                age_hours, warning_hours, critical_hours
            )
            self.add(
                f"system-service:{service}:result",
                severity,
                f"{service} last completed {format_age(age_hours)} ago",
                notification_class="durable",
            )

    def system_service_journal_completed_epoch(self, service: str) -> float | None:
        journal = self.run_command(
            [
                "journalctl",
                "-u",
                service,
                "--output=json",
                "--reverse",
                "--lines=100",
                "--no-pager",
            ]
        )
        finished_prefix = f"Finished {service}"
        success_message = f"{service}: Deactivated successfully."
        for line in journal.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = str(event.get("MESSAGE", ""))
            timestamp = event.get("__REALTIME_TIMESTAMP")
            unit = event.get("UNIT") or event.get("_SYSTEMD_UNIT")
            if timestamp and unit == service and (
                message.startswith(finished_prefix) or message == success_message
            ):
                return int(timestamp) / 1_000_000
        return None

    def system_service_journal_age_hours(self, service: str) -> float | None:
        completed_at = self.system_service_journal_completed_epoch(service)
        if completed_at is None:
            return None
        return max(0.0, (self.now - completed_at) / 3600)

    def add_endpoint_issue(
        self,
        endpoint: dict[str, Any],
        severity: str,
        summary: str,
        details: str,
    ) -> None:
        key = f"endpoint:{endpoint['name']}"
        self.add(
            key,
            severity,
            summary,
            details,
            notification_class="network",
        )

    def check_online_compiler_availability(self) -> None:
        check = self.config.get("online_compiler_availability")
        if not check:
            return

        def read_json(url: str) -> object:
            request = urllib.request.Request(url, headers={
                "User-Agent": "gp-monitor/1.0", "Cache-Control": "no-cache",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(request, timeout=8) as response:
                if response.status != 200 or response.geturl() != url:
                    raise ValueError("availability HTTP status or redirect")
                raw = response.read(16385)
                if len(raw) > 16384:
                    raise ValueError("availability size")
                return json.loads(raw.decode("utf-8"))

        try:
            dynamic = read_json(check["api_url"])
        except urllib.error.HTTPError as error:
            dynamic = None if error.code in {502, 503, 504} else {}
            error.close()
        except (OSError, ValueError, UnicodeError):
            dynamic = {}  # malformed/transport failure must not look hibernated
        fallback = None
        if dynamic is None:
            try:
                fallback = read_json(check["fallback_url"])
            except (OSError, ValueError, UnicodeError):
                fallback = {}
        problem = online_compiler_availability_problem(
            fallback, dynamic, self.now, int(check["transition_seconds"])
        )
        self.add(
            "endpoint:online-compiler-availability",
            "critical" if problem else "ok",
            problem or "Online Compiler public availability matches its schedule",
            notification_class="network",
        )

    def check_http(self) -> None:
        for endpoint in self.config["endpoints"]:
            started = time.monotonic()
            headers = {"User-Agent": "gp-monitor/1.0"}
            if "host_header" in endpoint:
                headers["Host"] = endpoint["host_header"]
            request = urllib.request.Request(
                endpoint["url"],
                headers=headers,
                method="GET",
            )
            try:
                response = urllib.request.urlopen(
                    request, timeout=endpoint.get("timeout_seconds", 8)
                )
                status = response.status
                body = response.read(1024)
            except urllib.error.HTTPError as error:
                status = error.code
            except Exception as error:
                self.add_endpoint_issue(
                    endpoint,
                    "critical",
                    f"{endpoint['name']} is unreachable",
                    str(error),
                )
                continue

            elapsed = time.monotonic() - started
            allowed = endpoint.get("allowed_statuses")
            healthy = status in allowed if allowed else status < 500
            if not healthy:
                self.add_endpoint_issue(
                    endpoint,
                    "critical",
                    f"{endpoint['name']} returned HTTP {status}",
                    endpoint["url"],
                )
                continue
            if "expected_json" in endpoint:
                try:
                    payload = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self.add_endpoint_issue(
                        endpoint,
                        "critical",
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
                        "critical",
                        f"{endpoint['name']} returned unhealthy JSON",
                        ", ".join(mismatches),
                    )
                    continue
            if elapsed > self.config["thresholds"]["http_critical_seconds"]:
                self.add_endpoint_issue(
                    endpoint,
                    "critical",
                    f"{endpoint['name']} response took {elapsed:.2f}s",
                    endpoint["url"],
                )
            elif elapsed > self.config["thresholds"]["http_warning_seconds"]:
                self.add_endpoint_issue(
                    endpoint,
                    "warning",
                    f"{endpoint['name']} response took {elapsed:.2f}s",
                    endpoint["url"],
                )
            else:
                self.add(
                    f"endpoint:{endpoint['name']}",
                    "ok",
                    f"{endpoint['name']} is healthy ({status}, {elapsed:.2f}s)",
                    notification_class="network",
                )

    def check_dns(self) -> None:
        warning_seconds = self.config["thresholds"].get("dns_warning_seconds", 2)
        for endpoint in self.config.get("dns_endpoints", []):
            for transport in endpoint.get("transports", ["udp", "tcp"]):
                timeout = endpoint.get("timeout_seconds", 3)
                command = [
                    "dig",
                    f"@{endpoint['server']}",
                    endpoint.get("query_name", "example.com"),
                    endpoint.get("query_type", "A"),
                    f"+time={timeout}",
                    "+tries=2",
                    "+noall",
                    "+comments",
                    "+answer",
                ]
                if transport == "tcp":
                    command.append("+tcp")

                started = time.monotonic()
                result = self.run_command(command, timeout=timeout * 3)
                elapsed = time.monotonic() - started
                status, answers = parse_dig_output(result.stdout)
                key = f"dns:{endpoint['name']}:{transport}"
                if result.returncode != 0 or status != "NOERROR" or answers == 0:
                    self.add(
                        key,
                        "critical",
                        (
                            f"{endpoint['name']} DNS {transport.upper()} "
                            "query failed"
                        ),
                        (
                            result.stderr.strip()
                            or f"status={status or 'missing'}, answers={answers}"
                        ),
                        notification_class="network",
                    )
                else:
                    severity = "warning" if elapsed >= warning_seconds else "ok"
                    self.add(
                        key,
                        severity,
                        (
                            f"{endpoint['name']} DNS {transport.upper()} "
                            f"query succeeded in {elapsed:.2f}s"
                        ),
                        notification_class="network",
                    )

    def check_tls(self) -> None:
        hosts = sorted(
            {
                urllib.parse.urlparse(endpoint["url"]).hostname
                for endpoint in self.config["endpoints"]
                if endpoint["url"].startswith("https://")
            }
        )
        context = ssl.create_default_context()
        for host in hosts:
            if not host:
                continue
            try:
                with socket.create_connection((host, 443), timeout=8) as connection:
                    with context.wrap_socket(
                        connection, server_hostname=host
                    ) as tls_connection:
                        certificate = tls_connection.getpeercert()
                expiry = ssl.cert_time_to_seconds(certificate["notAfter"])
                days = (expiry - self.now) / 86400
                if days < 7:
                    severity = "critical"
                elif days < 14:
                    severity = "warning"
                else:
                    severity = "ok"
                self.add(
                    f"tls:{host}",
                    severity,
                    f"TLS certificate for {host} expires in {days:.0f} days",
                    notification_class="network",
                )
            except Exception as error:
                self.add(
                    f"tls:{host}",
                    "critical",
                    f"Unable to validate TLS certificate for {host}",
                    str(error),
                    notification_class="network",
                )

    def check_cron(self) -> None:
        crontab = self.run_command(["crontab", "-u", "ubuntu", "-l"])
        if crontab.returncode != 0:
            self.add(
                "cron:ubuntu",
                "critical",
                "Unable to read ubuntu crontab",
                crontab.stderr.strip(),
            )
            return

        for job in self.config["cron_jobs"]:
            if job["command_marker"] not in crontab.stdout:
                self.add(
                    f"cron:{job['name']}:definition",
                    "critical",
                    f"ubuntu cron job is missing: {job['name']}",
                )
            else:
                self.add(
                    f"cron:{job['name']}:definition",
                    "ok",
                    f"ubuntu cron job exists: {job['name']}",
                )

            heartbeat = Path(job["heartbeat"])
            if not heartbeat.exists():
                self.add(
                    f"cron:{job['name']}:freshness",
                    "warning",
                    f"No success heartbeat yet for {job['name']}",
                    notification_class="durable",
                )
                continue

            age_hours = (self.now - heartbeat.stat().st_mtime) / 3600
            warning_hours, critical_hours = cron_age_thresholds(job, self.now)
            severity = threshold_severity(age_hours, warning_hours, critical_hours)
            self.add(
                f"cron:{job['name']}:freshness",
                severity,
                f"{job['name']} last success was {format_age(age_hours)} ago",
                notification_class="durable",
            )

    def user_systemctl(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return self.run_command(
            [
                "runuser",
                "-u",
                self.config["user_units"]["user"],
                "--",
                "env",
                f"XDG_RUNTIME_DIR=/run/user/{self.config['user_units']['uid']}",
                "systemctl",
                "--user",
                *arguments,
            ]
        )

    def check_user_timers(self) -> None:
        for timer in self.config["user_units"]["timers"]:
            unit = timer["unit"]
            suspended_until = active_suspension_until(timer, self.now)
            if suspended_until:
                self.add(
                    f"user-timer:{unit}",
                    "ok",
                    f"{unit} monitoring is suspended until {suspended_until}",
                )
                self.add(
                    f"user-service:{timer['service']}:result",
                    "ok",
                    f"{timer['service']} monitoring is suspended until {suspended_until}",
                )
                continue

            active = self.user_systemctl("is-active", unit)
            enabled = self.user_systemctl("is-enabled", unit)
            if active.stdout.strip() != "active":
                self.add(
                    f"user-timer:{unit}",
                    "critical",
                    f"{unit} is not active",
                    active.stderr.strip(),
                )
            elif enabled.stdout.strip() not in {"enabled", "indirect"}:
                self.add(
                    f"user-timer:{unit}",
                    "warning",
                    f"{unit} is not enabled",
                    enabled.stdout.strip(),
                )
            else:
                self.add(f"user-timer:{unit}", "ok", f"{unit} is active")

            service = timer["service"]
            properties = self.user_systemctl(
                "show",
                service,
                "--property=Result",
                "--property=ExecMainStatus",
                "--property=InactiveExitTimestamp",
                "--property=InvocationID",
            )
            values = self.parse_properties(properties.stdout)
            result = values.get("Result", "")
            exit_status = values.get("ExecMainStatus", "")
            timestamp = values.get("InactiveExitTimestamp", "")
            if service_result_failed(result, exit_status):
                self.add(
                    f"user-service:{service}:result",
                    "critical",
                    f"{service} failed",
                    f"result={result}, exit={exit_status}",
                    incident_id=values.get("InvocationID", ""),
                    notification_class="durable",
                )
                continue

            age_hours = timestamp_age_hours(timestamp)
            if age_hours is None:
                age_hours = self.user_service_journal_age_hours(service)
            if age_hours is None:
                timer_properties = self.user_systemctl(
                    "show", unit, "--property=LastTriggerUSec"
                )
                timer_values = self.parse_properties(timer_properties.stdout)
                if not timer_values.get("LastTriggerUSec"):
                    self.add(
                        f"user-service:{service}:result",
                        "ok",
                        f"{service} is awaiting its first scheduled run",
                    )
                else:
                    self.add(
                        f"user-service:{service}:result",
                        "warning",
                        f"{service} has no recorded successful run",
                        notification_class="durable",
                    )
                continue
            severity = threshold_severity(
                age_hours, timer["warning_hours"], timer["critical_hours"]
            )
            self.add(
                f"user-service:{service}:result",
                severity,
                f"{service} last completed {format_age(age_hours)} ago",
                notification_class="durable",
            )

    def user_service_journal_age_hours(self, service: str) -> float | None:
        journal = self.run_command(
            [
                "journalctl",
                f"_SYSTEMD_USER_UNIT={service}",
                "_UID=1000",
                "--output=json",
                "--reverse",
                "--lines=100",
                "--no-pager",
            ]
        )
        for line in journal.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = str(event.get("MESSAGE", ""))
            timestamp = event.get("__REALTIME_TIMESTAMP")
            if timestamp and (
                message.startswith("Finished ")
                or event.get("_SYSTEMD_USER_UNIT") == service
            ):
                return max(0.0, (self.now - int(timestamp) / 1_000_000) / 3600)
        return None

    def check_ocs(self) -> None:
        timer = "ocsinventory-agent.timer"
        active = self.run_command(["systemctl", "is-active", timer])
        enabled = self.run_command(["systemctl", "is-enabled", timer])
        if active.stdout.strip() != "active":
            self.add("ocs:timer", "critical", f"{timer} is not active")
        elif enabled.stdout.strip() != "enabled":
            self.add("ocs:timer", "warning", f"{timer} is not enabled")
        else:
            self.add("ocs:timer", "ok", f"{timer} is active")

        properties = self.run_command(
            [
                "systemctl",
                "show",
                "ocsinventory-agent.service",
                "--property=Result",
                "--property=ExecMainStatus",
                "--property=InactiveExitTimestamp",
                "--property=InvocationID",
            ]
        )
        values = self.parse_properties(properties.stdout)
        result = values.get("Result", "")
        exit_status = values.get("ExecMainStatus", "")
        timestamp = values.get("InactiveExitTimestamp", "")
        if service_result_failed(result, exit_status):
            self.add(
                "ocs:last-run",
                "critical",
                "OCS inventory last run failed",
                f"result={result}, exit={exit_status}",
                incident_id=values.get("InvocationID", ""),
                notification_class="durable",
            )
            return
        age_hours = timestamp_age_hours(timestamp)
        if age_hours is None:
            self.add(
                "ocs:last-run",
                "warning",
                "OCS has no recorded successful run",
                notification_class="durable",
            )
            return
        severity = threshold_severity(age_hours, 30, 48)
        self.add(
            "ocs:last-run",
            severity,
            f"OCS inventory last completed {format_age(age_hours)} ago",
            notification_class="durable",
        )

    def process_alerts(self) -> None:
        checks = self.state.setdefault("checks", {})
        current_keys = {result.key for result in self.results}
        notifications = self.config.get("notifications", {})
        suppressed_prefixes = notifications.get("suppressed_key_prefixes", [])
        suppressed_recovery_keys = {
            str(key)
            for key in notifications.get("suppressed_recovery_keys", [])
        }
        recovery_cycles = max(
            1, int(notifications.get("recovery_confirmation_cycles", 2))
        )
        new_events: dict[str, str] = {}
        resolved_events: set[str] = set()

        for result in self.results:
            previous = checks.get(result.key, {})
            previous_severity = previous.get("severity", "ok")
            last_notification = previous.get("last_notification", 0)
            previous_incident_id = previous.get("incident_id", "")
            alert_active = bool(
                previous.get(
                    "alert_active",
                    previous_severity != "ok" and bool(last_notification),
                )
            )
            notified_severity = previous.get(
                "notified_severity",
                previous_severity if alert_active else "ok",
            )
            failure_streak = int(previous.get("failure_streak", 0))
            recovery_streak = int(previous.get("recovery_streak", 0))
            escalation_streak = int(previous.get("escalation_streak", 0))
            pending_severity = previous.get("pending_severity", "ok")
            notifications_suppressed = any(
                result.key.startswith(prefix) for prefix in suppressed_prefixes
            )
            if result.notification_class == "durable":
                failure_cycles = 1
            elif result.notification_class == "network":
                failure_cycles = max(
                    1,
                    int(
                        notifications.get(
                            "network_failure_confirmation_cycles", 3
                        )
                    ),
                )
            else:
                failure_cycles = max(
                    1,
                    int(
                        notifications.get(
                            "default_failure_confirmation_cycles", 2
                        )
                    ),
                )

            incident_changed = bool(
                result.incident_id
                and result.incident_id != previous_incident_id
            )
            if incident_changed:
                alert_active = False
                notified_severity = "ok"
                failure_streak = 0
                recovery_streak = 0
                escalation_streak = 0
                pending_severity = "ok"

            if result.severity == "ok":
                failure_streak = 0
                escalation_streak = 0
                pending_severity = "ok"
                if alert_active:
                    recovery_streak += 1
                    if recovery_streak >= recovery_cycles:
                        if (
                            not notifications_suppressed
                            and result.key not in suppressed_recovery_keys
                        ):
                            resolved_events.add(result.key)
                        alert_active = False
                        notified_severity = "ok"
                        recovery_streak = 0
                else:
                    recovery_streak = 0
                checks[result.key] = {
                    "severity": "ok",
                    "last_seen": self.now,
                    "last_notification": last_notification,
                    "summary": result.summary,
                    "incident_id": result.incident_id,
                    "notification_class": result.notification_class,
                    "alert_active": alert_active,
                    "notified_severity": notified_severity,
                    "failure_streak": failure_streak,
                    "recovery_streak": recovery_streak,
                    "escalation_streak": escalation_streak,
                    "pending_severity": pending_severity,
                    "notification_suppressed": notifications_suppressed,
                }
                continue

            recovery_streak = 0
            if notifications_suppressed:
                alert_active = False
                notified_severity = "ok"
                escalation_streak = 0
                failure_streak = min(failure_cycles, failure_streak + 1)
                if SEVERITY_ORDER[result.severity] > SEVERITY_ORDER.get(
                    pending_severity, 0
                ):
                    pending_severity = result.severity
            elif not alert_active:
                if previous.get("notification_suppressed"):
                    failure_streak = max(failure_streak, failure_cycles - 1)
                if previous_severity == "ok" or incident_changed:
                    failure_streak = 1
                    pending_severity = result.severity
                else:
                    failure_streak += 1
                    if SEVERITY_ORDER[result.severity] > SEVERITY_ORDER.get(
                        pending_severity, 0
                    ):
                        pending_severity = result.severity
                if failure_streak >= failure_cycles:
                    alert_active = True
                    notified_severity = pending_severity
                    new_events[result.key] = notified_severity
                    last_notification = self.now
                    failure_streak = 0
                    pending_severity = "ok"
            elif SEVERITY_ORDER[result.severity] > SEVERITY_ORDER.get(
                notified_severity, 0
            ):
                if SEVERITY_ORDER.get(previous_severity, 0) >= SEVERITY_ORDER[
                    result.severity
                ]:
                    escalation_streak += 1
                else:
                    escalation_streak = 1
                if escalation_streak >= failure_cycles:
                    notified_severity = result.severity
                    new_events[result.key] = notified_severity
                    last_notification = self.now
                    escalation_streak = 0
            else:
                escalation_streak = 0

            checks[result.key] = {
                "severity": result.severity,
                "last_seen": self.now,
                "last_notification": last_notification,
                "summary": result.summary,
                "incident_id": result.incident_id,
                "notification_class": result.notification_class,
                "alert_active": alert_active,
                "notified_severity": notified_severity,
                "failure_streak": failure_streak,
                "recovery_streak": recovery_streak,
                "escalation_streak": escalation_streak,
                "pending_severity": pending_severity,
                "notification_suppressed": notifications_suppressed,
            }

        for key in list(checks):
            if key not in current_keys and self.now - checks[key].get("last_seen", 0) > 86400:
                del checks[key]

        active_events = sorted(
            (
                key,
                entry.get("notified_severity", entry.get("severity", "warning")),
            )
            for key, entry in checks.items()
            if entry.get("alert_active")
            and not any(key.startswith(prefix) for prefix in suppressed_prefixes)
        )
        last_active_summary = self.state.get("last_active_summary", None)
        if last_active_summary is None:
            last_active_summary = max(
                (
                    float(entry.get("last_notification", 0))
                    for entry in checks.values()
                    if entry.get("alert_active")
                ),
                default=0,
            )
        reminder_seconds = max(1, int(notifications.get("reminder_seconds", 21600)))
        reminder_due = bool(active_events) and (
            self.now - float(last_active_summary) >= reminder_seconds
        )
        should_send = bool(new_events or resolved_events or reminder_due)
        if should_send:
            body, reason = format_notification_batch(
                sorted(new_events.items()),
                active_events,
                sorted(resolved_events),
                reminder=reminder_due,
                max_items=max(0, int(notifications.get("max_items_per_message", 20))),
            )
            delivered = self.send(body)
            for key, _severity in active_events:
                checks[key]["last_notification"] = self.now
            for key in resolved_events:
                checks[key]["last_notification"] = self.now
            self.state["last_active_summary"] = self.now if active_events else 0
            if delivered:
                if new_events:
                    audit_severity = max(new_events.values(), key=SEVERITY_ORDER.get)
                elif active_events:
                    audit_severity = max(
                        (severity for _key, severity in active_events),
                        key=SEVERITY_ORDER.get,
                    )
                else:
                    audit_severity = "ok"
                print(
                    "NOTIFY_SENT "
                    f"reason={reason} severity={audit_severity} "
                    f"new={len(new_events)} active={len(active_events)} "
                    f"resolved={len(resolved_events)}",
                    file=sys.stderr,
                )
        elif not active_events:
            self.state["last_active_summary"] = 0
        self.state["notification_state_version"] = 2

    def send(self, summary: str, details: str = "") -> bool:
        if not self.notify:
            return False
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing")

        text = format_telegram_notification(summary, self.config["host_label"])
        payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = json.loads(response.read().decode())
                if not body.get("ok"):
                    raise RuntimeError("Telegram rejected message")
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"Telegram HTTP {error.code}") from error
        return True

    def run_all(self) -> None:
        self.check_filesystems()
        self.check_memory()
        self.check_load()
        self.check_reboot()
        self.check_system_services()
        self.check_database_readiness()
        self.check_routes()
        self.check_system_timers()
        self.check_journal_errors()
        self.check_online_compiler_schedulers()
        self.check_external_json_heartbeats()
        self.check_http()
        self.check_online_compiler_availability()
        self.check_dns()
        self.check_tls()
        self.check_cron()
        self.check_user_timers()
        self.check_ocs()


def threshold_severity(value: float, warning: float, critical: float) -> str:
    if value >= critical:
        return "critical"
    if value >= warning:
        return "warning"
    return "ok"


def parse_dig_output(output: str) -> tuple[str, int]:
    status = ""
    answers = 0
    for line in output.splitlines():
        if line.startswith(";; ->>HEADER<<-") and "status:" in line:
            status = line.split("status:", 1)[1].split(",", 1)[0].strip()
        elif line and not line.startswith(";"):
            fields = line.split()
            if len(fields) >= 5:
                answers += 1
    return status, answers


def route_interface(output: str) -> str:
    fields = output.split()
    try:
        return fields[fields.index("dev") + 1]
    except (ValueError, IndexError):
        return ""


def service_result_failed(result: str, exit_status: str) -> bool:
    """Use ExecMainStatus only when systemd did not provide Result."""
    if result:
        return result != "success"
    return exit_status not in {"0", ""}


def format_age(hours: float) -> str:
    if hours < 1:
        return f"{round(hours * 60)}m"
    return f"{hours:.1f}h"


def format_seconds_age(seconds: float) -> str:
    if seconds < 120:
        return f"{round(seconds)}s"
    if seconds < 7200:
        return f"{round(seconds / 60)}m"
    return f"{seconds / 3600:.1f}h"


def timestamp_age_hours(timestamp: str) -> float | None:
    epoch = timestamp_epoch(timestamp)
    if epoch is None:
        return None
    return max(0.0, (time.time() - epoch) / 3600)


def timestamp_epoch(timestamp: str) -> float | None:
    if not timestamp or timestamp == "n/a":
        return None
    try:
        parsed = datetime.strptime(timestamp, "%a %Y-%m-%d %H:%M:%S %Z")
    except ValueError:
        try:
            parsed = datetime.fromisoformat(timestamp)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = (
            parsed.replace(tzinfo=timezone.utc)
            if timestamp.endswith((" UTC", " GMT"))
            else parsed.astimezone()
        )
    return parsed.timestamp()


def active_suspension_until(item: dict[str, Any], now_epoch: float) -> str | None:
    value = str(item.get("suspended_until", "")).strip()
    if not value:
        return None

    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        until = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            until = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    if until.tzinfo is None:
        until = until.astimezone()
    now = datetime.fromtimestamp(now_epoch).astimezone()
    if now >= until:
        return None
    return until.astimezone().isoformat(timespec="minutes")


def cron_age_thresholds(job: dict[str, Any], now_epoch: float) -> tuple[float, float]:
    if job.get("schedule") != "weekdays":
        return job["warning_hours"], job["critical_hours"]

    now = datetime.fromtimestamp(now_epoch).astimezone()
    latest_expected = now.replace(hour=7, minute=0, second=0, microsecond=0)
    if now < latest_expected:
        latest_expected -= timedelta(days=1)
    while latest_expected.weekday() >= 5:
        latest_expected -= timedelta(days=1)
    expected_age = (now - latest_expected).total_seconds() / 3600
    return expected_age + 6, expected_age + 12


def system_timer_age_thresholds(
    timer: dict[str, Any], now_epoch: float
) -> tuple[float, float]:
    schedule = timer.get("calendar_schedule")
    if not schedule:
        return timer["warning_hours"], timer["critical_hours"]

    timezone_name = schedule.get("timezone", "Europe/Warsaw")
    now = datetime.fromtimestamp(now_epoch, ZoneInfo(timezone_name))
    weekdays = set(schedule["weekdays"])
    latest_expected = now.replace(
        hour=int(schedule["hour"]),
        minute=int(schedule.get("minute", 0)),
        second=0,
        microsecond=0,
    )
    if now < latest_expected:
        latest_expected -= timedelta(days=1)
    while latest_expected.weekday() not in weekdays:
        latest_expected -= timedelta(days=1)
    expected_age = (now - latest_expected).total_seconds() / 3600
    return (
        expected_age + float(schedule.get("warning_delay_hours", 6)),
        expected_age + float(schedule.get("critical_delay_hours", 12)),
    )


def load_config(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", default="/etc/gp-monitor/config.json", help="Monitor config"
    )
    parser.add_argument(
        "--state-file",
        help="Override state and completion-marker location for isolated validation",
    )
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
        monitor.send("[OK] gp-monitor.service test notification")
        return 0

    monitor.run_all()
    monitor.process_alerts()
    monitor.save_state()
    monitor.mark_completed()
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
        print(f"gp-monitor failed: {exc}", file=sys.stderr)
        raise
