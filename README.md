<p align="center">
  <img src="docs/assets/logo.svg" alt="mswap" width="240" />
</p>

<p align="center"><em>Unbreakable agy sessions.</em></p>

[![ci](https://github.com/shaurya-disciplined/mswap/actions/workflows/ci.yml/badge.svg)](https://github.com/shaurya-disciplined/mswap/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/mswap)](https://pypi.org/project/mswap/)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

<p align="center">
  <img src="docs/assets/demo.gif" alt="mswap demo" width="700" />
</p>

## Why

- **Pool-aware quota tracking**: View all your accounts' 5-hour and weekly quota pools (Gemini, Claude & GPT) in a single unified dashboard.
- **One-second switching**: Switch accounts instantly without manual `/logout`, browser authentication popups, or lost session context.
- **Zero data loss**: Every switch is transactional, journaled, and backed up before writing. If anything fails, it rolls back automatically.

## Install

```powershell
# recommended
uv tool install mswap
# if Smart App Control blocks the mswap.exe launcher (Windows):
uv tool install mswap ; mswap shim install   # writes %USERPROFILE%\.local\bin\mswap.cmd
# or, without installing a launcher at all:
uvx --from mswap python -m mswap list
```

## Quick Start

Get two accounts working in under 3 minutes:

1. **Sign in to your first account in agy**:
   ```powershell
   agy
   ```
2. **Save your first account**:
   ```powershell
   mswap add
   ```
3. **Prepare agy for a fresh sign-in**:
   ```powershell
   mswap add --new
   ```
4. **Sign in to your second account in agy**:
   ```powershell
   agy
   ```
5. **Save your second account**:
   ```powershell
   mswap add
   ```
6. **View your accounts and remaining quota**:
   ```powershell
   mswap list
   ```
7. **Switch between accounts**:
   ```powershell
   mswap switch
   ```

## Commands

| Command | Description |
|---|---|
| `mswap` / `mswap list` | Display saved accounts, active status, and quota bars (`--refresh` for live API query) |
| `mswap add` | Save the current active `agy` login to `mswap` (`--new` also signs agy out here so you can add another account, `--alias NAME` names it) |
| `mswap switch [SELECTOR]` | Switch to account by slot number, email, or alias (rotates if omitted; `--wait`, `--resume`, `--force`) |
| `mswap current` | Display the active account |
| `mswap alias SELECTOR NAME` | Assign a human-friendly alias to an account (`--clear` to remove) |
| `mswap disable SELECTOR` | Exclude an account from automatic rotation |
| `mswap enable SELECTOR` | Re-enable an account for rotation |
| `mswap remove SELECTOR` | Remove a saved account and its stored vault credential |
| `mswap status` | Cache-only one-liner for shell prompts, never touches the network ([prompt setup](docs/prompt-integration.md)) |
| `mswap watch` | Live dashboard with keyboard controls (`q` quit, `r` refresh, `s` next, `1`-`9` switch) |
| `mswap auto` | Autopilot: switch before a limit stops the work (`--once`, `--dry-run`, `--threshold N`, `--strategy`, `--focus`) |
| `mswap hook install\|remove\|status` | Run autopilot checks at the end of each agy turn through agy's `Stop` hook |
| `mswap schedule install\|remove\|status` | Run autopilot in the background (Task Scheduler, launchd or systemd) |
| `mswap log [-n N]` | Show the audit trail of switches and autopilot decisions |
| `mswap config [get\|set\|unset\|path\|list]` | Read and write `settings.toml` with validated keys |
| `mswap export FILE [--accounts SEL,...]` | Write saved accounts to a passphrase-encrypted bundle (needs `mswap[export]`) |
| `mswap import FILE [--force]` | Restore accounts from a bundle: new emails get the next free slot, existing ones are skipped (`--force` overwrites) |
| `mswap completions SHELL` | Print a tab-completion script for PowerShell, bash, zsh or fish ([install](docs/completions.md)) |
| `mswap shim install` | Windows: write a Smart App Control-safe `mswap.cmd` launcher |
| `mswap doctor` | Check environment, vault health, and diagnostics (`--repair` to fix issues, `--online` for network checks) |
| `mswap debug record` | Save secret-free API response shapes to attach to a bug report |
| `mswap --version` | Show version and license information |

Every flag, example and exit code is in the [command reference](docs/commands.md). Machine-readable output is described in [docs/json-schema.md](docs/json-schema.md).

Global options: `--json` for machine-readable output, `--no-color` to disable ANSI colors, `--ascii` for plain-text symbols, `-q`/`--quiet` to silence status messages, `-v`/`--verbose` for diagnostics.

## Moving Accounts Between Machines

`mswap export` and `mswap import` carry your saved logins to another computer in one encrypted file. They need the optional crypto package:

```
uv tool install "mswap[export]"
mswap export accounts.mswap            # asks for a passphrase twice (12+ characters)
mswap import accounts.mswap            # on the other machine
```

- The bundle is encrypted with a key derived from your passphrase (scrypt, then AES-256-GCM). A wrong passphrase or a damaged file is reported as `Wrong passphrase or damaged file.`
- Anyone with this file AND the passphrase can use these accounts. Delete the file once you have imported it.
- For scripts, set `MSWAP_EXPORT_PASSPHRASE`; the passphrase is never accepted on the command line, so it stays out of shell history.
- Import never changes your active agy login. An email you already have is skipped (or replaced when its saved login is quarantined, or with `--force`).
- `client.json` (the discovered OAuth client) is never exported.

## Running agy Sessions

When you run `mswap switch`, `mswap` detects running `agy.exe` instances and warns you:

```text
! agy is running (PID 1234). It won't pick up the switch until you restart it.
```

- **Wait for active sessions**: Use `mswap switch --wait` to wait until all running `agy` sessions exit before completing the switch.
- **Resume your session**: Use `mswap switch --resume` to wait for running sessions to finish and print the exact resumption command for your task.
- **Switch anyway**: Use `mswap switch --force` if you want to switch immediately while another terminal runs.

Learn more in [How It Works](docs/how-it-works.md).

## Safety Model

- **Dual backups**: `mswap` preserves `mswap:backup-last` (previous switch state) and `mswap:backup-original` (initial state) before modifying credentials.
- **Transactional journal**: Switches write state to a journal file before mutating credentials. If an error occurs mid-write, `mswap` rolls back.
- **Zero tokens on disk**: OAuth refresh tokens and access tokens are stored strictly in the operating system's native credential vault (Windows Credential Manager via DPAPI). `accounts.json` holds only metadata and token fingerprints.
- **Refuses unknown logins**: `mswap` refuses to overwrite an active agy login that has not been saved, unless you explicitly pass `--force`.
- **Minimal surface**: `mswap` touches only `gemini:antigravity` and `mswap:*` entries. It never touches Git, GitHub CLI (`gh`), MCP servers, or browser profiles.

Read the complete [Security Policy](docs/security.md).

## Platform Support

| Platform | Credential Backend | Status | Notes |
|---|---|---|---|
| **Windows** | Windows Credential Manager (`advapi32`) | **Supported** | Verified with real `agy` installs |
| **macOS** | Keychain Services (`security` CLI) | **Experimental** | CI-tested backend; real-world storage unverified until W5.S1 probe results arrive |
| **Linux** | Secret Service (`secret-tool`) | **Experimental** | CI-tested backend; real-world storage unverified until W5.S1 probe results arrive |
| **Linux (Headless)** | File Vault fallback (`MSWAP_VAULT=file`) | **Experimental** | Opt-in unencrypted file fallback for headless/WSL environments |

See [docs/platforms.md](docs/platforms.md) for architecture, security details, and instructions on running the probe to help verify real-world POSIX storage.

## Frequently Asked Questions

### Will I be charged?
No. Switching accounts in `mswap` is completely free and costs nothing. Each Google account's own plan (e.g. Free tier, Google One AI Premium) determines its quota limits and any applicable billing.

### Does it break my MCP servers or GitHub CLI?
No. `mswap` touches only the Antigravity CLI login credential `gemini:antigravity`. Your MCP servers, GitHub CLI (`gh`), Git credentials, and browser sessions are completely untouched. Account-linked features within agy (such as Google Drive integration or synced history) follow the switched account.

### Is this allowed by Google?
`mswap` is an independent developer utility that manages your own login tokens locally on your machine. It performs no automated sign-ins, does not bypass quota limits, and sends standard requests to official endpoints using your own credentials. You are responsible for ensuring your use complies with Google's Terms of Service.

### Why not use agy's `/logout`?
Running agy's `/logout` command revokes the OAuth token on Google servers, requiring a full web browser sign-in next time. `mswap` rotates between your saved tokens locally without revoking them.

### Does it work on macOS and Linux?
Yes. macOS Keychain and Linux Secret Service backends are supported experimentally starting in v0.6.0. All backends are verified by contract test suites in CI; real-world storage will graduate to fully supported once verified on physical hardware (see [Platform Support](#platform-support) and [docs/platforms.md](docs/platforms.md)).

---

## Troubleshooting

If you encounter unexpected warnings or errors, run `mswap doctor` or see the [Troubleshooting Guide](docs/troubleshooting.md).

### Reporting a bug

Run `mswap debug record` and attach the folder it saves to your [GitHub issue](https://github.com/shaurya-disciplined/mswap/issues/new/choose). It holds only the *shape* of agy's quota API responses (every text value is replaced by its length), plus version and OS details. mswap scans the capture before it finishes and deletes it if anything looks like a token or an email, so it is safe to attach. Never paste tokens, credential blobs or `client.json`.

## Updates

`mswap list` and `mswap doctor` print a dim line when a newer version is on PyPI, checked at most once a day. Turn it off with `mswap config set updates.check false` or `MSWAP_NO_UPDATE_CHECK=1`. mswap never upgrades itself; run `uv tool upgrade mswap`.

---

## Disclaimer

mswap is an independent project, not affiliated with or endorsed by Google. Antigravity is a trademark of Google LLC. mswap reads and writes only your own agy login on your own machine. Using several accounts may be subject to Google's terms. You are responsible for how you use it.

---

Built by Meteor · TUT: The Unbreakable Titan
