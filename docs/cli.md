# Byte lifecycle command contract

Byte Core uses one `byte` command for lifecycle operations. The command separates checking, planning, applying, verifying, and backing out changes so that read-only discovery cannot silently become mutation.

Only `byte check` is implemented in the current bootstrap. The other command names in this contract are reserved design commitments, not functional capabilities.

## Grammar

```text
byte [--help]
byte check [--format text|json]

# Reserved; not implemented
byte init
byte plan
byte apply
byte verify
byte update
byte remove
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

- Python is version 3.11 or newer;
- the operating system is macOS or Linux;
- the process environment is POSIX-compatible;
- the machine architecture can be identified without declaring unapproved architecture support; and
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

## Reserved lifecycle behavior

- `init` will create deployment-owned configuration and starter documents through an explicit reviewed flow.
- `plan` will show exact intended targets and backout steps without mutation.
- `apply` will perform only an exact reviewed plan.
- `verify` will prove documented postconditions.
- `update` will use the versioned update and rollback contract.
- `remove` will reverse Core integration while preserving deployment-owned data.
- `doctor` will construct minimal local diagnostics under the privacy contract.

These descriptions do not imply implementation.
