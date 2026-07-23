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

## Reliability and transport boundary

Byte-owned commands can construct a report when they retain control after a documented error. A hard interpreter, operating-system, or Codex process failure may prevent report creation. The current bootstrap has no external watcher and does not claim hard-crash capture.

No mode performs network access. GitHub issue creation, authentication, retry policy, rate limiting, exact outbound review, and preservation of transmitted Markdown belong to the separately reviewed transport contract. Byte Care never deploys a fix.
