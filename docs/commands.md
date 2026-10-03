# Command reference

Every `mswap` command, its flags, an example and what it returns. For the shape of `--json`
output see [json-schema.md](json-schema.md).

```text
mswap [--json] [--no-color] [--ascii] [-q] [-v] COMMAND ...
```

## Global options

These work on every command.

| Option | Effect |
|---|---|
| `--json` | One JSON object on stdout and nothing else. See [json-schema.md](json-schema.md). |
| `--no-color` | No ANSI colors. `NO_COLOR` does the same. |
| `--ascii` | ASCII bars and symbols instead of `━ ─ ▸ ✓ ✗`. `MSWAP_ASCII=1` does the same. |
| `-q`, `--quiet` | Progress and status messages off. Errors still print. |
| `-v`, `--verbose` | Extra diagnostics. |
| `-V`, `--version` | `mswap X.Y.Z · not affiliated with Google` |

Running `mswap` with no command is the same as `mswap list` once you have an account, and shows
help before that. Results go to stdout. Errors, warnings and progress go to stderr.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Failure: network, vault, damaged state, or a failed `doctor` check. |
| `2` | Nothing to do (for example, only one account is available to switch to). `auto`: held. |
| `3` | No viable target. `auto`: blocked. |
| `4` | Not signed in, or a saved login is dead (the refresh token was revoked or expired). |
| `5` | agy not found. |
| `6` | Unsafe operation refused (for example, an unsaved login would be overwritten). |
| `7` | Another mswap holds the lock. |
| `64` | Usage error: bad command, flag or value. |
| `70` | Internal error (this is a bug; please report it). |
| `130` | Interrupted with Ctrl-C. |

In `--json` mode an error is `{"schema": 1, "ok": false, "command": ..., "error": {...}}` on
stdout, with the same exit code.

## Selectors

Commands that take `SELECTOR` accept a slot number (`2`), an email (`alice@example.com`) or an
alias (`work`). Emails and aliases are case-insensitive.

---

## Accounts

### `mswap add [--new] [--alias NAME] [--force]`

Saves the account agy is signed in to right now as the next free slot. Adding an email you
already have refreshes its saved login instead.

| Flag | Effect |
|---|---|
| `--new` | After saving, sign agy out on this PC only so you can sign in as another account. The saved copy stays valid. |
| `--alias NAME` | Give the account a name (`^[a-z][a-z0-9-]{0,19}$`, unique). |
| `--force` | Allow it while inside an agy session. |

```text
mswap add --alias work
mswap add --new
```

### `mswap list [--refresh]`  (alias `ls`)

Shows every account with its 5-hour and weekly quota for each pool (Gemini, Claude & GPT).
Quota comes from a cache that refreshes itself when it is stale; `--refresh` forces a fresh
read (a rate-limit backoff is still respected). After the table, a dim line tells you when a
newer mswap is out (see [updates](#update-check)).

```text
mswap list
mswap list --refresh --json
```

### `mswap switch [SELECTOR] [--force] [--wait] [--wait-timeout SEC] [--resume]`

Switches agy to another saved account. Without `SELECTOR` it rotates to the next account that
is neither disabled nor quarantined. The switch is journaled, verified, and rolled back on
failure.

| Flag | Effect |
|---|---|
| `--force` | Switch even if agy holds a login mswap has not saved (it is kept in `backup-last`), to a quarantined account, or while inside agy. |
| `--wait` | Wait for running agy sessions to exit first. |
| `--wait-timeout SEC` | Give up waiting after SEC seconds (`0` = wait forever). Exit `2` if agy is still running. |
| `--resume` | Wait for agy to exit, switch, then print the command that continues your conversation. Not available inside agy. |

```text
mswap switch            # rotate
mswap switch work
mswap switch 2 --wait
```

### `mswap current`

Prints the active account on one line (`slot  email  · alias name`). Exit `4` when agy is
signed out.

### `mswap remove SELECTOR [--yes]`

Deletes a saved account and its stored login. agy itself stays signed in. In a terminal it asks first; in a script
it refuses (exit `64`) unless you pass `--yes` (`-y`).

### `mswap alias SELECTOR NAME` / `mswap alias SELECTOR --clear`

Sets or clears an alias.

### `mswap disable SELECTOR` / `mswap enable SELECTOR`

A disabled account is skipped by rotation and autopilot. You can still switch to it by naming
it.

---

## Watching usage

### `mswap status [--format FMT]`

A one-line, cache-only summary for shell prompts. It never touches the network and prints
nothing (exit `0`) when there is no active account, so a prompt never breaks.

Default format: `{slot}:{email_short} G{gemini_5h}% C{3p_5h}%`, for example `1:alice G99% C100%`.

Placeholders: `{slot}` `{email}` `{email_short}` `{alias}` `{gemini_5h}` `{gemini_week}`
`{3p_5h}` `{3p_week}` `{age}`. Missing data shows as `?`. An unknown placeholder is a usage
error. Prompt setups are in [prompt-integration.md](prompt-integration.md).

### `mswap watch [--force]`

A live dashboard that redraws every second and refreshes quota at most as often as the poll
policy allows. It needs an interactive terminal.

Keys: `q` or Esc quit, `r` refresh, `s` switch to the next account, `1`-`9` switch to that
slot. `--force` allows switching while inside agy.

### `mswap log [-n N]`

Shows the audit trail (switches, autopilot decisions, quarantines, hook events), newest last.
`-n` is how many events to show (default 20).

---

## Autopilot

### `mswap auto [--once] [--dry-run] [--interval SEC] [--threshold N] [--strategy S] [--focus F] [--force]`

Switches for you before a limit stops the work. It keeps running and checks every `--interval`
seconds (default 60) until you press Ctrl-C.

| Flag | Effect |
|---|---|
| `--once` | Run one check and exit. Exit `0` switched, `2` held, `3` blocked. |
| `--dry-run` | Decide, but never switch. |
| `--threshold N` | Percent used in the tightest bucket that triggers a switch (default from settings, 90). |
| `--strategy S` | `best` or `consume-first`. |
| `--focus F` | `auto`, `gemini`, `3p` or `both`. |
| `--force` | Allow switching while inside agy. |

With `--json` every check is one JSON object per line (an event stream).

### `mswap hook install|remove|status`

Registers (or removes, or reports) an agy `Stop` hook named `mswap-autopilot`, so autopilot
checks happen at the end of each agy turn. Your other hooks are untouched. By default the hook
only notifies; set `autopilot.hook_action` to `switch` to let it switch.

### `mswap schedule install|remove|status [--every MIN]`

Runs `mswap auto --once` in the background through Windows Task Scheduler, every `MIN` minutes
(1-60, default 5), only while you are logged on. On macOS and Linux it uses launchd and
systemd.

---

## Settings and portability

### `mswap config [list | get KEY | set KEY VALUE | unset KEY | path]`

Reads and writes `settings.toml`. With no action it lists every setting; defaults are marked
`(default)`. Keys are dotted:

| Key | Values |
|---|---|
| `autopilot.threshold` | 50-99 |
| `autopilot.margin` | 0-50 |
| `autopilot.cooldown` | 60-86400 (seconds) |
| `autopilot.strategy` | `best`, `consume-first` |
| `autopilot.focus` | `auto`, `gemini`, `3p`, `both` |
| `autopilot.hook_action` | `notify`, `switch` |
| `ui.ascii` | `true`, `false` |
| `ui.color` | `auto`, `always`, `never` |
| `updates.check` | `true`, `false` |

An invalid value is a usage error that names the allowed values. Unknown keys already in the
file are kept. Comments in the file are **not** preserved when mswap rewrites it.

```text
mswap config set autopilot.threshold 85
mswap config get updates.check
```

### `mswap export FILE [--accounts SEL,...]`

Writes saved accounts to a passphrase-encrypted file (scrypt + AES-256-GCM). Needs the
optional extra: `uv tool install "mswap[export]"`. The passphrase is asked for twice (12+
characters) or read from `MSWAP_EXPORT_PASSPHRASE`; it is never taken from the command line.
Anyone with the file and the passphrase can use those accounts. It never overwrites: if
`FILE` already exists the command stops with an error (choose a new name or delete the old
file). See the README section "Moving Accounts Between Machines".

### `mswap import FILE [--force]`

Restores accounts from a bundle. New emails take the next free slot; emails you already have
are skipped, unless `--force` is given or your saved login for them is quarantined. It never
changes the active agy login. It works out every slot first, so running out of slots changes
nothing, and if saving fails part-way the logins it already wrote are removed or restored.
Prints `Imported N, updated N, skipped N.`

### `mswap completions powershell|bash|zsh|fish`

Prints a tab-completion script generated from mswap's own grammar. Install lines are in
[completions.md](completions.md).

---

## Health and support

### `mswap doctor [--repair] [--online]`

Checks the agy install, the login, the saved accounts, the journal, storage and (on Windows)
launcher blocking. `--online` adds token refresh and quota API checks. `--repair` recovers an
interrupted switch and cleans up stale session files. Exit `1` when any check fails; warnings
exit `0`. Check IDs are explained in [troubleshooting.md](troubleshooting.md).

### `mswap debug record [--out DIR]`

Saves a bug-report capture that is safe to attach to a GitHub issue. For the active account it
calls the quota summary, `loadCodeAssist` and `fetchAvailableModels` endpoints and writes the
**shape** of each response: every text value becomes `<str:N>` (N is its length), numbers and
true/false stay, and only these labels are kept as they are: `window`, `bucketId`,
`displayName`, `id`, `modelProvider`, `tokenType`, `status`, `reasonCode`. It also writes
`env.json` with the mswap, agy and Python versions, the OS and architecture, and the vault
backend name.

Before it finishes, mswap scans everything it wrote. If anything looks like a token, a secret
or an email, it deletes the capture and exits `6` without saving. Your login is only read,
never changed.

| Flag | Effect |
|---|---|
| `--out DIR` | Where to write. Must not exist or must be empty. Default: a `debug-<timestamp>` folder in mswap's data folder. |

```text
mswap debug record
Saved to C:\...\mswap\debug-20261002-120000. Safe to attach to a GitHub issue.
```

Files: `quota_summary.json`, `load_code_assist.json`, `fetch_available_models.json`,
`env.json`. If one endpoint fails, its file holds only `{"error": {"status": N, "kind": "..."}}`;
if all three fail the command fails and saves nothing. Exit `4` when agy is signed out.

### `mswap shim install [--dir DIR] [--force]`

Windows only. Writes `mswap.cmd`, a launcher that runs `python -P -m mswap` (`-P` keeps the current directory out of Python's
import path), so Smart App Control
never sees an unsigned `.exe`. `DIR` defaults to `%USERPROFILE%\.local\bin`. A file there that
is not an mswap shim is refused unless `--force`, which backs it up to `mswap.cmd.bak`. It
tells you whether `DIR` is on your PATH.

---

## Update check

After a human `mswap list` or `mswap doctor`, mswap may print
`mswap X is available (you have Y): uv tool install --force git+https://github.com/shaurya-disciplined/mswap@vX`.
It asks GitHub for the latest release at most once a day
(3 second timeout, silent on failure) and never prints this with `--json` or `--quiet`, or in
`status`, `hook`, `auto` or `schedule`. Turn it off with `mswap config set updates.check false`
or `MSWAP_NO_UPDATE_CHECK=1`. mswap never upgrades itself.

## Environment variables

| Variable | Effect |
|---|---|
| `MSWAP_HOME` | Use this folder for all mswap state files. |
| `MSWAP_AGY_EXE` | Path to the agy binary. |
| `MSWAP_AGY_STATE` | agy's CLI state folder (where `hooks.json` lives). |
| `MSWAP_VAULT` | `native` (default), or `file` for the opt-in Linux file fallback. |
| `MSWAP_EXPORT_PASSPHRASE` | Passphrase for `export` and `import` in scripts. |
| `MSWAP_NO_UPDATE_CHECK` | `1` turns the update check off. |
| `NO_COLOR`, `MSWAP_ASCII` | Plain output (see global options). |
| `MSWAP_DEBUG` | `1` prints tracebacks on internal errors (still redacted). |
