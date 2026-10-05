# System Monitor — Task State

## Goal

Consolidate the devbox and GP monitors into this repository while preserving
two independent runtime instances, their local probes, permissions, state and
failure domains.

## Current implementation

- The repository contains the imported GP and devbox probe adapters, the
  shared notification engine, common priority routing and source profiles.
- Telegram routing uses the private `sysmon-high`, `sysmon-medium` and
  `sysmon-low` groups. Each contains the owner and `mt_monitoring_agent`; the
  exact configuration message was verified in all three groups. Chat IDs and
  the bot token remain only in protected runtime environment files.
- Incident lifecycle is keyed by the technical check and continuous unhealthy
  period. A changing durable evidence ID updates the stored evidence but does
  not reopen `[NEW]`; another `[NEW]` is possible only after two healthy cycles
  confirm recovery. Pending events are refreshed from the current registry.
- Backup, verification, retention, restore-test and GP mail-archive jobs have
  explicit LOW display names, including `Backup Devbox`, `Backup GP` and
  `Archiwizacja poczty GP`.
- The notification fix was deployed devbox-first. Natural cycles at 07:15 and
  07:20 CEST completed with exit code 0; no deployment-triggered notification
  was sent, the user timer is active and state v3 remains mode 0600.
- GP natural cycles at 07:25 and 07:30 CEST completed with exit code 0. The
  continuously failing Meta API status watcher retained one active MEDIUM
  incident with no pending event or new delivery; the system timer is active
  and state v3 remains mode 0600.
- Existing incidents migrated without `[NEW]` replay. Devbox delivered four
  preserved LOW incidents as one reminder digest. GP delivered the preserved
  `dysk-sieciowy-sync` incident as a LOW reminder.
- Rollback snapshots are at
  `/home/miket/.local/state/system-monitor-rollbacks/20261004T215708+0200/devbox`
  and root-only `/root/system-monitor-rollbacks/20261004T215708+0200/gp` for
  the original cutover. The notification-fix rollbacks are at
  `/home/miket/.local/state/system-monitor-rollbacks/20261005T070919+0200/devbox`
  and root-only `/root/system-monitor-rollbacks/20261005T070919+0200/gp`.

## Validation

- PASS: 181 shared and imported unit tests: 23 common, 60 devbox legacy and 98
  GP legacy. Coverage includes changing durable evidence IDs, confirmed
  recovery and reopen, six-hour MEDIUM reminders, pending-event refresh,
  distinct LOW display names, transient/sample/durable confirmation, LOW
  digest, DST, state migration and partial Telegram failure retry.
- PASS: host-context private-state shadow runs on devbox and GP; GP returned
  all 313 configured results without queuing another Meta API event.
- PASS: JSON validation, `py_compile`, per-host `systemd-analyze verify`, diff
  checks, secret scan and source-runtime hashes. The devbox systemd check also
  reported an unrelated legacy `/var/run` warning from the installed AnyDesk
  unit; both monitor-unit checks exited 0.
- BLOCKED (test invocation only): the first targeted unit command omitted
  `PYTHONPATH=src` and could not import the local package. The corrected
  command and all complete suites passed; this was not an application failure.
- The first imported-suite run had two expected test failures because legacy
  assertions excluded Password Reset; the tests were updated to the approved
  new coverage and the complete rerun passed. This result is retained rather
  than rewritten as an initial pass.

## Constraints

- Do not clear or replace production state. `--no-notify` still writes the
  selected state file, so validation must always use a private copy/path.
- Keep devbox as a user unit and GP as a root system unit.
- Preserve legacy `TELEGRAM_CHAT_ID` for unrelated consumers.
- Do not manufacture incidents or expose URLs, payloads, tokens or chat IDs in
  notifications and logs.

## START HERE

1. Read this file and check all three repository worktrees before editing.
2. For a monitor change, run the shared and both imported regression suites,
   then a private `--no-notify --state-file` shadow cycle for the target host.
3. Preserve installed entrypoints, state paths, environment permissions and
   the three-channel message contract during future deployments.
4. Investigate application incidents in their owning repositories; do not
   clear monitor state or manufacture recovery observations.
