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
TELEGRAM_MESSAGE_LIMIT = 4096
DEFAULT_MAX_ITEMS_PER_MESSAGE = 20
SEVERITY_RANK = {"ok": 0, "warning": 1, "critical": 2}
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
    group: str = ""


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
            priority = int(rule["priority"])
            if priority not in PRIORITY_NAMES:
                raise ValueError("Invalid priority assignment")
            if not rule.get("patterns"):
                raise ValueError("Priority assignment without patterns")
            group = str(rule.get("group", ""))
            if group and (
                priority != 3
                or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", group) is None
            ):
                raise ValueError("Invalid LOW notification group")
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
                group = _clean(rule.get("group", ""), 64)
                return Display(int(rule["priority"]), name, problem, resolved, group)
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
        max_items_per_message: int = DEFAULT_MAX_ITEMS_PER_MESSAGE,
        default_failure_confirmation_cycles: int = 2,
        network_failure_confirmation_cycles: int = 3,
        recovery_confirmation_cycles: int = 2,
        medium_reminder_seconds: int = 21600,
        suppressed_recovery_keys: Iterable[str] = (),
        suppressed_key_prefixes: Iterable[str] = (),
    ) -> None:
        if host not in {"devbox", "gp"}:
            raise ValueError("Unsupported host label")
        self.registry = registry
        self.host = host
        self.now = now
        self.notify = notify
        self.sender = sender or TelegramSender()
        if max_items_per_message < 1:
            raise ValueError("max_items_per_message must be positive")
        if min(
            default_failure_confirmation_cycles,
            network_failure_confirmation_cycles,
            recovery_confirmation_cycles,
            medium_reminder_seconds,
        ) < 1:
            raise ValueError("Notification policy values must be positive")
        self.max_items_per_message = max_items_per_message
        self.default_failure_confirmation_cycles = default_failure_confirmation_cycles
        self.network_failure_confirmation_cycles = network_failure_confirmation_cycles
        self.recovery_confirmation_cycles = recovery_confirmation_cycles
        self.medium_reminder_seconds = medium_reminder_seconds
        self.suppressed_recovery_keys = frozenset(suppressed_recovery_keys)
        self.suppressed_key_prefixes = tuple(suppressed_key_prefixes)

    @staticmethod
    def _pending_id(event: dict[str, Any]) -> str:
        return f"{event['priority']}:{event['status']}:{event['key']}"

    def _queue(self, state: dict[str, Any], event: dict[str, Any]) -> None:
        pending = state.setdefault("pending_notifications", {})
        existing = [
            (event_id, queued)
            for event_id, queued in pending.items()
            if queued.get("key") == event["key"]
        ]
        existing_statuses = {queued.get("status") for _, queued in existing}
        for event_id, _ in existing:
            pending.pop(event_id, None)

        # A recovery that was confirmed before NEW could be delivered needs no
        # external lifecycle message. A reminder must not replace an unsent NEW.
        if event["status"] == "RESOLVED" and "NEW" in existing_statuses:
            return
        if event["status"] == "REMINDER" and "NEW" in existing_statuses:
            event = {**event, "status": "NEW"}
        pending[self._pending_id(event)] = event

    def _refresh_pending(
        self,
        state: dict[str, Any],
        *,
        key: str,
        display: Display,
        alert_active: bool,
    ) -> None:
        pending = state.setdefault("pending_notifications", {})
        matching = [
            (event_id, event)
            for event_id, event in pending.items()
            if event.get("key") == key
        ]
        if not matching:
            return

        statuses = {event.get("status") for _, event in matching}
        selected: dict[str, Any] | None
        if alert_active:
            preferred = "NEW" if "NEW" in statuses else "REMINDER"
            selected = next(
                (event for _, event in matching if event.get("status") == preferred),
                None,
            )
        else:
            selected = next(
                (event for _, event in matching if event.get("status") == "RESOLVED"),
                None,
            )

        for event_id, _ in matching:
            pending.pop(event_id, None)
        if selected is None:
            return
        refreshed = {
            **selected,
            "priority": display.priority,
            "name": display.name,
            "problem": display.resolved if selected.get("status") == "RESOLVED" else display.problem,
            "host": self.host,
            "group": display.group,
        }
        pending[self._pending_id(refreshed)] = refreshed

    def _chunks(
        self,
        selected: list[tuple[str, dict[str, Any]]],
        formatter: Callable[[Iterable[dict[str, Any]]], str],
    ) -> list[list[tuple[str, dict[str, Any]]]]:
        chunks: list[list[tuple[str, dict[str, Any]]]] = []
        current: list[tuple[str, dict[str, Any]]] = []
        for item in selected:
            candidate = [*current, item]
            candidate_text = formatter(event for _, event in candidate)
            exceeds_limit = (
                len(candidate) > self.max_items_per_message
                or len(candidate_text) > TELEGRAM_MESSAGE_LIMIT
            )
            if exceeds_limit and not current:
                raise ValueError("Notification event exceeds Telegram message limit")
            if exceeds_limit:
                chunks.append(current)
                current = [item]
                if len(formatter([item[1]])) > TELEGRAM_MESSAGE_LIMIT:
                    raise ValueError("Notification event exceeds Telegram message limit")
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks

    def _reconcile_notification_groups(
        self,
        state: dict[str, Any],
        checks: dict[str, Any],
    ) -> None:
        members: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for key, entry in checks.items():
            group = str(entry.get("notification_group", ""))
            if group:
                members.setdefault(group, []).append((key, entry))

        group_states = state.setdefault("notification_groups", {})
        pending = state.setdefault("pending_notifications", {})
        for group, grouped_checks in members.items():
            priorities = {int(entry.get("priority", 2)) for _, entry in grouped_checks}
            names = {str(entry.get("name", "")) for _, entry in grouped_checks}
            problems = {str(entry.get("problem", "")) for _, entry in grouped_checks}
            resolved_messages = {
                str(entry.get("resolved", "")) for _, entry in grouped_checks
            }
            if (
                priorities != {3}
                or len(names) != 1
                or len(problems) != 1
                or len(resolved_messages) != 1
            ):
                raise ValueError("Notification group members differ")

            member_keys = {key for key, _ in grouped_checks}
            individual_events = [
                (event_id, event)
                for event_id, event in pending.items()
                if event.get("key") in member_keys
            ]
            individual_statuses = {
                str(event.get("status", "")) for _, event in individual_events
            }
            for event_id, _ in individual_events:
                pending.pop(event_id, None)

            previous = group_states.get(group)
            active = any(bool(entry.get("alert_active")) for _, entry in grouped_checks)
            group_key = f"notification-group:{group}"
            name = next(iter(names))
            problem = next(iter(problems))
            resolved = next(iter(resolved_messages))
            event = {
                "key": group_key,
                "priority": 3,
                "name": name,
                "host": self.host,
                "group": group,
            }

            if previous is None:
                if active and "NEW" in individual_statuses:
                    self._queue(state, {**event, "status": "NEW", "problem": problem})
                elif not active and "RESOLVED" in individual_statuses:
                    self._queue(
                        state,
                        {**event, "status": "RESOLVED", "problem": resolved},
                    )
            else:
                was_active = bool(previous.get("alert_active"))
                if active and not was_active:
                    self._queue(state, {**event, "status": "NEW", "problem": problem})
                elif not active and was_active:
                    self._queue(
                        state,
                        {**event, "status": "RESOLVED", "problem": resolved},
                    )
                elif active and individual_statuses & {"NEW", "REMINDER"}:
                    self._queue(
                        state,
                        {**event, "status": "REMINDER", "problem": problem},
                    )

            last_notification = max(
                [float(previous.get("last_notification", 0) or 0) if previous else 0]
                + [
                    float(entry.get("last_notification", 0) or 0)
                    for _, entry in grouped_checks
                ]
            )
            group_states[group] = {
                **(previous or {}),
                "key": group_key,
                "priority": 3,
                "name": name,
                "problem": problem,
                "resolved": resolved,
                "alert_active": active,
                "last_notification": last_notification,
            }
            self._refresh_pending(
                state,
                key=group_key,
                display=Display(3, name, problem, resolved, group),
                alert_active=active,
            )

    def process(self, state: dict[str, Any], results: Iterable[Any]) -> None:
        checks = state.setdefault("checks", {})
        current_keys: set[str] = set()
        config_cycles = {
            "transient": self.network_failure_confirmation_cycles,
            "sampled": self.default_failure_confirmation_cycles,
            "durable": 1,
        }

        for result in results:
            key = str(result.key)
            current_keys.add(key)
            previous = checks.get(key, {})
            notifications_suppressed = any(
                key.startswith(prefix) for prefix in self.suppressed_key_prefixes
            )
            display = self.registry.display(
                key,
                str(result.summary),
                str(getattr(result, "notification_code", "") or ""),
                getattr(result, "notification_context", None),
            )
            notification_code = str(
                getattr(result, "notification_code", "") or ""
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
            notified_severity = str(previous.get("notified_severity", ""))
            notified_code = str(previous.get("notified_code", ""))
            if not notified_severity:
                if alert_active and str(previous_severity) != "ok":
                    notified_severity = str(previous_severity)
                    notified_code = str(previous.get("notification_code", ""))
                elif alert_active and severity != "ok":
                    # Migrate an active v3 incident after one unconfirmed OK
                    # observation without replaying an escalation notification.
                    notified_severity = severity
                    notified_code = notification_code
                else:
                    notified_severity = "ok"

            if severity == "ok":
                failure_streak = 0
                if alert_active:
                    recovery_streak += 1
                    if recovery_streak >= self.recovery_confirmation_cycles:
                        if (
                            not notifications_suppressed
                            and key not in self.suppressed_recovery_keys
                        ):
                            self._queue(
                                state,
                                {
                                    "key": key,
                                    "priority": int(previous.get("priority", display.priority)),
                                    "status": "RESOLVED",
                                    "name": str(previous.get("name", display.name)),
                                    "problem": str(previous.get("resolved", display.resolved)),
                                    "host": self.host,
                                    "group": str(
                                        previous.get("notification_group", display.group)
                                    ),
                                },
                            )
                        alert_active = False
                        recovery_streak = 0
                        notified_severity = "ok"
                        notified_code = ""
                else:
                    recovery_streak = 0
            else:
                recovery_streak = 0
                if notifications_suppressed:
                    alert_active = False
                    failure_streak = min(required, failure_streak + 1)
                elif not alert_active:
                    failure_streak += 1
                    if failure_streak >= required:
                        alert_active = True
                        failure_streak = 0
                        notified_severity = severity
                        notified_code = notification_code
                        self._queue(
                            state,
                            {
                                "key": key,
                                "priority": display.priority,
                                "status": "NEW",
                                "name": display.name,
                                "problem": display.problem,
                                "host": self.host,
                                "group": display.group,
                            },
                        )
                else:
                    failure_streak = 0
                    severity_increased = (
                        SEVERITY_RANK.get(severity, 0)
                        > SEVERITY_RANK.get(notified_severity, 0)
                    )
                    code_changed = bool(
                        notified_code
                        and notification_code
                        and notified_code != notification_code
                    )
                    if severity_increased or code_changed:
                        notified_severity = severity
                        notified_code = notification_code
                        self._queue(
                            state,
                            {
                                "key": key,
                                "priority": display.priority,
                                "status": "REMINDER",
                                "name": display.name,
                                "problem": display.problem,
                                "host": self.host,
                                "group": display.group,
                            },
                        )

            checks[key] = {
                **previous,
                "severity": severity,
                "last_seen": self.now,
                "last_notification": last_notification,
                "summary": _clean(result.summary, 300),
                "incident_id": incident,
                "notification_class": classification,
                "notification_code": notification_code,
                "notified_severity": notified_severity,
                "notified_code": notified_code,
                "alert_active": alert_active,
                "failure_streak": failure_streak,
                "recovery_streak": recovery_streak,
                "priority": display.priority,
                "name": display.name,
                "problem": display.problem,
                "resolved": display.resolved,
                "notification_group": display.group,
            }
            self._refresh_pending(
                state,
                key=key,
                display=display,
                alert_active=alert_active,
            )

        for key in list(checks):
            if key not in current_keys and self.now - float(checks[key].get("last_seen", 0)) > 86400:
                del checks[key]

        self._reconcile_notification_groups(state, checks)

        pending = state.setdefault("pending_notifications", {})
        pending_new_keys = {
            event["key"] for event in pending.values() if event.get("status") == "NEW"
        }
        reminders = {1: 900, 2: self.medium_reminder_seconds}
        for key, entry in checks.items():
            priority = int(entry.get("priority", 2))
            if (
                priority in reminders
                and entry.get("alert_active")
                and not entry.get("notification_group")
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
                        "group": "",
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
                formatter = lambda events, status=status: format_message(status, events)
                for chunk in self._chunks(selected, formatter):
                    try:
                        self.sender.send(
                            priority,
                            formatter(event for _, event in chunk),
                        )
                    except Exception as error:
                        print(
                            f"NOTIFY_FAILED priority={PRIORITY_NAMES[priority]} status={status} error={type(error).__name__}",
                            file=sys.stderr,
                        )
                        continue
                    for event_id, event in chunk:
                        if event["key"] in checks:
                            checks[event["key"]]["last_notification"] = self.now
                        pending.pop(event_id, None)
                    print(
                        f"NOTIFY_SENT priority={PRIORITY_NAMES[priority]} status={status} count={len(chunk)}",
                        file=sys.stderr,
                    )

    def _deliver_low(self, state: dict[str, Any], checks: dict[str, Any]) -> None:
        local = datetime.fromtimestamp(self.now, ZoneInfo("Europe/Warsaw"))
        today = local.date().isoformat()
        if local.hour < 8 or state.get("last_low_digest_date") == today:
            return
        if state.get("low_digest_partial_date") != today:
            state.pop("low_digest_partial_date", None)
            state.pop("low_digest_sent_keys", None)
        sent_keys = set(state.get("low_digest_sent_keys", []))
        pending = state.setdefault("pending_notifications", {})
        selected = [
            (event_id, event)
            for event_id, event in pending.items()
            if event["priority"] == 3
        ]
        selected_keys = {event["key"] for _, event in selected}
        for key, entry in checks.items():
            if (
                int(entry.get("priority", 2)) != 3
                or not entry.get("alert_active")
                or entry.get("notification_group")
                or key in selected_keys
                or key in sent_keys
            ):
                continue
            event = {
                "key": key,
                "priority": 3,
                "status": "REMINDER",
                "name": entry.get("name", _key_subject(key)),
                "problem": entry.get("problem", "wykryto nieprawidłowy stan"),
                "host": self.host,
                "group": "",
            }
            self._queue(state, event)
        for group, entry in state.get("notification_groups", {}).items():
            key = str(entry.get("key", f"notification-group:{group}"))
            if (
                int(entry.get("priority", 2)) != 3
                or not entry.get("alert_active")
                or key in selected_keys
                or key in sent_keys
            ):
                continue
            self._queue(
                state,
                {
                    "key": key,
                    "priority": 3,
                    "status": "REMINDER",
                    "name": entry.get("name", _title(group)),
                    "problem": entry.get(
                        "problem", "wykryto nieprawidłowy stan"
                    ),
                    "host": self.host,
                    "group": group,
                },
            )
        selected = [
            (event_id, event)
            for event_id, event in pending.items()
            if event["priority"] == 3
        ]
        if not selected:
            return
        if not self.notify:
            return
        failed = False
        for chunk in self._chunks(selected, format_low_digest):
            try:
                self.sender.send(
                    3,
                    format_low_digest(event for _, event in chunk),
                )
            except Exception as error:
                failed = True
                print(
                    f"NOTIFY_FAILED priority=low status=digest error={type(error).__name__}",
                    file=sys.stderr,
                )
                continue
            for event_id, event in chunk:
                if event["key"] in checks:
                    checks[event["key"]]["last_notification"] = self.now
                group = str(event.get("group", ""))
                if group in state.get("notification_groups", {}):
                    state["notification_groups"][group]["last_notification"] = self.now
                    for entry in checks.values():
                        if entry.get("notification_group") == group:
                            entry["last_notification"] = self.now
                sent_keys.add(event["key"])
                pending.pop(event_id, None)
            print(
                f"NOTIFY_SENT priority=low status=digest count={len(chunk)}",
                file=sys.stderr,
            )
        if not failed:
            state["last_low_digest_date"] = today
            state.pop("low_digest_partial_date", None)
            state.pop("low_digest_sent_keys", None)
        else:
            state["low_digest_partial_date"] = today
            state["low_digest_sent_keys"] = sorted(sent_keys)
