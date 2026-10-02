# How mswap Works

`mswap` is an account switcher, quota viewer, and safety layer for Google's Antigravity CLI (`agy`). It lets you switch between multiple Google accounts in seconds without losing active context, revoking tokens, or going through browser authentication prompts.

This document describes `mswap`'s architecture, storage model, and transaction lifecycle.

---

## Architecture Overview

`mswap` is built with pure Python standard library: zero external runtime dependencies. It never intercepts agy traffic, never proxies network requests, and never touches browser cookies or external developer credentials (like GitHub CLI `gh`).

```mermaid
flowchart TD
    subgraph Host["Host Machine"]
        subgraph Processes["Running Processes"]
            AGY["Antigravity CLI (agy)"]
            MSWAP["mswap CLI"]
        end

        subgraph SecureVault["OS Secure Vault (Windows Credential Manager)"]
            LIVE["gemini:antigravity<br/>(Active Session Token)"]
            SLOT1["mswap:slot1<br/>(Saved Account 1 Token)"]
            SLOT2["mswap:slot2<br/>(Saved Account 2 Token)"]
            SLOTN["mswap:slotN<br/>(Saved Account N Token)"]
            BACKUP_LAST["mswap:backup-last<br/>(Rollback Target)"]
            BACKUP_ORIG["mswap:backup-original<br/>(First Stored State)"]
        end

        subgraph DataDir["mswap Data Directory (%LOCALAPPDATA%/mswap)"]
            ACCOUNTS["accounts.json<br/>(Metadata & Fingerprints, No Tokens)"]
            JOURNAL["journal.json<br/>(Active Transaction State)"]
            LOCK["mswap.lock<br/>(Cross-Process Mutex)"]
            CLIENT["client.json<br/>(Discovered agy OAuth Client)"]
            EVENTS["events.log<br/>(Audit History)"]
        end
    end

    AGY <-->|Reads & writes on refresh| LIVE
    MSWAP <-->|Atomic transaction switch| LIVE
    MSWAP <-->|Slot read & write| SLOT1
    MSWAP <-->|Slot read & write| SLOT2
    MSWAP <-->|Slot read & write| SLOTN
    MSWAP -->|Pre-switch backup| BACKUP_LAST
    MSWAP -->|Initial safety backup| BACKUP_ORIG

    MSWAP <-->|Loads & saves metadata| ACCOUNTS
    MSWAP <-->|Transaction begin / commit| JOURNAL
    MSWAP <-->|Process concurrency lock| LOCK
    MSWAP <-->|Reads OAuth client credentials| CLIENT
    MSWAP -->|Appends switch records| EVENTS
```

---

## Where Data Lives

`mswap` splits operational state cleanly into two boundaries:

1. **Credential Vault (OS Native Secret Store)**:
   - On Windows, credentials live in Windows Credential Manager encrypted via Windows DPAPI.
   - `gemini:antigravity`: The live credential read and written by `agy.exe`.
   - `mswap:slot1`, `mswap:slot2`, ...: Saved account tokens managed by `mswap`.
   - `mswap:backup-last`: Copy of the live credential prior to the most recent switch.
   - `mswap:backup-original`: Permanent backup taken before `mswap` performed its first mutation.
   - Plaintext tokens never touch filesystem files on disk.

2. **Data Directory (`%LOCALAPPDATA%\mswap`)**:
   - `accounts.json`: Schema version 2 metadata file. Stores slot numbers, account email addresses, aliases, disabled states, and SHA-256 token fingerprints. Does not contain OAuth tokens or secrets.
   - `journal.json`: Records in-flight switch transactions for automatic recovery on startup.
   - `mswap.lock`: Cross-process mutex file preventing race conditions during concurrent operations.
   - `client.json`: Discovered agy OAuth client ID and secret extracted from `agy.exe`.
   - `events.log`: Local append-only event log recording switch actions and timestamps.

---

## The Switch Transaction Protocol

Every account switch is a journaled, atomic transaction. If `mswap` or the operating system crashes mid-operation, the next run of `mswap` detects the incomplete transaction and automatically rolls back.

```mermaid
sequenceDiagram
    autonumber
    actor User as User
    participant CLI as mswap switch
    participant Lock as Lock (mswap.lock)
    participant Store as AccountStore (accounts.json)
    participant Vault as Vault (Credential Manager)
    participant Journal as Journal (journal.json)
    participant Process as Process Detection

    User->>CLI: run mswap switch [SELECTOR]
    CLI->>Lock: acquire(timeout=10s)
    CLI->>Store: load() accounts
    CLI->>Vault: read(live_target)
    CLI->>CLI: resolve_target(selector, active)
    CLI->>Vault: read(slot_target(target))
    CLI->>CLI: validate_blob(target_blob)
    CLI->>Journal: begin(op="switch", from, to, live)

    alt Normal Switch Flow
        CLI->>Vault: write(backup-last, live)
        CLI->>Vault: write(backup-original, live) [if first switch]
        CLI->>Vault: write(slot_target(active), live) [keep freshest token]
        CLI->>Vault: write(live_target, target_blob)
        CLI->>Vault: read(live_target) [verify read-back]
        CLI->>Journal: commit()
    else Write or Verify Failure (Rollback)
        CLI->>Vault: write(live_target, live) [best-effort rollback]
        CLI->>Journal: fail()
        CLI-->>User: error: switch failed (rolled back)
    end

    CLI->>Lock: release()
    CLI->>Process: check running_agy()
    CLI-->>User: Switched agy to target account (warn if agy running)
```

### Transaction Steps

1. **Locking**: `mswap` acquires an exclusive file lock (`mswap.lock`) with a 10-second timeout.
2. **Pre-flight Validation**:
   - Ensures target account exists and is not quarantined.
   - Validates that target credential JSON has a valid refresh token.
   - Verifies whether agy is logged into an unsaved account. If an unknown account is active, `mswap` refuses to overwrite it unless `--force` is passed.
3. **Journal Initiation**: Records `op="switch"`, `from_fp`, `to_fp`, and `live_fp` into `journal.json` with status `pending`.
4. **Backup**:
   - Saves current live credential to `mswap:backup-last`.
   - If `mswap:backup-original` does not exist, saves current live credential there as a permanent safety point.
   - Saves freshest live token back into the slot of the account being left.
5. **Atomic Write & Verification**:
   - Writes the target account's credential blob to `gemini:antigravity`.
   - Reads back `gemini:antigravity` and verifies bit-for-bit equality.
   - If read-back fails or writing raises an error, `mswap` immediately rolls back by writing the original live blob back to `gemini:antigravity`, marks the journal failed, and aborts.
6. **Commit**: Updates journal status to `committed`.
7. **Process Awareness**: Inspects running processes. If active `agy.exe` instances are detected, warns the user that running sessions keep tokens in memory until restarted.

---

## Running agy Sessions & Process Detection

When switching accounts:

- `agy` loads credentials into memory upon starting a turn or refreshing tokens.
- If `agy.exe` is running while you run `mswap switch`, `mswap` detects running agy instances via native Windows system queries.
- `mswap` prints a clear notice: `! agy is running (PID 1234). It won't pick up the switch until you restart it.`
- You can use `--wait` to have `mswap switch` wait until running agy instances terminate before completing the switch.
- You can use `--resume` to wait for agy to exit and receive the exact resumption command for your session.

---

## Client Discovery

Antigravity CLI communicates with Google Cloud Code Assist endpoints using an internal OAuth 2.0 client ID and client secret. To maintain zero repo secrets:

- `mswap` memory-maps the local `agy.exe` binary.
- Scans binary byte sequences for Google OAuth client ID (`*.apps.googleusercontent.com`) and secret (`GOCSPX-*`) patterns.
- Stores the discovered pair in `%LOCALAPPDATA%\mswap\client.json` with an executable signature (`size:mtime`).
- Caches the discovery so the binary is only scanned when agy updates.
