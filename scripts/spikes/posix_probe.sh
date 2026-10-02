#!/usr/bin/env bash
# posix_probe.sh · Safe, read-only diagnostic probe for agy storage on macOS & Linux.
# Part of mswap W5.S1 spike kit (research question U5).
#
# Rules:
# - Strictly read-only: never modifies keychain, secret store, or filesystem.
# - Zero secret leakage: never captures or prints secret tokens (ya29., 1//, etc.).
# - No sudo required.

set -euo pipefail

REPORT_FILE="${HOME}/mswap-probe-report.txt"

# Ensure private permissions for the report file
touch "${REPORT_FILE}"
chmod 600 "${REPORT_FILE}"
: > "${REPORT_FILE}"

log() {
    echo "$@" >> "${REPORT_FILE}"
}

echo "=== mswap POSIX agy Storage Probe (W5.S1) ==="
echo "Writing report to: ${REPORT_FILE}"
echo ""

log "================================================================================"
log "mswap POSIX agy Storage Probe Report"
log "Generated at: $(date -u '+%Y-%m-%d %H:%M:%SZ' 2>/dev/null || date)"
log "================================================================================"
log ""

# -----------------------------------------------------------------------------
# 1. System & agy binary paths
# -----------------------------------------------------------------------------
log "## 1. System & agy Binary Paths"
log "uname -sm: $(uname -sm)"
log "uname -a: $(uname -a)"
log ""

AGY_BIN="$(command -v agy 2>/dev/null || true)"
if [ -n "${AGY_BIN}" ]; then
    log "command -v agy: ${AGY_BIN}"
else
    log "command -v agy: not found on PATH"
fi
log ""

log "Candidate binary path checks:"
for candidate in "${HOME}/.local/bin/agy" "/usr/local/bin/agy" "/opt/homebrew/bin/agy" "${HOME}/Applications" "/Applications"; do
    if [ -e "${candidate}" ]; then
        log "  ${candidate}: exists"
    else
        log "  ${candidate}: not found"
    fi
done
log ""

log "agy version check:"
if [ -n "${AGY_BIN}" ]; then
    AGY_VER="$("${AGY_BIN}" --version 2>&1 || true)"
    log "  ${AGY_BIN} --version: ${AGY_VER}"
else
    log "  agy binary not available to run --version"
fi
log ""

# -----------------------------------------------------------------------------
# 2. Directory structure (names only)
# -----------------------------------------------------------------------------
log "## 2. Directory Structure (Names Only)"

if [ -d "${HOME}/.gemini" ]; then
    log "Entries in ~/.gemini:"
    ls -la "${HOME}/.gemini" 2>/dev/null >> "${REPORT_FILE}" || true
else
    log "~/.gemini directory does not exist."
fi
log ""

if [ -d "${HOME}/.gemini/antigravity-cli" ]; then
    log "Entries in ~/.gemini/antigravity-cli:"
    ls -la "${HOME}/.gemini/antigravity-cli" 2>/dev/null >> "${REPORT_FILE}" || true
else
    log "~/.gemini/antigravity-cli directory does not exist."
fi
log ""

# -----------------------------------------------------------------------------
# 3. macOS Keychain Storage Check
# -----------------------------------------------------------------------------
log "## 3. macOS Keychain Storage (Darwin)"

if [ "$(uname -s)" = "Darwin" ] || command -v security >/dev/null 2>&1; then
    echo "Checking macOS Keychain..."
    echo "Note: If macOS prompts for Keychain access, click 'Allow' (not 'Always Allow')."
    log "macOS security tool detected."
    log ""

    log "Attributes for generic password (service=gemini, account=antigravity):"
    # security find-generic-password WITHOUT -w or -g prints attributes only.
    # Keep lines for svce, acct, labl, cdat.
    ATTRS="$(security find-generic-password -s gemini -a antigravity 2>&1 | grep -E '"(svce|acct|labl|cdat)"' || true)"
    if [ -n "${ATTRS}" ]; then
        log "${ATTRS}"
    else
        log "  No matching attributes found or item does not exist in login keychain."
    fi
    log ""

    log "Secret length and encoding prefix class (computed via python3, secret never printed):"
    if command -v python3 >/dev/null 2>&1; then
        PY_CLASSIFIER='import sys; v=sys.stdin.read().strip(); print("len:", len(v)); print("prefix:", "go-keyring-base64" if v.startswith("go-keyring-base64:") else "go-keyring-encoded" if v.startswith("go-keyring-encoded:") else "json" if v.startswith("{") else "other")'
        # Note: -w extracts password, immediately piped to python stdin without writing to disk or terminal
        PW_META="$(security find-generic-password -s gemini -a antigravity -w 2>/dev/null | python3 -c "${PY_CLASSIFIER}" 2>/dev/null || true)"
        if [ -n "${PW_META}" ]; then
            log "${PW_META}"
        else
            log "  Could not read secret value or item not found."
        fi
    else
        log "  python3 not found; unable to classify value prefix/length safely."
    fi
else
    log "Not macOS / security tool not available. Skipping Keychain check."
fi
log ""

# -----------------------------------------------------------------------------
# 4. Linux Secret Service Storage Check
# -----------------------------------------------------------------------------
log "## 4. Linux Secret Service Storage"

SECRET_TOOL="$(command -v secret-tool 2>/dev/null || true)"
if [ -n "${SECRET_TOOL}" ]; then
    log "command -v secret-tool: ${SECRET_TOOL}"
    log ""

    log "secret-tool lookup length & prefix class (service=gemini, username=antigravity):"
    if command -v python3 >/dev/null 2>&1; then
        PY_CLASSIFIER='import sys; v=sys.stdin.read().strip(); print("len:", len(v)); print("prefix:", "go-keyring-base64" if v.startswith("go-keyring-base64:") else "go-keyring-encoded" if v.startswith("go-keyring-encoded:") else "json" if v.startswith("{") else "other")'
        LOOKUP_META="$(secret-tool lookup service gemini username antigravity 2>/dev/null | python3 -c "${PY_CLASSIFIER}" 2>/dev/null || true)"
        if [ -n "${LOOKUP_META}" ]; then
            log "${LOOKUP_META}"
        else
            log "  Lookup returned empty or item not found."
        fi
    else
        log "  python3 not found; unable to classify value prefix/length safely."
    fi
    log ""

    log "secret-tool search attributes (label and attribute.* only; secret values discarded):"
    SEARCH_ATTRS="$(secret-tool search service gemini username antigravity 2>/dev/null | grep -E '^(label|attribute\.)' || true)"
    if [ -n "${SEARCH_ATTRS}" ]; then
        log "${SEARCH_ATTRS}"
    else
        log "  No matching secret-tool attributes found."
    fi
else
    log "secret-tool: not found on PATH."
fi
log ""

log "Secret Service D-Bus presence check:"
if command -v busctl >/dev/null 2>&1; then
    BUSCTL_COUNT="$(busctl --user list 2>/dev/null | grep -c org.freedesktop.secrets || true)"
    log "  busctl --user list org.freedesktop.secrets count: ${BUSCTL_COUNT}"
elif command -v dbus-send >/dev/null 2>&1; then
    DBUS_REPLY="$(dbus-send --session --dest=org.freedesktop.DBus --type=method_call --print-reply /org/freedesktop/DBus org.freedesktop.DBus.ListNames 2>/dev/null || true)"
    DBUS_COUNT="$(echo "${DBUS_REPLY}" | grep -c org.freedesktop.secrets || true)"
    log "  dbus-send org.freedesktop.secrets count: ${DBUS_COUNT}"
else
    log "  Neither busctl nor dbus-send available to query D-Bus."
fi
log ""

log "File-based fallback candidates (names only):"
SEARCH_PATHS=()
[ -d "${HOME}/.gemini" ] && SEARCH_PATHS+=("${HOME}/.gemini")
[ -d "${HOME}/.config" ] && SEARCH_PATHS+=("${HOME}/.config")

if [ ${#SEARCH_PATHS[@]} -gt 0 ]; then
    FALLBACK_FILES="$(find "${SEARCH_PATHS[@]}" -maxdepth 3 \( -iname '*cred*' -o -iname '*token*' \) 2>/dev/null || true)"
    if [ -n "${FALLBACK_FILES}" ]; then
        log "${FALLBACK_FILES}"
    else
        log "  No candidate credential/token files found under ${SEARCH_PATHS[*]} (maxdepth 3)."
    fi
else
    log "  Neither ~/.gemini nor ~/.config directory exists."
fi
log ""

# -----------------------------------------------------------------------------
# 5. User-Agent platform mapping
# -----------------------------------------------------------------------------
log "## 5. User-Agent Platform Mapping"

SYS_OS="$(uname -s)"
case "${SYS_OS}" in
    Darwin) GOOS="darwin" ;;
    Linux)  GOOS="linux" ;;
    *)      GOOS="$(echo "${SYS_OS}" | tr '[:upper:]' '[:lower:]')" ;;
esac

SYS_ARCH="$(uname -m)"
case "${SYS_ARCH}" in
    x86_64|amd64)     GOARCH="amd64" ;;
    arm64|aarch64)    GOARCH="arm64" ;;
    i386|i686)        GOARCH="386" ;;
    armv7l|armv6l)    GOARCH="arm" ;;
    *)                GOARCH="${SYS_ARCH}" ;;
esac

log "System OS (uname -s): ${SYS_OS} -> GOOS: ${GOOS}"
log "System Machine (uname -m): ${SYS_ARCH} -> GOARCH: ${GOARCH}"
log "Mapped platform segment: ${GOOS}/${GOARCH}"
if [ -n "${AGY_BIN}" ] && [ -n "${AGY_VER:-}" ]; then
    log "Projected User-Agent: antigravity/${AGY_VER} ${GOOS}/${GOARCH}"
else
    log "Projected User-Agent: antigravity/<version> ${GOOS}/${GOARCH}"
fi
log ""

# -----------------------------------------------------------------------------
# Report Footer
# -----------------------------------------------------------------------------
log "Paste this whole file to agy. It contains no secrets."

# -----------------------------------------------------------------------------
# Self-Check: Verify report contains zero secret patterns
# -----------------------------------------------------------------------------
if grep -E -q '(ya29\.|1//|GOCSPX-|secret =)' "${REPORT_FILE}"; then
    echo "ERROR: Probe self-check failed! Secret pattern detected in ${REPORT_FILE}." >&2
    rm -f "${REPORT_FILE}"
    echo "Report deleted immediately for security. Aborting." >&2
    exit 1
fi

echo ""
echo "Probe completed successfully!"
echo "Report generated at: ${REPORT_FILE}"
echo "Self-check passed: 0 secret patterns detected."
echo ""
echo "Paste this whole file to agy. It contains no secrets."
