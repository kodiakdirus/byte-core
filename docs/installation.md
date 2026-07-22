# Core installation manifest

Byte Core installation planning separates Core-managed program files, Byte-generated installation state, and deployment-owned truth.

> Byte Core owns behavior and structure; each deployment owns identity and truth.

## Logical layout

- `CORE_ROOT/releases/VERSION/` contains immutable files from one reviewed Core artifact.
- `STATE_ROOT/installation.json` contains the Byte-generated installation manifest.
- `DEPLOYMENT_ROOT` remains separate and is never a Core removal target.

The current bootstrap requires explicit absolute roots. It does not select operating-system defaults, install files, elevate privileges, activate a release, or delete files.

## Manifest contract

Manifest schema 1 records the Core version, active state, Core and state roots, relative release directory, aggregate artifact digest, managed relative paths, per-file SHA-256 digests and modes, removable directories, and a checksum over the complete unsigned manifest.

Paths are normalized, relative within the release where required, unique, and free of parent traversal. Core and state roots cannot overlap. Files use only the initial executable (`0700`) or non-executable (`0600`) modes.

The checksum detects corruption or accidental editing. It does not authenticate a release or replace future signing and provenance requirements.

The manifest must not include deployment configuration, canonical documents, credentials, inventory, environment values, or copies of deployment-owned content.

## Install planning

Planning reads a bounded artifact directory without following symbolic links. It fails closed on missing, empty, oversized, unsupported, or changing inputs. The resulting deterministic plan lists each source-relative file and exact versioned target, its digest and mode, the proposed active manifest, preconditions, postconditions, backout rule, and content-bound plan ID.

Planning writes nothing. The plan is a private local artifact because it contains exact paths.

## Removal planning

Removal planning accepts only an active, checksummed manifest. It verifies every managed file still exists at its exact contained path with the recorded digest and mode. A missing, changed, linked, or ambiguous managed path refuses the plan.

The removal list is derived exclusively from manifest-owned paths. Directories are eligible only when the manifest names them and a future apply operation proves they are empty. The manifest itself is removed last. Explicit preservation roots must not overlap Core-managed paths and are recorded as postconditions.

This contract authorizes read-only planning only. Installation, activation, destructive removal, rollback, and interrupted-operation recovery require a later reviewed implementation.
