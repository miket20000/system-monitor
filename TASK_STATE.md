# System Monitor — Task State

Updated: 2026-11-09 18:36 CET.

## Goal and ownership

`system-monitor` is the canonical source for independent `devbox-monitor`
(user `miket`) and `gp-monitor` (root). They share notification code and
priority policy, but use separate profiles and incident states. Devbox owns
local probes and the GP dead-man check; GP owns its workloads and public checks.

This stage moved automatic Telegram alerts from Password Reset on devbox and
Schedule Publisher, OpenAI Key Monitor, and Designmodo daily sync on GP to the
central monitors. The source Telegram chat `mt_monitoring_agent` held direct
Password Reset success messages and failures from these GP jobs. DNS IP sync
messages in the sampled chat predated its earlier central-monitor migration.

## Current implementation

- GP profile now covers `teachers-discord-tools-publisher.timer` and its
  completed service result, warning after 30 hours and critical after 36.
  It reads only systemd state; the separate publisher is owned by
  `vps-gp/scripts/teachers-discord-tools-publisher/`.
- Password Reset's user systemd unit runs `--no-telegram`; the CLI leaves
  explicit `--test-notification` available. Devbox monitor already covers
  timer and service result with its normal priority routing.
- GP Schedule Publisher preserves `ok:false` and `lastFailedAttemptAt` in
  its private status file on failed publication, and the marker survives a
  subsequent success. The GP adapter reads those fields, including a short
  manual failure between monitor cycles. Worker and API units no longer load
  Telegram credentials.
- GP OpenAI Key Monitor runs `--no-notify`. The GP adapter reads only the
  allowlisted status and last-seen timestamp, detects missing/stale samples,
  and routes business failures through MEDIUM. Provider response content and
  credentials are not copied into alerts. The service may exit 0 for an
  unhealthy key, so its result check alone is insufficient.
- GP Designmodo daily sync runs `--no-telegram`; its failure exit and timer
  are already covered by GP monitor with LOW routing. Explicit test commands
  remain separate from automatic units.
- New GP profile keys are `openai_key_status_file` and
  `schedule_publisher_status_file`. Two explicit MEDIUM priority assignments
  are in `config/priorities.json`. Live GP priorities have unrelated drift;
  deployment merged only the two new assignments and profile keys.
- State v3 retains technical incident keys, two-cycle recovery, three-cycle
  network confirmation, pending-event coalescence, and LOW digest scheduling.
  `--no-notify` still writes state; shadow runs need a private `--state-file`.
  Do not reset active incidents or manufacture/replay notifications.

## Verification and deployment

- Source GP profile JSON is valid; `tests/legacy/test_gp_monitor.py` passed
  107 tests and 38 subtests in host context. First sandbox run had 17
  filesystem-denied `BLOCKED` cases and 90 passes; it did not diagnose a monitor
  defect. The GP profile was merged by adding only the new timer entry to the
  active config, preserving unrelated live drift and incident state. Private
  snapshot: `/var/lib/gp-monitor/rollbacks/20261109-teacher-tools-before/`.
  A `--no-notify` shadow run with private state returned both new checks `ok`;
  first shadow attempt used the obsolete `/etc/gp-monitor/priorities.json` and
  failed before probing, then the effective priority path passed. The first
  natural cycle after the profile update completed 18:36 CET with `success/0`,
  both new checks `ok`, and zero pending notifications.
- Source suite: `PYTHONPATH=src pytest -q` passed 255 tests and 50 subtests;
  focused new status tests, JSON parsing, and `git diff --check` passed.
  GP adapter installed SHA-256:
  `2f33301f66e86baa7446f95fedf7970d9f61670dd05b029eebf5af0ba2a1d00c`.
- GP monitor private snapshot:
  `/var/lib/gp-monitor/rollbacks/20261108-telegram-routing-before/`.
  The installed adapter and merged live config passed the 09:46 natural cycle.
  The 09:51 natural cycle finished with `Result=success`, exit 0. Both new
  checks were `ok`, without active alerts or pending GP notifications.
- GP application source and units were installed after root-only snapshot
  `/var/backups/system-monitor/20261108-telegram-routing-before/`.
  Schedule Publisher API restarted active; local status and public current
  JSON returned HTTP 200. All four relevant GP timers were enabled. Natural
  Schedule Publisher and OpenAI cycles at 10:00 finished success/0; private
  business statuses were `ok`. The following GP monitor 10:01 cycle finished
  success/0, with their status and service-result checks `ok`, the Designmodo
  alarm still active, and zero pending GP events. Designmodo's next daily
  cycle is 2026-11-09 06:45 CET.
- Devbox Password Reset source commit: `31dd39f` in `scripts-devbox`.
  GP app source commit: `1bbbebd` in `vps-gp`. First natural Password Reset
  runs at 09:48:34 and 10:03:44 passed exit 0 with `--no-telegram`.
  Devbox pre-change private snapshot:
  `~/.local/state/password-reset/rollbacks/20261108-telegram-cutover-before/`.
  The 09:50 and 10:05 natural devbox monitor cycles passed exit 0. After the
  second Password Reset run, its timer and service result remained `ok`;
  per-account daily checks were `ok` at 09:50. Its one
  pending LOW event belongs to the unrelated `telegram-daily-reason.timer`.

## Active alarms and limits

- GP Designmodo daily sync failed on 8 Nov at 06:45 and its existing central
  result alarm remains active. This stage changed alert ownership and did not
  repair the import failure.
- Earlier devbox active alarms included Social Media weekly invitations,
  Restic check/retention, and Restic GP mail purge; earlier GP alarms included
  mail archive compression for `forkids` and `forkids-biuro`. Refresh
  incident state before acting; these observations may have changed.
- A prior 7 Nov full Restic data-check attempt failed because of an orphaned
  lock. After separately authorized unlock and retry, the full service passed
  and natural monitor cycles closed that alarm. Preserve first-attempt FAIL.
- Telegram inspection covered only 60 loaded messages from 13 Oct–8 Nov
  2026 plus an older bounded sample back to 2 Oct. Further historical
  families were disk usage, Social Media invitations, Restic GP mail purge,
  VPN, Minecraft Bedrock, devbox backup, Lesson Starters and Trustpilot; all
  are in current monitor profiles or are retired. Minecraft Bedrock and old
  Lesson Starters units are disabled/inactive; GP replacement Lesson Materials
  timer is active. DNS host-local alert timers are retired on both proxy hosts.
  The chat had no new Password Reset `[OK]` at 09:58 after its 09:48 run.
  This sample is not the entire Telegram history. No synthetic incident,
  direct Telegram test, notification replay, or manual incident-state edit
  was used for this stage.

## Important files

- `src/system_monitor/adapters/gp.py`, `profiles/gp.json`,
  `config/priorities.json`, `tests/test_openai_key_status.py`,
  `tests/test_schedule_publisher_status.py`, `README.md`.
- Application states: `scripts-devbox/password-reset/TASK_STATE.md` and
  `vps-gp/scripts/{schedule-publisher,openai-key-monitor,designmodo-api}/TASK_STATE.md`.

## START HERE

1. Read this file and `README.md`; check `git status --short`, live adapter
   hash, timer state, active incidents, and pending queues before changes.
   Verify the publisher's first scheduled 05:30 run on 2026-11-10 and the
   following natural monitor cycle.
2. Observe later natural cycles and incoming routes without manually calling
   paid APIs or sending test alerts. The first post-cutover GP application
   cycles and the two devbox Password Reset cycles passed.
3. For the unrelated Designmodo import failure, refresh its live service and
   incident state before action; preserve any active alarm until natural recovery.
4. Preserve private snapshots and live GP priority drift. Restore only the
   relevant files from snapshots if cutover fails, retaining first-attempt
   evidence and production incident state.
