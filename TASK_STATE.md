# System Monitor — Task State

Updated: 2026-10-10 21:22 CEST (Europe/Warsaw)

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
  separate incident key. The source candidate keeps the five-minute warning
  for steady `OPEN`/`CLOSED` cycles, uses a 15-minute warning for expected
  `STARTING`/`STOPPING` convergence and retains critical `CYCLE_OVERDUE` after
  1860 seconds. An idle PROD scheduler warns at 600 seconds and becomes
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
  recovery observation, without notification replay. An inherited active-state
  baseline of `ok` is also treated as invalid migration input and initialized
  from the conclusive incident rather than emitted as an escalation.
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
- Minecraft Bedrock is intentionally retired on devbox. Its start and stop
  timers are disabled, the server is stopped, and the deployed devbox profile
  contains no Minecraft checks. The six exact check records, two timer-service
  cache records and one pending LOW recovery event were removed without
  changing unrelated monitor state or sending a synthetic notification.
- AnyDesk is intentionally retired on devbox: `anydesk.service` autostart is
  disabled, the unit is absent from the deployed devbox profile and its exact
  obsolete state entry was removed without changing other incidents. No
  synthetic recovery notification was generated.
- GP Monitor runs at minutes `01/06/11/...`, one minute after the network-disk
  mirror's `00/05/10/...` cadence. This preserves the five-minute interval and
  samples the mirror's completed result instead of its transient running state.
- The deployed monitor adds stable `ONLINE_COMPILER_STARTING_SLOW` and
  `ONLINE_COMPILER_STOPPING_SLOW` message codes. A transition warning remains
  durable, so its first observation past 900 seconds sends `[NEW]` immediately;
  recovery still requires two healthy observations. Steady `OPEN`/`CLOSED`
  cycles retain the 300-second warning and every cycle retains the 1860-second
  overdue boundary.
- Password Reset retains separate technical account/orchestrator checks, while
  deployed commit `04957e4` aggregates their LOW notification lifecycle under
  one `Password Reset` group. Partial member recovery stays silent and the
  group resolves only after every member is healthy. Online Compiler
  environment/scheduler signals retain distinct user-facing names and technical
  incident keys.
- Backup, verification, retention, restore-test and GP mail-archive jobs have
  explicit LOW display names, including `Backup Devbox`, `Backup GP` and
  `Archiwizacja poczty GP`.
- GP-local scheduler checks run in the root system runtime, and the three-cycle
  GP dead-man runs in the devbox user runtime. Production state v3 was preserved
  on both hosts. The installed phase-aware adapter, notification allowlist, GP
  profile and priority registry match commit `b06652b`; later repository commits
  did not change those runtime bytes. Both timers were enabled/active at final
  readback.
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
- Password Reset grouping rollout snapshots are at
  `/home/miket/.local/state/system-monitor-rollbacks/20261007T095208+0200/password-reset-group`
  and root-only
  `/root/system-monitor-rollbacks/20261007T095208+0200/password-reset-group`.
- Notification-baseline rollout snapshots are at
  `/home/miket/.local/state/system-monitor-rollbacks/20261007T102648+0200/devbox-notification-baseline`
  and root-only
  `/root/system-monitor-rollbacks/20261007T103606+0200/gp-notification-baseline`.
  Repair snapshots are at
  `/home/miket/.local/state/system-monitor-rollbacks/20261007T104312+0200/devbox-stale-baseline-repair`
  and root-only
  `/root/system-monitor-rollbacks/20261007T105133+0200/gp-stale-baseline-repair`.
- Phase-aware scheduler-alert rollout snapshots are at
  `/home/miket/.local/state/system-monitor-rollbacks/20261010T2103+0200-transition-threshold-devbox`
  and root-only
  `/root/system-monitor-rollbacks/20261010T2113+0200-transition-threshold` on GP.

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
  later independent Next Dev FAILED/HOLD incident was a scheduler/runtime
  incident rather than a monitor regression and was recovered on 2026-10-07.
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
- PASS: owner-authorized Minecraft Bedrock retirement on 2026-10-08 stopped the
  service, removed its container and disabled both system timers. Source and
  deployed devbox profiles have the same SHA-256, a private-state `--no-notify`
  shadow returned zero Minecraft results, and production state contains no
  Minecraft check, timer-service or pending-notification entry. The natural
  08:55 monitor cycle exited 0 without a Minecraft result or notification.
  Rollback is at
  `/home/miket/.local/state/system-monitor-rollbacks/20261008T085034+0200-minecraft-bedrock-retirement`.
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
  224 tests and 48 subtests. Regression coverage proves that `CRITICAL ->` one
  unconfirmed `OK -> CRITICAL` remains silent, active v3 migration does not
  replay a reminder, and a real `WARNING ->` one unconfirmed `OK -> CRITICAL`
  escalation still sends an immediate reminder. It also covers a historical
  active state carrying the inconsistent baseline `notified_severity=ok`.
- PASS: commit `04957e4` was deployed devbox-first without clearing or replacing
  production state. Devbox natural 09:55 and 10:00 cycles and GP natural 10:05
  and 10:10 cycles completed with exit 0. The devbox group initialized as active
  under the single name `Password Reset`, with no replayed `NEW`, no pending
  group event and the existing `last_low_digest_date=2026-10-07`. State mode
  remained `0600`; GP has no configured notification groups and devbox dead-man
  remained OK.
- During GP staging, concurrent source HEAD advanced to `f7bd091`. GP was
  corrected to the exact `04957e4` notification engine before its first natural
  cycle; `f7bd091` remains undeployed on both hosts as required by its separate
  authorization gate. The 10:05 and 10:10 GP cycles emitted MEDIUM reminders
  for existing non-Password-Reset incidents; they are outside this grouping
  change and no synthetic incident was created.
- FAIL: the first GP natural cycle after deploying `f7bd091` at 10:40 migrated
  the active Next Dev freshness check correctly, but sent one false MEDIUM
  reminder for the separate active Next Dev scheduler-health check. Its
  preserved state contained an inherited `notified_severity=ok`; the original
  migration handled an absent field but trusted this inconsistent value. No
  state was cleared or replaced and no rollback was performed. The smallest
  source repair rejects `ok` as the baseline of an active incident and passed
  the complete 224-test suite.
- PASS: repair commit `231fd52` was deployed devbox-first. Private-state shadow
  runs used `--no-notify`; natural devbox cycles at 10:45 and 10:50 and GP
  cycles at 10:56 and 11:01 all exited 0. No cycle emitted a notification,
  pending remained empty, both active Next Dev checks retained their prior
  `last_notification`, and state/Telegram file modes remained unchanged. A
  final private run against the exact real pre-10:40 state migrated
  `notified_severity=ok` to the active CRITICAL/code pair with no pending event
  and without changing the 10:10 notification timestamp.
- BLOCKED (pre-install readback only): the first GP repair installation check
  found the natural 10:50 monitor cycle still `activating` and stopped before
  replacing a file. Its timer-restoration trap succeeded; after that cycle
  completed at 10:51, the exact same candidate was installed once. This was not
  a monitor failure or an ambiguous mutation.
- PASS: AnyDesk retirement passed 64 devbox legacy tests and JSON/diff checks.
  The deployed profile matches the repository file; the unit is
  `disabled/inactive`, the retired key is absent from production state, pending
  notifications remain empty and `last_low_digest_date=2026-10-07` is
  preserved. The natural 10:45 devbox cycle exited 0 without recreating it.
- PASS: the staggered GP timer passed `systemd-analyze verify` and calendar
  parsing. The deployed unit is enabled/active with
  `OnCalendar=*-*-* *:1/5:00 Europe/Warsaw`. Mirror failed naturally at
  10:45:04 with exit 3; GP Monitor completed at 10:46:18 with exit 0 and
  recorded the completed mirror result as CRITICAL. Production state was not
  cleared or replaced.
- PASS: the 21-path network-disk divergence was reconciled GP to Giganci after
  immutable backups of both sides. Eight content differences and thirteen
  metadata-only differences now match; post-copy SHA-256 verification, mirror
  sync and checksum audit passed with zero divergence. The heartbeat was
  refreshed at 14:57 CEST. Natural mirror cycles at 15:00 and 15:05 and GP
  Monitor cycles at 15:01 and 15:06 exited 0; both mirror and cron-freshness
  incidents are resolved with no pending notifications. Backups remain at
  `/var/backups/dysk-sieciowy-reconcile-20261007T125641Z-gp` and
  `/var/backups/dysk-sieciowy-reconcile-20261007T125641Z-giganci`.
- PASS: the owner-authorized 2026-10-08 mirror policy now resolves existing-file
  conflicts automatically by newer `mtime` (GP wins exact ties) and archives
  the losing version below `!_ARCHIWUM_!/mirror-conflicts`; it does not delete
  user files. Three older Giganci versions were archived and both directions
  reached zero mismatches. The natural 09:10 mirror cycle made zero changes,
  the natural 09:11 GP Monitor cycle reported the service `OK`, and the exact
  alert plus pending queue are empty without manual state editing.
- PASS: the authorized controlled Social Media invitation run processed all 33
  in-scope Facebook pages and excluded `Main-Instagram`. It sent 10 confirmed
  invitations across seven pages; 26 pages had no current pool, with zero
  failed, blocked or ambiguous sends. The service exited 0 and its result file
  is `0600` with status `PASS`. Natural devbox monitor cycles at 16:15 and 16:20
  confirmed recovery; the check is now OK/inactive and its LOW `RESOLVED`
  event remains in the normal pending queue for policy-driven delivery.
- BLOCKED (test invocation only): plain `pytest -q` could not import the local
  package. The corrected `PYTHONPATH=src pytest -q` passed 223 tests and 48
  subtests before the concurrent baseline repair added its final regression;
  that repair separately passed 224 tests and 48 subtests.
- PASS: the phase-aware scheduler candidate passed 227 tests and 50 subtests in
  host context. Its targeted final rerun passed 87 tests and 10 subtests; JSON,
  bytecode and diff checks passed. The first sandbox full run retained 17 known
  EROFS harness failures and also exposed two stale profile expectations; those
  expectations were corrected before the clean host-context run.
- PASS: the owner-authorized 2026-10-10 production rollout deployed the exact
  phase-aware runtime bytes from `b06652b` devbox-first without clearing or
  replacing either production state file. The fresh sandbox rerun retained the
  17 known EROFS harness failures with 107 tests and 48 subtests passing; the
  identical host-context scope passed 124 tests and 48 subtests. JSON, bytecode,
  diff and installed GP unit verification passed.
- PASS: private-state `--no-notify` shadows returned 81/81 OK on devbox and
  318/318 OK on GP. Natural devbox cycles at 21:05 and 21:10 and GP cycles at
  21:16 and 21:21 CEST exited 0. Devbox dead-man remained OK; the GP Online
  Compiler checks, scheduler health and public availability were healthy.
  Installed adapter, notification allowlist, GP profile and priority registry
  hashes match the candidate, and both timers remain enabled/active.
- The old GP runtime emitted one final truthful-to-its-old-policy `CYCLE_SLOW`
  `[NEW]` at 21:06 while the already-running production `STOPPING` transition
  exceeded 300 seconds. That transition completed `PASS_CLOSED` before the
  deployment; the new runtime observed the second healthy sample and sent its
  normal `[RESOLVED]` at 21:16. It opened no new incident. The unrelated pending
  `vm-manager-retention` LOW resolution remains preserved and unsent.
- PASS: after the explicitly authorized Next Dev recovery, natural GP Monitor
  cycles observed fresh `CLOSED/HEALTHY/NONE` scheduler state, `HIBERNATED`,
  zero HOLD and healthy idle status. The environment incident resolved at
  17:11 CEST after its second healthy observation; scheduler freshness and
  scheduler-health resolved together at 17:16 after their second observations.
  The monitor emitted only the expected `[RESOLVED]` events and has all three
  Next Dev keys OK/inactive with no pending Next Dev notification, while PROD
  remained healthy. One unrelated pending LOW resolution for network-disk
  synchronization remains untouched. Production monitor state was never
  cleared or replaced.

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
   Treat the exact phase-aware runtime files from `b06652b` as deployed on both
   monitor runtimes and retain the historical 10:40 false-reminder failure from
   `f7bd091`; do not replay or delete it.
2. Preserve current state v3 without clearing or replacement. The corrected
   owner-requested LOW digest cleared all pending LOW events on 2026-10-07; do
   not replay it or reset `last_low_digest_date`.
3. Keep GP-local scheduler checks and the devbox three-cycle dead-man probe.
   Any shadow validation must use both `--no-notify` and a private state path;
   do not manufacture a failure to test notification delivery.
   The 2026-10-07 Next Dev environment, freshness and scheduler incidents are
   recovered and inactive; do not replay or delete their history.
4. The first 24-hour five-minute-cadence PROD observation and the phase-aware
   rollout are complete and passed. The deployed runtime keeps 300 seconds for
   steady cycles, uses 900 seconds for `STARTING`/`STOPPING`, retains 1860
   seconds for overdue and keeps the 600/900-second idle thresholds. Verify the
   next natural opening/closing transition without synthesizing an incident;
   never classify `CYCLE_SLOW` as scheduler failure.
5. Preserve installed entrypoints, state paths, environment permissions and
   the three-channel message contract. Keep GP Monitor one minute after the
   mirror cadence so completed mirror failures remain observable. Retain the
   two 2026-10-07 network-disk reconciliation backups until an explicit
   retention decision, and retain the 2026-10-08 conflict archives and mirror
   rollback backup until the owner decides their retention. Investigate
   application incidents in their owning repository and never clear monitor
   state to force recovery.
6. Keep Minecraft Bedrock retired: do not restore its two devbox profile
   entries, re-enable its timers or replay the removed LOW recovery event
   without explicit owner authorization. Runtime data under
   `/opt/minecraft-bedrock` remains preserved.
