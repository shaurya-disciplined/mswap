# JSON output

Every command that takes `--json` prints **one JSON object on stdout** and nothing else.
Errors and warnings go to stderr as usual.

```json
{"schema": 1, "ok": true, "command": "list", "data": { ... }}
```

| Field | Meaning |
|---|---|
| `schema` | Output version. It is `1` today. |
| `ok` | `true` on success. |
| `command` | The command that ran (`enable` and `disable` report their own name). |
| `data` | The command's result, described below. |

**Compatibility.** New fields may be added at any time, so ignore fields you do not know.
Renaming or removing a field, or changing its type, needs `"schema": 2`.

**Errors** keep the same exit code as the plain-text run:

```json
{"schema": 1, "ok": false, "command": "switch",
 "error": {"code": 6, "kind": "UnsafeOperation", "message": "...", "hint": "..."}}
```

`kind` is the error class name (`UsageError`, `NotSignedIn`, `TokenDead`, `AgyNotFound`,
`UnsafeOperation`, `LockTimeout`, `NothingToDo`, `NoViableTarget`, `ApiError`, `NetworkError`,
`VaultError`, `CorruptState`). `hint` is `null` when there is none. Codes are listed in
[commands.md](commands.md#exit-codes). Messages and hints are redacted: they never hold a token.

Times are ISO 8601 strings. `null` means "not set" or "not known".

## Shared shapes

**Account** (used by `current`, and as the base of `list` entries):

```json
{"slot": 1, "email": "alice@example.com", "fp": "5c48cb6c4dc2e6e1",
 "added_at": "2026-10-02T01:23:28+05:30", "updated_at": "2026-10-02T01:23:28+05:30",
 "alias": null, "disabled": false, "quarantined": null, "plan": null}
```

`fp` is a short fingerprint of the saved login, not a secret. `quarantined` is `null` or
`{"reason": "invalid_grant" | "revoked" | "unknown", "at": "..."}`.

**Bucket**: `{"window": "5h", "remaining": 0.99, "reset_at": "..."}`. `remaining` is the
fraction left, 0 to 1. `reset_at` is `null` when the bucket is full.

**Pool**: `{"key": "gemini", "name": "Gemini", "buckets": [Bucket, ...]}`. Keys seen so far are
`gemini` and `3p` (Claude & GPT). Treat unknown keys and windows as valid.

## Account commands

### `list`

```json
{"active_slot": 1,
 "accounts": [{
   "slot": 1, "email": "alice@example.com", "alias": null, "active": true,
   "disabled": false, "quarantined": null, "plan": "g1-pro-tier",
   "usage": {
     "fetched_at": "2026-10-02T12:00:00+00:00", "stale": false, "error": null,
     "pools": [{"key": "gemini", "name": "Gemini", "buckets": [
       {"window": "weekly", "remaining": 0.99, "reset_at": "2026-10-08T04:59:12+00:00",
        "pace": {"expected_used": 0.12, "actual_used": 0.01, "ahead": false,
                 "exhaust_at": null, "lasts_to_reset": true}}]}]}}]}
```

`active_slot` is `null` when agy is signed in to an account mswap has not saved (or is signed
out). `usage.error` is a short message or `null`; `pools` is empty when no quota is known yet.
`pace` is `null` except for weekly buckets that are past their first day and have a reset time. With no saved accounts: `{"active_slot": null,
"accounts": []}`.

### `current`

`{"active": Account | null, "live_present": true}`. `active` is `null` when agy's login is not
one of your saved accounts. When agy is signed out the command fails with `NotSignedIn`.

### `add`

`{"status": "added" | "updated", "slot": 2, "email": "...", "alias": null, "signed_out": false}`

### `switch`

`{"status": "switched" | "already_active", "from_slot": 1, "to_slot": 2, "agy_running": false}`

`from_slot` is `null` when agy held no saved account.

### `remove`

`{"removed_slot": 2, "email": "..."}`

### `alias`

`{"slot": 2, "alias": "work"}` (`alias` is `null` after `--clear`).

### `enable` / `disable`

`{"slot": 2, "disabled": true}`

## Usage commands

### `status`

```json
{"slot": 1, "email": "alice@example.com", "email_short": "alice", "alias": null,
 "gemini_5h": 99, "gemini_week": 99, "3p_5h": 100, "3p_week": 100,
 "age": "2m", "formatted": "1:alice G99% C100%"}
```

The percentages are whole numbers (rounded down) or `null` when unknown. With no active
account `status` prints nothing at all, not even JSON, and exits `0`.

### `log`

`{"events": [Event, ...]}`, newest last. An event is the raw audit record: `at`, `event`, plus
fields that depend on the event.

| `event` | Extra fields |
|---|---|
| `switch` | `from_slot`, `to_slot`, `forced`, `source` (`manual` or `autopilot`) |
| `hold`, `blocked` | `reason`, `from_slot`, `to_slot`, `focus`, `active_pressure`, `target_pressure`, `dry_run`, `source` |
| `hook_switch`, `hook_hold`, `hook_blocked` | `reason`, `from_slot`, `to_slot`, `hook_action` |
| `quarantine` | `slot`, `email`, `reason` |
| `writeback_suspected` | `from_slot`, `to_slot` |
| `sign_out` | `from_fp` |
| `error` | `reason` |

### `auto`

An event stream: **one JSON object per line**, not the envelope above.

```json
{"schema": 1, "event": "hold", "at": "2026-10-02T12:00:00+00:00", "reason": "...",
 "from_slot": 1, "to_slot": null, "focus": ["gemini"], "active_pressure": 0.4,
 "target_pressure": null, "dry_run": false}
```

`event` is `switch`, `hold` or `blocked`. A failure while running is reported as
`{"schema": 1, "event": "error", "at": "...", "message": "..."}` before the command exits.

## Setup commands

### `config`

| Action | `data` |
|---|---|
| `get KEY` | `{"key": "autopilot.threshold", "value": 90, "default": true}` |
| `set KEY VALUE` | `{"action": "set", "key": "...", "value": 85}` |
| `unset KEY` | `{"action": "unset", "key": "..."}` |
| `path` | `{"path": "...", "comments_preserved": false}` |
| `list` | `{"settings": {"autopilot": {"threshold": 90, ...}, "ui": {...}, "updates": {...}}, "defaults": ["autopilot.threshold", ...], "items": [{"key": "...", "value": 90, "default": true}, ...]}` plus each section again at the top level of `data` |

`defaults` lists the keys that still have their default value.

### `export`

`{"file": "accounts.mswap", "count": 3}`

### `import`

`{"imported": 2, "updated": 1, "skipped": 1}`

### `hook`

| Action | `data` |
|---|---|
| `install` | `{"action": "install", "changed": true, "hooks_path": "...", "set_name": "mswap-autopilot"}` |
| `remove` | `{"action": "remove", "changed": true}` |
| `status` | `{"installed": true, "command": "...", "hooks_path": "...", "backup_exists": false}` (`command` is `null` when not installed) |

### `shim`

`{"path": "...\\mswap.cmd", "dir": "...", "backup": null, "on_path": true}`

## Health and support

### `doctor`

```json
{"schema": 1, "ok": true, "command": "doctor",
 "data": {"checks": [{"id": "agy.installed", "status": "ok", "message": "...", "hint": null}],
          "ok": true},
 "checks": [ ... same list ... ]}
```

`status` is `ok`, `warn` or `fail`. `ok` is `false` when any check fails (exit `1`); warnings
alone keep it `true`. After `--repair`, `data.repaired` lists what was fixed. The top-level
`checks` (and `repaired`) are copies kept for older scripts; read them from `data`.

### `debug record`

`{"dir": "...", "files": ["env.json", "fetch_available_models.json", "load_code_assist.json", "quota_summary.json"]}`

The files themselves are shape-only captures, not part of this contract: see
[commands.md](commands.md#mswap-debug-record---out-dir).

## No JSON output

`watch` (interactive), `completions` (a script) and `schedule` print plain text only.
