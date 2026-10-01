# Contributing to mswap

Thank you for your interest in contributing to `mswap`!

## Development Setup

`mswap` uses `uv` for dependency management and running developer tools.

1. Clone the repository:
   ```bash
   git clone https://github.com/shaurya-disciplined/mswap.git
   cd mswap
   ```
2. Synchronize the development virtual environment:
   ```bash
   uv sync --all-groups
   ```

## Running Quality Gates

Before opening a pull request, all gates must pass locally:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q
uv run python scripts/check_fixtures.py
uv run python scripts/check_privacy.py
```

In addition, layer boundary checks ensure architecture rules are respected:
```bash
uv run python scripts/check_layers.py
```

## Safety Environment Variables

The test harness uses safety environment variables to isolate execution from real OS credentials and live network calls:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MSWAP_HOME` | Platform data dir | Overrides the application state and data directory |
| `MSWAP_LIVE_TARGET` | `gemini:antigravity` | Credential target agy reads. Tests always set `mswaptest:live`. |
| `MSWAP_VAULT_PREFIX` | `mswap:` | Prefix for mswap credentials. Tests always set `mswaptest:`. |
| `MSWAP_VAULT` | `native` | Set to `memory` for the in-process test fake |
| `MSWAP_NO_NETWORK` | Unset | When set to `1`, disables network I/O in HTTP clients |

Tests automatically configure these variables via `tests/conftest.py` so that test runs never access real credentials or live endpoints.

## Secrets and Hygiene

- **Never paste tokens or credential blobs anywhere.** Never commit, log, or include tokens (`ya29.*`, `1//*`), client secrets (`GOCSPX-*`), JWTs, or credential blobs in code, tests, issues, PR descriptions, or discussions.
- All tests must use fake tokens (e.g. `ya29.FAKE-access-1`) and safe test emails (e.g. `alice@example.com`).
- Automated privacy checks scan all tracked files to prevent committing personal paths or real emails.

## Commit and Pull Request Guidelines

- **Conventional Commits:** Write commit messages following the Conventional Commits specification (e.g., `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `ci:`, `chore:`).
- **One PR per change:** Keep pull requests focused on a single change or step with clear rationale and verification steps.
- Ensure all CI workflows pass on your branch.
