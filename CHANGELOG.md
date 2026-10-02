# Changelog

All notable changes to this project are documented here. Format: Keep a Changelog. Versioning: SemVer.

## [Unreleased]

### Changed

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

[Unreleased]: https://github.com/shaurya-disciplined/mswap/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/shaurya-disciplined/mswap/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/shaurya-disciplined/mswap/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/shaurya-disciplined/mswap/releases/tag/v0.1.0
