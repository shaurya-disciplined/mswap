# Platform Support & Backends

mswap is built to run cross-platform across Windows, macOS, and Linux with native credential store integration and zero runtime dependencies.

## Support Status

| Platform | Credential Backend | Status | Introduced |
|---|---|---|---|
| **Windows** | Windows Credential Manager (`advapi32`) | Supported (Verified) | v0.1.0 |
| **macOS** | Keychain Services (`security` CLI / `MacKeychainVault`) | Experimental | v0.6.0 (W5.S2) |
| **Linux** | Secret Service (`secret-tool` / `SecretToolVault`) | In Development | v0.6.0 (W5.S3) |

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

This script collects keychain attribute names, encoding prefix classes, and lengths only—it **never captures or prints secrets or token values**, and it aborts immediately if any secret pattern is detected. Share the output in the GitHub discussion to help graduate macOS support from experimental to fully supported!
