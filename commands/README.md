# Commands and trust boundary

Every program that inspects or changes shared-library, Steam-account, or Proton
prefix state lives in this directory. The GUI only gathers choices, shows
status, and sends an allow-listed command plus an argument array to
`gui-admin-session.py`.
At authorization time the helper freezes these files in a root-private
temporary snapshot; subsequent edits to the checkout cannot alter that
authorized session. The privileged Python interpreter uses isolated,
no-bytecode mode so it neither imports checkout-local modules nor leaves
root-owned cache files behind.

## Administrative commands

- `setup-shared-library.sh` creates a missing or empty shared-library folder.
- `repair-shared-library.sh` repairs group ownership, permissions, and ACLs in a
  recognized Steam library.
- `grant-library-users.sh` adds existing Linux accounts to the access group.
- `add-user.sh` combines a clean Steam shutdown, group access, tool install,
  and optional default-storage configuration.
- `configure-steam-storage.py` registers the shared folder and selects its
  folder index as the default for each initialized Steam account. It is invoked
  by `add-user.sh` when `--default-library` is used.
- `close-steam.sh` asks selected users' native Steam clients to exit; `--force`
  is an explicit SIGTERM fallback when desktop IPC is unavailable or ignored.
- `install.sh` installs or updates the compatibility wrapper for selected users.
- `migrate-existing-games.sh` backs up and changes explicit Proton mappings.
- `uninstall.sh` removes this tool and, only with `--remove-prefixes`, personal
  Proton prefixes.

## Read-only and runtime commands

- `status.py` produces the read-only JSON report used by the GUI.
- `proton` is copied into each user's Steam configuration. At game launch it
  selects that user's private prefix and delegates to the shared official
  Proton installation.
- `gui-admin-session.py` accepts only its fixed command allow-list. It is kept
  alive for one open manager window so PolicyKit authorization is not repeated.
- `_common.sh` contains shared path validation and prefix-permission helpers; it
  is sourced by other commands and is not a standalone command.

Each administrative script validates all important inputs before changing
state, refuses unsafe symlinked library paths and personal primary Steam
folders, and stops if Steam could rewrite the same account files. Steam
configuration changes use unique backups and atomic replacement. The automatic
storage writer also aborts rather than overwrite a file changed after
preflight. If a multi-account run stops partway through, its output identifies
the account in progress and the command can be rerun. Read the individual
script headers or run a command with `--help` for its exact interface.
