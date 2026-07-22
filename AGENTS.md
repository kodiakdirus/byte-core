# Repository guidance

Byte Core is a public repository. Treat every file, commit, issue, pull request, diagnostic, and generated artifact as potentially permanent public history.

## Ownership boundary

> Byte Core owns behavior and structure; each deployment owns identity and truth.

Core-managed content may define generic behavior, structure, validation, schemas, and fictional examples.

Deployment-owned identity, inventory, configuration, credentials, documentation, and observed state must remain outside version-controlled Core content unless a future public design explicitly defines a safe example or interface.

Unknown deployment facts remain unknown. Do not invent hostnames, addresses, paths, services, credentials, device state, or other infrastructure details.

## Public-repository safety

Do not import, copy, adapt, or reconstruct private repository or deployment:

- files or Git history;
- prompts or transcripts;
- inventory or configuration;
- credentials or environment values;
- logs, diagnostics, or reports; or
- operating instructions or deployment-specific knowledge.

Create public material from approved public requirements and fresh fictional examples. Redaction does not make private source material appropriate for import.

Ignored paths are defense-in-depth, not authorized storage for sensitive information.

## Work discipline

Before changing state:

1. Check the current repository and target state.
2. Plan the exact scope, validation, and backout.
3. Apply only the approved change.
4. Verify the result with explicit evidence.
5. Preserve or execute a safe backout when required.

Keep destructive targets explicit. Preserve unrelated user work. Do not claim success without validation evidence.

Separate repository planning from implementation when a checkpoint requires review. Do not expand work into adjacent issues without approval.

## Current bootstrap boundary

Byte Core has no functional release or command-line interface yet. Do not claim that `byte check`, installation, update, validation, removal, or other planned behavior is implemented.

The complete Codex integration and authority contract belongs to [issue #12](https://github.com/kodiakdirus/byte-core/issues/12). This file provides only the minimal public-repository safety baseline until that design is approved.
