# mswap threat model

Method: STRIDE over mswap's assets and trust boundaries. Written for the v1.0 security review. Every threat lists what already mitigates it (with `file:line` or the test that proves
it) and what was left over. Anything found during the review was fixed in the same change, with a
test, or is listed in [Residual risks](#residual-risks) with a severity and a reason.

Severity: **CRITICAL** (credential theft or code execution with no precondition), **HIGH**
(code execution or credential exposure with a realistic precondition), **MEDIUM** (data loss,
local disclosure on shared hosts, supply chain), **LOW** (hardening, unlikely preconditions).
At the end of this review there are **no unaddressed CRITICAL or HIGH findings**.

## 1. What mswap is, in one paragraph

mswap keeps several agy (Google Antigravity CLI) logins in the operating system's credential
store and swaps the one agy reads. It also calls two Google endpoints for quota data, can install
an agy `Stop` hook and a background schedule, and can export logins to an encrypted file. It runs
as the current user, in short-lived processes, with no server and no telemetry.

## 2. Assets

| ID | Asset | Where it lives | Why it matters |
|----|-------|----------------|----------------|
| A1 | **Saved logins** (`mswap:slot<N>`, `backup-last`, `backup-original`) | OS credential store (Windows Credential Manager, macOS Keychain, Linux Secret Service; opt-in `FileVault`) | A refresh token is a long-lived Google credential for Gemini/Claude/GPT quota and the account behind it |
| A2 | **The live login** (`gemini:antigravity` or `MSWAP_LIVE_TARGET`) | OS credential store; owned by agy | Whatever is here is the account agy uses right now. mswap rewrites it in exactly one place (`core/switcher.py`, `switch`) |
| A3 | **`client.json`** | data dir | The OAuth client id and secret discovered from the user's own agy install. A public installed-app client, not a user secret, but never printed or committed |
| A4 | **Export files** | wherever the user puts them | AES-256-GCM bundle of A1 plus metadata. Safe only as long as the passphrase is |
| A5 | **`events.log`** (+ `.1`) | data dir | Audit trail: slots, fingerprints, reasons, and in one event type an email address |
| A6 | `accounts.json`, `journal.json`, `usage.json`, `autopilot.json`, `settings.toml`, `update.json` | data dir | Metadata: emails, aliases, token fingerprints. No secrets |
| A7 | agy's `hooks.json` | `~/.gemini/antigravity-cli` | Commands agy runs on events. Write access = code execution as the user |
| A8 | The release pipeline (workflows, PyPI publishing identity) | GitHub | A compromised release reaches every user |

## 3. Trust boundaries

```
                       ┌──────────────── same-user boundary ────────────────┐
  user / shell / agy ──► mswap process ──► B1 OS credential store (A1, A2)
   (cwd = any dir)           │      ├────► B2 data dir (A3, A5, A6)
                             │      ├────► B3 subprocesses: agy, schtasks, tasklist, icacls,
                             │      │        launchctl, systemctl, security, secret-tool, ps
                             │      ├────► B4 network: oauth2.googleapis.com,
                             │      │        cloudcode-pa.googleapis.com, api.github.com
                             │      └────► B5 agy hooks.json (A7)
                             └─ export/import files (A4)          B6 CI/release (A8)
```

| ID | Boundary | Who is on the other side |
|----|----------|--------------------------|
| B1 | OS credential store | The OS, which only releases entries to the same user. Other programs running as that user can read them too |
| B2 | Data dir (`%LOCALAPPDATA%\mswap`, `~/Library/Application Support/mswap`, `$XDG_DATA_HOME/mswap`) | Other local users (POSIX), other programs of the same user |
| B3 | Subprocesses | Programs found by name on `PATH` or in the current directory |
| B4 | Network | Google, GitHub (update check), and anyone on the path (TLS protects it) |
| B5 | agy's hooks file | agy, which executes what is in it |
| B6 | CI | GitHub Actions, third-party actions, PyPI trusted publishing |

**Out of scope, on purpose:** malware already running as the same user. It can read the
credential store, the data dir and agy's own memory without mswap's help, so no mswap control
could stop it. mswap's job is to not make that easier (no plaintext secrets on disk, none in
argv, none in logs) and to not widen exposure to *other* users, *other directories* and *other
hosts*.

## 4. STRIDE

### S: Spoofing

| # | Threat | Mitigations | Status |
|---|--------|-------------|--------|
| S1 | A fake Google endpoint receives tokens | Fixed https URLs, certificate checks left at urllib's defaults; **https only** (`util/http.py`, `UrllibHttp.request`); `test_urllib_http_refuses_plain_http_and_other_schemes` | Closed (F8) |
| S2 | A redirect sends the `Authorization` header or a refresh-token form to another host | **Redirects are never followed** (`util/http.py`, `_NoRedirect`); `test_redirects_are_never_followed_so_credentials_stay_on_the_first_host` | Closed (F8) |
| S3 | A planted program stands in for a system tool (`schtasks`, `tasklist`, `icacls`, `security`, `ps`, ...) | OS tools are resolved from the system directories first (`util/systools.py`, `system_tool`/`run_system`); `test_system_tool_prefers_the_system_directory_over_the_current_directory`. `security` receives the saved login on stdin, so this matters most on macOS | Closed (F2) |
| S4 | A planted `mswap.py`/`mswap/` in the current directory is imported instead of the real package when a launcher runs `python -m mswap` | Every launcher passes `-P` (`util/shellquote.py`, `RUN_MODULE`): the agy hook, the Windows task, the launchd agent, the systemd unit and the shim. `test_every_background_launcher_keeps_the_current_directory_off_sys_path`, `test_python_p_flag_really_blocks_a_module_planted_in_the_cwd` | Closed (F1) |
| S5 | A fake `agy` binary is trusted for client discovery | `MSWAP_AGY_EXE` and the default install path are the user's own configuration; the discovered client is tried against the token endpoint when a refresh token is available (`agy/client_discovery.py`, `discover`) | Accepted (R5): choosing the agy binary is the user's decision |
| S6 | A forged "newer version" nudges users to a bad install | The update check reads only the latest release's tag from `api.github.com`, compares it as a regex-validated version string, and builds the upgrade hint from that validated string alone (digits, dots, `a`/`b`/`rc`); it never installs (`core/updates.py`, `_VERSION_RE`, `is_newer`) | Closed |

### T: Tampering

| # | Threat | Mitigations | Status |
|---|--------|-------------|--------|
| T1 | Another user edits mswap's files | Data dir is `0700` and files `0600` on POSIX **whichever component creates them first** (`util/fsx.py`, `ensure_private_dir`, `write_private_text`, `append_private_text`; used by the store, journal, usage cache, autopilot state, events, settings, update cache, client cache and the lock file); `test_first_writer_creates_a_private_data_dir_and_files` (POSIX CI). Windows relies on the inherited user-only ACL of `%LOCALAPPDATA%` | Closed (F4); see R3 for custom `MSWAP_HOME` |
| T2 | A crash leaves a half-written file or a half-done switch | Atomic writes (temp file + `replace`); the switch is journaled and verified by read-back, with rollback and a "never guess" recovery (`core/switcher.py`, `switch`; `test_switch_readback_mismatch_triggers_rollback`, `test_recovery_scenarios`) | Closed |
| T3 | `hooks.json` is modified in a way that breaks agy, or its permissions are loosened | Only the `mswap-autopilot` key is touched, a first-time backup is kept, and the file's existing mode is preserved (`agy/hooks.py`, `_write_hooks`); `test_hooks_install_keeps_the_existing_file_mode` | Closed (F11) |
| T4 | The executable path is spliced into a command line unquoted or half-quoted, so a path with a space, quote, `$`, backtick or newline breaks it or injects commands | Platform-correct quoting for the hook (`agy/hooks.py`, `_default_command`: `shlex.quote` on POSIX, refused-if-unquotable on Windows), the scheduled task (`util/schedule_win.py`, `build_install_argv`), systemd (`util/schedule_posix.py`, `quote_systemd_arg`: escapes `\`, `"`, `%`, `$`, newlines), launchd (argv array in a plist) and the `.cmd` shim (`cli/commands/shim.py`, `shim_text`: `%` doubled). Tests: `test_posix_quote_round_trips_to_one_argument`, `test_systemd_*`, `test_launchd_plist_*`, `test_shim_text_*`, `test_schtasks_*` | Closed (F3) |
| T5 | A third-party GitHub Action is re-pointed at malicious code (moved tag) | **Every action is pinned to a full commit SHA with a version comment**; Dependabot bumps SHA and comment together (`.github/dependabot.yml`); `test_every_action_is_pinned_to_a_commit_sha_with_a_version_comment` | Closed (F7) |
| T6 | Attacker-shaped text (a tag name) is spliced into a workflow shell script | Event text goes through `env:` (`release.yml`, `post-release.yml`); `test_no_event_text_is_interpolated_into_a_shell_script` | Closed (F7) |
| T7 | A tampered or crafted export bundle | AES-256-GCM authenticates the whole payload; scrypt cost parameters are bounded before any work is done (`core/bundle.py`, `decrypt_bundle`; `test_bundle_rejects_out_of_bounds_kdf_params`, `test_bundle_tampered_kdf_params`); every entry is validated before anything is written (`cli/commands/import_.py`, `_parse_entry`) | Closed |
| T8 | An import fails half-way and leaves logins in the credential store that no account record points to | The import is **planned first** (slots, replacements, the 99-account limit) and only then written; a failure restores or removes every login it wrote (`cli/commands/import_.py`, `_plan_import`, `_apply_import`); `test_import_out_of_slots_mid_bundle_writes_nothing`, `test_import_vault_failure_removes_the_logins_it_already_wrote`, `test_import_vault_failure_restores_a_replaced_login` | Closed (F6) |

### R: Repudiation

| # | Threat | Mitigations | Status |
|---|--------|-------------|--------|
| Rep1 | "Who switched my account, and why?" cannot be answered | `events.log` records every switch, quarantine and autopilot decision with a timestamp and its source (`core/events.py`, `emit`); `mswap log` | Closed |
| Rep2 | The log can be edited or truncated by the same user | Not tamper-evident, and rotation at 1 MB keeps only one older file (`core/events.py`, `MAX_LOG_SIZE`) | Accepted (R8): it is a convenience trail on the owner's machine, not forensic evidence |

### I: Information disclosure

| # | Threat | Mitigations | Status |
|---|--------|-------------|--------|
| I1 | A secret appears in a **command line** (visible to every process of the user and in the process list) | The only subprocesses that handle a credential get it on **stdin**: `secret-tool store` (`vault/linux.py`, `_run`/`write`) and `security -i` (`vault/macos.py`, `write`). The Windows backend uses the Credential Manager API through ctypes. **A suite-wide guard** (`tests/conftest.py`, `_no_secrets_in_argv`) wraps `subprocess.Popen` for every test and fails on any argument shaped like `ya29.`, `1//`, `GOCSPX-`, a JWT or a credential JSON key; `test_argv_guard_trips_on_every_secret_shape`, `test_linux_vault_real_argv_carries_no_secret`, `test_macos_vault_real_argv_carries_no_secret` push the real vault commands through it. The export passphrase is deliberately not an option (`test_passphrase_is_never_an_argv_option`) | Closed |
| I2 | A secret reaches **disk** | `accounts.json`, the journal and the usage cache hold fingerprints only (`sha256(refresh_token)[:16]`); import strips the blob before saving (`test_import_does_not_store_blob_in_accounts_json`); `client.json` is `0600` inside a `0700` directory; the export file is `0600`/ACL-restricted and encrypted | Closed |
| I3 | A secret reaches **a log, an error or a traceback** | Every error message and hint goes through `redact` (`cli/__init__.py`, `main`); `MSWAP_DEBUG=1` tracebacks are redacted too, including chained exceptions; events are redacted on write (`core/events.py`, `emit`). `redact` covers `ya29.`, `1//`, `GOCSPX-`, JWTs, JSON keys, **Python-repr keys and `key=value` form bodies** (`util/redact.py`). `test_debug_traceback_redacts_chained_exceptions`, `test_internal_error_exit_70_with_debug_redacted`, `test_redact_python_repr_and_form_bodies` | Closed (F9) |
| I4 | A bug-report capture leaks a token, an email or an identifier | `mswap debug record` keeps only shapes (`<str:N>`), an allow-list of labels, and **small numbers only**: a number of 10+ digits (a project number, an epoch timestamp) is reduced to its size. The files on disk are scanned afterwards and deleted on any hit (`core/debug_record.py`, `shape`, `scan_text`); `test_shape_hides_identifier_sized_numbers_but_keeps_quota_figures`, `test_record_refuses_and_deletes_when_something_secret_shaped_remains` | Closed (F10) |
| I5 | PII: where do emails go? (see [section 5](#5-pii-review-emails)) | Emails live in `accounts.json` (needed), the credential store's user label (needed), `events.log` (one event type) and normal terminal output. They are **not** in argv (`secret-tool` labels use `mswap:slot<N>`), `journal.json`, `usage.json`, export files in the clear, `debug record` output or CI | Accepted for events (R1) |
| I6 | A local user reads another user's data dir | `0700` data dir, see T1 | Closed (F4) |
| I7 | The export passphrase leaks through the environment | It can be supplied through `MSWAP_EXPORT_PASSPHRASE` (for scripts) or typed at a prompt; the environment of a process is readable by the same user and inherited by its children | Accepted (R4): documented, prompt is the default |
| I8 | `FileVault` (opt-in, headless Linux) stores logins unencrypted | Base64 in `0600` files in a `0700` directory, explicit opt-in with a warning, refused on Windows (`vault/file.py`); the docstring now says "not encrypted" | Accepted (R9): documented trade-off for machines with no Secret Service |

### D: Denial of service

| # | Threat | Mitigations | Status |
|---|--------|-------------|--------|
| D1 | Two mswap processes (or the hook and a user) switch at once | Cross-process lock with a 10 s timeout and `LockTimeout` (exit 7) (`core/locking.py`, `acquire`); the lock is an OS file lock, so a crashed process never leaves it held | Closed |
| D2 | A hostile bundle makes scrypt burn CPU/RAM | KDF `n`/`r`/`p` are range- and power-of-two-checked before derivation (`core/bundle.py`, `decrypt_bundle`) | Closed |
| D3 | The audit log grows without bound | Rotated at 1 MB, one older file kept (`core/events.py`) | Closed |
| D4 | A server answers with an enormous body | `UrllibHttp` reads the whole body; only Google/GitHub endpoints over TLS are contacted, and every call has a timeout | Accepted (R2) |
| D5 | A failed export destroys the user's file | Export **never overwrites** (`core/bundle.py`, `write_secure_file` uses `O_EXCL`, plus a pre-check before the passphrase prompt); a failed write only removes what it created; `test_export_refuses_to_overwrite_an_existing_file`, `test_write_secure_file_never_overwrites_even_when_the_pre_check_is_raced` | Closed (F5) |

### E: Elevation of privilege

| # | Threat | Mitigations | Status |
|---|--------|-------------|--------|
| E1 | Code execution from an untrusted repository through the agy hook (cwd module hijack) | `-P` on every launcher, see S4 | Closed (F1) |
| E2 | Command injection through the interpreter path | See T4 | Closed (F3) |
| E3 | The scheduled task runs with more rights than needed | `schtasks /Create` is issued without `/RL HIGHEST` and without credentials, so the task runs as the user with limited rights; launchd and systemd entries are per-user (`util/schedule_win.py`, `build_install_argv`; `util/schedule_posix.py`) | Closed |
| E4 | A compromised release or CI job publishes a malicious package | Actions pinned (T5); workflows run with `permissions: contents: read` and grant `id-token: write` only to the two publish jobs; PyPI publishing is off unless the `PUBLISH_TO_PYPI` repository variable is set, and then sits behind the `pypi` environment's manual approval; no long-lived PyPI token exists (trusted publishing) | Closed |
| E5 | mswap writes the live login in an unintended place | One writer only (`core/switcher.py`, `switch` and its recovery), guarded in tests by the `gemini:antigravity` safety net (`tests/conftest.py`, `_isolate`; `vault/memory.py`, `TOUCHED`) | Closed |
| E6 | Shell injection through subprocess use | No `shell=True` anywhere; arguments are lists; the only `os.system` call is a constant empty string used to enable VT colour on Windows (`ui/theme.py`) | Closed |

## 5. PII review: emails

| Where | Why it is there | Handling |
|-------|-----------------|----------|
| `accounts.json` | Needed to show and select accounts | Local only, `0600` in a `0700` dir |
| Credential store user label | agy/Windows convention | Same-user readable only |
| `events.log`, `quarantine` events | Says which account died | **LOW**, kept: the field is part of the documented event shape and the file is the owner's own. Everything else in the log identifies accounts by slot or fingerprint |
| Terminal output of `list`, `current`, `switch`, `doctor` | The product | Not logged anywhere |
| `--json` output | Scripting | Same as terminal |
| Export bundle | Restore on another machine | Inside the encrypted payload only; `test_export_file_has_no_plaintext_secrets` checks the email is absent from the file |
| `debug record` output, CI logs, issues | Must contain none | Emails are masked as `<str:N>`, and a post-write scan rejects anything email-shaped (`core/debug_record.py`, `scan_text`) |
| Test fixtures and docs | Must contain none | Only `@example.com` addresses; `scripts/check_privacy.py` fails the build otherwise |

Data minimisation was applied where it was free: no email in any subprocess argument, none in
the journal or usage cache, none in the update check. Retention: `events.log` keeps at most two
files (about 2 MB); `mswap remove` deletes an account's login and metadata.

## 6. Findings fixed in this review

| ID | Severity | Finding | Fix |
|----|----------|---------|-----|
| F1 | **HIGH** | Hook, schedule and shim ran `python -m mswap`, which puts the **current directory first on `sys.path`**. agy runs the hook with a project directory as cwd, so a freshly cloned repository containing `mswap.py` or `mswap/__main__.py` executed arbitrary code as the user whenever a turn ended | `-P` on all five launchers (`RUN_MODULE`) |
| F2 | MEDIUM | OS tools were started by bare name. On Windows the current directory is searched first, so a planted `schtasks.exe`/`tasklist.exe`/`icacls.exe` ran; on macOS a planted `security` would have received a saved login on stdin | `util/systools.py`: system directories first; used by every subprocess call |
| F3 | MEDIUM | The interpreter path was quoted only for spaces (systemd) or with naive double quotes (hook, shim). A path containing `$`, a backtick, a quote or a newline broke the command or injected into it; a `%` in the Windows shim was expanded | `util/shellquote.py`, a full systemd escaper, `%%` in the shim |
| F4 | MEDIUM | Only some writers made the data dir `0700`. If the event log, journal, lock or settings were created first the directory kept the process umask (typically `0755`) with `0644` files, exposing emails and `client.json` to other local users | `util/fsx.py` used by every writer; files created `0600` |
| F5 | MEDIUM | `export` silently overwrote any existing file, and a failed write then deleted it | Never overwrites (`O_EXCL`) |
| F6 | MEDIUM | `import` that ran out of slots or hit a vault error half-way left logins in the credential store with no account record | Plan first, write second, roll back on failure |
| F7 | MEDIUM | Actions referenced by mutable tags; two workflows interpolated a tag name into a shell script | SHA pins, `env:` for event text, Dependabot, enforcing tests |
| F8 | LOW | `UrllibHttp` accepted `http://` and followed redirects, forwarding credentials | https only, no redirects |
| F9 | LOW | `redact` missed Python-repr dicts and `key=value` bodies | New patterns |
| F10 | LOW | `debug record` kept every number verbatim, including identifier-sized ones | 10+ digit numbers reduced to their size |
| F11 | LOW | Installing the hook could loosen `hooks.json` permissions to the process umask | Mode preserved |

## 7. Residual risks

Each is also recorded in the private follow-up list with the same severity.

| ID | Severity | Risk | Why it is accepted |
|----|----------|------|--------------------|
| R1 | LOW | `quarantine` events in `events.log` carry an email | Part of the event shape the stability contract freezes; owner-only file; slot already identifies the account |
| R2 | LOW | No response-size cap in `UrllibHttp` | Only fixed Google/GitHub hosts over TLS with timeouts; a malicious body needs a TLS compromise. Capping needs the test doubles reworked; revisit if more hosts are ever added |
| R3 | LOW | On Windows, a custom `MSWAP_HOME` outside `%LOCALAPPDATA%` gets no ACL hardening; on POSIX mswap `chmod 0700`s whatever directory `MSWAP_HOME` names | The default location is already user-only; pointing the data dir elsewhere is an explicit choice |
| R4 | LOW | `MSWAP_EXPORT_PASSPHRASE` is visible in the process environment | Needed for unattended use; the interactive prompt is the default and the docs say so |
| R5 | LOW | `MSWAP_AGY_EXE` / the agy path decides which binary is run for `--version` and client discovery | Whoever controls the user's environment already controls agy |
| R6 | INFO | Same-user malware can read A1/A2 | Out of scope (section 3) |
| R7 | LOW | A Windows interpreter path containing `%NAME%` is expanded by `cmd.exe` when agy runs the hook through it | Cannot be escaped reliably on a `cmd` command line; needs an unusual install path that names a defined variable |
| R8 | LOW | `events.log` is not tamper-evident | Convenience trail, not evidence |
| R9 | LOW | `FileVault` is not encrypted | Opt-in fallback for headless hosts, warned about on use |
| R10 | LOW | agy itself is not under mswap's lock: it may rewrite the live login while mswap switches | Detected after the fact by the write-back detector and documented in `docs/how-it-works.md`; the switch is read-back verified |

## 8. Mandatory checks, as shipped

| Check | Where it is enforced |
|-------|----------------------|
| No secret in any subprocess argv | `tests/conftest.py` autouse `_no_secrets_in_argv` (every test), `tests/unit/test_security_hardening.py` |
| Data dir `0700`, files `0600` on POSIX | `util/fsx.py`; `test_first_writer_creates_a_private_data_dir_and_files` (runs on the Linux and macOS CI jobs) |
| Quoting with spaces and quotes: hook, task, unit, plist, shim | `tests/unit/test_security_hardening.py` |
| `MSWAP_DEBUG` tracebacks redacted | `test_debug_traceback_redacts_chained_exceptions` |
| Actions pinned to SHAs, Dependabot keeps them fresh | `tests/unit/test_workflow_pinning.py`, `.github/dependabot.yml` |

## 9. Reviewing a future change

Ask of any new code: Does a credential cross a process boundary other than stdin? Is a new file
created through `util/fsx.py`? Is a new subprocess started through `util/systools.py`, with a list
and no shell? Is a path spliced into a command line through `util/shellquote.py`? Does a new URL
use https and stay on a fixed host? Does a new log line or error carry anything `redact` would
not catch? Does a new workflow step pin its action and keep event text out of `run:`?
