# Changelog

All notable changes to this project are documented here. Format: Keep a Changelog. Versioning: SemVer.

## [Unreleased]

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

- 1.x stability contract and deprecation policy: `docs/stability.md` (W7.S2).
- Golden contract snapshots for every command's `--json` output (`tests/golden/json/*.json`) and every command's `--help` definition (`tests/golden/help/*.txt`) (W7.S2).
- Public contract guard (`tests/integration/test_stability_contract.py`) enforcing intentional schema evolution: fails on contract changes unless overridden with `MSWAP_ALLOW_CONTRACT_CHANGE=1`, and rejects JSON removals or renames without incrementing `jsonout.SCHEMA_VERSION` (W7.S2).
- Release launch kit: announcement templates for X/Twitter, Reddit, and Hacker News in `.agent/launch/` (W7.S4).

### Security in 1.0

- Threat model (STRIDE) for assets, trust boundaries and mitigations: `docs/security/threat-model.md` (W7.S1).
- The agy hook, the Windows scheduled task, the launchd agent, the systemd unit and the `mswap.cmd` shim now start `python -P -m mswap`. Without `-P`, Python put the current directory first on its import path, so a `mswap.py` or `mswap/` in a cloned repository ran as you whenever agy ended a turn there. Run `mswap hook install`, `mswap schedule install` and `mswap shim install` again to update existing installs (W7.S1).
- OS tools (`schtasks`, `tasklist`, `icacls`, `security`, `ps`, `launchctl`, `systemctl`, `secret-tool`) are resolved from the system directories first, not from the current directory or an early `PATH` entry (W7.S1).
- The executable path in the hook command, systemd unit and `.cmd` shim is now quoted for its platform: spaces, quotes, `$`, backticks, `%` and newlines can no longer break the command or inject into it (W7.S1).
- The data directory is created `0700` and every file `0600` on macOS and Linux, whichever component creates them first (before, only some writers did) (W7.S1).
- `mswap export` never overwrites an existing file, and a failed write can no longer delete one (W7.S1).
- `mswap import` plans every slot before writing anything and undoes its vault writes if saving fails, so it can't leave orphan logins behind (W7.S1).
- HTTP is https-only and never follows redirects, so credentials can't be forwarded to another host (W7.S1).
- Redaction also covers Python-repr dicts and `key=value` bodies; `debug record` reduces identifier-sized numbers (10+ digits) to their length (W7.S1).
- Installing the agy hook keeps `hooks.json`'s existing permissions (W7.S1).
- CI: every GitHub Action is pinned to a full commit SHA with a version comment, tag names are passed to scripts through `env:`, Dependabot groups action updates, and tests enforce all of it (W7.S1).

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

- `mswap debug record [--out DIR]` saves shape-only captures of agy's quota summary, `loadCodeAssist` and `fetchAvailableModels` responses plus an `env.json` (versions, OS/arch, vault backend). Every text value becomes `<str:N>`, only a short allow-list of labels is kept, and the files on disk are scanned for tokens, secrets and emails: on any hit the capture is deleted and nothing is saved (W6.S5).
- Full documentation pass: `docs/commands.md` (every command, flag, example and exit code), `docs/json-schema.md` (the `--json` shape of every command), a complete README commands table, bug-reporting guidance in the README, troubleshooting guide and security notes (W6.S5).
- Logo (`docs/assets/logo.svg`, dark-mode friendly through `currentColor`) in the README (W6.S5).
- `mswap completions powershell|bash|zsh|fish` prints a shell completion script generated from the argument parser, with dynamic completion of account selectors (slots, emails and aliases) through a hidden `mswap __complete selectors` that reads `accounts.json` only; install steps in `docs/completions.md` (W6.S3).
- Polite update check (PyPI JSON, cached 24 h in `update.json`, 3 s timeout, silent on failure) shown as a dim line after human `mswap list` and `mswap doctor`; opt out with `mswap config set updates.check false` or `MSWAP_NO_UPDATE_CHECK=1` (W6.S4).
- `mswap shim install [--dir DIR] [--force]` writes a Smart App Control-safe `mswap.cmd` that runs `python -m mswap`; `mswap doctor` now points its blocked-launcher warning at it (W6.S4).
- `mswap config [get KEY | set KEY VALUE | unset KEY | path | list]` to read and write `settings.toml` with validated dotted keys, a tiny flat-table TOML writer that preserves unknown keys (comments are not preserved), and `--json` output (W6.S2).
- Encrypted account export/import (`mswap export FILE [--accounts SEL,...]`, `mswap import FILE [--force]`) using scrypt + AES-256-GCM via the optional `mswap[export]` extra (W6.S1).

## [0.6.0] - 2026-10-02

### Added

- Cross-platform CI matrix contract test suite running real backends on native CI runners (Windows Credential Manager, macOS temporary keychain, Linux headless gnome-keyring Secret Service, POSIX FileVault) (W5.S5).
- Cross-platform support table in README and documentation (W5.S5).
- Added: macOS/Linux paths, process detection, schedule via launchd/systemd (W5.S4).
- Added: experimental Linux Secret Service support; opt-in file vault (W5.S3).
- Added: experimental macOS Keychain support (W5.S2).
- Safe, read-only diagnostic probe script for agy login storage on macOS and Linux (W5.S1).

## [0.5.0] - 2026-10-02

### Added

- Audit trail viewer (`mswap log [-n 20] [--json]`) reading rotated audit events in chronological order with local timestamps and kind-specific summaries (W4.S5).
- Autopilot safety review and lock contention audit error logging (W4.S5).
- Background autopilot on Windows via Task Scheduler (`mswap schedule install|remove|status [--every MIN]`) (W4.S4).
- agy Stop-hook integration (`mswap hook install|remove|status`) running autopilot checks at the end of each agy turn with an 8s budget (W4.S3).
- Foreground autopilot loop (`mswap auto [--once] [--dry-run] [--json]`) with state persistence and agy write-back detection (W4.S2).
- Pool-aware autopilot policy engine (`core/policy.py`) with dynamic focus and "best" / "consume-first" strategies (W4.S1).

## [0.4.0] - 2026-10-02

### Added

- Zero-dependency live dashboard (`mswap watch`) with alternate screen buffer, 1s tick, 15s refresh, and non-blocking keyboard controls (q, r, s, 1-9) (W3.S5).
- Cache-only status one-liner (`mswap status [--format FMT] [--json]`) with < 150 ms execution overhead and zero network imports for shell prompt integration (W3.S5).
- Shell prompt integration documentation (`docs/prompt-integration.md`) with PowerShell, Starship, Bash, and Zsh snippets (W3.S5).
- Weekly pace marker and JSON forecasts (W3.S4).
- Usage cache with adaptive polling and rate-limit backoff; list --refresh (W3.S2).

### Changed

- Clearer quota bars, plan labels, markers for alias/disabled/quarantined (W3.S3).
- Sturdier quota parsing with automatic fallback (W3.S1).

## [0.3.0] - 2026-10-02

### Added

- Automated release workflow with PyPI Trusted Publishing, CodeQL analysis, post-release smoke verification, and full cross-platform CI matrix (W2.S6).
- Docs: README, how it works, troubleshooting, security (W2.S5).
- Demo tape (`docs/demo.tape`) and rendered walkthrough GIF (`docs/assets/demo.gif`) (W2.S5).
- Relative markdown link validation script (`scripts/check_links.py`) integrated into CI quality job (W2.S5).
- Demo environment support with hidden `mswap __demo-seed` command and `DemoVault` (W2.S5).
- ADC session mode spike runner in `scripts/spikes/adc_spike.py` evaluating `AGY_ADC_AUTH` behavior and billing safety (W2.S3).
- agy process awareness, switch --wait / --resume, guard when run inside agy (W2.S2).
- Interactive live switch spike kit in `scripts/spikes/live_switch_spike.ps1` for guided evaluation of running agy process switch and write-back behavior (W2.S1).

## [0.2.0] - 2026-10-02

### Added

- Diagnostic command `mswap doctor` with 10 offline checks, `--online` quota and refresh checks, and `--repair` automatic recovery (W1.S7).
- Account management commands: `remove`, `alias`, `enable`, `disable`, `current`, and `add --alias` (W1.S6).
- Structured `--json` output across all commands adhering to §A17 schema-1 contract (W1.S6, W1.S7).
- Dead-login quarantine on `invalid_grant` and automatic re-detection of agy's OAuth client (W1.S5).
- Crash-safe switching transaction engine with journal, rollback, and recovery; refuses to overwrite unsaved logins without `--force` (W1.S4).

### Changed

- Migrated account storage to `%LOCALAPPDATA%\mswap` with non-destructive backup (`accounts.v1.bak`) (W1.S3).
- Hardened error taxonomy with distinct exit codes (1–7, 64), helpful remediation hints, and token redaction (W1.S1).

### Fixed

- Windows Credential Manager integration with readable OS error formatting and 2560-byte blob limit guard (W1.S2).

## [0.1.0] - 2026-10-02

### Added

- Project skeleton (W0.S1).
- Test harness and safety net (W0.S2).
- Port of the personal mswap (add, list, switch) into the package (W0.S3).
- CI workflow (W0.S4).
- Community files and templates (W0.S5).

[Unreleased]: https://github.com/shaurya-disciplined/mswap/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/shaurya-disciplined/mswap/compare/v0.9.0rc1...v1.0.0
[0.9.0rc1]: https://github.com/shaurya-disciplined/mswap/compare/v0.6.0...v0.9.0rc1
[0.6.0]: https://github.com/shaurya-disciplined/mswap/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/shaurya-disciplined/mswap/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/shaurya-disciplined/mswap/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/shaurya-disciplined/mswap/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/shaurya-disciplined/mswap/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/shaurya-disciplined/mswap/releases/tag/v0.1.0
