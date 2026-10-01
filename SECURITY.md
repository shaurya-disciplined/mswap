# Security Policy

## Reporting a Vulnerability

Please **do not** report security vulnerabilities through public GitHub issues or discussions.

To report a vulnerability privately:
1. Navigate to the repository's [Security tab](https://github.com/shaurya-disciplined/mswap/security).
2. Click **Advisories** and then **Report a vulnerability** (or open [New Advisory](https://github.com/shaurya-disciplined/mswap/security/advisories/new)).
3. Provide details on how to reproduce the issue, along with any relevant technical context.

All vulnerability reports are investigated promptly through GitHub Private Vulnerability Reporting.

## What mswap Stores

`mswap` follows strict local storage and privacy boundaries:
- **Account Metadata:** Stored locally in the application data directory (`accounts.json`, `usage.json`, etc.). This contains non-sensitive metadata such as slot numbers, email addresses, and cryptographic fingerprints (`sha256(refresh_token)[:16]`).
- **Logins & Credentials:** Stored exclusively in your operating system's native credential store (Windows Credential Manager, macOS Keychain, or Linux Secret Service) under the target prefix `mswap:*`.

## What mswap Never Stores

`mswap` is designed with zero-leak principles:
- **Tokens on Disk:** Plaintext OAuth tokens (access tokens `ya29.*`, refresh tokens `1//*`) are never written to disk or configuration files.
- **Passwords:** `mswap` never asks for, manages, or stores account passwords.
- **Remote Telemetry:** `mswap` has no telemetry, analytics, or third-party servers. All requests go directly to Google's official endpoints for token exchange and quota retrieval.

## Supported Versions

Only the latest minor release receives active security updates and patches.

| Version | Supported |
| ------- | --------- |
| 0.1.x   | Yes       |
| < 0.1   | No        |
