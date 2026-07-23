# Core installation manifest

Byte Core installation planning separates Core-managed program files, Byte-generated installation state, and deployment-owned truth.

> Byte Core owns behavior and structure; each deployment owns identity and truth.

## Logical layout

- `CORE_ROOT/releases/VERSION/` contains immutable files from one reviewed Core artifact.
- `STATE_ROOT/installation.json` contains the Byte-generated installation manifest.
- `STATE_ROOT/manifests/SHA256.json` preserves immutable manifest generations.
- `STATE_ROOT/active.json` is checksummed activation metadata for the installed release.
- `DEPLOYMENT_ROOT` remains separate and is never a Core removal target.

The current bootstrap requires explicit absolute roots. It can apply and verify an exact install plan, but it does not select operating-system defaults, elevate privileges, authenticate release provenance, or delete files.

## Manifest contract

Manifest schema 1 records the Core version, active state, Core and state roots, relative release directory, aggregate artifact digest, managed relative paths, per-file SHA-256 digests and modes, generated state paths, removable directories, and a checksum over the complete unsigned manifest.

Paths are normalized, relative within the release where required, unique, and free of parent traversal. Existing parent aliases are resolved before a plan records absolute paths, which accommodates canonical operating-system aliases such as macOS `/var` to `/private/var`. A managed target that is itself a symbolic link remains forbidden. Core and state roots cannot overlap. Files use only the initial executable (`0700`) or non-executable (`0600`) modes.

The checksum detects corruption or accidental editing. It does not authenticate a release or replace future signing and provenance requirements.

The manifest must not include deployment configuration, canonical documents, credentials, inventory, environment values, or copies of deployment-owned content.

## Install planning

Planning reads a bounded artifact directory without following symbolic links. It fails closed on missing, empty, oversized, unsupported, or changing inputs. The resulting deterministic plan lists each source-relative file and exact versioned target, its digest and mode, the proposed active manifest, preconditions, postconditions, backout rule, and content-bound plan ID.

Planning writes nothing. The plan is a private local artifact because it contains exact paths.

## Install apply and verification

`byte apply --plan PLAN.json` accepts an untampered install plan, re-scans the bounded artifact, and requires previously absent Core and state roots. It creates every release path exclusively, verifies file hashes and exact modes, publishes an immutable manifest generation and the compatibility `installation.json` copy, and atomically publishes `active.json` last as the commit point. It never reads or writes a deployment root.

An operation journal at `STATE_ROOT/operations/PLAN_ID.json` records only generic transaction state and exact Core-owned paths. Before activation, failure cleanup removes only unchanged paths created by that invocation. Changed or otherwise ambiguous partial state is preserved with the journal and reported as recovery required. After activation, Byte never guesses at rollback; any failed final verification also preserves the journal for inspection.

Exact replay succeeds only when the plan ID, active metadata, manifest, complete release tree, hashes, and modes all match. `byte verify --plan PLAN.json` performs the same full verification without mutation. Unexpected files, directories, links, metadata changes, or mode drift fail verification.

## Removal planning

Removal planning accepts only an active, checksummed manifest. It verifies every managed file still exists at its exact contained path with the recorded digest and mode. A missing, changed, linked, or ambiguous managed path refuses the plan.

The removal list is derived exclusively from manifest-owned paths. Directories are eligible only when the manifest names them and a future apply operation proves they are empty. The manifest itself is removed last. Explicit preservation roots must not overlap Core-managed paths and are recorded as postconditions.

Destructive removal remains planning-only. Removal apply, automated interrupted-operation recovery, artifact signing, platform defaults, and privilege elevation require later reviewed slices.

## Update planning

`byte plan update` accepts the canonical active installation manifest and an explicit local release-artifact root. Before planning, it verifies the manifest checksum, active metadata, current release path set, per-file hashes, and exact modes. Dirty, missing, linked, ambiguous, or inactive current state is refused.

The artifact must contain a schema-1 `release.json`. This package metadata is not installed as a Core file. It binds the Core version, inclusive configuration-schema compatibility range, explicit migration declaration, release-notes path, complete deployable file inventory with hashes and modes, aggregate artifact checksum, and checksum of the descriptor itself. The current proof accepts only configuration schema 1 and the explicit declaration `migration: "none"`. Missing declarations, required migrations, incompatible ranges, unlisted content, missing release notes, and checksum or mode mismatches fail closed.

The planner derives the version from the verified descriptor, inventories the bounded artifact, and targets a previously absent immutable `releases/VERSION` directory under the existing Core root. Its deterministic plan embeds the descriptor checksum, every create action, the complete next manifest, the current activation checksum, the intended next activation fields, and the previous release and manifest checksum as the exact backout target. The activation transition uses the literal `$plan_id` marker because the final activation metadata binds to the resulting plan ID; this avoids a circular plan checksum.

Planning does not create the new release, replace the manifest, change activation, remove the previous release, read deployment content, or perform schema migration. Apply likewise has no deployment-root input, and tests preserve a separate fictional deployment tree byte-for-byte. Downgrades and same-version replacements are not updates and are refused.

## Experimental update apply and verification

The internal bootstrap can reload an exact update plan through `byte apply --plan PLAN.json`. It re-verifies the current activation, manifest, release, and new artifact before mutation; creates the new release exclusively; verifies it; preserves immutable current and next manifest generations; re-verifies the previous release; and atomically replaces `active.json` as the sole activation commit point. `installation.json` is refreshed afterward as a compatibility copy, not activation authority.

Failure before activation removes only unchanged paths created by that invocation. After activation, automatic backout occurs only when the activation record still exactly matches the attempted update and the previous immutable manifest and release still verify. Backout atomically restores the prior activation and compatibility copy while preserving the new release. Ambiguous state is left with the journal and reported as recovery required.

Exact replay reports `already_updated` only after the new activation, immutable manifest, compatibility copy, complete new release, and preserved previous release all verify. `byte verify --plan PLAN.json` performs the same proof without mutation. Descriptor and artifact checksums provide integrity, not publisher authentication or tagged-release provenance. This experimental interface does not migrate deployment configuration, fetch releases, verify signatures, garbage-collect old releases, implement the reserved top-level `byte update`, or constitute a supported installed CLI.
