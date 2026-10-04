from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .adapters import devbox, gp
from .notifications import NotificationEngine, PriorityRegistry, TelegramSender, format_message


ADAPTERS = {"devbox": devbox, "gp": gp}


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        config = json.load(handle)
    if config.get("schema_version") != 1 or config.get("adapter") not in ADAPTERS:
        raise ValueError("Unsupported system-monitor profile")
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--priorities")
    parser.add_argument("--state-file")
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--test-notification", choices=("high", "medium", "low"))
    arguments = parser.parse_args(argv)

    config = load_config(arguments.config)
    if arguments.state_file:
        config = dict(config)
        config["state_file"] = arguments.state_file
    priorities_path = arguments.priorities or config["priorities_file"]
    registry = PriorityRegistry.load(priorities_path)
    adapter = ADAPTERS[config["adapter"]]
    monitor = adapter.Monitor(config, notify=False)

    if arguments.test_notification:
        priority = {"high": 1, "medium": 2, "low": 3}[arguments.test_notification]
        TelegramSender().send(
            priority,
            format_message(
                "NEW",
                [{
                    "name": "System Monitor",
                    "problem": "kanał powiadomień został skonfigurowany",
                    "host": config["host_label"],
                }],
            ),
        )
        return 0

    monitor.run_all()
    engine = NotificationEngine(
        registry,
        host=config["host_label"],
        now=monitor.now,
        notify=not arguments.no_notify,
    )
    engine.process(monitor.state, monitor.results)
    monitor.save_state()
    if hasattr(monitor, "mark_completed"):
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
        print(f"system-monitor failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
