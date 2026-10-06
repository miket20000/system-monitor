from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo


PRIORITY_NAMES = {1: "high", 2: "medium", 3: "low"}
CHAT_ENV = {
    1: "TELEGRAM_CHAT_ID_HIGH",
    2: "TELEGRAM_CHAT_ID_MEDIUM",
    3: "TELEGRAM_CHAT_ID_LOW",
}
STATUS_ORDER = ("NEW", "REMINDER", "RESOLVED")
TYPE_WORDS = re.compile(
    r"\b(endpoint|service|timer|cron|heartbeat|filesystem|database)\b", re.I
)
MESSAGE_CONTEXT_FIELDS = {
    "ONLINE_COMPILER_SCHEDULER_BLOCKED": {
        "environmentStatus": {
            "OPEN", "CLOSED", "STARTING", "STOPPING", "ACTIVE", "HIBERNATED",
            "FAILED", "UNKNOWN",
        },
        "holdCount": "nonnegative_integer",
    },
    "ONLINE_COMPILER_SCHEDULER_DEGRADED": {
        "environmentStatus": {
            "OPEN", "CLOSED", "STARTING", "STOPPING", "ACTIVE", "HIBERNATED",
            "FAILED", "UNKNOWN",
        },
        "reasonCode": {
            "READ_UNAVAILABLE", "AUTH_OR_CYCLE_FAILED", "CYCLE_BLOCKED",
            "CYCLE_UNKNOWN", "TIMER_INACTIVE", "HOLD_PRESENT", "CYCLE_OVERDUE",
            "STATE_UNVERIFIED",
        },
    },
    "ONLINE_COMPILER_STATE_STALE": {},
    "ONLINE_COMPILER_CYCLE_SLOW": {},
    "ONLINE_COMPILER_CYCLE_OVERDUE": {},
    "ONLINE_COMPILER_IDLE_OVERDUE": {
        "idleSeconds": "nonnegative_integer",
        "idleThresholdSeconds": "nonnegative_integer",
    },
    "GP_MONITOR_DEAD_MAN": {},
}


@dataclass(frozen=True)
class Display:
    priority: int
    name: str
    problem: str
    resolved: str


def _clean(value: object, limit: int = 180) -> str:
    return " ".join(str(value).split()).strip()[:limit]


def _title(value: str) -> str:
    value = re.sub(r"\.(service|timer)$", "", value)
    value = value.replace("@", " ").replace("_", " ").replace("-", " ")
    return " ".join(part.capitalize() for part in value.split()) or "System Monitor"


def _key_subject(key: str) -> str:
    parts = [part for part in key.split(":") if part]
    ignored = {
        "endpoint", "service", "system-service", "user-service", "system-unit",
        "user-unit", "system-timer", "user-timer", "cron", "database", "route",
        "journal", "external-heartbeat", "filesystem", "space", "inodes", "result",
        "timer", "freshness", "tls", "dns", "memory", "load", "mount", "vpn",
    }
    candidate = next((part for part in parts if part not in ignored and part != "/"), "")
    if key.startswith("filesystem:space"):
        return "Dysk systemowy"
    if key.startswith("filesystem:inodes"):
        return "System plików"
    if key.startswith("memory:"):
        return "Pamięć operacyjna"
    if key.startswith("load:"):
        return "Obciążenie maszyny"
    if key.startswith("mount:"):
        return "Dysk backupowy"
    return _title(candidate)


def _problem(summary: str, resolved: bool = False) -> str:
    text = _clean(summary).lower()
    if resolved:
        if any(word in text for word in ("backup", "archive", "sync", "timer", "completed")):
            return "zadanie ponownie zakończyło się pomyślnie"
        return "usługa ponownie działa prawidłowo"
    patterns = (
        (("http ", "unreachable", "invalid json", "unhealthy json", "response took"), "usługa nie odpowiada prawidłowo"),
        (("not active", "inactive", "failed state", "is failed"), "usługa nie działa"),
        (("last run failed", "non-zero", "last known run failed", "result failed"), "ostatni przebieg zakończył się niepowodzeniem"),
        (("interrupted", "condition", "blocked"), "ostatni przebieg nie zakończył się prawidłowo"),
        (("no successful", "last completed", "last success", "missed", "stale", "expired", "overdue"), "ostatni poprawny przebieg jest przeterminowany"),
        (("disk usage", "space"), "wykorzystanie dysku przekroczyło próg"),
        (("inode",), "wykorzystanie systemu plików przekroczyło próg"),
        (("memory usage",), "wykorzystanie pamięci przekroczyło próg"),
        (("load average", "load:"), "obciążenie maszyny przekroczyło próg"),
        (("read-only",), "system plików działa w trybie tylko do odczytu"),
        (("mount", "uuid", "filesystem"), "wymagany dysk nie jest dostępny prawidłowo"),
        (("vpn", "route", "ppp"), "połączenie VPN nie działa prawidłowo"),
        (("certificate", "tls"), "certyfikat nie spełnia wymagań ważności"),
        (("dns",), "rozwiązywanie nazw nie działa prawidłowo"),
        (("heartbeat",), "brak aktualnego potwierdzenia działania"),
        (("reboot",), "wykryto ponowne uruchomienie maszyny"),
    )
    for needles, value in patterns:
        if any(needle in text for needle in needles):
            return value
    return "wykryto nieprawidłowy stan"


class PriorityRegistry:
    def __init__(self, payload: dict[str, Any]) -> None:
        if payload.get("schema_version") != 1:
            raise ValueError("Unsupported priorities schema_version")
        self.default_priority = int(payload.get("default_priority", 2))
        self.rules = payload.get("assignments", [])
        self.message_codes = payload.get("message_codes", {})
        if self.default_priority not in PRIORITY_NAMES:
            raise ValueError("Invalid default priority")
        for rule in self.rules:
            if int(rule["priority"]) not in PRIORITY_NAMES:
                raise ValueError("Invalid priority assignment")
            if not rule.get("patterns"):
                raise ValueError("Priority assignment without patterns")
        if set(self.message_codes) != set(MESSAGE_CONTEXT_FIELDS):
            raise ValueError("Priority registry message code allowlist differs")
        for code, message in self.message_codes.items():
            if not isinstance(message, str) or not message.strip():
                raise ValueError(f"Invalid priority message for {code}")

    @classmethod
    def load(cls, path: str | Path) -> "PriorityRegistry":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def _message(self, code: str, context: object) -> str:
        if code not in MESSAGE_CONTEXT_FIELDS or not isinstance(context, dict):
            raise ValueError("Unsupported notification message code")
        expected = MESSAGE_CONTEXT_FIELDS[code]
        if set(context) != set(expected):
            raise ValueError("Notification message context differs")
        clean_context: dict[str, str | int] = {}
        for field, validator in expected.items():
            value = context[field]
            if validator == "nonnegative_integer":
                if type(value) is not int or value < 0 or value > 10_000_000:
                    raise ValueError("Invalid notification integer context")
            elif value not in validator:
                raise ValueError("Invalid notification enum context")
            clean_context[field] = value
        try:
            return _clean(self.message_codes[code].format_map(clean_context))
        except (KeyError, ValueError) as error:
            raise ValueError("Invalid priority message template") from error

    def display(
        self,
        key: str,
        summary: str,
        message_code: str = "",
        message_context: object = None,
    ) -> Display:
        for rule in self.rules:
            if any(fnmatch.fnmatchcase(key, pattern) for pattern in rule["patterns"]):
                name = _clean(rule.get("name") or _key_subject(key))
                problem = (
                    self._message(message_code, message_context or {})
                    if message_code
                    else _clean(rule.get("problem") or _problem(summary))
                )
                resolved = _clean(rule.get("resolved") or _problem(summary, True))
                return Display(int(rule["priority"]), name, problem, resolved)
        return Display(
            self.default_priority,
            _key_subject(key),
            _problem(summary),
            _problem(summary, True),
        )


def observation_class(result: Any) -> str:
    raw = getattr(result, "notification_class", "")
    if raw == "network":
        return "transient"
    if raw == "durable" or getattr(result, "incident_id", ""):
        return "durable"
    if str(result.key).startswith(("endpoint:", "dns:", "tls:", "vpn:")):
        return "transient"
    return "sampled"


def format_message(status: str, events: Iterable[dict[str, Any]]) -> str:
    if status not in STATUS_ORDER:
        raise ValueError("Unsupported notification status")
    lines = [f"[{status}]"]
    for event in events:
        name = _clean(event["name"])
        problem = _clean(event["problem"])
        host = _clean(event["host"])
        if not name or not problem or host not in {"devbox", "gp"}:
            raise ValueError("Invalid notification event")
        line = f"{name} : {problem} : {host}"
        if TYPE_WORDS.search(line) or "http://" in line.lower() or "https://" in line.lower():
            raise ValueError("Notification contains a forbidden technical detail")
        lines.append(line)
    if len(lines) == 1:
        raise ValueError("Notification has no events")
    return "\n".join(lines)


def format_low_digest(events: Iterable[dict[str, Any]]) -> str:
    grouped = {status: [] for status in STATUS_ORDER}
    for event in events:
        grouped[event["status"]].append(event)
    sections = [format_message(status, grouped[status]) for status in STATUS_ORDER if grouped[status]]
    if not sections:
        raise ValueError("Low digest has no events")
    return "\n\n".join(sections)


class TelegramSender:
    def __init__(self, opener: Callable[..., Any] = urllib.request.urlopen) -> None:
        self.opener = opener

    def send(self, priority: int, text: str) -> None:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get(CHAT_ENV[priority])
        if not token or not chat_id:
            raise RuntimeError(f"Missing Telegram credentials for {PRIORITY_NAMES[priority]}")
        body = json.dumps({"chat_id": chat_id, "text": text}).encode()
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if not payload.get("ok"):
                    raise RuntimeError("Telegram rejected notification")
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"Telegram HTTP {error.code}") from error


class NotificationEngine:
    def __init__(
        self,
        registry: PriorityRegistry,
        *,
        host: str,
        now: float,
        notify: bool,
        sender: TelegramSender | None = None,
    ) -> None:
        if host not in {"devbox", "gp"}:
            raise ValueError("Unsupported host label")
        self.registry = registry
        self.host = host
        self.now = now
        self.notify = notify
        self.sender = sender or TelegramSender()

    @staticmethod
    def _pending_id(event: dict[str, Any]) -> str:
        return f"{event['priority']}:{event['status']}:{event['key']}"

    def _queue(self, state: dict[str, Any], event: dict[str, Any]) -> None:
        pending = state.setdefault("pending_notifications", {})
        pending[self._pending_id(event)] = event

    def _refresh_pending(
        self,
        state: dict[str, Any],
        *,
        key: str,
        display: Display,
    ) -> None:
        pending = state.setdefault("pending_notifications", {})
        for event_id, event in list(pending.items()):
            if event.get("key") != key:
                continue
            refreshed = {
                **event,
                "priority": display.priority,
                "name": display.name,
                "problem": display.resolved if event.get("status") == "RESOLVED" else display.problem,
                "host": self.host,
            }
            refreshed_id = self._pending_id(refreshed)
            if refreshed_id != event_id:
                pending.pop(event_id, None)
            pending[refreshed_id] = refreshed

    def process(self, state: dict[str, Any], results: Iterable[Any]) -> None:
        checks = state.setdefault("checks", {})
        current_keys: set[str] = set()
        config_cycles = {"transient": 3, "sampled": 2, "durable": 1}

        for result in results:
            key = str(result.key)
            current_keys.add(key)
            previous = checks.get(key, {})
            display = self.registry.display(
                key,
                str(result.summary),
                str(getattr(result, "notification_code", "") or ""),
                getattr(result, "notification_context", None),
            )
            previous_severity = previous.get("severity", "ok")
            last_notification = float(previous.get("last_notification", 0) or 0)
            alert_active = bool(
                previous.get(
                    "alert_active",
                    previous_severity != "ok" and last_notification > 0,
                )
            )
            incident = str(getattr(result, "incident_id", "") or "")

            failure_streak = int(previous.get("failure_streak", 0))
            recovery_streak = int(previous.get("recovery_streak", 0))
            classification = observation_class(result)
            required = config_cycles[classification]
            severity = str(result.severity)

            if severity == "ok":
                failure_streak = 0
                if alert_active:
                    recovery_streak += 1
                    if recovery_streak >= 2:
                        self._queue(
                            state,
                            {
                                "key": key,
                                "priority": int(previous.get("priority", display.priority)),
                                "status": "RESOLVED",
                                "name": str(previous.get("name", display.name)),
                                "problem": str(previous.get("resolved", display.resolved)),
                                "host": self.host,
                            },
                        )
                        alert_active = False
                        recovery_streak = 0
                else:
                    recovery_streak = 0
            else:
                recovery_streak = 0
                if not alert_active:
                    failure_streak += 1
                    if failure_streak >= required:
                        alert_active = True
                        failure_streak = 0
                        self._queue(
                            state,
                            {
                                "key": key,
                                "priority": display.priority,
                                "status": "NEW",
                                "name": display.name,
                                "problem": display.problem,
                                "host": self.host,
                            },
                        )
                else:
                    failure_streak = 0

            checks[key] = {
                **previous,
                "severity": severity,
                "last_seen": self.now,
                "last_notification": last_notification,
                "summary": _clean(result.summary, 300),
                "incident_id": incident,
                "notification_class": classification,
                "alert_active": alert_active,
                "failure_streak": failure_streak,
                "recovery_streak": recovery_streak,
                "priority": display.priority,
                "name": display.name,
                "problem": display.problem,
                "resolved": display.resolved,
            }
            self._refresh_pending(state, key=key, display=display)

        for key in list(checks):
            if key not in current_keys and self.now - float(checks[key].get("last_seen", 0)) > 86400:
                del checks[key]

        pending = state.setdefault("pending_notifications", {})
        pending_new_keys = {
            event["key"] for event in pending.values() if event.get("status") == "NEW"
        }
        reminders = {1: 900, 2: 21600}
        for key, entry in checks.items():
            priority = int(entry.get("priority", 2))
            if (
                priority in reminders
                and entry.get("alert_active")
                and key not in pending_new_keys
                and self.now - float(entry.get("last_notification", 0) or 0) >= reminders[priority]
            ):
                self._queue(
                    state,
                    {
                        "key": key,
                        "priority": priority,
                        "status": "REMINDER",
                        "name": entry.get("name", _key_subject(key)),
                        "problem": entry.get("problem", "wykryto nieprawidłowy stan"),
                        "host": self.host,
                    },
                )

        self._deliver_immediate(state, checks)
        self._deliver_low(state, checks)
        state["notification_state_version"] = 3

    def _deliver_immediate(self, state: dict[str, Any], checks: dict[str, Any]) -> None:
        pending = state.setdefault("pending_notifications", {})
        for priority in (1, 2):
            for status in STATUS_ORDER:
                selected = [
                    (event_id, event)
                    for event_id, event in pending.items()
                    if event["priority"] == priority and event["status"] == status
                ]
                if not selected or not self.notify:
                    continue
                try:
                    self.sender.send(priority, format_message(status, [event for _, event in selected]))
                except Exception as error:
                    print(
                        f"NOTIFY_FAILED priority={PRIORITY_NAMES[priority]} status={status} error={type(error).__name__}",
                        file=sys.stderr,
                    )
                    continue
                for event_id, event in selected:
                    if event["key"] in checks:
                        checks[event["key"]]["last_notification"] = self.now
                    pending.pop(event_id, None)
                print(
                    f"NOTIFY_SENT priority={PRIORITY_NAMES[priority]} status={status} count={len(selected)}",
                    file=sys.stderr,
                )

    def _deliver_low(self, state: dict[str, Any], checks: dict[str, Any]) -> None:
        local = datetime.fromtimestamp(self.now, ZoneInfo("Europe/Warsaw"))
        today = local.date().isoformat()
        if local.hour < 8 or state.get("last_low_digest_date") == today:
            return
        pending = state.setdefault("pending_notifications", {})
        selected = [
            (event_id, event)
            for event_id, event in pending.items()
            if event["priority"] == 3
        ]
        selected_keys = {event["key"] for _, event in selected}
        for key, entry in checks.items():
            if int(entry.get("priority", 2)) != 3 or not entry.get("alert_active") or key in selected_keys:
                continue
            event = {
                "key": key,
                "priority": 3,
                "status": "REMINDER",
                "name": entry.get("name", _key_subject(key)),
                "problem": entry.get("problem", "wykryto nieprawidłowy stan"),
                "host": self.host,
            }
            self._queue(state, event)
        selected = [
            (event_id, event)
            for event_id, event in pending.items()
            if event["priority"] == 3
        ]
        if not selected:
            return
        if not self.notify:
            return
        try:
            self.sender.send(3, format_low_digest([event for _, event in selected]))
        except Exception as error:
            print(
                f"NOTIFY_FAILED priority=low status=digest error={type(error).__name__}",
                file=sys.stderr,
            )
            return
        for event_id, event in selected:
            if event["key"] in checks:
                checks[event["key"]]["last_notification"] = self.now
            pending.pop(event_id, None)
        state["last_low_digest_date"] = today
        print(f"NOTIFY_SENT priority=low status=digest count={len(selected)}", file=sys.stderr)
