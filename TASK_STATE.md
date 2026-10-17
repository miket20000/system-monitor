# System Monitor — Task State

Updated: 2026-10-17, Europe/Warsaw.

## Goal and ownership

`system-monitor` is the canonical source for independent `devbox-monitor`
(user `miket`) and `gp-monitor` (root). They share code and notification
policy while retaining separate profiles, schedules and private state v3.
Devbox owns local services, GP dead-man visibility and the new SSH host
checks for `dnsproxy1`/`dnsproxy2`. GP owns its local workloads, Online
Compiler scheduler checks and eight independent external DNS checks for the
two DNS hosts (IPv4/IPv6, UDP/TCP).

## Current implementation

- Devbox runtime: wrapper
  `/home/miket/.local/lib/devbox-monitor/devbox-monitor.py`, package
  `/home/miket/.local/lib/system-monitor/system_monitor/`, profile
  `/home/miket/.config/devbox-monitor/config.json`, priorities
  `/home/miket/.config/system-monitor/priorities.json`, state
  `/home/miket/.local/state/devbox-monitor/state.json`. The timer runs at
  `*:0/5` Europe/Warsaw. GP runs at `*:1/5` as a root system unit.
- The alert engine preserves technical incident keys, two-cycle recovery,
  three-cycle network failure confirmation, pending-event coalescence and
  three priority channels. LOW is a daily digest. Active state v3 and pending
  notifications must not be cleared to force a result. A private shadow needs
  both `--no-notify` and `--state-file`; `--no-notify` alone writes state.
- Devbox now calls the bounded read-only JSON probe on each DNS VPS through
  verified SSH aliases with strict host-key checks. The stable keys are
  `dnsproxy:<host>:<check>`. Transport and invalid-payload failures are
  separate visibility checks; an absent or invalid sample leaves prior DNS
  incidents intact. Devbox compares boot ID and container restart count.
  DNS availability, sync freshness, IPv6 protection and visibility are HIGH;
  other host health is MEDIUM. The probe source and host rollout are in
  `/home/miket/coding/vps-dnsproxy`.
- Existing GP DNS external checks are unchanged. GP-local Online Compiler
  scheduler health, environment, freshness, idle and public availability
  remain independent. The phase-aware steady/transition thresholds are
  300/900 seconds warning, 1860 seconds overdue and 600/900 seconds idle.
- Retired services stay retired: Minecraft Bedrock and AnyDesk on devbox;
  Ads Dashboard, Google API and Meta API on GP. Lesson Materials sync is
  GP-owned with 18/26-hour freshness thresholds. Password Reset LOW alerts
  keep per-account technical keys with one grouped lifecycle.
- Telegram token and chat IDs live only in protected runtime environment
  files. Notification text excludes raw command output, URLs and payloads.

## Verified status and validation

- DNS candidate passed `PYTHONPATH=src pytest -q`: 243 tests and 50 subtests;
  JSON, Python compilation and diff checks passed. Both source and installed
  devbox baseline hashes matched before deployment, so the runtime update
  contained only the reviewed DNS changes.
- The source candidate first ran as a `--no-notify` private-state shadow:
  60 DNS host results, 29 from `dnsproxy1` and 31 from `dnsproxy2`; every
  result was `ok`, with no DNS proxy incident. The full host probes were 25
  and 27 controls respectively; the central monitor adds visibility, reboot
  and restart checks. This shadow did not touch production state.
- A private devbox rollback snapshot, including package, wrapper, profile,
  priorities, environment and state, is at
  `/home/miket/.local/state/system-monitor-rollbacks/20261017-dnsproxy-migration-before`.
  The devbox package, profile and priority changes were installed while its
  timer was stopped, checked byte-for-byte against source and the timer was
  restarted. Production state was preserved with mode `0600`.
- Natural devbox cycles at 18:10 and 18:15 CEST completed with exit 0;
  60/60 DNS proxy results were `ok`, with no active or pending DNS proxy
  incident. The local sync incidents closed through natural healthy cycles.
  The old `dnsproxy-monitor.timer` and `.service` were then disabled and
  removed on `dnsproxy2`, followed by `dnsproxy1`; their state and
  configuration remain in private host snapshots. The read-only probes and
  sync heartbeat paths remain active.
- GP natural cycles at 18:06, 18:11 and 18:16 CEST completed successfully
  with all eight external DNS results `ok`. The 18:16 cycle followed removal
  of the local monitors. Direct GP queries to both hosts on both families
  and transports returned `NOERROR` after the IPv6 allowlist deployment.
- The first post-retirement natural devbox cycle at 18:20 CEST completed with
  exit 0 and 60/60 DNS proxy keys `ok`, with no active or pending DNS proxy
  notification. Both VPS timers/services remained `not-found`; neither local
  monitor wrote a journal entry after cutover, minute sync heartbeats advanced
  and each IPv6 ACL retained three clients.
- The post-retirement GP natural cycle at 18:21 CEST completed with exit 0;
  all eight external DNS checks remained `ok` and no DNS proxy notification
  was pending. Together with the devbox 18:20 cycle and absence of new local
  monitor journal entries, the cutover showed no duplicate DNS alerts.

## Constraints and known limits

- The user approved this specific DNS monitoring/IPv6 migration. Do not alter
  GP's eight external DNS checks or unrelated state. New devbox SSH host
  checks must not replace GP as an independent external vantage point.
- No synthetic incidents or notification tests during cutover. Preserve
  incident state and prior failures; an unavailable sample is not recovery.
- The previous 2026-10-07 false MEDIUM Next Dev reminder remains a historical
  FAIL; the later baseline repair is deployed. Do not replay or erase it.
- For any later code rollout, compare source with installed bytes and state;
  keep devbox/GP deployment scopes separate. The private snapshot above is
  the rollback source for this DNS integration.

## Important files

- `src/system_monitor/adapters/devbox.py` — devbox and DNS SSH checks.
- `src/system_monitor/notifications.py`, `src/system_monitor/cli.py` — state
  retention and notification policy.
- `profiles/devbox.json`, `profiles/gp.json` — separate host profiles.
- `config/priorities.json` — HIGH/MEDIUM/LOW routing and displayed text.
- `tests/test_dnsproxy_adapter.py` — probe JSON, transport, reboot/restart and
  missing-sample regression tests.
- `README.md` — deployment layout and DNS handoff notes.

## START HERE

1. Read this file and `README.md`; inspect `git status --short` and installed
   devbox runtime hashes. Keep the production state v3 and pending queue.
2. On later incidents, verify devbox SSH visibility, the DNS host probe,
   minute sync heartbeat and GP's eight external DNS results together. Keep
   the retired local timers/services off and retain the heartbeat directory
   and read-only probe. Do not manufacture failures or clear state.
3. Preserve the private DNS and devbox rollback snapshots. A future change to
   code, profile or priorities needs a fresh installed-byte, state and timer
   readback before deployment; GP remains a separate runtime.
