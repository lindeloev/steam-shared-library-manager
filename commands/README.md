# Commands and trust boundary

Every program that inspects or changes shared-library, Steam-account, or Proton
prefix state lives in this directory. The GUI only gathers choices, shows
status, and sends an allow-listed command plus an argument array to
`gui-admin-session.py`.

## Administrative commands

- `setup-shared-library.sh` creates a missing or empty shared-library folder.
- `repair-shared-library.sh` repairs group ownership, permissions, and ACLs in a
  recognized Steam library.
- `grant-library-users.sh` adds existing Linux accounts to the access group.
- `add-user.sh` combines a clean Steam shutdown, group access, and tool install.
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
state, refuses unsafe Steam-root symlinks, and stops if Steam could rewrite the
same account files. Config migrations use unique backups and atomic replacement;
if a multi-account run stops partway through, its output identifies the account
in progress and the command can be rerun. Read the individual script headers or
run a command with `--help` for its exact interface.
