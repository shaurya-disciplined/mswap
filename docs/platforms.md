# Platform Support & Backends

mswap is built to run cross-platform across Windows, macOS, and Linux with native credential store integration and zero runtime dependencies.

## Support Status

| Platform | Credential Backend | Status | Notes | Introduced |
|---|---|---|---|---|
| **Windows** | Windows Credential Manager (`advapi32`) | **Supported** | Verified with real `agy` installs | v0.1.0 |
| **macOS** | Keychain Services (`security` CLI / `MacKeychainVault`) | **Experimental** | CI-tested backend; real-world storage unverified | v0.6.0 |
| **Linux** | Secret Service (`secret-tool` / `SecretToolVault`) | **Experimental** | CI-tested backend; real-world storage unverified | v0.6.0 |
| **Linux (Headless)** | File Vault fallback (`FileVault` / `CompositeVault`) | **Experimental (Opt-in)** | Unencrypted file storage for headless/WSL environments | v0.6.0 |

---

## macOS Keychain Backend

macOS support uses `MacKeychainVault` (`mswap.vault.macos`), storing generic passwords in the user's login keychain via the native macOS `security` CLI.

### Experimental Status

macOS Keychain integration is labelled **experimental** until verified against real-world `agy` installations on Darwin hardware. When running on macOS without acknowledgement, mswap emits a dim notice to standard error once per process:

```text
macOS support is experimental. See docs/platforms.md.
```

You can acknowledge and suppress this warning in your shell or profile by setting:

```bash
export MSWAP_ACK_EXPERIMENTAL=1
```

### Assumptions & Design Model

1. **Target Mapping**:
   A vault target formatted as `service:account` maps directly:
   - `gemini:antigravity` &rarr; Service `gemini`, Account `antigravity` (matching `go-keyring`, agy's credential library on macOS).
   - `mswap:slot1` &rarr; Service `mswap`, Account `slot1`.
   - `mswap:backup-last` &rarr; Service `mswap`, Account `backup-last`.

2. **Zero Secrets in Command Line Arguments (`argv`)**:
   Writing credentials via `security add-generic-password -w <secret>` on the command line exposes secrets to other users and processes via `ps` and audit logging.
   To prevent this exposure, `MacKeychainVault` writes credentials **strictly over standard input** using `security -i` interactive mode:
   ```text
   add-generic-password -U -s <service> -a <account> -l "<label>" -w <encoded> [<keychain>]
   ```
   Secrets and tokens are never passed in command-line arguments.

3. **Prefix Encoding & Binary Safety**:
   `go-keyring` encodes values with prefixes on macOS to protect against encoding edge cases:
   - Base64 encoding: prefixed with `go-keyring-base64:`
   - Hex encoding: prefixed with `go-keyring-encoded:`
   - Plain values: raw UTF-8 or JSON payload (no prefix).

   On reading, `MacKeychainVault` automatically detects and strips either prefix, decoding the binary payload.
   On writing to the live target (`gemini:antigravity`), mswap inspects the existing live credential and **mirrors the prefix encoding** that agy previously established.
   For mswap's internal account slots (`mswap:slot<N>`), it always stores `go-keyring-base64:` + Base64 for 100% binary safety.

4. **Privacy Protection**:
   User email addresses are never written to Keychain comments, labels, or attributes. The item label is set strictly to `"<service>:<account>"` (e.g. `"mswap:slot1"`). Account email mappings reside in `accounts.json` under your local user data directory.

5. **Custom & Temporary Keychains**:
   By default, operations target the user's default login keychain. To isolate testing or target an alternate keychain database:
   ```bash
   export MSWAP_MAC_KEYCHAIN="/path/to/custom.keychain-db"
   ```

### Helping Verify Real macOS Storage

To help verify where and how agy stores its login on macOS, run the safe, read-only diagnostic probe script:

```bash
bash scripts/spikes/posix_probe.sh
```

This script collects keychain attribute names, encoding prefix classes, and lengths only—it **never captures or prints secrets or token values**, and it aborts immediately if any secret pattern is detected. Share the output in the GitHub discussion if you encounter issues.

---

## Linux Secret Service Backend

Linux support uses `SecretToolVault` (`mswap.vault.linux`), integrating with the freedesktop.org Secret Service API (gnome-keyring, KWallet) through the standard `secret-tool` CLI.

### Experimental Status

Linux Secret Service integration is labelled **experimental** until verified against real-world `agy` installations on Linux systems. When running on Linux without acknowledgement, mswap emits a dim notice to standard error once per process:

```text
Linux support is experimental. See docs/platforms.md.
```

You can suppress this warning by setting:

```bash
export MSWAP_ACK_EXPERIMENTAL=1
```

### Assumptions & Design Model

1. **go-keyring Attribute Mapping**:
   `go-keyring` stores items with Secret Service attributes `{"service": "<service>", "username": "<account>"}`:
   - `gemini:antigravity` &rarr; attributes `service gemini username antigravity` (in default collection).
   - `mswap:slot1` &rarr; attributes `service mswap username slot1 mswap 1`.
   For mswap-managed account entries, the additional attribute `mswap 1` allows efficient searching and listing via `secret-tool search --all mswap 1` without dumping or enumerating unrelated credentials in the user's keyring.

2. **Zero Secrets in Command Line Arguments (`argv`)**:
   `SecretToolVault` writes credentials strictly via `secret-tool store --label <label> <attributes...>` passing the secret payload over **standard input**. Secrets and tokens are never placed into command-line arguments.

3. **Prefix Encoding & Binary Safety**:
   `SecretToolVault` mirrors the go-keyring prefix encoding conventions:
   - Auto-decodes `go-keyring-base64:` and `go-keyring-encoded:` prefixes on reading.
   - For internal slots, always writes `go-keyring-base64:` + Base64 for 100% binary safety.
   - For the live target (`gemini:antigravity`), mirrors the existing prefix encoding (plain, base64, or hex).

4. **Missing Daemon & D-Bus Error Mapping**:
   If `secret-tool` is missing or the session D-Bus daemon is unavailable (e.g. headless or container environments), `SecretToolVault` raises a descriptive `VaultError` with actionable instructions:
   ```text
   No Secret Service available.
   Hint: Install gnome-keyring + libsecret-tools, or set MSWAP_VAULT=file (less secure) on headless machines.
   ```

---

## File Vault Fallback (Headless / WSL)

For headless servers, Docker containers, or minimal WSL environments without a graphical session or D-Bus Secret Service, mswap offers an opt-in file-based storage backend: `FileVault` (`mswap.vault.file`).

### Enabling the File Vault

```bash
export MSWAP_VAULT=file
```

When active, mswap warns once per process:
```text
! Using the file vault: logins are stored unencrypted in /path/to/vault. Prefer a Secret Service.
```

*Note: The file vault is strictly rejected on Windows (`UsageError`), where Windows Credential Manager is always present.*

### Security & Architecture

1. **Permissions**:
   The vault directory is created with mode `0700` (`rwx------`). Each credential file and the mapping index are created with mode `0600` (`rw-------`) using `os.open` with `O_CREAT | O_EXCL` and atomic replace to prevent symlink attacks and race conditions.

2. **Obfuscated Names**:
   Credential filenames on disk are SHA-256 hashes of the target name (`sha256(target)[:32]`). A secure `index.json` maps hashes back to target names.

3. **Live Target Routing via `CompositeVault`**:
   `agy` itself reads its login exclusively from the Linux Secret Service. Therefore, when `MSWAP_VAULT=file` is used, mswap constructs a `CompositeVault`:
   - Internal slots (`mswap:slot<N>`) are stored in `FileVault`.
   - The live target (`gemini:antigravity`) continues to route to `SecretToolVault`.
   - If switching is attempted in an environment where Secret Service is truly unavailable, mswap raises:
     ```text
     UsageError: agy's login lives in the Secret Service, which isn't available here.
     ```

### Helping Verify Real Linux Storage

Run the diagnostic probe to check your environment:

```bash
bash scripts/spikes/posix_probe.sh
```
