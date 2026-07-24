# Contributing to Byte Core

Byte Core is currently in a pre-alpha, sole-contributor bootstrap stage. Its architecture, tooling, tests, review practices, and release process are still being established.

Future participation is welcome. This document describes the current baseline without promising a mature contribution process that does not yet exist.

## Before proposing a change

Check the open [issues](https://github.com/kodiakdirus/byte-core/issues) for relevant design or implementation work.

For substantial changes, architecture decisions, or work already covered by an issue, discuss the intended scope before implementation and link the change to the relevant issue. Small corrections do not require a dedicated issue unless coordination would materially reduce risk or duplicated work.

Keep changes focused. Do not expand an issue into adjacent implementation work without explicit agreement.

## Contribution licensing

Byte Core is licensed under the [Apache License 2.0](LICENSE).

Contributors retain copyright ownership of their contributions. By intentionally submitting a contribution for inclusion in Byte Core, you agree that the contribution is submitted under Apache-2.0.

Byte Core currently requires:

- no Contributor License Agreement;
- no copyright assignment; and
- no Developer Certificate of Origin sign-off.

The DCO decision may be reconsidered if outside contributions begin. Any future change will be documented before it becomes a contribution requirement.

## Core and deployment ownership

The project follows this rule:

> Byte Core owns behavior and structure; each deployment owns identity and truth.

Core contributions may define generic behavior, safe structure, validation, schemas, and fictional examples. They must not embed or assume a real deployment's identity, inventory, credentials, addresses, paths, service locations, or current state.

Updates must not overwrite deployment-owned configuration or canonical deployment documentation.

## Privacy review

Byte Core is a public repository. Before submitting any change, inspect both its content and the commits being published.

Do not submit:

- credentials, tokens, keys, cookies, or environment values;
- real hostnames, addresses, usernames, account identifiers, or filesystem paths;
- real infrastructure inventory or configuration;
- prompts, transcripts, terminal output, or broad logs;
- private diagnostic data or unreviewed reports;
- files, history, instructions, or examples copied from a private repository or deployment; or
- redacted private material when a fresh fictional example can be created instead.

Use reserved documentation domains and address ranges for examples. Construct minimal fictional fixtures rather than collecting real data and attempting to redact it.

Ignore rules are defense-in-depth only. An ignored path is not approved storage for secrets or private deployment state.

## Validation

Run validation appropriate to the files and behavior changed.

At minimum:

- inspect the complete diff;
- check for whitespace and text-normalization errors;
- verify every affected link;
- confirm tests relevant to the change pass, when such tests exist;
- perform a privacy and leakage review; and
- report the evidence used to conclude the change is correct.

Do not claim success based only on implementation. Distinguish check, plan, apply, verify, and backout phases when the change has operational effects.

The repository has unit, privacy, support-matrix, and deterministic candidate-artifact gates. Manual supported-platform evidence remains incomplete. Do not represent configured CI or pending manual evidence as completed validation.

## Versioning, branches, and releases

Byte Core uses [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html) for release identifiers. Release versions have the form `MAJOR.MINOR.PATCH`, and Git tags add a leading `v`.

While the major version is `0`, Byte Core is in initial development. Interfaces, behavior, documentation, and repository structure may change incompatibly. A pre-1.0 version does not promise stability, backward compatibility, production support, or a maintenance period.

### Branches

- `main` is the canonical integration branch.
- The active repository ruleset prevents deletion and non-fast-forward updates to `main`.
- The current bootstrap workflow permits reviewed, validated changes to reach `main` through normal fast-forward updates.
- Optional `agent/*` branches are short-lived work branches. They do not represent releases or supported versions and should be removed after their reviewed changes are integrated or abandoned.
- Byte Core does not currently define long-lived development, release, or maintenance branches.

### Tags and GitHub releases

- Release tags use the form `vMAJOR.MINOR.PATCH` and identify an exact reviewed commit on `main`.
- A tag is created only after the stated milestone criteria have been reviewed and validation evidence has been recorded.
- `v0.0.1` is the published bootstrap pre-release and has no functional assets.
- `v0.1.0` is created only after the repeatable release gate and its manual platform evidence are complete and approved.
- A published tag must not be moved or reused. Corrections are made through new commits and, when appropriate, a later version.
- A GitHub release may be created manually from an approved tag to summarize its contents and known limitations.
- A GitHub release does not by itself imply package publication, compatibility, production support, or ongoing maintenance.

Byte Core does not currently define automatic tag publication, package-manager publication, artifact signing, a changelog process, or a support policy. The deterministic candidate builder and release gate do not create or publish a release.

### Validation and backout

Before creating a tag or GitHub release:

1. Confirm the worktree is clean and `main` is synchronized with `origin/main`.
2. Resolve and record the exact commit to be tagged.
3. Confirm the milestone criteria are satisfied.
4. Run the applicable tests and repository checks that exist at that time.
5. Complete a privacy and leakage review.
6. Record the validation evidence and known limitations.

Back out an incorrect repository change with a normal reviewed revert or follow-up commit. Do not rewrite `main` history or move a published tag. If a published version requires correction, document the problem and use a later version for the correction.

## Changes and review

Explain:

- what changed;
- why it changed;
- which issue or decision it implements, when applicable;
- how it was validated;
- any privacy or compatibility implications; and
- how the change can be backed out.

Review requirements may evolve as the project and contributor base grow.

## Security reports

Do not disclose an unpatched vulnerability in a public issue or pull request. Follow [SECURITY.md](SECURITY.md) to submit a private vulnerability report.
