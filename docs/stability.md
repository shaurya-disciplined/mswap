# Stability Contract & Deprecation Policy

`mswap` follows semantic versioning (SemVer 2.0). This document defines the stability guarantees for `mswap` 1.x, distinguishing between frozen public interfaces and mutable internal details.

---

## What Is Stable in 1.x

The following interfaces and behaviors are guaranteed to remain backward-compatible across all `1.x` releases:

### 1. CLI Grammar & Flags
- All commands, subcommands, arguments, and options documented in [commands.md](commands.md) are stable.
- Global flags (`--json`, `--no-color`, `--ascii`, `-q` / `--quiet`, `-v` / `--verbose`) are supported by all relevant subcommands.
- Existing flags, positional arguments, choices, and default values will not be removed or renamed in any 1.x release.
- Flags and subcommands are snapshotted in `tests/golden/help/*.txt` and guarded by automated contract tests.

### 2. Exit Codes
- Semantic exit codes are frozen and guaranteed across all commands:
  - `0`: Success (or nothing to do in advisory contexts)
  - `1`: General error / API failure / network failure / vault failure
  - `2`: Invalid CLI syntax or bad arguments (`UsageError`)
  - `4`: agy is signed out (`NotSignedIn`)
  - `5`: OAuth refresh token revoked or dead (`TokenDead`)
  - `6`: Unsafe operation or invariant violation (`UnsafeOperation`)
  - `7`: Lock acquisition timeout (`LockTimeout`)
  - `8`: Nothing to do (`NothingToDo`, e.g. already on target slot)
  - `9`: No viable account target (`NoViableTarget`)
  - `64`: Account selector not found
  - `70`: Internal error / unhandled bug
- Scripts and external tools can safely branch on these codes.

### 3. JSON Output Contract (Schema Version 1)
- The structured JSON output format produced when `--json` is passed is frozen under Schema Version 1.
- Standard envelope for command results:
  ```json
  {"schema": 1, "ok": true, "command": "<cmd>", "data": { ... }}
  ```
- Error envelope:
  ```json
  {"schema": 1, "ok": false, "command": "<cmd>", "error": {"code": N, "kind": "...", "message": "...", "hint": "..."}}
  ```
- Continuous streaming output for `mswap auto --json`:
  ```json
  {"schema": 1, "event": "...", "at": "...", ...}
  ```
- **Forward-compatibility policy**:
  - Adding new fields to JSON objects is permitted in minor and patch releases. Consumers and scripts must ignore unrecognized fields.
  - Renaming existing fields, removing fields, or changing field types within Schema 1 is strictly forbidden.
  - All JSON output shapes are snapshotted in `tests/golden/json/*.json` and documented in [json-schema.md](json-schema.md).

### 4. Data File Locations & Formats
- Application data is stored in the standard platform directory:
  - **Windows**: `%LOCALAPPDATA%\mswap\` (or custom override `$env:MSWAP_HOME`)
  - **Linux**: `~/.local/share/mswap` (or `$XDG_DATA_HOME/mswap`)
  - **macOS**: `~/Library/Application Support/mswap`
- The file names, roles, and JSON/TOML schemas within the data directory remain stable:
  - `accounts.json`: Saved account metadata (slot numbers, emails, fingerprints, aliases, timestamps)
  - `client.json`: Discovered agy OAuth client credentials cache
  - `events.log`: Append-only audit history of switch and autopilot events
  - `journal.json`: Switch transaction journal for atomic state transitions
  - `usage.json`: Cached quota and bucket snapshots
  - `settings.toml`: User configuration keys and sections

### 5. Vault Target Names
- The credential target naming scheme within OS credential stores remains frozen:
  - `gemini:antigravity`: Active agy session credential
  - `mswap:slot<N>` (`mswap:slot1`, `mswap:slot2`, ...): Saved account credential blobs
  - `mswap:backup-last`: Immediate rollback credential from the most recent switch
  - `mswap:backup-original`: Permanent baseline recovery credential created on initial setup
  - `mswaptest:*`: Prefix reserved for test harness isolation

---

## What Is NOT Stable (Unstable Surfaces)

The following aspects of `mswap` are **not** covered by the 1.x stability guarantee:

### 1. Human-Readable Terminal Layout
- Formatting, colors, ANSI styling, glyphs (`▸`, `✓`, `✗`, `━`, `─`), spacing, table column widths, and progress bars.
- These representations are designed for human interaction and may evolve between minor or patch releases to improve readability or UX.
- **Rule for automations**: Automations, scripts, shell plugins, and IDE wrappers must **never** parse human-readable stdout/stderr. Always use `--json` or dedicated query subcommands like `mswap current` or `mswap status`.

### 2. Internal Python Modules & APIs
- All Python code under `src/mswap/` (`mswap.core.*`, `mswap.agy.*`, `mswap.cli.*`, `mswap.vault.*`, `mswap.ui.*`, `mswap.util.*`) is considered private implementation detail.
- `mswap` is an executable CLI application, not an importable library.
- Internal functions, classes, module structures, and signatures may be refactored or moved without notice between releases.

---

## Deprecation Policy

When a feature, command, flag, or configuration option needs to be retired:

1. **Advance Notice**: The item will be formally marked as deprecated for at least **one minor release cycle** before being removed (e.g., deprecated in 1.1, removed no earlier than 1.2 or 2.0).
2. **Warning Messages**: Using a deprecated command or flag will emit a warning to `stderr` with migration instructions and the targeted removal version. When running with `--json`, stderr warnings will not corrupt or alter stdout JSON payloads.
3. **Documentation**: Deprecated features will be clearly documented in the CLI `--help` text, release notes, and documentation guides.
4. **Breaking Schema Changes**: Any breaking change to `--json` output (removal, rename, or type change of a field) requires bumping `jsonout.SCHEMA_VERSION` (from 1 to 2) and will only occur across a major version boundary.
5. **Contract Guard**: CI runs golden snapshot checks on every pull request. A change to any golden snapshot will fail CI unless explicitly authorized with `MSWAP_ALLOW_CONTRACT_CHANGE=1`. If breaking changes are detected without a schema version bump, the test suite aborts.
