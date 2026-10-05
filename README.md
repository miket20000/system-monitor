# System Monitor

Canonical source for the independent `devbox-monitor` and `gp-monitor`
instances. Both instances run local probes every five minutes and share one
notification engine, priority registry and Telegram message contract.

## Layout

- `src/system_monitor/` — shared engine and host-specific probe adapters.
- `profiles/` — versioned devbox and GP profiles.
- `config/priorities.json` — priority, display-name and routing assignments.
- `deployments/` — source systemd units for each host.
- `tests/` — shared behavior tests and imported regression suites.

## Runtime compatibility

The source repository is new, but installed entrypoints, profiles and state
remain at their established locations:

| instance | entrypoint | profile | state |
|---|---|---|---|
| devbox | `~/.local/lib/devbox-monitor/devbox-monitor.py` | `~/.config/devbox-monitor/config.json` | `~/.local/state/devbox-monitor/state.json` |
| GP | `/usr/local/lib/gp-monitor/gp-monitor.py` | `/etc/gp-monitor/config.json` | `/var/lib/gp-monitor/state.json` |

The entrypoints are compatibility wrappers. Shared package code is installed
under `~/.local/lib/system-monitor` on devbox and
`/usr/local/lib/system-monitor` on GP. The legacy Online Compiler heartbeat
receiver keeps its established `/usr/local/lib/gp-monitor/` entrypoint for
rollback compatibility, but the active GP profile reads the local system
scheduler units and owner-only state under
`/var/lib/online-compiler-scheduler/`. Scheduler health, environment state,
state freshness and Next Dev idle expiry are reported independently. The
established external-heartbeat incident keys remain unchanged so an existing
incident is not replayed as `[NEW]` during migration.

The devbox profile has one bounded read-only dead-man probe for GP. It executes
a fixed SSH `systemctl show gp-monitor.service` command and is classified as a
network observation, so a new alert requires three consecutive failed cycles.

Before a rollout, copy the profile, unit, environment file, installed code and
state into a private timestamped rollback directory. Run a shadow cycle with
both `--no-notify` and a private `--state-file`; `--no-notify` alone is not
read-only. Deploy devbox first and GP only after two successful natural cycles.

## Notification contract

Messages are routed to `sysmon-high`, `sysmon-medium` or `sysmon-low`. The
channel carries the priority; message text uses only `[NEW]`, `[REMINDER]` and
`[RESOLVED]`. Every event occupies one line:

```text
Service name : problem : devbox|gp
```

Tracked configuration contains only the environment variable names. Bot token
and chat IDs remain in the protected runtime environment files.
