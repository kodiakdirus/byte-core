# Privacy classification and leakage prevention

Byte Core is developed in public and may run near private deployment data. This document defines the privacy boundary for public development, examples, diagnostics, reports, and releases.

> Byte Core owns behavior and structure; each deployment owns identity and truth.

This is a design contract. The current bootstrap provides internal privacy-scanning, allowlisting, redaction, bounded input-adapter primitives, and a CI release-artifact gate. It does not claim that an end-user CLI, automatic reporting, or a complete packaging pipeline is implemented.

## Security objective

Public Core artifacts must not disclose deployment identity, inventory, configuration, credentials, documentation, observed state, or private source material.

Byte Core minimizes what it reads and records. It constructs small, purpose-specific diagnostic objects from approved fields instead of collecting broad data and attempting to redact it afterward.

Redaction reduces exposure in an already authorized local artifact. It does not authorize collection, retention, publication, or transmission.

## Data classes

Every value handled by a privacy-sensitive Core interface belongs to one of five classes. When a value could belong to more than one class, the most restrictive class applies.

### Public

Public data is eligible for the public repository and release artifacts after normal public review. Its review must not require access to private deployment context.

Examples include Core source code, schemas, generic documentation, and fresh fictional examples that use reserved documentation values. A value is not public merely because it can be found on the internet or in a public service response.

### Reviewed diagnostic

Reviewed diagnostic data is a minimal, locally generated description prepared for a specific troubleshooting purpose. It may leave the machine only after local preview, privacy scanning, and explicit user review of the exact payload and destination.

Reviewed diagnostics contain only fields defined by an approved diagnostic schema. They do not contain arbitrary logs, files, command output, environment variables, prompts, or transcripts.

### Deployment-sensitive

Deployment-sensitive data identifies or describes a real deployment without necessarily being a credential.

This class includes real hostnames, usernames, addresses, filesystem paths, service locations, inventory, configuration, canonical documentation, topology, device state, and observed behavior. It remains deployment-owned and must not enter tracked Core content, public examples, release artifacts, or CI fixtures.

### Secret

Secret data grants access, proves identity, or protects another secret.

This class includes passwords, private keys, access tokens, session material, recovery codes, credential values, and unredacted authentication headers. Secrets must not be stored in Core configuration, diagnostics, reports, fixtures, logs, or repository history. Core may handle an opaque credential reference only where another approved contract defines it.

No allowlist entry may authorize a real secret.

### Prohibited-source

Prohibited-source data is material whose origin makes it unsuitable for public Core work even if individual values appear harmless or have been redacted.

This class includes private repository files or history, private prompts or transcripts, deployment inventory or configuration, credentials or environment values, logs, diagnostics, reports, operating instructions, and deployment-specific knowledge. Core contributors must not copy, adapt, reconstruct, summarize into fixtures, or sanitize this material for inclusion in public artifacts.

Public examples are created from approved public requirements and fresh fictional facts.

## Collection and reporting boundary

Byte Core does not upload prompts, transcripts, environment variables, arbitrary file contents, broad logs, or command output by default.

Automatic outbound reporting is unsupported for v0.1. Any future outbound report must provide all of the following before transmission:

1. a minimal, documented schema for the report;
2. local generation without network access;
3. a preview of the exact payload;
4. a privacy scan that does not echo matched values;
5. an explicit user review and approval of the payload and destination; and
6. a clear cancellation path that leaves the payload local.

Consent to one report does not authorize later reports, broader collection, or a different destination.

## Public examples and fixtures

Examples and tests use only newly created fictional identities and values reserved for documentation.

- DNS names use `example.com`, `example.net`, `example.org`, or the `.test` top-level domain.
- IPv4 addresses use the documentation networks `192.0.2.0/24`, `198.51.100.0/24`, or `203.0.113.0/24`.
- IPv6 addresses use `2001:db8::/32`.
- Paths are generic, platform-appropriate examples that do not reproduce a contributor or deployment path.
- Token-shaped and secret-shaped fixtures are synthetic and cannot authenticate to any service.

Unknown deployment facts remain unknown. Examples must not invent facts about a real deployment.

## Scanning contract

The local scanner inspects only explicitly selected inputs. Current internal adapters cover Core-public files, staged Git content, release artifact directories, and already-constructed diagnostic payloads. The repository CI runs the unit suite, scans the public documentation release path, and proves that the release gate rejects a runtime-generated synthetic leakage artifact. Future packaging workflows must pass their complete release artifact directory through the same gate before publication.

Scanner output must identify the rule, classification, and safe location needed for review without printing the discovered value. Context must be omitted or reduced to a non-sensitive structural description.

A finding fails closed until it is removed, replaced with a safe fictional value, or covered by an approved false-positive allowlist entry. A clean scan reduces risk but does not prove that content is safe or authorize publication.

## Redaction

Redaction helpers must be deterministic for a defined input and policy so repeated references can be compared without revealing the original value. Output must indicate the value class, not preserve unnecessary fragments of an identity, path, or secret.

Redaction occurs as close as possible to the approved source field. Raw values must not be copied into error messages, debug logs, exceptions, scanner output, or temporary reports.

When the source itself is prohibited, redaction is not an acceptable path to public use.

## False positives and allowlisting

Allowlisting is limited to verified false positives. Each entry must record:

- the narrow rule and exact safe scope;
- a justification based on public, independently reviewable facts;
- the reviewer and review date; and
- an expiry date or an explicit future review condition.

Entries must not use broad path, repository, file-type, or rule-wide exclusions when a narrower scope is possible. Expired entries fail closed. Allowlist changes are auditable public changes and receive the same review as the content they permit.

Real secrets and prohibited-source material cannot be allowlisted.

## Incident response

If sensitive material is found, stop publication and preserve the minimum evidence needed to identify affected artifacts without repeating the value.

Containment may include removing an unpublished local artifact, revoking a credential through its owner, or pausing a release. History rewriting, deletion of published metadata, and other irreversible remediation require an explicit, separately reviewed plan and approval. Redaction after exposure does not undo the exposure.

Incident notes intended for the public repository must contain only public facts and must not reproduce the sensitive value, private diagnostics, or deployment-specific detail.

## Review responsibilities

Authors verify the origin and classification of every added example, fixture, report, and generated artifact. Reviewers verify that collection is minimal, examples are independently fictional, exceptions are narrow, and the proposed destination is appropriate.

Ignore rules and local-only directories are defense in depth. They are not approved storage locations for sensitive or secret data.
