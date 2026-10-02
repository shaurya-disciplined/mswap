# Changelog

All notable changes to this project are documented here. Format: Keep a Changelog. Versioning: SemVer.

## [Unreleased]

### Added

- Crash-safe switching with journal, rollback and recovery; refuses to overwrite an unsaved login (W1.S4).

### Changed

- Data moves to %LOCALAPPDATA%\mswap with automatic, non-destructive migration (W1.S3).
- Errors now have exit codes and hints; --json errors (W1.S1).

### Fixed

- Windows vault errors are readable; credential size checked (W1.S2).

## [0.1.0] - 2026-10-02

### Added

- Project skeleton (W0.S1).
- Test harness and safety net (W0.S2).
- Port of the personal mswap (add, list, switch) into the package (W0.S3).
- CI workflow (W0.S4).
- Community files and templates (W0.S5).

[Unreleased]: https://github.com/shaurya-disciplined/mswap/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/shaurya-disciplined/mswap/releases/tag/v0.1.0
