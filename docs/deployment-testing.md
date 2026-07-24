# Byte Core 0.1.0 deployment-candidate testing

This guide is the public handoff for manual supported-platform testing. It uses a fresh checkout, a deterministic candidate artifact, disposable fictional roots, and exact plans. It does not install Byte into operating-system directories, require elevated privileges, or claim that 0.1.0 has been released.

Use only a disposable test environment. Do not substitute real deployment configuration, inventory, credentials, logs, or documentation.

## Supported test targets

- Ubuntu 24.04 LTS on `x86_64`, using Bash
- macOS 15 on Apple silicon (`arm64`), using Zsh
- macOS 26 on Apple silicon (`arm64`), using Zsh

Python 3.11 through 3.14 and Git must be available. Optional Zsh syntax highlighting is not part of this smoke test and is never installed by Byte.

## 1. Prepare a reviewed checkout

Clone the public repository, check out the exact commit under review, and confirm that the checkout is clean:

```text
git clone https://github.com/kodiakdirus/byte-core.git
cd byte-core
git checkout COMMIT_SHA
git status --short
python3 -m unittest discover -s tests
./bin/byte check --format json
```

Record the full `COMMIT_SHA`, Python version, Git version, operating-system version, architecture, and shell. `byte check` must report the expected target as supported. Do not continue on a different host and relabel the result.

## 2. Build and package the candidate

Create one new disposable root and build the unpacked artifact:

```text
BYTE_TEST_ROOT=$(mktemp -d)
python3 scripts/build_release_artifact.py \
  --version 0.1.0 \
  --output "$BYTE_TEST_ROOT/byte-core-0.1.0"
python3 scripts/check_v01_release.py \
  --artifact "$BYTE_TEST_ROOT/byte-core-0.1.0" \
  --evidence release/v0.1/manual-evidence.json
python3 scripts/package_release_candidate.py \
  --artifact "$BYTE_TEST_ROOT/byte-core-0.1.0" \
  --output "$BYTE_TEST_ROOT/byte-core-0.1.0.tar.gz"
```

Record the archive SHA-256 printed by the packager. On Ubuntu, independently verify it with `sha256sum`. On macOS, use `shasum -a 256`.

Extract the archive into a new directory and use the extracted copy for every remaining step:

```text
mkdir "$BYTE_TEST_ROOT/extracted"
tar -xzf "$BYTE_TEST_ROOT/byte-core-0.1.0.tar.gz" \
  -C "$BYTE_TEST_ROOT/extracted"
BYTE_CANDIDATE="$BYTE_TEST_ROOT/extracted/byte-core-0.1.0"
"$BYTE_CANDIDATE/bin/byte" --help
"$BYTE_CANDIDATE/bin/byte" check --format json
```

## 3. Initialize fictional deployment documents

Every plan is private local test state because it contains absolute paths.

```text
"$BYTE_CANDIDATE/bin/byte" plan init \
  --deployment-root "$BYTE_TEST_ROOT/deployment" \
  > "$BYTE_TEST_ROOT/init-plan.json"
"$BYTE_CANDIDATE/bin/byte" apply \
  --plan "$BYTE_TEST_ROOT/init-plan.json"
"$BYTE_CANDIDATE/bin/byte" verify \
  --plan "$BYTE_TEST_ROOT/init-plan.json"
```

Review the four generated canonical documents. Add one fictional deployment-owned sentinel line to `notebook.md`, then record its SHA-256 before lifecycle testing.

## 4. Install and verify Core in disposable roots

```text
"$BYTE_CANDIDATE/bin/byte" plan install \
  --artifact-root "$BYTE_CANDIDATE" \
  --core-root "$BYTE_TEST_ROOT/core" \
  --state-root "$BYTE_TEST_ROOT/state" \
  --core-version 0.1.0 \
  > "$BYTE_TEST_ROOT/install-plan.json"
"$BYTE_CANDIDATE/bin/byte" apply \
  --plan "$BYTE_TEST_ROOT/install-plan.json"
"$BYTE_CANDIDATE/bin/byte" verify \
  --plan "$BYTE_TEST_ROOT/install-plan.json"
"$BYTE_CANDIDATE/bin/byte" apply \
  --plan "$BYTE_TEST_ROOT/install-plan.json"
```

The first apply must report `installed`, verification must report `verified`, and replay must report `already_installed`.

## 5. Test reversible shell integration

Use a disposable home, not the tester's real profile:

```text
mkdir "$BYTE_TEST_ROOT/test-home"
BYTE_TEST_SHELL=zsh
```

Use `BYTE_TEST_SHELL=bash` for the Ubuntu target. Then run:

```text
"$BYTE_CANDIDATE/bin/byte" shell plan \
  --home-root "$BYTE_TEST_ROOT/test-home" \
  --shell "$BYTE_TEST_SHELL" \
  --shell-script "$BYTE_CANDIDATE/shell/byte-shell.sh" \
  > "$BYTE_TEST_ROOT/shell-plan.json"
"$BYTE_CANDIDATE/bin/byte" shell apply \
  --plan "$BYTE_TEST_ROOT/shell-plan.json"
"$BYTE_CANDIDATE/bin/byte" shell verify \
  --plan "$BYTE_TEST_ROOT/shell-plan.json"
"$BYTE_CANDIDATE/bin/byte" shell plan-remove \
  --home-root "$BYTE_TEST_ROOT/test-home" \
  --shell "$BYTE_TEST_SHELL" \
  > "$BYTE_TEST_ROOT/shell-remove-plan.json"
"$BYTE_CANDIDATE/bin/byte" shell remove \
  --plan "$BYTE_TEST_ROOT/shell-remove-plan.json"
"$BYTE_CANDIDATE/bin/byte" shell verify \
  --plan "$BYTE_TEST_ROOT/shell-remove-plan.json"
```

Confirm the profile is absent again when Byte created it. If testing an existing fictional profile, confirm unrelated content is byte-for-byte unchanged.

## 6. Remove Core and prove preservation

```text
"$BYTE_CANDIDATE/bin/byte" plan remove \
  --manifest "$BYTE_TEST_ROOT/state/installation.json" \
  --preserve-root "$BYTE_TEST_ROOT/deployment" \
  > "$BYTE_TEST_ROOT/remove-plan.json"
"$BYTE_CANDIDATE/bin/byte" apply \
  --plan "$BYTE_TEST_ROOT/remove-plan.json"
"$BYTE_CANDIDATE/bin/byte" verify \
  --plan "$BYTE_TEST_ROOT/remove-plan.json"
"$BYTE_CANDIDATE/bin/byte" apply \
  --plan "$BYTE_TEST_ROOT/remove-plan.json"
```

The first removal must report `removed`, verification must report `verified`, and replay must report `already_removed`. The disposable Core and state roots must be absent. Recalculate the deployment document hashes and prove the fictional sentinel and all other deployment-owned bytes are unchanged.

## 7. Prove offline behavior

Repeat the candidate check, initialization plan/verify, install plan/verify, shell plan/verify, and removal plan/verify while network access is disabled by a method appropriate to the disposable test environment. Record the method and result. Do not change firewall or network policy on an operational host merely to perform this test.

The GitHub Byte Care transport is the only intentionally network-using path and is excluded from offline success. Do not use `--github-submit`; its behavior is covered by mock-only automated tests.

## 8. Record evidence

Copy the matching template from `release/v0.1/evidence/`, replace every placeholder with fresh non-sensitive observations, and submit it through a focused pull request. Do not include usernames, home paths, hostnames, addresses, inventory, credentials, broad logs, or terminal transcripts.

The independent reviewer separately completes `fresh-user-review-template.md` from a fresh checkout without private assistance. A project implementer cannot self-certify that criterion.

The release ledger changes from `pending` to `passed` only after each record and the exact tested commit are reviewed. The final command is:

```text
python3 scripts/check_v01_release.py \
  --artifact "$BYTE_TEST_ROOT/byte-core-0.1.0" \
  --evidence release/v0.1/manual-evidence.json \
  --require-complete
```

It must remain blocked until all three platform records and the independent review are present.
