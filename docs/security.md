# Security Policy & Storage Model

`mswap` manages credentials locally on your machine. This document explains where sensitive data is stored, what is never stored, and how to report vulnerabilities.

---

## What Is Stored Where

| Data Item | Storage Location | Protection / Encryption | Lifetime |
|---|---|---|---|
| **Active agy Token** | `gemini:antigravity` | Windows Credential Manager (DPAPI encrypted) | Managed by agy; updated during `mswap switch` |
| **Saved Account Tokens** | `mswap:slot1`, `mswap:slot2`, ... | Windows Credential Manager (DPAPI encrypted) | Until removed with `mswap remove` |
| **Last Backup Token** | `mswap:backup-last` | Windows Credential Manager (DPAPI encrypted) | Overwritten on each switch |
| **Original Backup Token** | `mswap:backup-original` | Windows Credential Manager (DPAPI encrypted) | Permanent baseline safety backup |
| **Account Metadata** | `%LOCALAPPDATA%\mswap\accounts.json` | Local user file permissions | Until removed |
| **Transaction Journal** | `%LOCALAPPDATA%\mswap\journal.json` | Local user file permissions | In-flight during switches; cleared on commit |
| **OAuth Client Config** | `%LOCALAPPDATA%\mswap\client.json` | Local user file permissions | Cache of discovered agy OAuth client ID & secret |
| **Event History** | `%LOCALAPPDATA%\mswap\events.log` | Local user file permissions | Append-only audit record of switch events |

### Metadata vs Credentials

`mswap` maintains a strict separation between metadata and credentials:
- **`accounts.json` never contains tokens.** It contains slot numbers, email addresses, aliases, and SHA-256 token fingerprints (`sha256(refresh_token)[:16]`).
- **All credential blobs reside exclusively in the OS vault** (Windows Credential Manager).
- Plaintext OAuth refresh tokens and access tokens are never written to disk files.

---

## What mswap Never Stores

1. **Passwords**: `mswap` never prompts for, reads, or stores Google account passwords.
2. **Prompts & Transcripts**: `mswap` never reads, records, or stores user prompts, code snippets, or agent conversation history.
3. **Third-party Credentials**: `mswap` never touches GitHub CLI (`gh`), Git credentials, AWS, Azure, or MCP configuration files.
4. **Browser Cookies**: `mswap` does not access browser profiles or session cookies.

---

## Threat Model & Boundary Notes

- **Workstation Security Boundary**: `mswap` runs with the permissions of the current logged-in user. Any process running under the same user account on Windows has access to the user's DPAPI-protected Credential Manager store.
- **Direct Official Communication**: Network requests are made directly over HTTPS/TLS to Google's official endpoints:
  - OAuth token refresh: `https://oauth2.googleapis.com/token`
  - Quota reporting: `https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist`
  `mswap` uses no intermediary proxies, telemetry servers, or third-party cloud infrastructure.
- **Zero Runtime Dependencies**: The core package relies solely on the Python standard library, eliminating third-party supply-chain attack surfaces.
- **Automated Redaction**: All error handlers and output routines pass through regex scrubbers that redact OAuth access tokens (`ya29.*`), refresh tokens (`1//*`), and client secrets (`GOCSPX-*`).

---

## Bug-Report Captures

`mswap debug record` is built so that a capture can be attached to a public issue:

- It reads the live login in memory to call agy's quota endpoints. It never writes the login anywhere.
- Each response is reduced to its shape: every text value becomes `<str:N>`, and only a short allow-list of labels (`window`, `bucketId`, `displayName`, `id`, `modelProvider`, `tokenType`, `status`, `reasonCode`) is kept as it is. A kept label that looks like a token or an email is replaced as well.
- After writing, mswap scans the files on disk for tokens, client secrets, JWTs and anything email-shaped. On any hit it deletes the capture and exits without saving.
- Server error messages are never recorded, only the HTTP status and an error kind.

---

## Reporting Vulnerabilities

If you discover a security vulnerability in `mswap`, please report it privately:

1. Use [GitHub Security Advisories](https://github.com/shaurya-disciplined/mswap/security/advisories) to submit a private report.
2. Do **not** create public GitHub issues or discussions for suspected security vulnerabilities.
3. Never include real tokens, credentials, or client secrets in bug reports or correspondence.
