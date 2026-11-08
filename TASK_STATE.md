# System Monitor — Task State

Updated: 2026-11-08 09:52 CET.

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
  worker and OpenAI cycles after cutover are pending at 10:00; Designmodo's
  next daily cycle is 2026-11-09 06:45 CET.
- Devbox Password Reset source commit: `31dd39f` in `scripts-devbox`.
  GP app source commit: `1bbbebd` in `vps-gp`. First natural Password Reset
  run at 09:48:34 passed exit 0 with `--no-telegram`; next is 10:03:34.
  Devbox pre-change private snapshot:
  `~/.local/state/password-reset/rollbacks/20261108-telegram-cutover-before/`.
  The 09:50 natural devbox monitor cycle passed exit 0, and Password Reset
  timer, service result, and per-account daily checks were `ok`. Its one
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
  2026, not the entire history. No synthetic incident, direct Telegram test,
  notification replay, or manual incident-state edit was used for this stage.

## Important files

- `src/system_monitor/adapters/gp.py`, `profiles/gp.json`,
  `config/priorities.json`, `tests/test_openai_key_status.py`,
  `tests/test_schedule_publisher_status.py`, `README.md`.
- Application states: `scripts-devbox/password-reset/TASK_STATE.md` and
  `vps-gp/scripts/{schedule-publisher,openai-key-monitor,designmodo-api}/TASK_STATE.md`.

## START HERE

1. Read this file and `README.md`; check `git status --short`, live adapter
   hash, timer state, active incidents, and pending queues before changes.
2. Finish readback of GP 10:00 Schedule Publisher and OpenAI cycles, and
   devbox 10:03 Password Reset cycle. Confirm following
   central-monitor cycles. Do not manually call paid APIs or send test alerts.
3. At the next natural Designmodo daily sync (9 Nov 06:45 CET), verify service
   result and central alarm routing. Investigate its existing failed import in
   the owning project; preserve the active alarm until natural recovery.
4. Preserve private snapshots and live GP priority drift. Restore only the
   relevant files from snapshots if cutover fails, retaining first-attempt
   evidence and production incident state.
