# Core installation manifest

Byte Core installation planning separates Core-managed program files, Byte-generated installation state, and deployment-owned truth.

> Byte Core owns behavior and structure; each deployment owns identity and truth.

## Logical layout

- `CORE_ROOT/releases/VERSION/` contains immutable files from one reviewed Core artifact.
- `STATE_ROOT/installation.json` contains the Byte-generated installation manifest.
- `STATE_ROOT/active.json` is checksummed activation metadata for the installed release.
- `DEPLOYMENT_ROOT` remains separate and is never a Core removal target.

The current bootstrap requires explicit absolute roots. It can apply and verify an exact install plan, but it does not select operating-system defaults, elevate privileges, authenticate release provenance, or delete files.

## Manifest contract

Manifest schema 1 records the Core version, active state, Core and state roots, relative release directory, aggregate artifact digest, managed relative paths, per-file SHA-256 digests and modes, generated state paths, removable directories, and a checksum over the complete unsigned manifest.

Paths are normalized, relative within the release where required, unique, and free of parent traversal. Core and state roots cannot overlap. Files use only the initial executable (`0700`) or non-executable (`0600`) modes.

The checksum detects corruption or accidental editing. It does not authenticate a release or replace future signing and provenance requirements.

The manifest must not include deployment configuration, canonical documents, credentials, inventory, environment values, or copies of deployment-owned content.

## Install planning

Planning reads a bounded artifact directory without following symbolic links. It fails closed on missing, empty, oversized, unsupported, or changing inputs. The resulting deterministic plan lists each source-relative file and exact versioned target, its digest and mode, the proposed active manifest, preconditions, postconditions, backout rule, and content-bound plan ID.

Planning writes nothing. The plan is a private local artifact because it contains exact paths.

## Install apply and verification

`byte apply --plan PLAN.json` accepts an untampered install plan, re-scans the bounded artifact, and requires previously absent Core and state roots. It creates every release path exclusively, verifies file hashes and exact modes, publishes the checksummed manifest, and atomically publishes `active.json` last as the commit point. It never reads or writes a deployment root.

An operation journal at `STATE_ROOT/operations/PLAN_ID.json` records only generic transaction state and exact Core-owned paths. Before activation, failure cleanup removes only unchanged paths created by that invocation. Changed or otherwise ambiguous partial state is preserved with the journal and reported as recovery required. After activation, Byte never guesses at rollback; any failed final verification also preserves the journal for inspection.

Exact replay succeeds only when the plan ID, active metadata, manifest, complete release tree, hashes, and modes all match. `byte verify --plan PLAN.json` performs the same full verification without mutation. Unexpected files, directories, links, metadata changes, or mode drift fail verification.

## Removal planning

Removal planning accepts only an active, checksummed manifest. It verifies every managed file still exists at its exact contained path with the recorded digest and mode. A missing, changed, linked, or ambiguous managed path refuses the plan.

The removal list is derived exclusively from manifest-owned paths. Directories are eligible only when the manifest names them and a future apply operation proves they are empty. The manifest itself is removed last. Explicit preservation roots must not overlap Core-managed paths and are recorded as postconditions.

Destructive removal remains planning-only. Removal apply, automated interrupted-operation recovery, artifact signing, platform defaults, and privilege elevation require later reviewed slices.
