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

`lesson-materials-sync.timer` is owned by GP as a root system timer and is
monitored only by the GP profile with 18-hour warning and 26-hour critical
freshness thresholds. The retired devbox user timer must not be restored.

The entrypoints are compatibility wrappers. Shared package code is installed
under `~/.local/lib/system-monitor` on devbox and
`/usr/local/lib/system-monitor` on GP. The legacy Online Compiler heartbeat
receiver keeps its established `/usr/local/lib/gp-monitor/` entrypoint for
rollback compatibility, but the active GP profile reads the local system
scheduler units and owner-only state under
`/var/lib/online-compiler-scheduler/`. Scheduler health, environment state,
state freshness and Next Dev idle expiry are reported independently. A running
steady-state cycle is `IN_PROGRESS` and becomes `CYCLE_SLOW` after five
minutes. A `STARTING` or `STOPPING` transition remains `IN_PROGRESS` for 15
minutes because its verified SQL, fixed-workload and warm-capacity convergence
normally exceeds five minutes. Every running cycle becomes `CYCLE_OVERDUE`
after the service's 30-minute deadline plus one minute of tolerance. An idle
production scheduler warns after two missed five-minute slots and becomes
critical after three. The established external-heartbeat
incident keys remain unchanged so an existing incident is not replayed as
`[NEW]` during migration.

The devbox profile has one bounded read-only dead-man probe for GP. It executes
a fixed SSH `systemctl show gp-monitor.service` command and is classified as a
network observation, so a new alert requires three consecutive failed cycles.

The Canva3 daily-success check also reads that account's `enabled` flag from
the local Password Reset provider configuration. A disabled account stays
critical even when its state file records success today. This prevents a
temporary hold after a failed independent login check from closing the
incident based only on the saved success date. An unreadable provider
configuration also fails closed; no credential value is read.

The devbox profile also reads `dnsproxy1` and `dnsproxy2` over the verified
SSH aliases. Each host runs a bounded JSON probe from the DNS proxy repository;
it performs the former local host, container, firewall, DNS and sync-freshness
checks without sending Telegram messages or writing incident state. Devbox
tracks reboot and container restart counters, using stable
`dnsproxy:<host>:<check>` keys. Failed SSH and invalid JSON have separate
visibility alerts. Missing samples retain earlier DNS proxy incidents instead
of treating the missing results as recovery. DNS availability, sync freshness,
firewall protection and probe visibility are routed to HIGH. GP keeps its
independent public IPv4 and IPv6 DNS checks over UDP and TCP.

Timer-backed oneshot services keep their last completed result while a new
invocation is active. A running invocation is not recovery evidence; only a
completed healthy result can advance the two-cycle recovery confirmation.
Filesystem discovery on devbox isolates inaccessible ephemeral mounts so one
short-lived BuildKit mount cannot abort the remaining checks.

Before a rollout, copy the profile, unit, environment file, installed code and
state into a private timestamped rollback directory. Run a shadow cycle with
both `--no-notify` and a private `--state-file`; `--no-notify` alone is not
read-only. Deploy devbox first and GP only after two successful natural cycles.
For the DNS proxy migration, install and verify the host probes before enabling
their devbox profile entries. Observe two natural devbox cycles with private
state before retiring either host's local timer. Retain each host's local
monitor configuration and state in a private rollback snapshot.

## Notification contract

Messages are routed to `sysmon-high`, `sysmon-medium` or `sysmon-low`. The
channel carries the priority; message text uses only `[NEW]`, `[REMINDER]` and
`[RESOLVED]`. Every event occupies one line:

```text
Service name : problem : devbox|gp
```

Resource alerts include the current measurement and the warning or critical
threshold that was crossed. Unit-health alerts include normalized systemd
active/failure states. Only allowlisted numeric values and state names reach the
message; raw command output and probe payloads remain excluded.

Tracked configuration contains only the environment variable names. Bot token
and chat IDs remain in the protected runtime environment files.

Pending delivery is coalesced to the current lifecycle state of each check, so
an undelivered transition cannot later produce contradictory `NEW` and
`RESOLVED` lines. Severity escalation and a stable notification-code change
produce an immediate `REMINDER`. Messages are split at the configured item
limit and Telegram's 4096-character limit; successful chunks are not retried
when another chunk fails. The GP profile controls failure/recovery confirmation,
the MEDIUM reminder interval, recovery suppression and chunk size. LOW remains
a daily digest after 08:00 Europe/Warsaw. Password Reset retains separate
technical checks for each account and the orchestrator, but their notification
lifecycle is aggregated into one `Password Reset` LOW entry. Partial account
recovery does not emit `RESOLVED`; the group resolves only when all member
checks are healthy.
