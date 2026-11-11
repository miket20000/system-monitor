# System Monitor — Task State

Updated: 2026-11-11 11:40 CET.

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

- The disabled Telegram Daily Reason receiver and timer were retired from
  the devbox source and live profile on 11 Nov. Only their three check keys
  were removed from live state to prevent reminders. The corresponding two
  priority patterns were removed from source and live policy; all other
  incidents were retained. Private rollback:
  `~/.local/state/devbox-monitor/rollbacks/20261111-daily-reason-before-113611/`.
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

- Before Daily Reason retirement the source and live devbox profiles matched
  exactly. JSON parsing, `git diff --check` and `PYTHONPATH=src pytest -q`
  passed (255 tests, 50 subtests). A host-context shadow cycle with
  `--no-notify` and private state passed without Daily Reason probes. Live
  readback found no Daily Reason entries in profile, priority policy or
  incident state; the devbox timer was restored active. Its first natural
  11:40 CET cycle finished success/0, did not recreate Daily Reason checks,
  had zero pending events and preserved all five other devbox incidents.
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
  alarm still active, and zero pending GP events. Subsequent Designmodo
  cycles through 11 Nov continued to fail.
- Devbox Password Reset source commit: `31dd39f` in `scripts-devbox`.
  GP app source commit: `1bbbebd` in `vps-gp`. First natural Password Reset
  runs at 09:48:34 and 10:03:44 passed exit 0 with `--no-telegram`.
  Devbox pre-change private snapshot:
  `~/.local/state/password-reset/rollbacks/20261108-telegram-cutover-before/`.
  The 09:50 and 10:05 natural devbox monitor cycles passed exit 0. After the
  second Password Reset run, its timer and service result remained `ok`;
  per-account daily checks were `ok` at 09:50.

## Active alarms and limits (11 Nov 11:38 CET)

- Devbox system backup: exit 3 on 11 Nov 01:59. Snapshot saved with incomplete
  coverage because the active CRM Elasticsearch container has no usable
  consistency adapter; its `devbox-restic` snapshot repository returned 404.
- VPS Restic backup: exit 1 on 11 Nov 03:43. DNS proxy and giganci backups
  succeeded; GP backup failed on an exclusive repository lock created 9 Nov
  12:08 by PID 4041971. That PID is no longer running on GP. Do not unlock or
  retry before checking all repository clients.
- VPS Restic check: exit 1 on 9 Nov 08:30 because another backup operation
  was running; there has been no successful completed check since.
- GP mail purge: exit 124 on 9 Nov 12:13 after 6h13m, during snapshot rewrite
  after archive verification. The backup script's operation timeout is 6h.
  This task changes retained snapshots; do not rerun it as a smoke test.
  Its interruption may relate to the GP repository lock, but this is not
  established conclusively.
- Social Media invitations: exit 20/BLOCKED on 9 Nov 10:03, 0/33 pages.
  The task's own result cites a missing `Facebook_pages.csv` and no free,
  owned Meta Home tab. Do not retry completed prior batches.
- GP Designmodo daily sync: exit 1 on 11 Nov 06:45, repeated since at least
  7 Nov. The local import API returned HTTP 404 for a batch of three
  `giganciprogramowania` projects. Import-run rows show `error`, zero imported
  and zero per-project errors, consistent with failure while discovering a
  project. The precise upstream error body was not read; do not call the
  provider merely to reproduce it.
- GP public `komputery` endpoint: HTTP 526. Its Apache origin certificate
  expired 11 Nov 09:08 CET; direct origin TLS validation fails. Certbot's
  recent attempts failed challenges. Apache itself is active.
- These are diagnoses only. No backup repair, unlock, retry, certificate
  renewal or GP service restart was authorized in this stage.

## Important files

- `src/system_monitor/adapters/gp.py`, `profiles/gp.json`,
  `config/priorities.json`, `tests/test_openai_key_status.py`,
  `tests/test_schedule_publisher_status.py`, `README.md`.
- Application states: `scripts-devbox/password-reset/TASK_STATE.md` and
  `vps-gp/scripts/{schedule-publisher,openai-key-monitor,designmodo-api}/TASK_STATE.md`.

## START HERE

1. Read this file and `README.md`; check `git status --short`, live profiles,
   timer state, active incidents and pending notifications before changes.
2. Confirm current incidents and later natural cycles before further changes;
   the first post-retirement devbox cycle at 11:40 CET passed.
3. Prioritize the expired `komputery` origin certificate and backup coverage/
   Restic lock investigation. Repair and retry are outside this stage.
4. Preserve private rollback snapshots, GP priority drift and unrelated
   incident state. Use natural cycles for recovery evidence.
