import json
import subprocess
from pathlib import Path

from system_monitor.adapters.devbox import Monitor
from system_monitor.notifications import NotificationEngine, PriorityRegistry


ROOT = Path(__file__).parents[1]
BOOT_A = "d07c9b52-d5eb-472e-a89c-27d8f9da108c"
BOOT_B = "82672599-395b-47d6-890d-173918cd09ef"


def monitor(tmp_path):
    return Monitor({
        "state_file": str(tmp_path / "state.json"),
        "dnsproxy_hosts": [{"host": "dnsproxy1", "ssh_config": "/home/miket/.ssh/config"}],
    })


def payload(boot=BOOT_A, restarts=0, severity="ok"):
    return {
        "schema_version": 1,
        "host": "dnsproxy1",
        "boot_id": boot,
        "container_restart_count": restarts,
        "checks": [
            {"key": key, "severity": severity, "summary": "Probe result", "details": ""}
            for key in ("sync:freshness", "dns:ipv4:udp", "dns:ipv4:tcp",
                        "dns:ipv6:udp", "dns:ipv6:tcp", "sync:cron-definition",
                        "filesystem:space:/", "filesystem:inodes:/",
                        "memory:used", "load:5m", "service:docker.service",
                        "service:cron.service", "service:ssh.service",
                        "service:systemd-timesyncd.service", "network:ipv4:address",
                        "network:ipv6:address", "network:ipv4:default-route",
                        "network:ipv6:default-route", "container:dnsproxy:state",
                        "container:dnsproxy:image", "container:dnsproxy:bindings",
                        "firewall:drop", "firewall:tcp:53", "firewall:udp:53",
                        "firewall:ipv6-dns-acl")
        ],
    }


def reply(data=None, status=0):
    return subprocess.CompletedProcess([], status, json.dumps(data) if data is not None else "", "")


def result(monitor, suffix):
    return next(item for item in monitor.results if item.key == "dnsproxy:dnsproxy1:" + suffix)


def test_probe_import_and_counter_changes(tmp_path):
    subject = monitor(tmp_path)
    calls = []
    subject.run_command = lambda command, timeout=15: (calls.append((command, timeout)) or reply(payload()))
    subject.check_dnsproxies()
    command, timeout = calls[0]
    assert command[:4] == ["/usr/bin/ssh", "-F", "/home/miket/.ssh/config", "-o"]
    assert "StrictHostKeyChecking=yes" in command
    assert command[-1] == "--probe" and timeout == 90
    assert result(subject, "probe-payload").severity == "ok"
    assert result(subject, "dns:ipv6:tcp").severity == "ok"
    assert result(subject, "host:reboot").severity == "ok"

    subject.results.clear()
    subject.run_command = lambda *_args, **_kwargs: reply(payload(restarts=1))
    subject.check_dnsproxies()
    assert result(subject, "container:dnsproxy:restarts").severity == "warning"
    assert result(subject, "container:dnsproxy:restarts").notification_class == "durable"

    subject.results.clear()
    subject.run_command = lambda *_args, **_kwargs: reply(payload(boot=BOOT_B, restarts=0))
    subject.check_dnsproxies()
    assert result(subject, "host:reboot").severity == "warning"
    assert result(subject, "container:dnsproxy:restarts").severity == "ok"


def test_transport_and_payload_failures_are_separate(tmp_path):
    subject = monitor(tmp_path)
    subject.run_command = lambda *_args, **_kwargs: reply(status=255)
    subject.check_dnsproxies()
    assert [item.key for item in subject.results] == ["dnsproxy:dnsproxy1:probe-transport"]
    assert subject.results[0].notification_class == "network"

    subject.results.clear()
    bad = payload()
    bad["host"] = "dnsproxy2"
    subject.run_command = lambda *_args, **_kwargs: reply(bad)
    subject.check_dnsproxies()
    assert result(subject, "probe-transport").severity == "ok"
    assert result(subject, "probe-payload").severity == "critical"
    assert not any(item.key.endswith(":sync:freshness") for item in subject.results)


def test_incomplete_or_duplicate_payload_cannot_clear_incidents(tmp_path):
    subject = monitor(tmp_path)
    for change in (lambda data: data["checks"].pop(),
                   lambda data: data["checks"].append(data["checks"][0])):
        data = payload()
        change(data)
        subject.run_command = lambda *_args, **_kwargs: reply(data)
        subject.results.clear()
        subject.check_dnsproxies()
        assert result(subject, "probe-payload").severity == "critical"
        assert len(subject.results) == 2


def test_parent_error_keys_replace_conditional_children(tmp_path):
    subject = monitor(tmp_path)
    data = payload()
    data["checks"] = [
        item for item in data["checks"]
        if not item["key"].startswith(("network:", "container:dnsproxy:", "firewall:"))
        or item["key"] == "firewall:ipv6-dns-acl"
    ]
    for key in ("network:addresses", "container:dnsproxy", "firewall:docker-user"):
        data["checks"].append({"key": key, "severity": "critical", "summary": "Inspection failed"})
    subject.run_command = lambda *_args, **_kwargs: reply(data)
    subject.check_dnsproxies()
    assert result(subject, "probe-payload").severity == "ok"
    for key in ("network:addresses", "container:dnsproxy", "firewall:docker-user"):
        assert result(subject, key).severity == "critical"


def test_missing_unconditional_check_is_invalid(tmp_path):
    subject = monitor(tmp_path)
    for missing in ("filesystem:space:/", "service:ssh.service", "firewall:ipv6-dns-acl",
                    "network:ipv6:default-route", "container:dnsproxy:bindings"):
        data = payload()
        data["checks"] = [item for item in data["checks"] if item["key"] != missing]
        subject.run_command = lambda *_args, **_kwargs: reply(data)
        subject.results.clear()
        subject.check_dnsproxies()
        assert result(subject, "probe-payload").severity == "critical", missing


def test_missing_remote_sample_preserves_active_incident_after_a_day(tmp_path):
    registry = PriorityRegistry.load(ROOT / "config/priorities.json")
    state = {"checks": {
        "dnsproxy:dnsproxy1:sync:freshness": {
            "severity": "critical", "alert_active": True,
            "last_seen": 1, "last_notification": 1,
            "priority": 1, "name": "DNS Proxy 1", "problem": "sync stale",
        },
    }}
    engine = NotificationEngine(registry, host="devbox", now=100000,
                                notify=False, retain_missing_prefixes=("dnsproxy:",))
    engine.process(state, [])
    assert state["checks"]["dnsproxy:dnsproxy1:sync:freshness"]["alert_active"]
    assert not any(event["status"] == "RESOLVED" for event in state.get("pending_notifications", {}).values())


def test_dns_and_sync_priorities_are_high():
    registry = PriorityRegistry.load(ROOT / "config/priorities.json")
    for host in ("dnsproxy1", "dnsproxy2"):
        for suffix in ("dns:ipv4:udp", "dns:ipv6:tcp", "sync:freshness",
                       "firewall:drop", "network:ipv6:address",
                       "probe-transport", "probe-payload"):
            assert registry.display(f"dnsproxy:{host}:{suffix}", "failed").priority == 1
        assert registry.display(f"dnsproxy:{host}:host:reboot", "rebooted").priority == 2
