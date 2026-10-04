#!/usr/bin/env python3
"""Validate and atomically store the Online Compiler schedule heartbeat."""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any


TARGETS = {
    "online-compiler-production-schedule": pathlib.Path(
        "/var/lib/gp-monitor/external/online-compiler-production-schedule.json"
    ),
    "online-compiler-next-dev-hibernation": pathlib.Path(
        "/var/lib/gp-monitor/external/online-compiler-next-dev-hibernation.json"
    ),
}
MAX_BYTES = 2048
PRODUCTION_FIELDS = {
    "schemaVersion",
    "check",
    "sourceHost",
    "status",
    "reconciledAt",
    "stateUpdatedAt",
    "lastCycleResult",
    "timerActive",
    "timerEnabled",
    "holdCount",
    "schedulerHealthy",
}
PRODUCTION_V2_FIELDS = PRODUCTION_FIELDS | {
    "heartbeatAt", "cycleState", "cycleStartedAt", "cycleDeadlineAt",
}
PRODUCTION_V3_FIELDS = PRODUCTION_V2_FIELDS | {"schedulerHealth", "schedulerReasonCode"}
SCHEDULER_REASONS = {"NONE", "READ_UNAVAILABLE", "AUTH_OR_CYCLE_FAILED", "CYCLE_BLOCKED",
                     "CYCLE_UNKNOWN", "TIMER_INACTIVE", "HOLD_PRESENT", "CYCLE_OVERDUE",
                     "STATE_UNVERIFIED"}
NEXT_DEV_FIELDS = {
    "schemaVersion",
    "check",
    "sourceHost",
    "status",
    "reconciledAt",
    "lastCycleResult",
    "timerActive",
    "timerEnabled",
    "holdCount",
    "activityCount",
    "leaseCount",
    "idleSeconds",
    "idleThresholdSeconds",
    "hibernationHealthy",
}


def _valid_nonnegative_integer(value: object) -> bool:
    return type(value) is int and value >= 0


def validate_payload(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("heartbeat size differs")
    value = json.loads(raw.decode("ascii"))
    if not isinstance(value, dict):
        raise ValueError("heartbeat schema differs")
    check = value.get("check")
    if check == "online-compiler-production-schedule":
        valid = (
            ((value.get("schemaVersion") == 1 and set(value) == PRODUCTION_FIELDS)
             or (value.get("schemaVersion") == 2 and set(value) == PRODUCTION_V2_FIELDS)
             or (value.get("schemaVersion") == 3 and set(value) == PRODUCTION_V3_FIELDS))
            and value.get("sourceHost") == "devbox"
            and value.get("status") in (
                {"OPEN", "CLOSED", "FAILED", "STARTING", "STOPPING"}
                if value.get("schemaVersion") in {2, 3} else {"OPEN", "CLOSED", "FAILED"})
            and value.get("lastCycleResult") in (
                {"SUCCESS", "BLOCKED", "FAILED", "UNKNOWN"}
                if value.get("schemaVersion") in {2, 3} else {"SUCCESS", "BLOCKED", "FAILED"})
            and type(value.get("timerActive")) is bool
            and type(value.get("timerEnabled")) is bool
            and _valid_nonnegative_integer(value.get("holdCount"))
            and type(value.get("schedulerHealthy")) is bool
            and (isinstance(value.get("reconciledAt"), str)
                 or (value.get("schemaVersion") in {2, 3} and value.get("reconciledAt") is None))
            and isinstance(value.get("stateUpdatedAt"), str)
        )
    elif check == "online-compiler-next-dev-hibernation":
        valid = (
            set(value) == NEXT_DEV_FIELDS
            and value.get("schemaVersion") == 1
            and value.get("sourceHost") == "devbox"
            and value.get("status") in {"ACTIVE", "HIBERNATED", "FAILED"}
            and value.get("lastCycleResult") in {"SUCCESS", "BLOCKED", "FAILED"}
            and type(value.get("timerActive")) is bool
            and type(value.get("timerEnabled")) is bool
            and all(
                _valid_nonnegative_integer(value.get(field))
                for field in (
                    "holdCount",
                    "activityCount",
                    "leaseCount",
                    "idleSeconds",
                    "idleThresholdSeconds",
                )
            )
            and value.get("idleThresholdSeconds") == 3600
            and type(value.get("hibernationHealthy")) is bool
            and isinstance(value.get("reconciledAt"), str)
        )
    else:
        valid = False
    if value.get("schemaVersion") == 3:
        valid = (valid and value.get("schedulerHealth") in {"HEALTHY", "DEGRADED"}
                 and value.get("schedulerReasonCode") in SCHEDULER_REASONS
                 and (value["schedulerHealth"] == "HEALTHY") == (value["schedulerReasonCode"] == "NONE")
                 and (value["schedulerHealth"] == "HEALTHY") == value.get("schedulerHealthy"))
    if not valid:
        raise ValueError("heartbeat schema differs")
    timestamp_fields = (
        ("reconciledAt", "stateUpdatedAt")
        if check == "online-compiler-production-schedule"
        else ("reconciledAt",)
    )
    if check == "online-compiler-production-schedule" and value.get("schemaVersion") in {2, 3}:
        if value.get("cycleState") not in {"RUNNING", "IDLE"}:
            raise ValueError("heartbeat cycle state differs")
        if (value.get("lastCycleResult") == "UNKNOWN") != (value.get("reconciledAt") is None):
            raise ValueError("heartbeat completion differs")
        timestamp_fields = ("stateUpdatedAt", "heartbeatAt") + (
            ("reconciledAt",) if value.get("reconciledAt") is not None else ())
        if value["cycleState"] == "RUNNING":
            timestamp_fields += ("cycleStartedAt", "cycleDeadlineAt")
        elif value.get("cycleStartedAt") is not None or value.get("cycleDeadlineAt") is not None:
            raise ValueError("idle heartbeat cycle bounds differ")
    for field in timestamp_fields:
        timestamp = value[field]
        if not isinstance(timestamp, str):
            raise ValueError("heartbeat timestamp differs")
        normalized = timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
        observed = datetime.fromisoformat(normalized)
        if observed.tzinfo is None:
            raise ValueError("heartbeat timestamp lacks timezone")
    if check == "online-compiler-production-schedule" and value.get("schemaVersion") in {2, 3}:
        observed = {field: datetime.fromisoformat(value[field].replace("Z", "+00:00"))
                    for field in timestamp_fields}
        now = datetime.now(timezone.utc)
        for field, stamp in observed.items():
            if field != "cycleDeadlineAt" and (stamp - now).total_seconds() > 60:
                raise ValueError("heartbeat timestamp is in the future")
        if value["cycleState"] == "RUNNING":
            duration = (observed["cycleDeadlineAt"] - observed["cycleStartedAt"]).total_seconds()
            if duration != 1800 or observed["cycleStartedAt"] > observed["heartbeatAt"]:
                raise ValueError("heartbeat cycle deadline differs")
    return value


def store_payload(raw: bytes, target: pathlib.Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    temporary = pathlib.Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        value = validate_payload(raw)
        canonical = (
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("ascii")
        store_payload(canonical, TARGETS[value["check"]])
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        print(f"heartbeat rejected: {type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
