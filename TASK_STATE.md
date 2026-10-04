# System Monitor — Task State

## Goal

Consolidate the devbox and GP monitors into this repository while preserving
two independent runtime instances, their local probes, permissions, state and
failure domains.

## Current implementation

- The repository contains the imported GP and devbox probe adapters, the
  shared notification engine, common priority routing and source profiles.
- Telegram routing uses the private `sysmon-high`, `sysmon-medium` and
  `sysmon-low` groups. Each contains the owner and `mt_monitoring_agent`; the
  exact configuration message was verified in all three groups. Chat IDs and
  the bot token remain only in protected runtime environment files.
- Full imported and shared test suites pass after updating the expected
  password-reset coverage. Isolated host-context shadow runs match all 71
  legacy devbox probes and all 312 legacy GP probes; intentional additions are
  six password-reset checks and the public Designmodo MCP endpoint.
- Devbox was cut over first. Natural cycles at 22:00 and 22:05 CEST completed
  with exit code 0; the user timer is active and the state is v3 mode 0600.
- GP was then cut over. Natural cycles at 22:10 and 22:15 CEST completed with
  exit code 0; the system timer is active and the state is v3 mode 0600.
- Existing incidents migrated without `[NEW]` replay. Devbox delivered four
  preserved LOW incidents as one reminder digest. GP delivered the preserved
  `dysk-sieciowy-sync` incident as a LOW reminder. Two distinct failed
  `meta-api-status-watcher` invocation IDs generated separate MEDIUM events as
  required by the durable-evidence policy.
- Rollback snapshots are at
  `/home/miket/.local/state/system-monitor-rollbacks/20261004T215708+0200/devbox`
  and root-only `/root/system-monitor-rollbacks/20261004T215708+0200/gp`.

## Validation

- PASS: 178 shared and imported unit tests, including transient/sample/durable
  confirmation, recovery, reminders, LOW digest, DST, state migration and
  partial Telegram failure retry.
- PASS: JSON validation, `py_compile`, `systemd-analyze verify`, calendar
  parsing, diff checks and secret scan.
- The first imported-suite run had two expected test failures because legacy
  assertions excluded Password Reset; the tests were updated to the approved
  new coverage and the complete rerun passed. This result is retained rather
  than rewritten as an initial pass.

## Constraints

- Do not clear or replace production state. `--no-notify` still writes the
  selected state file, so validation must always use a private copy/path.
- Keep devbox as a user unit and GP as a root system unit.
- Preserve legacy `TELEGRAM_CHAT_ID` for unrelated consumers.
- Do not manufacture incidents or expose URLs, payloads, tokens or chat IDs in
  notifications and logs.

## START HERE

1. Read this file and check all three repository worktrees before editing.
2. For a monitor change, run the shared and both imported regression suites,
   then a private `--no-notify --state-file` shadow cycle for the target host.
3. Preserve installed entrypoints, state paths, environment permissions and
   the three-channel message contract during future deployments.
4. Investigate application incidents in their owning repositories; do not
   clear monitor state or manufacture recovery observations.
