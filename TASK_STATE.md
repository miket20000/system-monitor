# System Monitor — Task State

Updated: 2026-11-07 12:42 CET.

## Goal and ownership

`system-monitor` is the canonical source for independent `devbox-monitor`
(user `miket`) and `gp-monitor` (root). They share notification code and
priority policy, but keep separate profiles and incident state. Devbox owns
local probes, the GP dead-man check and read-only DNS proxy checks. GP owns
its workloads, Online Compiler scheduler checks and public DNS checks.

The current task fixes repeated identical lines within one Telegram message
and reports active alarms. It does not repair the monitored applications or
clear incident state. AI Apps DEV and existing Next Dev alarm remediation remain
out of scope.

## Current implementation and runtime

- Devbox timer runs at `*:0/5` and GP at `*:1/5 Europe/Warsaw`. Installed code
  lives in `~/.local/lib/system-monitor` and `/usr/local/lib/system-monitor`;
  profiles and states are listed in `README.md`. Source and live GP priorities
  have pre-existing unrelated drift; do not overwrite the live registry.
- State v3 keeps technical incident keys, two-cycle recovery, three-cycle
  network confirmation, pending-event coalescence and LOW digest scheduling.
  `--no-notify` still writes its selected state file: shadow runs require a
  private `--state-file`. Do not reset active state or manufacture alerts.
- The Telegram defect was multiple technical keys rendering the same
  `name : problem : host` line. GP state had 20 AI Text checks sharing the
  shown recovery line and 12 mail archive checks sharing the other. The new
  formatter emits each visible line once per status; chunking counts visible
  lines while retaining every underlying key for successful-delivery
  acknowledgement and failed-chunk retry. Different statuses remain separate.
- The source patch changes only `src/system_monitor/notifications.py`, tests,
  `README.md` and this snapshot. Installed GP notification code before this
  task was older than source by the `retain_missing_prefixes` feature. Active
  GP profile sets no such prefixes, so this difference has no GP effect.

## Verification and deployment

- The final source candidate passed the complete test suite in a private
  `/tmp` copy: 250 tests and 50 subtests. Targeted notification tests passed
  46/46; JSON parsing and `git diff --check` passed. Regression tests cover
  high-priority and LOW recovery duplicates, failed-chunk retry, all-key
  acknowledgement, and separate status sections.
- Private devbox shadow ran with `--no-notify` and separate state. The devbox
  module was installed with SHA-256
  `79cfb82559277cf79d30d85525cbf8b2df3ac1b313dd141eb66b927df5383c2d`.
  Natural 12:30 and 12:35 cycles ended `Result=success`, exit 0; timer stayed
  active/enabled and pending notifications remained 0.
- Private GP shadow with candidate notification module, `--no-notify` and
  separate state exited 0: 342 results (329 OK, 13 CRITICAL samples). This is
  not the active-incident count. The GP module was installed with the same
  SHA-256. Natural 12:36 and 12:41 GP cycles ended `Result=success`, exit 0;
  timer stayed active/enabled and pending notifications remained 0. The 12:36
  cycle logged one HIGH `RESOLVED` delivery (`count=1`) for AI Text; its tools
  endpoint then returned HTTP 200 with the configured host header.
- Private pre-change snapshots: devbox
  `~/.local/state/devbox-monitor/rollbacks/20261107-telegram-dedup-before`;
  GP `/var/lib/gp-monitor/rollbacks/20261107-telegram-dedup-before` (root-only).
  Production state and priority registry were not reset or replaced.

## Active alarms and unresolved causes

Read-only state and source checks at 12:25–12:26 CET found five active devbox
alarms and four GP alarms. The AI Text alarm naturally resolved at 12:36.
At 12:41–12:42, five devbox and three GP alarms remained active; both pending
notification queues were empty.

- Devbox: Social Media weekly invitations WARNING — 2 Nov run skipped at
  `ExecCondition` because its working directory was missing (`CHDIR`);
  journal also reports inotify `ENOSPC`. Restic check CRITICAL — 2 Nov run
  refused while another backup operated. Restic data-check and retention
  CRITICAL — 1 Nov `giganci` repository lock prevented both checks. Restic GP
  mail purge CRITICAL — 2 Nov SSH timeout to GP after about three hours;
  unfinished remote stage is unverified. The regular Restic backup succeeded
  7 Nov at 04:11, which does not resolve these separate jobs.
- GP: Designmodo daily sync CRITICAL — 7 Nov 06:45
  `giganciprogramowania` import batch had one error and imported zero;
  underlying batch error not in the bounded journal. Mail archive compression
  for `forkids` and `forkids-biuro` WARNING — no recorded successful run;
  timers active, next due 8 Nov; why prior run is absent is unconfirmed.
- Resolved during observation: AI Text tools was HTTP 503 and `ok=false` at
  12:26 while service and readiness were healthy; it returned HTTP 200 at
  12:42. The internal cause and natural recovery trigger are unconfirmed.
- No application remediation, production-state reset, notification replay or
  synthetic incident was performed. The 2026-10-24 first GP readiness candidate
  had two observed failures before its timeout correction; retain that history.

## Important files

- `src/system_monitor/notifications.py`, `tests/test_notifications.py` —
  notification behavior and regression coverage.
- `profiles/devbox.json`, `profiles/gp.json`, `config/priorities.json` — source
  checks and routing. `README.md` — deployment paths and notification contract.

## START HERE

1. Read this file and `README.md`; check `git status --short`, installed module
   hashes, timer state, active incidents and pending queues before further
   changes. Natural devbox 12:30/12:35 and GP 12:36/12:41 cycles passed.
2. Investigate the named application alarms in their owning projects before
   any repair. The precise stage of GP mail purge, Designmodo batch error and
   reason for absent mail-compression results remain unverified. Do not clear
   monitor state or create synthetic notifications as validation.
3. Preserve private snapshots and unrelated GP priority drift. If the new
   notification module later fails, restore the prior module from the relevant
   snapshot while retaining incident state and first-attempt evidence.
