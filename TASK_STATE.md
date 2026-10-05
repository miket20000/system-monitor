# System Monitor — Task State

Updated: 2026-10-05 15:22 CEST (Europe/Warsaw)

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
- GP-local scheduler checks are deployed in the root system runtime, and the
  three-cycle GP dead-man is deployed in the devbox user runtime. Production
  state v3 was preserved on both hosts; source/runtime/profile/priority hashes
  match and both timers are active.
- Private-state shadow runs used both `--no-notify` and a private state copy.
  GP returned all configured results with all seven Online Compiler checks OK;
  devbox returned `remote-dead-man:gp-monitor=OK`.
- Existing incidents migrated without `[NEW]` replay. Devbox delivered four
  preserved LOW incidents as one reminder digest. GP delivered the preserved
  `dysk-sieciowy-sync` incident as a LOW reminder.
- Rollback snapshots are at
  `/home/miket/.local/state/system-monitor-rollbacks/20261004T215708+0200/devbox`
  and root-only `/root/system-monitor-rollbacks/20261004T215708+0200/gp` for
  the original cutover. The notification-fix rollbacks are at
  `/home/miket/.local/state/system-monitor-rollbacks/20261005T070919+0200/devbox`
  and root-only `/root/system-monitor-rollbacks/20261005T070919+0200/gp`.
- Scheduler-cutover rollbacks are at
  `/home/miket/.local/state/system-monitor-rollbacks/20261005T1450-gp-deadman`
  on devbox and root-only
  `/root/system-monitor-rollbacks/20261005T1425-gp-scheduler-cutover` on GP.

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
- PASS: scheduler/dead-man additions bring the current total to 194 tests and
  38 subtests. Coverage includes local scheduler state, separate environment/
  health/freshness/idle signals, migration without replay, two-cycle recovery,
  six-hour reminders and three-cycle dead-man behavior.
- Retained final validation evidence: the first all-suite rerun inside the
  restricted sandbox had 15 test-harness failures because the repository was
  mounted read-only and legacy tests could not remove their temporary state
  file; 178 tests still passed. The identical host-context rerun passed all
  193 tests and 38 subtests. The final host-context suite after the UTC parser
  correction passed all 194 tests and 38 subtests.
- The first devbox shadow during deployment reported a false dead-man CRITICAL
  because systemd's textual `UTC` timestamp was parsed as naive local time. The
  parser now attaches UTC explicitly for `UTC`/`GMT`; the regression test,
  repeated shadow and natural cycles are healthy.
- Natural GP monitor cycles observed the migrated schedulers. The existing
  Next Dev incident recovered after healthy confirmation through its preserved
  key, without `[NEW]` replay. All Online Compiler checks have
  `alert_active=false`, severity OK and no pending notifications.

## Constraints

- Do not clear or replace production state. `--no-notify` still writes the
  selected state file, so validation must always use a private copy/path.
- Keep devbox as a user unit and GP as a root system unit.
- Preserve legacy `TELEGRAM_CHAT_ID` for unrelated consumers.
- Do not manufacture incidents or expose URLs, payloads, tokens or chat IDs in
  notifications and logs.

## START HERE

1. Read this file and check all three repository worktrees before editing.
2. Verify the GP system timer and devbox user timer remain active and their most
   recent services completed successfully. Check the seven Online Compiler keys
   and `remote-dead-man:gp-monitor` without clearing or replacing state.
3. Keep GP-local scheduler checks and the devbox three-cycle dead-man probe.
   Any shadow validation must use both `--no-notify` and a private state path;
   do not manufacture a failure to test notification delivery.
4. Preserve installed entrypoints, state paths, environment permissions and
   the three-channel message contract. Investigate application incidents in
   their owning repository and never clear monitor state to force recovery.
