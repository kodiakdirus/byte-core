# Byte Core v0.1 support matrix

Byte Core is pre-alpha software without a supported functional release. This matrix defines the host combinations the v0.1 implementation and release gate are intended to validate; it is not a production-support promise.

## Initial host boundary

| Operating system | Architecture | Shell coverage | Automated gate | v0.1 target |
| --- | --- | --- | --- | --- |
| Ubuntu 24.04 LTS | x86_64 | Bash launcher smoke test; Zsh optional and not installed by Byte | Configured in GitHub Actions | Supported target |
| macOS 15 | Apple silicon (`arm64`) | Bash and Zsh launcher smoke tests | Configured in GitHub Actions | Supported target |
| macOS 26 | Apple silicon (`arm64`) | Bash and Zsh launcher smoke tests | Configured in GitHub Actions | Supported target |
| Other Linux distributions | Any | Not established | None | Unsupported |
| Linux | `arm64` or other architectures | Not established | None | Unsupported |
| Other or unrecognized macOS releases | Apple silicon (`arm64`) | Not established | Deterministic refusal tests | Unsupported |
| macOS | Intel (`x86_64`) | Not established | None | Unsupported |
| Windows, BSD, appliance operating systems, and unknown hosts | Any | Not applicable | Deterministic refusal tests | Unsupported |

`byte check` recognizes macOS and Linux separately from approving a complete operating-system release and architecture combination. A recognized platform with an unapproved release or architecture returns status 3 and identifies the unsupported combination without guessing compatibility.

## Runtime prerequisites

- Python 3.11 through 3.14 are the initial CI matrix. Python 3.11 is the minimum runtime; later Python 3 versions are not claimed until exercised by CI.
- Git must be available as `git` and return a parseable dotted numeric version. Byte Core does not yet depend on a narrower Git feature-version floor.
- The launcher requires a POSIX process environment and `/bin/sh` behavior. Bash is exercised on every supported host. Zsh is exercised on supported macOS runners, where it is native; Linux users may choose to install Zsh independently, but Byte does not require or install it.
- Shell enhancements such as syntax highlighting are optional. The experimental shell lifecycle includes them only when a Zsh user supplies an explicit existing source file. Byte does not make them a prerequisite or install the package.
- Runtime operation is standard-library-first and offline. CI action setup may access GitHub infrastructure; Byte lifecycle commands do not require network access.

## Evidence boundary

The support-matrix workflow is configured to prove unit behavior and launcher smoke tests on disposable GitHub-hosted runners. Evidence requires a passing workflow run; configuration alone is not a passing result. Even a passing run does not prove every hardware revision, distribution derivative, filesystem, local security policy, package-manager configuration, or long-running deployment.

The pinned operating-system labels follow GitHub's [published hosted-runner image inventory](https://github.com/actions/runner-images#available-images). A later change in GitHub's `latest` aliases does not silently expand Byte Core's support boundary.

Before a functional v0.1 release, the release gate still requires recorded manual installation, verification, backout, preservation, and offline smoke-test evidence on the supported target platforms. Until that evidence and every other release criterion are complete, README language must continue to describe Byte Core as pre-alpha and unsupported for operational use.

The current ledger intentionally records all three targets as pending in `release/v0.1/manual-evidence.json`. The final gate and record format are defined in the [release checklist](release-checklist.md); no pending entry is support evidence.

## Expanding support

A new host combination becomes a supported target only through a reviewed change that adds deterministic detection, automated coverage where feasible, documented manual evidence requirements, and known limitations. Recognition by `platform.system()` or `platform.machine()` alone is not evidence of support.
