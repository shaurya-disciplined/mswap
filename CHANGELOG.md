# Changelog

All notable changes to this project are documented here. Format: Keep a Changelog. Versioning: SemVer.

## [Unreleased]

## [1.0.1] - 2026-10-03

Run `uv tool install --force git+https://github.com/shaurya-disciplined/mswap@v1.0.1` (or the one-line installer again) to upgrade. 1.0.0 can't tell you about this release, because its update check asked PyPI.

### Fixed

- The update check now asks GitHub for the latest release instead of PyPI, where mswap isn't published, so it could never report a new version. The hint now prints the command that actually upgrades a GitHub install (`uv tool install --force git+https://github.com/shaurya-disciplined/mswap@vX`); `uv tool upgrade mswap` stays on the tag you installed.
- Releases no longer depend on PyPI: the PyPI publish jobs only run when the `PUBLISH_TO_PYPI` repository variable is `true`, so a tag push creates the GitHub Release on its own, and the post-release check installs the tag from GitHub instead of PyPI.

### Documentation

- Quick start shows what each step really prints, and the second account is saved after signing in.
- The agy hooks file is `~/.gemini/antigravity-cli/hooks.json`.
- macOS and Linux are listed as Supported everywhere (the documented "experimental" notice never existed).
- `docs/how-it-works.md` explains that a running agy session may keep the account it started with, what `--wait` and `--resume` do, and how write-back is detected.

### CI

- A single `ci-ok` check gathers every CI job; `main` is protected and requires it.
- Pinned actions bumped: checkout v7, setup-uv v10, upload-artifact v7, download-artifact v8, CodeQL v4.

## [1.0.0] - 2026-10-02

The first production release of **mswap**: multi-account switching, real-time quota intelligence, and hands-free autopilot for Google's Antigravity CLI (`agy`).

### 1.0 Highlights

- **Zero runtime dependencies**: Pure Python standard library. Nothing to supply-chain-attack, nothing for Windows Smart App Control to block.
- **Zero secrets in the repo**: agy's OAuth client is discovered from the user's local agy install. Refresh and access tokens stay in the OS credential store (Windows Credential Manager, macOS Keychain, Linux Secret Service). No plaintext secrets on disk, none in argv, none in logs.
- **Transactional switching**: Switches are dual-backed up, journaled, and verified before committing. Crash recovery via `mswap doctor --repair` ensures no login is ever lost.
- **Pool-aware autopilot**: Tracks Antigravity quota across Gemini and Claude/GPT pools over both 5-hour and weekly windows. Switches accounts automatically at turn boundaries via agy's `Stop` hook or background scheduler.
- **1.x stability guarantee**: Frozen CLI grammar, semantic exit codes, and Schema 1 JSON output protected by automated golden contract guards (`docs/stability.md`).
- **Comprehensive security review**: Full STRIDE threat model (`docs/security/threat-model.md`), subprocess argument protection, HTTPS-only transport, and owner-only data permissions.

### Added in 1.0

- 1.x stability contract and deprecation policy: `docs/stability.md`.
- Golden contract snapshots for every command's `--json` output (`tests/golden/json/*.json`) and every command's `--help` definition (`tests/golden/help/*.txt`).
- Public contract guard (`tests/integration/test_stability_contract.py`) enforcing intentional schema evolution: fails on contract changes unless overridden with `MSWAP_ALLOW_CONTRACT_CHANGE=1`, and rejects JSON removals or renames without incrementing `jsonout.SCHEMA_VERSION`.

### Fixed in 1.0

- macOS vault always correctly base64 encodes the blob for `security -i` to prevent spaces and quotes from causing errors or escaping bounds.
- `mswap --version` exactly matches the required spec format, and safely falls back if the agy version cannot be discovered.
- Linux `SecretToolVault.list()` properly includes the current live target when it matches the search prefix.
- `is_stale` strictly matches the specified contract by evaluating `near_limit` with the caller providing it.

### Security in 1.0

- Threat model (STRIDE) for assets, trust boundaries and mitigations: `docs/security/threat-model.md`.
- The agy hook, the Windows scheduled task, the launchd agent, the systemd unit and the `mswap.cmd` shim now start `python -P -m mswap`. Without `-P`, Python put the current directory first on its import path, so a `mswap.py` or `mswap/` in a cloned repository ran as you whenever agy ended a turn there. Run `mswap hook install`, `mswap schedule install` and `mswap shim install` again to update existing installs.
- OS tools (`schtasks`, `tasklist`, `icacls`, `security`, `ps`, `launchctl`, `systemctl`, `secret-tool`) are resolved from the system directories first, not from the current directory or an early `PATH` entry.
- The executable path in the hook command, systemd unit and `.cmd` shim is now quoted for its platform: spaces, quotes, `$`, backticks, `%` and newlines can no longer break the command or inject into it.
- The data directory is created `0700` and every file `0600` on macOS and Linux, whichever component creates them first (before, only some writers did).
- `mswap export` never overwrites an existing file, and a failed write can no longer delete one.
- `mswap import` plans every slot before writing anything and undoes its vault writes if saving fails, so it can't leave orphan logins behind.
- HTTP is https-only and never follows redirects, so credentials can't be forwarded to another host.
- Redaction also covers Python-repr dicts and `key=value` bodies; `debug record` reduces identifier-sized numbers (10+ digits) to their length.
- Installing the agy hook keeps `hooks.json`'s existing permissions.
- CI: every GitHub Action is pinned to a full commit SHA with a version comment, tag names are passed to scripts through `env:`, Dependabot groups action updates, and tests enforce all of it.

### Changes Since 0.1.0 (Full Journey)

- **W1 (Core Engine / v0.2.0)**: Strict error taxonomy with typed exit codes (1–9, 64, 70), Windows Credential Manager integration with 2560-byte limits, account store v2 in `%LOCALAPPDATA%\mswap`, journaled switch engine with crash recovery, OAuth token refresh with client discovery and dead-token quarantine, core account commands (`add`, `remove`, `alias`, `enable`, `disable`, `current`, `--json`), and `mswap doctor` with 10 diagnostic checks and `--repair`.
- **W2 (Live agy & Public Launch / v0.3.0)**: agy process detection with `--wait` and `--resume` flags, initial documentation, demo tape and walkthrough GIF, demo seed environment, and public GitHub release pipeline with PyPI Trusted Publishing.
- **W3 (Usage Intelligence / v0.4.0)**: Google Cloud Code Assist API integration, resilient quota fallback parser, adaptive usage cache (`usage.json`), rich `list` rendering with quota bars and plan tags, weekly pace calculation and JSON forecasts, terminal watch dashboard (`mswap watch`), prompt status one-liner (`mswap status`), and prompt integrations.
- **W4 (Autopilot / v0.5.0)**: Policy engine supporting pool focus (Gemini vs Claude/GPT) and selection strategies (`best`, `consume-first`), foreground auto runner (`mswap auto`), agy `Stop` hook integration (`mswap hook`), Windows Task Scheduler support (`mswap schedule`), and append-only audit event log (`mswap log`).
- **W5 (Cross-Platform / v0.6.0)**: Native macOS Keychain (`security` CLI) backend, Linux Secret Service (`secret-tool`) backend, headless fallback (`FileVault`), POSIX schedule via launchd and systemd, and cross-OS CI matrix verification.
- **W6 (Portability & Polish / v0.9.0rc1)**: Passphrase-encrypted backup export/import (`mswap export`/`import` with scrypt + AES-256-GCM), configuration file (`settings.toml` via `mswap config`), shell completions for PowerShell, Bash, Zsh, and Fish, Smart App Control-safe shim (`mswap shim install`), polite daily update notifications, and sanitized diagnostics export (`mswap debug record`).
- **W7 (v1.0 Production Readiness)**: Formal STRIDE threat model, security hardening across subprocesses, file permissions, and HTTP transport, and frozen 1.x stability contract with automated golden snapshot testing.

## [0.9.0rc1] - 2026-10-02

Release candidate for 1.0: portability and polish.

### Added

- `mswap debug record [--out DIR]` saves shape-only captures of agy's quota summary, `loadCodeAssist` and `fetchAvailableModels` responses plus an `env.json` (versions, OS/arch, vault backend). Every text value becomes `<str:N>`, only a short allow-list of labels is kept, and the files on disk are scanned for tokens, secrets and emails: on any hit the capture is deleted and nothing is saved.
- Full documentation pass: `docs/commands.md` (every command, flag, example and exit code), `docs/json-schema.md` (the `--json` shape of every command), a complete README commands table, bug-reporting guidance in the README, troubleshooting guide and security notes.
- Logo (`docs/assets/logo.svg`, dark-mode friendly through `currentColor`) in the README.
- `mswap completions powershell|bash|zsh|fish` prints a shell completion script generated from the argument parser, with dynamic completion of account selectors (slots, emails and aliases) through a hidden `mswap __complete selectors` that reads `accounts.json` only; install steps in `docs/completions.md`.
- Polite update check (PyPI JSON, cached 24 h in `update.json`, 3 s timeout, silent on failure) shown as a dim line after human `mswap list` and `mswap doctor`; opt out with `mswap config set updates.check false` or `MSWAP_NO_UPDATE_CHECK=1`.
- `mswap shim install [--dir DIR] [--force]` writes a Smart App Control-safe `mswap.cmd` that runs `python -m mswap`; `mswap doctor` now points its blocked-launcher warning at it.
- `mswap config [get KEY | set KEY VALUE | unset KEY | path | list]` to read and write `settings.toml` with validated dotted keys, a tiny flat-table TOML writer that preserves unknown keys (comments are not preserved), and `--json` output.
- Encrypted account export/import (`mswap export FILE [--accounts SEL,...]`, `mswap import FILE [--force]`) using scrypt + AES-256-GCM via the optional `mswap[export]` extra.

## [0.6.0] - 2026-10-02

### Added

- Cross-platform CI matrix contract test suite running real backends on native CI runners (Windows Credential Manager, macOS temporary keychain, Linux headless gnome-keyring Secret Service, POSIX FileVault).
- Cross-platform support table in README and documentation.
- Added: macOS/Linux paths, process detection, schedule via launchd/systemd.
- Added: Linux Secret Service support; opt-in file vault.
- Added: macOS Keychain support.
- Safe, read-only diagnostic probe script for agy login storage on macOS and Linux.

## [0.5.0] - 2026-10-02

### Added

- Audit trail viewer (`mswap log [-n 20] [--json]`) reading rotated audit events in chronological order with local timestamps and kind-specific summaries.
- Autopilot safety review and lock contention audit error logging.
- Background autopilot on Windows via Task Scheduler (`mswap schedule install|remove|status [--every MIN]`).
- agy Stop-hook integration (`mswap hook install|remove|status`) running autopilot checks at the end of each agy turn with an 8s budget.
- Foreground autopilot loop (`mswap auto [--once] [--dry-run] [--json]`) with state persistence and agy write-back detection.
- Pool-aware autopilot policy engine (`core/policy.py`) with dynamic focus and "best" / "consume-first" strategies.

## [0.4.0] - 2026-10-02

### Added

- Zero-dependency live dashboard (`mswap watch`) with alternate screen buffer, 1s tick, 15s refresh, and non-blocking keyboard controls (q, r, s, 1-9).
- Cache-only status one-liner (`mswap status [--format FMT] [--json]`) with < 150 ms execution overhead and zero network imports for shell prompt integration.
- Shell prompt integration documentation (`docs/prompt-integration.md`) with PowerShell, Starship, Bash, and Zsh snippets.
- Weekly pace marker and JSON forecasts.
- Usage cache with adaptive polling and rate-limit backoff; list --refresh.

### Changed

- Clearer quota bars, plan labels, markers for alias/disabled/quarantined.
- Sturdier quota parsing with automatic fallback.

## [0.3.0] - 2026-10-02

### Added

- Automated release workflow with PyPI Trusted Publishing, CodeQL analysis, post-release smoke verification, and full cross-platform CI matrix.
- Docs: README, how it works, troubleshooting, security.
- Demo tape (`docs/demo.tape`) and rendered walkthrough GIF (`docs/assets/demo.gif`).
- Relative markdown link validation script (`scripts/check_links.py`) integrated into CI quality job.
- Demo environment support with hidden `mswap __demo-seed` command and `DemoVault`.
- ADC session mode spike runner in `scripts/spikes/adc_spike.py` evaluating `AGY_ADC_AUTH` behavior and billing safety.
- agy process awareness, switch --wait / --resume, guard when run inside agy.
- Interactive live switch spike kit in `scripts/spikes/live_switch_spike.ps1` for guided evaluation of running agy process switch and write-back behavior.

## [0.2.0] - 2026-10-02

### Added

- Diagnostic command `mswap doctor` with 10 offline checks, `--online` quota and refresh checks, and `--repair` automatic recovery.
- Account management commands: `remove`, `alias`, `enable`, `disable`, `current`, and `add --alias`.
- Structured `--json` output across all commands adhering to §A17 schema-1 contract.
- Dead-login quarantine on `invalid_grant` and automatic re-detection of agy's OAuth client.
- Crash-safe switching transaction engine with journal, rollback, and recovery; refuses to overwrite unsaved logins without `--force`.

### Changed

- Migrated account storage to `%LOCALAPPDATA%\mswap` with non-destructive backup (`accounts.v1.bak`).
- Hardened error taxonomy with distinct exit codes (1–7, 64), helpful remediation hints, and token redaction.

### Fixed

- Windows Credential Manager integration with readable OS error formatting and 2560-byte blob limit guard.

## [0.1.0] - 2026-10-02

### Added

- Project skeleton.
- Test harness and safety net.
- Port of the personal mswap (add, list, switch) into the package.
- CI workflow.
- Community files and templates.

[Unreleased]: https://github.com/shaurya-disciplined/mswap/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/shaurya-disciplined/mswap/compare/v0.9.0rc1...v1.0.0
[0.9.0rc1]: https://github.com/shaurya-disciplined/mswap/compare/v0.6.0...v0.9.0rc1
[0.6.0]: https://github.com/shaurya-disciplined/mswap/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/shaurya-disciplined/mswap/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/shaurya-disciplined/mswap/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/shaurya-disciplined/mswap/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/shaurya-disciplined/mswap/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/shaurya-disciplined/mswap/releases/tag/v0.1.0
