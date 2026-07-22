# Canonical deployment documents

Byte Core uses four deployment-owned Markdown documents as the canonical knowledge model for a self-managed environment. Core supplies generic starter templates and read-only validation; operators own every deployed copy and its contents.

> Byte Core owns behavior and structure; each deployment owns identity and truth.

## Document roles

- `manifest.md` records what is understood to exist now. It is authoritative for declared inventory and relationships, but it does not prove live state.
- `runbook.md` records reviewed operating and recovery procedures. It is authoritative for intended procedure, not for whether a procedure was performed.
- `audit-log.md` records what changed, when, why, and how the result was validated. It does not replace the current-state manifest.
- `notebook.md` records durable lessons, quirks, preferences, and questions that do not yet belong in another canonical source.

When documents disagree, use each document only for its stated authority. Resolve the disagreement through an explicit reviewed change; do not silently infer which deployment fact is correct.

## Machine-readable marker

The first line of each canonical document is exactly:

```text
<!-- byte-core-document: schema=1 role=ROLE -->
```

`ROLE` is one of `manifest`, `runbook`, `audit-log`, or `notebook`. All four documents in a set use the same supported positive schema version. Filenames and roles are unique and fixed for schema 1.

The marker describes document structure only. It must not contain a deployment name, host identity, address, path, credential, or observed state.

## Validation boundary

The internal validator is read-only. It checks required files, exact role markers, synchronized supported schema versions, unique roles, expected top-level headings, and relative Markdown links between files in the document root.

Validation does not execute commands, access the network, expand environment variables, follow symbolic links, inspect targets outside the document root, or establish that documented infrastructure facts are true.

## Ownership and updates

Starter templates under `templates/canonical/` are Core-managed examples. A future explicit initialization flow may copy them into a deployment. The copied files immediately become deployment-owned and routine Core installation or update must never overwrite them.

Operators update the manifest when declared current state changes, the runbook when an approved procedure changes, the audit log after a reviewed action and its validation, and the notebook when durable context does not yet change an authoritative source.
