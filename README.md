# Byte Core

> **Status: pre-alpha repository bootstrap**

Byte Core is an independent community project exploring a safe, friendly, Codex-centered framework for self-managed infrastructure.

The repository has established its public foundation and initial configuration-ownership boundary. Broader architecture, governance, and safety work remains in progress. No functional release or supported installed command-line interface exists yet. Installation and operational use are not currently supported.

## Architectural rule

> Byte Core owns behavior and structure; each deployment owns identity and truth.

Byte Core is intended to provide reusable behavior, validation, templates, and lifecycle tooling without embedding the identity or private state of any particular deployment.

## Safety principles

Byte Core is being designed to:

- keep secrets and private inventory out of version-controlled Core files;
- preserve deployment-owned configuration and documentation across updates;
- distinguish checking, planning, applying, verifying, and backing out changes;
- resolve exact targets before destructive operations;
- preserve fallback access before critical connectivity changes;
- avoid inventing infrastructure facts;
- require validation evidence before claiming success; and
- keep diagnostics and reporting opt-in, minimal, and reviewable.

Ignored files remain private local state, not safe storage for secrets. Ignore rules are defense-in-depth and do not authorize sensitive material to be placed in the repository.

## Current capabilities

At this bootstrap stage, Byte Core provides its public repository foundation, Apache License 2.0 licensing, initial repository-security baseline, a documented [configuration and ownership contract](docs/configuration.md), and a [canonical deployment-document contract](docs/canonical-documents.md). TOML 1.0 and Python 3.11 are the initial minimum configuration-format and runtime baseline; Python 3.11 through 3.14 form the current CI target.

The repository also contains an internal, read-only layered configuration resolver, canonical document validator, neutral starter templates, an [installation lifecycle contract](docs/installation.md), checksummed local release descriptors, experimental exact-plan install and update apply and verification proofs, [optional reversible Bash/Zsh integration](docs/shell-integration.md), [local-only Byte Care diagnostics](docs/byte-care.md), a read-only removal planner, and tests. These are experimental implementation proofs, not stable public APIs or supported user-facing tools.

The repository includes experimental `byte check` and initial deployment planning, initialization, application, and verification proofs. They are not yet packaged or installed as a supported public CLI. There is no installer, update, removal, shell integration, functional release, or supported operational deployment yet.

The intended v0.1 host and runtime boundary, automated evidence, and remaining manual evidence are published in the [support matrix](docs/support-matrix.md). The matrix defines release targets without changing the repository's pre-alpha support status.

## Planned direction

The current v0.1 direction includes:

- a thin POSIX-compatible launcher backed by Python 3;
- standard-library-first implementation with no third-party runtime dependencies by default;
- initial support targets for macOS 15 and 26 on Apple silicon and Ubuntu 24.04 on x86_64;
- initial shell integration for Bash and Zsh;
- layered, deployment-owned configuration and inventory;
- reversible install, update, validation, and removal behavior; and
- privacy-preserving diagnostics and issue reporting.

The initial lifecycle proof implements `check`, guided `init`, read-only initialization, installation, update, shell-integration, and removal planning, exact-plan initialization, installation, update, and shell apply/verification, guided local update checking/application, reversible shell removal, local-only diagnostics with optional reviewed GitHub issue transport, and a preservation-only Core `remove` check. Their [grammar, exit statuses, mutation boundaries, and recovery behavior](docs/cli.md) are documented. Remote update discovery, destructive Core removal, and automatic diagnostics remain unimplemented.

The first bootstrap release, [`v0.0.1`](https://github.com/kodiakdirus/byte-core/releases/tag/v0.0.1), is published as a pre-release with no release assets. It records the repository bootstrap and is not a functional Byte Core release.

## Non-goals for v0.1

Byte Core v0.1 is not intended to provide:

- full configuration management;
- automatic network discovery;
- credential storage;
- automatic remote infrastructure mutation;
- multi-administrator or centrally managed enterprise operation;
- a web interface;
- native PowerShell deployment;
- anonymous telemetry; or
- automatic patch deployment.

See [roadmap issue #1](https://github.com/kodiakdirus/byte-core/issues/1) for the maintained architecture and release roadmap.

## Contributing

Byte Core is currently in a sole-contributor bootstrap stage, but thoughtful future participation is welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing changes.

Do not submit secrets, real infrastructure inventory, private transcripts, or private diagnostic data.

## Security

Do not report unpatched vulnerabilities in a public issue. Follow [SECURITY.md](SECURITY.md) to report a vulnerability privately.

## License

Byte Core is licensed under the [Apache License 2.0](LICENSE).

Contributors retain ownership of their contributions. Intentional contributions are submitted under Apache-2.0 as described in [CONTRIBUTING.md](CONTRIBUTING.md).

## Independent project

Byte Core is an independent community project. It is not affiliated with or endorsed by OpenAI.
