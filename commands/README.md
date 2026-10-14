# Commands and trust boundary

The unprivileged GUI sends allow-listed argument arrays to `gui-admin-session.py`.
Authorization freezes a private root-owned command snapshot. Privileged Python
runs in isolated, no-bytecode mode. Account files are mutated only after the worker
permanently drops to the selected account's UID and groups. Downloaded binaries
are never executed as root. Administrator operations and account writes are locked.

## Setup and administration

- `setup-shared-library.sh --library PATH --group GROUP USER...` prepares an empty
  dedicated game library, adds group members, grants immediate named user ACLs,
  and installs the game-update permission monitor.
- `repair-shared-library.sh --library PATH --group GROUP` validates dedicated
  storage and repairs pinned filesystem objects; private/system locations,
  symlink escapes, hardlinks, special and root/service-owned files are refused.
  Proton/runtime directories are excluded. It also installs/updates the monitor.
- `grant-library-users.sh --group GROUP [--library PATH] USER...` adds members;
  with a library, repairs direct ACL access for already-running sessions.
- `add-user.sh --close-steam --default-library PATH --group GROUP --base-proton PATH USER...`
  closes selected Steam clients, grants access, installs local wrappers, selects
  them by default, and registers shared storage with a local remembered destination.
- `install.sh --base-proton PATH USER...` stages per-account wrappers without
  root writes in home directories. Every person needs local Proton. Per-game
  overrides remain unchanged; their default previous choice is recorded.
- `migrate-existing-games.sh --dry-run|--apply USER...` preserves pinned official
  Proton versions using separate personal wrappers. Missing local versions cause
  a clear refusal rather than substitution.
- `migrate-shared-tools.sh [--dry-run] [--replace-conflicts] [--cleanup] --library PATH USER...`
  copies and hashes tool packages and app manifests, excludes runtime UID caches,
  removes shared permissions from local copies and retains conflict backups.
  Cleanup quarantines sources after every registered account's copy verifies.
- `uninstall.sh [--remove-prefixes] USER...` restores previous managed mappings
  while preserving later user choices. Saves remain unless explicitly removed;
  shared games/access and the separate monitor remain.
- `close-steam.sh [--force] USER...` requests desktop-session Steam shutdown.
  Forced SIGTERM requires explicit selection; configuration is not edited live.

## Inspection and internals

- `status.py --library PATH --group GROUP` produces structured read-only status.
- `preview-setup.py --library PATH --base-proton PATH USER...` describes proposed
  per-account setup, including local runtime dependencies and prefix locations.
- `audit-install-locations.py --library PATH [--compare BASELINE.json]` records
  actual tool locations for the signed-in account and the shared library.
- `account-worker.py` loads trusted assets before dropping identity, preflights
  accounts, stages changes and records recovery. Its `--data-home PATH` option
  can configure an owned private prefix location outside the default; it is
  persisted in the wrapper and used consistently by status and uninstall.
- `proton` uses the configured private prefix and local Proton, refreshes its
  runtime manifest, forwards termination and records the delegated exit result.
- `manifest-helper.py` generates manifests from validated fields and guards
  runtime dependency changes until Steam restarts.
- `vdf.py` provides strict parsing and source-preserving edits across backends.
- `library-backend.py` handles dedicated storage and descriptor-based ACL changes.
- `permission-monitor.py` repairs completed game updates, excluding support tools.
- `install-permission-monitor.py` writes root-owned code/configuration and a
  hardened systemd unit. The monitor checks every 30 seconds, not at every write.
- `operation-lock.py` serializes administrator and account changes.
- `transactions.py` journals staged publications and preserves interrupted data.
- `runtime-reuse.py` provisions missing matching public runtimes from another
  account during setup. An anonymous transfer descriptor connects account workers;
  source reads and destination writes run under their respective accounts. It
  preserves existing installations and branch choices, verifies package contents,
  omits generated caches, and journals interrupted imports for recovery.
- `_common.sh` retains CLI path checks.
- `repair-proton-fixups.py` is a legacy command; it drops root to the installation
  owner before restoring modes. Root/service-owned tools must be migrated instead.

Saved previous selections and journals are private. Diagnostic exports omit them,
raw VDF and desktop session environment. Backups/quarantine are retained for review.
See `MANUAL_TEST.md` for final host acceptance, including Adam's tool conflicts.
