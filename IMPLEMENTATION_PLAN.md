# Implementation and acceptance plan

Work is committed in these steps. Host game moves and launch verification are
reserved for the final user test; read-only host inspection is allowed.

1. Run Steam account mutations as the target user, with safe configuration writes.
2. Restrict privileged repair to dedicated game storage and pinned filesystem objects.
3. Investigate Steam's install preference and expose its actual limitations;
   prepare a repeatable acceptance check for tool download destinations.
4. Make readiness conservative and verify wrapper/runtime configuration.
5. Configure compatibility mappings automatically, preserving pinned Proton versions.
6. Add local tool installation hand-off and verified shared-tool copying/migration.
   Include Adam's Hotfix destination conflict and runtimes without Steam move menus.
7. Grant direct user ACLs so existing desktop sessions can access shared games.
8. Add locking, recovery records, transactional installation, and safe uninstall.
9. Consolidate backend parsing, prefix locations, runtime manifests, setup preview,
   automatic status refresh, and diagnostic export.

Every step needs focused regression coverage. Final acceptance on this PC checks:
- fresh/reconfigured accounts and actual desktop access without logout;
- shared Hotfix/Experimental/runtime copies, including destination conflicts;
- local dependency downloads before and after installing a shared game;
- default and pinned compatibility choices, private prefixes, native/unknown games;
- retries, cancellation/failure recovery, uninstall, and diagnostic export.

## Progress

- Step 1 complete: account worker drops identity before writes; pinned directory
  configuration updates; staged wrapper replacement; uninstall clears dangling
  mappings as the account. Live Steam behavior is not yet verified.

Additional requirements:
- Support existing installed games through validated import/recognition; leave
  ambiguous or conflicting installations untouched and explain the conflict.
- Make game updates resilient: default named-user ACLs plus targeted permission
  reconciliation when app manifests change; tolerate Steam/client schema changes.

- Step 2 complete: dedicated-library validation; descriptor-based recursive repair;
  protected/private paths, external symlinks, special files and hardlinks refused;
  legacy Proton permission restore drops root before mutation.

- Step 3 complete: read-only host baseline found Jonas's remembered destination
  currently shared, plus shared Hotfix and three runtimes. Steam is running.
  Added location audit/comparison and MANUAL_TEST.md; removed guaranteed-placement
  claims. Actual download placement remains an explicit final acceptance check.

- Step 4 complete: mixed/native file scans no longer prove launch safety; separate
  configured/recorded-launch states; runtime dependency, manifest freshness, launcher
  permissions and wrapper version verified; Proton exit results recorded privately.

- Step 5 complete: setup selects the personal wrapper automatically; original
  mappings saved for removal; per-game version migration creates distinct local
  wrappers and refuses unavailable pinned versions instead of silently substituting.
  Strict VDF editing preserves unrelated configuration and rejects ambiguity.

- Step 6 complete: verified local tool copying, destination conflict previews and
  explicit backup-and-replace, source-change detection, private copied permissions,
  runtime UID caches excluded, and recoverable shared quarantine only after all
  selected/registered account copies verify. Added GUI migration and Steam install
  hand-off with completion polling. No host installations have been moved.

- Step 7 complete: named access/default ACLs grant existing sessions immediate
  access; actual GUI identity checks replace stale group checks. Root-owned monitor
  code is installed when preparing/repairing a library, repairs completed game
  updates every 30 seconds, excludes support tools, and reconciles managed ACLs
  when group membership changes. Service installation awaits the manual host test.

- Step 8 complete: GUI/CLI administrator changes serialize with the monitor;
  per-account locks prevent concurrent setup writes; staged publications have
  recovery journals and recover on retry; uninstall restores previous mappings
  only where the user still selects a managed tool. Backups and saves retained.
