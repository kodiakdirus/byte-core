# Byte Core v0.1 release checklist

Byte Core 0.1.0 remains blocked until every automated check and every supported-platform manual record passes. This checklist is repeatable; it does not authorize a tag by itself.

## Candidate construction

From a clean, reviewed `main` commit:

```text
python3 scripts/build_release_artifact.py --version 0.1.0 --output /absolute/new/byte-core-0.1.0
python3 scripts/check_v01_release.py --artifact /absolute/new/byte-core-0.1.0 --evidence release/v0.1/manual-evidence.json
python3 scripts/package_release_candidate.py --artifact /absolute/new/byte-core-0.1.0 --output /absolute/new/byte-core-0.1.0.tar.gz
```

The builder accepts a new absolute output directory, copies only its explicit public source set, normalizes file modes, and writes a checksummed `release.json`. Rebuilding at another path must produce identical file bytes.

The second command validates descriptor integrity, scans the complete artifact for privacy findings, and validates the manual-evidence ledger. It deliberately succeeds while evidence is pending so pull-request CI can prove the automated candidate. The packager accepts only that complete descriptor-bounded, privacy-clean artifact and emits a normalized archive plus its SHA-256. Repackaging identical artifact bytes must produce an identical archive.

The final release command adds `--require-complete` and must remain blocked until every platform record and the independent fresh-user review pass.

## Automated gate

- Full unit suite passes on every configured operating-system/Python combination.
- Bash launcher smoke tests pass on all targets; native Zsh smoke tests pass on macOS.
- Canonical document validation and invalid/future schema refusal tests pass.
- Install, update, removal, shell, diagnostics, reviewed-report, offline, privacy, and interruption tests pass.
- Candidate descriptor and every managed file digest verify.
- Complete candidate privacy scan passes with no adapter error or active finding.
- `git diff --check` passes and the candidate is built from a clean reviewed commit.

CI configuration is not evidence by itself. Record the URLs and conclusions of passing required workflow runs in the release review before tagging.

## Manual platform record

Follow the exact [deployment-candidate testing guide](deployment-testing.md). Copy the matching template from [`release/v0.1/evidence/`](../release/v0.1/evidence/) for each supported target. Use only fresh fictional deployment data. Include the tested commit and candidate archive SHA-256, then record:

```text
# TARGET manual evidence

## Installation
Commands, expected result, and observed result.

## Verification
Commands, expected result, and observed result.

## Backout
Commands, expected result, and observed result.

## Preservation
Fictional deployment-owned sentinel setup and byte-for-byte result.

## Offline
How network independence was established and the observed result.

## Limitations
Platform-specific limitations or `None observed`.
```

Change that target’s ledger status to `passed` only after the record is reviewed. Its evidence object must contain `completed_on`, the exact 40-character `commit_sha`, and the record’s relative path from `release/v0.1/`.

## Fresh-user review

A reviewer who did not implement the feature follows the README and public docs without private assistance and completes the [fresh-user review template](../release/v0.1/evidence/fresh-user-review-template.md). Record unclear, missing, or assumed steps as release blockers and fix them before repeating the review. The `fresh_user_review` ledger entry remains `pending` until that record is reviewed.

## Final tag gate

1. Confirm all required PRs are merged and every v0.1 issue criterion is resolved.
2. Confirm `main` is clean and synchronized with `origin/main`.
3. Build a fresh candidate and run the gate with `--require-complete`.
4. Review workflow URLs, manual records, known limitations, and repository/artifact privacy results.
5. Resolve the exact `main` commit and create immutable tag `v0.1.0` only after approval.

Never move or reuse a published tag. Back out a repository mistake with a reviewed revert; correct a released version with a later version.
