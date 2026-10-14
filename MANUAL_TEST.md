# Manual acceptance on this PC

Run this after implementation. These steps change Steam state, so keep the normal
Steam download/exit confirmation visible. Do not use a test game with unsynced saves.

## Tool destination and Steam update behavior

1. Export a baseline as the signed-in Linux user:
   `python3 commands/audit-install-locations.py --library /srv/SteamLibrary > /tmp/sslm-before.json`
2. Run manager setup. Confirm local tools, the configured wrapper, and all selected
   users' actual access. Repeat under Adam's account.
3. Install a Windows game into shared storage, launch it, and allow dependencies
   to download. Verify the game's prefix is private and all required tools are local.
4. Compare: `python3 commands/audit-install-locations.py --library /srv/SteamLibrary --compare /tmp/sslm-before.json`
5. Repeat after a game update, Proton update, and Steam restart. Record whether
   Steam changes its remembered install destination. A local preference alone
   must never be reported as a guarantee that automatic downloads stay local.

## Existing tools and conflicts

- Preview migration for shared Hotfix and every runtime, under Adam's account.
- Verify an identical local destination is accepted without replacing it.
- Verify a different/incomplete local destination is reported as a conflict;
  keep both existing installations and resolve through the manager's explicit action.
- Confirm source copies remain until every selected account's copy verifies.
- Confirm runtimes without Steam move/install menus can be copied by the manager.
- After migration, refresh Step 4: unregistered runtime directories containing
  only var/ caches must not keep it red. Registered or unpackaged actual tool
  binaries must still be reported as shared tools.

## Access, mappings, and recovery

- In Step 5, select Adam, Jonas, Marcus and Valdemar. Confirm the preview proposes
  copying runtime 4183110 from Jonas for accounts missing it. Apply setup, confirm
  each account becomes ready, then start Steam as Adam and launch a shared game.
  Check that Steam recognizes the copied runtime and can verify/update it locally.
- Repeat setup: complete runtimes must be kept. Existing/incomplete packages and
  beta choices must be preserved with a remaining Steam installation instruction.
- Confirm runtime `var/` caches are recreated for each account and the source
  account's runtime remains usable after copying.

- Add a user with an existing desktop session; verify shared access without logout.
- Apply a game update; verify new/replaced game files remain accessible.
- Verify explicit per-game Proton versions are preserved during migration.
- Check native/mixed/unknown games are not incorrectly shown as confirmed ready.
- Interrupt a staged operation; retry and inspect its recovery record.
- Uninstall the wrapper without deleting saves; check restored compatibility choices.
- Export diagnostics and confirm no credentials or raw Steam configuration are included.
