# System Monitor — Task State

Updated: 2026-10-07 09:59 CEST (Europe/Warsaw)

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
  separate incident key. An active scheduler cycle is `IN_PROGRESS` through
  300 seconds, `CYCLE_SLOW` warning through 1860 seconds and then critical
  `CYCLE_OVERDUE`. An idle PROD scheduler warns at 600 seconds and becomes
  critical at 900 seconds, matching the five-minute scheduler cadence.
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
- Active incidents now retain the last notified severity and stable message
  code across an unconfirmed healthy observation. A single neutral/OK sample
  therefore cannot make the next identical unhealthy sample look like an
  escalation and emit an immediate false `REMINDER`; genuine severity or code
  escalation still does. Existing active state v3 is initialized from its last
  conclusive severity, or from the current unhealthy sample after one pending
  recovery observation, without notification replay.
- The notification-correctness candidate is deployed on devbox and GP.
  Pending delivery is coalesced to one current event per check, severity or
  stable message-code escalation emits an immediate `REMINDER`, and Telegram
  payloads are split by both item count and 4096-character length with retry
  isolated to failed chunks. The shared engine honors the profile confirmation,
  MEDIUM reminder and recovery-suppression policy.
- A running timer-backed oneshot is neutral rather than recovery evidence. GP
  omits the inconclusive result and devbox retains its last conclusive outcome;
  only a completed healthy result can advance recovery. Devbox filesystem
  discovery also isolates inaccessible ephemeral mounts instead of aborting the
  entire monitor cycle.
- The neutral-running rule is limited to `Type=oneshot`. An active long-running
  service such as `minecraft-bedrock.service` (`Type=simple`) remains healthy
  even when its timer history contains an older interrupted invocation.
- Password Reset retains separate technical account/orchestrator checks, while
  the repository candidate aggregates their LOW notification lifecycle under
  one `Password Reset` group. Partial member recovery stays silent and the
  group resolves only after every member is healthy. Online Compiler
  environment/scheduler signals retain distinct user-facing names and technical
  incident keys. The Password Reset grouping candidate is not yet deployed.
- Backup, verification, retention, restore-test and GP mail-archive jobs have
  explicit LOW display names, including `Backup Devbox`, `Backup GP` and
  `Archiwizacja poczty GP`.
- GP-local scheduler checks run in the root system runtime, and the three-cycle
  GP dead-man runs in the devbox user runtime. Production state v3 was preserved
  on both hosts, installed package/configuration hashes match the repository,
  and both timers were active at the final readback.
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
- Notification-correctness rollout snapshots are at
  `/home/miket/.local/state/system-monitor-rollbacks/20261007T081358+0200/devbox-notification-correctness`
  and root-only
  `/root/system-monitor-rollbacks/20261007T081358+0200/gp-notification-correctness`.

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
- PASS: scheduler/dead-man additions bring the current total to 195 tests and
  38 subtests. Coverage includes local scheduler state, separate environment/
  health/freshness/idle signals, migration without replay, two-cycle recovery,
  six-hour reminders and three-cycle dead-man behavior.
- Retained final validation evidence: the first all-suite rerun inside the
  restricted sandbox had 15 test-harness failures because the repository was
  mounted read-only and legacy tests could not remove their temporary state
  file; 178 tests still passed. The identical host-context rerun passed all
  193 tests and 38 subtests. The final host-context suite after the UTC parser
  correction passed all 194 tests and 38 subtests. The synchronized-cycle
  regression brought the final suite to 195 tests and 38 subtests, all passing.
- The first devbox shadow during deployment reported a false dead-man CRITICAL
  because systemd's textual `UTC` timestamp was parsed as naive local time. The
  parser now attaches UTC explicitly for `UTC`/`GMT`. Natural cycles then exposed
  a second race: both five-minute timers start together, and systemd clears the
  previous exit timestamp while GP is `activating`. A fresh active/activating
  start is now accepted as dead-man evidence; both cases have regression tests.
- The synchronized-cycle race produced one real false `[NEW]` dead-man event at
  15:15 CEST before it was diagnosed. No incident was synthesized and production
  state was not cleared. After deployment, natural 15:40 and 15:45 cycles were
  OK and closed it with the normal `[RESOLVED]` path; the check is now severity
  OK, `alert_active=false`, with no pending notification.
- At the October 5 cutover checkpoint, natural GP monitor cycles observed the
  migrated schedulers and the then-existing Next Dev incident recovered after
  healthy confirmation through its preserved key, without `[NEW]` replay. A
  later independent Next Dev FAILED/HOLD incident remains active and is not a
  monitor regression.
- Scheduler cadence monitoring validation on 2026-10-06 passed 44 shared and
  158 legacy tests, JSON parsing, bytecode compilation and diff checks. The
  sandbox legacy run retained 15 EROFS harness errors; the identical
  host-context run passed 158/158.
- GP deployment backup is root-only at
  `/root/system-monitor-rollbacks/20261006T0725-scheduler-freshness`. Installed
  adapter, notification allowlist, profile and priorities hashes match the
  repository candidate. A private state-copy `--no-notify` run recognized the
  running PROD cycle as `IN_PROGRESS`, kept the real Next Dev FAILED/HOLD
  signals critical and did not touch production state.
- The natural 07:26 monitor cycle passed with PROD healthy. During the
  controlled scheduler deployment the intentionally stopped PROD timer was
  observed at 07:30 and generated one MEDIUM scheduler alert. State was not
  cleared; concurrent 07:35 and 07:40 monitor cycles correctly classified the
  running new PROD cycles as `IN_PROGRESS`. The second healthy observation
  closed the controlled alert through the standard recovery path and emitted
  one `[RESOLVED]`. PROD freshness, environment and scheduler keys are now OK
  with no active incident or pending notification.
- The exact post-deployment window `[2026-10-06 07:35, 2026-10-07 07:35)`
  CEST passed. GP Monitor completed 288/288 natural cycles; PROD freshness was
  287 `IN_PROGRESS`, one truthful `CYCLE_SLOW`, zero `STALE` and zero
  `CYCLE_OVERDUE`. Public availability and the legacy scheduler signal were
  288/288 healthy. Environment observations were 191 CLOSED, 94 OPEN, two
  STARTING and one STOPPING, all correctly OK.
- PROD produced three notification events in that window: the 07:40 resolution
  of the controlled pre-window deployment incident, then `CYCLE_SLOW`
  `[NEW]` at 14:10 and `[RESOLVED]` at 14:20 for the real 729.157-second
  `PASS_OPEN` cycle. The 562.794-second `PASS_CLOSED` completed before the
  next warning sample. There were no false-positive PROD alerts, availability
  failures or active/pending PROD incidents at the end of the window.
- The 24-hour scheduler observation identified a separate false recovery for
  `dysk-sieciowy-mirror`: the monitor sampled an active invocation that later
  exited 3. The repository candidate now treats such an active oneshot as
  inconclusive, so it cannot advance recovery before a successful completion.
- A read-only notification audit for `[2026-10-06 07:43:34,
  2026-10-07 07:30:30]` CEST confirmed 24 successful Telegram sends and zero
  delivery failures. It also found two devbox cycles aborted by inaccessible
  BuildKit mounts, contradictory LOW lifecycle events and the false mirror
  recovery above. The source candidate fixes these monitor defects; underlying
  Meta API watcher, Next Dev, network-disk sync/mirror and AnyDesk incidents
  remain owned by their respective components.
- PASS: the integrated source candidate passed `pytest`: 217 tests and 48
  subtests. Coverage includes inaccessible mounts, active timer services after
  failure, pending-event coalescence, severity/code escalation without dynamic
  context spam, profile policy, Telegram item/length chunking, and partial
  HIGH/LOW retry. JSON parsing, `py_compile`, `git diff --check` and a targeted
  credential scan also passed.
- BLOCKED (test invocation only): `python3 -m unittest discover -s tests -t .`
  rejected the non-package `tests` directory. Direct execution of all four test
  modules passed 217/217, and the canonical full `pytest` run passed. This was
  not an application failure.
- The deployment preflight shadow exposed and retained one candidate regression:
  the new active-service rule initially applied to `minecraft-bedrock.service`
  as well as oneshots and would have queued a false alert from its older cached
  interruption. The rule is now constrained by systemd `Type`; the corrected
  candidate passes 219 tests and 48 subtests, and the repeat devbox shadow sees
  the active `Type=simple` service as healthy.
- PASS: fresh private-state shadow runs used both `--no-notify` and private
  state copies on devbox and GP. Devbox returned 88 results. GP returned 318
  results and truthfully detected the natural 08:10 mirror invocation that
  completed with exit 3 after the installed monitor had sampled it as running.
  Production state was not modified by either shadow run.
- PASS: the 2026-10-07 rollout deployed commit `9a547e1` devbox-first without
  clearing or replacing production state. Devbox natural 08:15 and 08:20 cycles
  and GP natural 08:25 and 08:30 cycles completed with exit 0. Both timers remain
  enabled/active; source/runtime package, wrapper, profile and active priority
  registry hashes match. State remains v3 mode `0600`; Telegram environment
  permissions remain `0600` on devbox and `root:www-data 0640` on GP.
- PASS: devbox dead-man remained OK at 08:20, 08:25 and 08:30 with no active
  incident or pending event. GP had no pending notification after two cycles.
  The earlier natural mirror failure was followed by a successful natural run
  before rollout, so it was healthy at final readback without a synthetic event.
- PASS: installed GP unit verification was clean. Devbox unit verification
  exited 0 with one unrelated existing `spice-vdagent.service` warning about an
  ignored `StandardError` key in `[Install]`.
- BLOCKED (observation helper only): the first shell counter for devbox natural
  cycles treated an empty `rg -c` result as an integer. It was stopped and the
  corrected direct journal counter confirmed both cycles; monitor execution and
  state were unaffected.
- PASS: the owner-requested corrected LOW daily report was sent at 09:38 CEST
  after a read-only preview confirmed six technical keys, six distinct visible
  rows and no contradictory lifecycle statuses. It used the deployed
  coalescence and the specific Password Reset names (`canva1`, `canva2`,
  `canva3`, `orchestrator`). Telegram returned success for one six-item digest;
  production state records `last_low_digest_date=2026-10-07`, zero pending LOW
  events and mode `0600`. The devbox timer remained active. The pre-send state
  snapshot is at
  `/home/miket/.local/state/system-monitor-rollbacks/20261007T093839+0200/manual-low-digest`.
- PASS: the Password Reset grouping candidate passes 220 tests and 48 subtests.
  Coverage verifies one group `NEW`, one daily `REMINDER`, silence during partial
  member recovery and one final `RESOLVED`. A private-state devbox shadow
  initialized the currently active group without replaying `NEW`; a forced
  in-memory preview contained exactly one `Password Reset` reminder. The shadow
  used `--no-notify` and did not modify production state.
- PASS: the notification-baseline fix on top of Password Reset grouping passes
  223 tests and 48 subtests. Regression coverage proves that `CRITICAL ->` one
  unconfirmed `OK -> CRITICAL` remains silent, active v3 migration does not
  replay a reminder, and a real `WARNING ->` one unconfirmed `OK -> CRITICAL`
  escalation still sends an immediate reminder. This combined source candidate
  is not deployed.

## Constraints

- Do not clear or replace production state. `--no-notify` still writes the
  selected state file, so validation must always use a private copy/path.
- Keep devbox as a user unit and GP as a root system unit.
- Preserve legacy `TELEGRAM_CHAT_ID` for unrelated consumers.
- Do not manufacture incidents or expose URLs, payloads, tokens or chat IDs in
  notifications and logs.
- Source commit, deployment and production-state migration remain separate
  authorization gates. The current candidate must not be copied to either host
  without a fresh runtime/state readback and explicit deployment authorization.

## START HERE

1. Read this file and check all three repository worktrees before editing.
   Treat commit `9a547e1` as the deployed baseline and the Password Reset group
   lifecycle plus notification-baseline changes as an undeployed candidate.
2. Preserve current state v3 without clearing or replacement. The corrected
   owner-requested LOW digest cleared all pending LOW events on 2026-10-07; do
   not replay it or reset `last_low_digest_date`.
3. Keep GP-local scheduler checks and the devbox three-cycle dead-man probe.
   Any shadow validation must use both `--no-notify` and a private state path;
   do not manufacture a failure to test notification delivery.
4. The first 24-hour five-minute-cadence PROD observation is complete and
   passed. Keep the current 300/1860-second running-cycle and 600/900-second
   idle thresholds; investigate repeated `CYCLE_SLOW` observations as real
   latency without classifying them as scheduler failure.
5. Preserve installed entrypoints, state paths, environment permissions and
   the three-channel message contract. Deploy devbox first, observe at least two
   natural cycles, then GP; verify that a running failing mirror cannot emit
   recovery. Investigate application incidents in their owning repository and
   never clear monitor state to force recovery.
