# System Monitor — Task State

## Goal

Make `system-monitor` the sole owner of Online Compiler alerts, reminders and
recovery while the schedulers move from devbox to GP. Preserve the two monitor
runtimes, existing incident identities and independent availability and
scheduler-health signals.

## Current implementation

- The repository contains the imported GP and devbox probe adapters, the
  shared notification engine, common priority routing and source profiles.
- GP profile code now reads the local PROD and Next Dev system units and state
  files. It reports environment status, scheduler health/reason and cycle
  freshness independently; ACTIVE past the configured idle limit has a
  separate incident key.
- Existing Online Compiler incident keys remain unchanged, so deployment over
  current state will not replay `[NEW]`. New keys are limited to stale state
  and idle-overdue conditions. Allowlisted message codes and context fields
  distinguish stale state, fresh BLOCKED/HOLD with last environment state,
  read degradation and idle-overdue without exposing raw payloads.
- Scheduler health remains MEDIUM with the six-hour reminder. Public
  availability remains an independent HIGH signal.
- The devbox profile has a fixed, read-only GP dead-man probe over SSH. Its
  network classification requires three consecutive failed cycles. This alert
  is emitted by `system-monitor`, never by Online Compiler.
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
- PASS: scheduler/dead-man additions bring the current total to 193 tests:
  23 notification tests, 12 scheduler/dead-man tests, 60 devbox legacy tests
  and 98 GP legacy tests. `py_compile`, all three JSON files, diff checks and
  the secret scan also pass.
- Retained final validation evidence: the first all-suite rerun inside the
  restricted sandbox had 15 test-harness failures because the repository was
  mounted read-only and legacy tests could not remove their temporary state
  file; 178 tests still passed. The identical host-context rerun passed all
  193 tests and 38 subtests.
- The new monitor code has not been deployed. The Online Compiler GP cutover is
  blocked before its first cycle because the scheduler identities lack
  authority to create the required WIF providers. GP scheduler timers remain
  disabled/inactive and devbox remains the only scheduler writer.

## Constraints

- Do not clear or replace production state. `--no-notify` still writes the
  selected state file, so validation must always use a private copy/path.
- Keep devbox as a user unit and GP as a root system unit.
- Preserve legacy `TELEGRAM_CHAT_ID` for unrelated consumers.
- Do not manufacture incidents or expose URLs, payloads, tokens or chat IDs in
  notifications and logs.

## START HERE

1. Read this file and check all three repository worktrees before editing.
2. Wait for Online Compiler GP WIF/auth validation and the scheduler
   single-writer cutover gate. Do not deploy local GP scheduler checks against
   an incomplete runtime or replace the production monitor state.
3. Before deployment, create private runtime/config/state backups, verify the
   existing incident keys in state, and run GP shadow validation with
   `--no-notify --state-file` on a private copy/path.
4. Deploy GP-local checks first while preserving the production state, then
   deploy the devbox three-cycle dead-man probe. Verify source/runtime hashes,
   unit configuration and two natural monitor cycles without generating a
   synthetic failure or notification.
5. Preserve installed entrypoints, state paths, environment permissions and
   the three-channel message contract. Investigate application incidents in
   their owning repository and never clear monitor state to force recovery.
