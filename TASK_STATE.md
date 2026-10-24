# System Monitor — Task State

Updated: 2026-10-24, Europe/Warsaw.

## Goal and ownership

`system-monitor` is the canonical source for independent `devbox-monitor`
(user `miket`) and `gp-monitor` (root). They share code and notification policy,
not runtime state. Devbox owns its local probes, the GP dead-man check and
read-only SSH checks for `dnsproxy1`/`dnsproxy2`. GP owns its local workloads,
Online Compiler scheduler checks and eight external DNS checks. The 2026-10-24
scope change closes observed GP production coverage gaps. AI Apps DEV and the
existing Next Dev alarms are outside this change.

## Current implementation

- GP runs every five minutes at `*:1/5 Europe/Warsaw` from
  `/usr/local/lib/gp-monitor/gp-monitor.py`; profile
  `/etc/gp-monitor/config.json`, priority registry
  `/etc/system-monitor/priorities.json`, state `/var/lib/gp-monitor/state.json`.
  Devbox runs at `*:0/5` with its established user-local paths.
- The GP profile now names 44 system services, 41 system timers and 115 HTTP
  endpoints. The new entries cover AI Admin, CGA authentication/backend,
  Scratch CG and ScratchJr API/Identity, their documented readiness routes,
  the public Scratch CG homepage, the AI Admin probe, application retention
  and backup, and VM Manager reconcile/inventory sync. CGA authentication has
  no unauthenticated readiness route, so only its systemd unit is checked.
- New timer warning/critical freshness limits: AI Admin probe 15/30 minutes;
  daily retention and backup 30/36 hours; VM reconcile 5/10 minutes; VM
  inventory sync 15/30 minutes. Production unit/readiness keys route HIGH,
  the frequent VM jobs MEDIUM, and daily retention/backup retain LOW.
- Coding Giants App readiness performs a full signed-catalog validation after
  its five-minute cache expires. Only this endpoint has a 30-second request
  timeout and 15/25-second warning/critical response thresholds; other GP
  HTTP probes retain 5/8 seconds.
- Devbox and GP profiles retain separate incident state v3. The engine keeps
  technical keys, two-cycle recovery, three-cycle network confirmation and
  pending-event coalescence. `--no-notify` writes state; a diagnostic run must
  also use `--state-file` in a private directory. Do not reset active state or
  manufacture incidents. OpenRouter key monitoring remains independent.
- The GP live priority registry had 49 assignments before this rollout versus
  55 in the previous source file. Six exact new assignments were inserted into
  that live file; its unrelated drift was preserved. Current source and live
  registries still differ because of that pre-existing drift. Do not deploy the
  entire source registry over GP without reconciling this drift.

## Verified status

- Repository was clean before work. Final source candidate passed
  `PYTHONPATH=src python3 -m pytest -q` (246 tests and 50 subtests), JSON
  parsing, priority routing readback and `git diff --check`.
- The GP profile and adapter matched installed bytes before the change.
  A private GP shadow used the candidate profile/priority registry with
  `--no-notify --state-file`: 328 results, 325 OK and the three pre-existing
  Next Dev CRITICAL results. All 27 new result keys were OK. This shadow did
  not use production state or send notifications.
- The first GP candidate had installed profile SHA-256
  `8af9fd51549560658e1fb9cd130b9c0348d45582f4dd34bc17f95d03f03b524a`.
  The GP timer was restarted enabled/active and production state was kept.
- Private root-owned pre-change snapshot (mode `0700`) is at
  `/var/lib/gp-monitor/rollbacks/20261024-coverage-before`; it contains the
  profile, priorities, unit/timer, environment, installed code and state.
- First natural GP cycle at 14:36 CEST ended with exit 0: 321 OK and four
  CRITICAL results. Three were the pre-existing Next Dev checks; the new
  `endpoint:codinggiants-app-ready-local` was unreachable once. This is a
  first-cycle FAIL for that endpoint and must remain in the evidence even if
  it later recovers. The AI Admin probe service result was omitted while its
  oneshot was running; this preserves the last conclusive state. The other
  25 new results were OK. Three direct readiness reads immediately afterward
  returned HTTP 200 in 22–29 ms; `codinggiants-app.service` remained active
  with zero restarts. The new endpoint's failure streak was one,
  `alert_active=false`, and no notification was pending. The 14:41 natural
  cycle repeated this endpoint FAIL (failure streak two) while other new
  checks were OK. Do not erase either first-candidate FAIL.
- Direct read-only execution of the backend's existing signed-catalog
  validation took 8.677 seconds, exceeding the GP monitor's former 8-second
  HTTP timeout. The second candidate adds per-endpoint limits without changing
  thresholds for other services; it passed 246 tests/50 subtests and a private
  GP shadow (323 OK, three unchanged Next Dev CRITICAL).
- The second candidate was installed with the GP timer briefly stopped.
  Installed profile SHA-256 is
  `1778b029ba83db7ecd00a0ee4dc88b558ab1be62c5ab785778821acdad10037e`,
  installed GP adapter SHA-256 is
  `55335a5367a6c005ab0466e4ae5f2e9aa26c833df561ee2dbede38cf37928a9e`,
  and live priority SHA-256 remains
  `464f0618211a07d3806374ade3bdc54fef4f1e27a4d3034289a86d6a6727b226`.
  Production state remained mode `0600` and was not reset. The first natural
  second-candidate cycle at 14:46 CEST ended with exit 0: 325 OK, only the
  three pre-existing Next Dev CRITICAL checks, and all 27 new keys OK. Coding
  Giants App readiness returned HTTP 200 in 8.66 seconds; its failure streak
  reset to zero with no active or pending notification. The second natural
  cycle at 14:51 CEST ended with exit 0: 321 OK, only the three unchanged
  Next Dev CRITICAL checks, and all 27 new keys OK. Coding Giants App
  readiness returned HTTP 200 in 0.03 seconds from its cache. GP timer stayed
  enabled/active, and installed profile/adapter/priority hashes were unchanged.
- The private root-owned second-candidate rollback snapshot is at
  `/var/lib/gp-monitor/rollbacks/20261024-coverage-v1`; it keeps the first
  candidate profile, GP adapter and state separately from the pre-change
  snapshot.

## Constraints and known limits

- The three active GP Next Dev CRITICAL checks are a detected application
  problem, not a coverage gap; the user excluded their remediation here.
- AI Apps DEV container health on devbox remains unmonitored by this change,
  as requested. Native devbox PostgreSQL/libvirt remain local development/test
  infrastructure. Devbox profile and runtime were not changed.
- Retired Minecraft Bedrock/AnyDesk on devbox and Ads Dashboard/Google API/
  Meta API on GP stay retired. The DNS proxy host probes and GP external DNS
  checks stay independent. The 2026-10-07 false Next Dev reminder remains a
  historical FAIL; do not erase it from evidence.
- A running timer-backed oneshot is not recovery evidence; the adapter waits
  for a completed result. An unavailable DNS sample is not recovery evidence.

## Important files

- `profiles/gp.json`, `config/priorities.json` — source GP scope and routing.
- `src/system_monitor/adapters/gp.py` — GP probes and per-endpoint HTTP
  response thresholds; `src/system_monitor/notifications.py` — unchanged
  incident policy.
- `tests/legacy/test_gp_monitor.py`, `tests/test_notifications.py` — coverage
  and priority checks.
- `README.md` — deployment paths, rollback and monitoring contract.

## START HERE

1. Read this file and `README.md`; check `git status --short`, the installed GP
   profile/adapter/priority hashes, timer state, active incident state and
   pending notifications before further changes. The final candidate passed
   natural 14:46 and 14:51 cycles. Keep the 14:36/14:41 first-candidate
   FAIL results in the evidence.
2. Preserve the private snapshot, unrelated GP priority drift and the active
   Next Dev incidents. If a new check fails, diagnose the service independently;
   do not clear state or create synthetic notifications.
3. Keep the requested exclusions: no AI Apps DEV change and no Next Dev alarm
   remediation in this stage. A later deployment must compare source and live
   configuration before writing either host.
