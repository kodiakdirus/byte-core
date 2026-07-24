# Byte Care local diagnostics

Byte Care is an opt-in, local-first diagnostic pipeline for Byte-owned failures. The v0.1 bootstrap constructs only a minimal allowlisted report and does not collect arbitrary context.

## Report schema

Schema 1 contains only:

- Byte Core version;
- component and lifecycle phase;
- documented error and process exit codes;
- normalized platform and architecture;
- Python version;
- configuration-schema version when already known; and
- a stable fingerprint derived from those fields.

Reports do not contain hostnames, usernames, home paths, deployment paths, configuration values, inventory, credentials, environment variables, logs, stack traces, prompts, transcripts, file content, or command output.

The stable fingerprint deduplicates the same bounded failure shape. It is not derived from deployment identity.

## Modes

Every invocation requires an explicit mode:

- `off` constructs no local artifact;
- `local-only` privacy-scans and stores the exact report locally;
- `ask-before-reporting` previews the exact report and local destination, then requires the full fingerprint before storage; and
- `automatic-sanitized` is recognized but refused because automatic outbound reporting is unsupported for v0.1.

There is no implicit or remembered consent. A choice for one invocation does not authorize a later report or a different destination.

## Local storage

The caller selects an explicit absolute report root. Byte creates it with mode `0700` when absent and creates reports exclusively with mode `0600`. The filename is the stable fingerprint. Exact repeats deduplicate; a conflicting existing target is refused.

The exact JSON is scanned before any report directory or file is created. Scanner findings never echo matched content. Reports and report roots are private local artifacts and must not be committed.

## Reviewed GitHub transport

The optional GitHub transport uses the installation owner's existing authenticated `gh` session. It accepts only the official `kodiakdirus/byte-core` repository and never stores or supplies a maintainer credential.

Planning authenticates `gh`, reads open issue bodies from the official repository, and searches for the exact fingerprint marker. It produces either a create action or a comment on the single matching open issue. Multiple matches are ambiguous and refused. Dry-run displays the exact repository, action, title, label, destination, and Markdown without issue mutation.

Submission requires the full report fingerprint again. Markdown is passed over standard input rather than command arguments and is saved locally with mode `0600` before the network mutation. Authentication and submission failures therefore retain the exact offline artifact. Successful state records the issue number and submission time. A one-hour minimum retry interval prevents retry storms, and a stale create action cannot be reused after that interval; the operator must plan again so an existing fingerprint becomes a comment.

The transport requests the documented `byte-care` label. GitHub ultimately authorizes issue creation or commenting using the user's account; Byte cannot grant that permission. Authentication, disabled issues, missing labels, insufficient repository permission, network failure, and GitHub refusal all fail without bundling or exposing credential material.

No report is sent automatically. GitHub submission is available only through exact text review and explicit fingerprint confirmation. The complete transmitted Markdown remains in the selected private transport root.

## Reliability boundary

Byte-owned commands can construct a report when they retain control after a documented error. A hard interpreter, operating-system, or Codex process failure may prevent report creation. The current bootstrap has no external watcher and does not claim hard-crash capture.

Local report modes perform no network access. Only the explicitly selected GitHub dry-run or submit path invokes `gh`; dry-run performs authenticated reads but no issue mutation. Byte Care never deploys a fix.
