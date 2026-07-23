# Byte lifecycle command contract

Byte Core uses one `byte` command for lifecycle operations. The command separates checking, planning, applying, verifying, and backing out changes so that read-only discovery cannot silently become mutation.

The current bootstrap implements `check`, the initial deployment lifecycle, and experimental exact-plan installation and update apply and verification proofs. Destructive removal, the top-level update workflow, and diagnostics remain reserved design commitments, not functional capabilities.

## Grammar

```text
byte [--help]
byte check [--format text|json]
byte init --deployment-root ABSOLUTE_PATH
byte plan init --deployment-root ABSOLUTE_PATH
byte plan install --artifact-root ABSOLUTE_PATH --core-root ABSOLUTE_PATH --state-root ABSOLUTE_PATH --core-version VERSION
byte plan update --manifest ABSOLUTE_PATH --artifact-root ABSOLUTE_PATH --core-version NEWER_VERSION
byte plan remove --manifest ABSOLUTE_PATH [--preserve-root ABSOLUTE_PATH]
byte apply --plan PLAN.json [--format text|json]
byte verify --plan PLAN.json [--format text|json]
byte remove --deployment-root ABSOLUTE_PATH [--format text|json]

# Reserved; not implemented
byte update
byte doctor
```

Unknown commands and unsupported options are usage errors. A reserved command must fail clearly; it must not perform a partial or substitute operation.

## Exit statuses

| Status | Name | Meaning |
| ---: | --- | --- |
| 0 | success | The requested operation completed and its stated postconditions hold. |
| 2 | usage | The command, option, or argument is invalid. |
| 3 | unsupported | The command is unavailable or the detected environment is outside the current support boundary. |
| 4 | invalid-input | Deployment-owned input failed validation. |
| 5 | refused | Safety or ownership checks refused the requested operation. |
| 6 | verification-failed | An applied operation did not satisfy its expected postconditions. |
| 7 | recovery-required | An interrupted or failed mutation requires explicit recovery. |
| 70 | internal-error | Byte Core could not complete the operation because of an internal failure. |

Commands may add structured error codes without changing these process-level categories. Success is never reported solely because a process ran; the command must validate its stated result.

## Output contract

Human-readable text is the default. `--format json` emits one JSON object with stable field names for tests and automation. JSON output is UTF-8, uses deterministic key ordering, and is written to standard output. Usage and internal failures use standard error.

Output must not include credentials, environment-variable contents, usernames, home-directory paths, private inventory, arbitrary command output, or inferred deployment facts.

## `byte check`

`byte check` is read-only environment discovery. It checks:

- Python is within the currently tested 3.11 through 3.14 range;
- the operating system is macOS or Linux;
- the process environment is POSIX-compatible;
- the machine architecture can be normalized;
- the complete host is an approved v0.1 target (macOS 15 or 26 on `arm64`, or Ubuntu 24.04 on `linux/x86_64`); and
- Git is available and reports a parseable version.

The command does not:

- create, edit, rename, or remove files;
- read deployment configuration or canonical deployment documents;
- inspect shell profiles;
- execute Git operations against a repository;
- access the network;
- resolve credentials;
- collect environment variables or arbitrary command output; or
- claim that installation, initialization, updates, or removal are available.

A supported check returns status 0. A recognized but currently unsupported environment returns status 3 with every check result still shown. An unexpected internal failure returns status 70 with a sanitized error.

The [v0.1 support matrix](support-matrix.md) records the exact operating-system, architecture, runtime, shell, automated-evidence, and manual-evidence boundary. A recognized operating system is not sufficient by itself to claim host support.

Zsh and shell enhancements are optional on Linux and are not environment-check prerequisites. Byte does not install a shell or modify shell profiles as part of `check`, installation, or initialization.

## Initialization lifecycle

`byte plan init` is read-only and emits a deterministic JSON plan to standard output. The operator may redirect that output to a private local plan file. A plan binds its schema, operation, absolute deployment root, exact relative targets, expected SHA-256 content digests, preconditions, postconditions, backout actions, and plan ID.

`byte apply` accepts only a valid, untampered plan. It re-derives the approved starter content, validates the plan ID and hashes, creates a previously absent deployment root, and creates every file exclusively. It never overwrites an existing path. An exact replay succeeds only when verification proves the existing deployment still matches the plan; any conflicting state is refused.

`byte verify` checks the exact file set and hashes, parses the identity-neutral TOML skeleton, and runs canonical-document validation.

`byte init` is the guided human interface over the same engine. It displays the deployment root, plan ID, exact created files, and backout boundary. Mutation begins only after the operator types the full plan ID.

If apply fails, Byte removes only files created by that invocation whose content still matches the plan. If a created file changed or safe cleanup is otherwise impossible, Byte preserves the remaining state and returns `recovery-required` rather than deleting ambiguous content.

The initial configuration contains only `schema_version = 1`; it does not invent deployment identity or infrastructure facts. The four copied canonical documents become deployment-owned immediately.

Plan files contain exact local target paths and are private local artifacts. They must not be committed to the public repository.

## Removal boundary

The current bootstrap has no installed Core integration: initialization creates only deployment-owned configuration and canonical documents. Accordingly, `byte remove` currently performs a read-only preservation check. It validates the explicit deployment root, configuration schema, and canonical documents; removes nothing; and reports `core_integration_absent`.

Configuration, canonical documents, operator-added files, and unrelated content remain byte-for-byte unchanged. Missing, symbolic-link, malformed, or ambiguous deployment roots are refused. Future installation work must extend removal through an exact reviewed plan and a Core-owned installation manifest before any deletion is authorized.

## Installation lifecycle

`byte plan install` is read-only. Its output can be passed unchanged to `byte apply` and `byte verify`. `byte plan remove` remains read-only and no destructive removal apply exists.

An install plan inventories a complete, bounded artifact tree; records each relative path, SHA-256 digest, and executable/non-executable mode; targets an immutable `releases/VERSION` directory; and embeds a checksummed installation manifest. Core and state roots are explicit, absolute, non-overlapping paths.

The manifest owns only Core release files and Byte-generated state. It records the manifest schema, Core version, active state, logical roots, release path, artifact digest, managed files, generated state paths, and directories that may be removed only when empty. It must not contain deployment configuration, canonical documents, credentials, inventory, or copied deployment truth.

Install apply reloads and validates the bounded plan, re-scans the artifact, creates absent Core and state roots exclusively, verifies the immutable release, publishes an immutable manifest generation and compatibility copy, and atomically publishes checksummed `active.json` last. A journal supports conservative pre-activation cleanup. Ambiguous cleanup or any post-activation failure returns recovery-required and preserves state for inspection.

Install verification requires the exact manifest, activation metadata, release paths, hashes, and modes. Exact apply replay reports `already_installed`; conflicting existing roots are refused.

A removal plan accepts only an active, integrity-valid manifest. It re-reads every managed file and refuses missing, modified, mode-changed, symbolic-link, or escaped targets. Its removal list comes exclusively from the manifest. Explicit preservation roots must not overlap Core-owned paths and are recorded as postconditions.

Plan output contains exact local paths and is a private local artifact. This slice does not implement removal apply behavior, artifact signing, operating-system defaults, privilege escalation, or release provenance.

## Experimental update lifecycle

`byte plan update` is read-only. It requires a fully verified active installation and a strictly newer semantic version, inventories the new artifact into a fresh immutable release target, and records the exact manifest and activation transition. The existing release is preserved as the backout target. Dirty Core files, altered activation state, existing target releases, same-version replacement, and downgrade requests are refused.

The planner never reads or writes deployment-owned content. An exact update plan may be passed to `byte apply` and `byte verify`. Apply re-verifies both artifacts, creates and verifies the new release, preserves checksum-addressed current and next manifests, re-verifies the backout release, and atomically replaces `active.json` as the commit point. `installation.json` is only a compatibility copy.

Before activation, failure cleanup removes only unchanged paths created by the invocation. After activation, Byte restores the prior activation only when the attempted activation is unchanged and the prior immutable manifest and release still verify; it preserves the new release. Any ambiguity returns recovery-required with the operation journal intact.

This proof does not migrate configuration, fetch or authenticate releases, garbage-collect releases, or implement the reserved top-level `byte update`. It is not a supported installed command-line interface.

## Reserved lifecycle behavior

- `update` will use the versioned update and rollback contract.
- `doctor` will construct minimal local diagnostics under the privacy contract.

These reserved descriptions do not imply implementation.
