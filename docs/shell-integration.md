# Optional shell integration

Byte Core's experimental shell integration is an explicit, reversible layer for Bash and Zsh. It does not change the login shell, install packages, select a shell framework, inspect host identity, or infer deployment paths.

## Generic shell asset

[`shell/byte-shell.sh`](../shell/byte-shell.sh) is POSIX-compatible and safe to source repeatedly. It provides:

- `byte_status`, which reports only that the generic integration is active; and
- `byte_repo PATH`, which changes to an explicit directory and verifies that it is a Git worktree.

The asset contains no hostnames, usernames, addresses, inventory, credentials, or deployment paths. Unknown hosts receive the same generic behavior.

## Managed profile block

Planning requires an explicit existing home root, `bash` or `zsh`, and an absolute readable shell-asset path. Bash targets `.bashrc`; Zsh targets `.zshrc`. A plan records the exact original profile checksum and mode, generated block, expected result checksum, preconditions, postconditions, backout rule, and content-bound plan ID.

Apply refuses any profile change after planning. It creates a mode-`0600` backup under `.byte-backups/` before atomically replacing the profile while preserving its prior mode. Existing content is retained byte-for-byte. Reapplying an exact plan is idempotent.

Removal has a separate read-only planning phase and a distinct removal backup. It accepts exactly one well-formed Byte block, preserves all unrelated content byte-for-byte, and restores profile absence when installation created a previously missing profile. Missing, duplicate, malformed, stale, or altered blocks are refused.

Plan and backup files contain exact local paths and profile content or copies. They are private local artifacts and must not be committed.

## Optional Zsh syntax highlighting

Syntax highlighting is never a dependency. A Zsh installation plan may include an explicit absolute `--syntax-highlighting` file selected by the operator. Byte verifies that file exists and then adds it to the exact managed block. Omitting the option produces no syntax-highlighting behavior.

Bash plans reject this option. Byte does not locate, download, install, upgrade, configure, or remove a syntax-highlighting package.

## Current boundary

This is an internal bootstrap proof, not a supported installed shell product. It does not customize prompts, discover repositories, execute inventory commands, install completions, alter shell history, or modify appliance hosts.
