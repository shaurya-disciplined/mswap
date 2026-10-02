# Prompt Integration

`mswap status` provides a sub-150ms, cache-only status line designed specifically for inclusion in shell prompts (PowerShell, Starship, Bash, Zsh).

- **Cache-only**: Never calls Google's quota API or performs network requests.
- **Fail-safe**: Exits `0` with completely empty output if no accounts exist or no account is currently active.
- **Single OS read**: Reads only the current active credential fingerprint from the local OS credential store.

## Output Formats

Default format:
```text
{slot}:{email_short} G{gemini_5h}% C{3p_5h}%
```
Example:
```text
1:freeagy G99% C100%
```

Custom format via `--format`:
```text
mswap status --format "[{slot} · {email_short} · {gemini_5h}%] "
```

Available placeholders:
- `{slot}`: Active account slot number (e.g. `1`)
- `{email}`: Full account email (e.g. `alice@example.com`)
- `{email_short}`: Local part of email truncated to 8 characters (e.g. `alice`)
- `{alias}`: Account alias if configured, otherwise empty string
- `{gemini_5h}`: Remaining Gemini 5-hour quota percentage (`0`-`100` or `?`)
- `{gemini_week}`: Remaining Gemini weekly quota percentage (`0`-`100` or `?`)
- `{3p_5h}`: Remaining Claude & GPT 5-hour quota percentage (`0`-`100` or `?`)
- `{3p_week}`: Remaining Claude & GPT weekly quota percentage (`0`-`100` or `?`)
- `{age}`: Age of cached quota data (e.g. `45s`, `6m`, `2h`, or `?`)

---

## PowerShell Prompt Snippet

Add this to your PowerShell `$PROFILE` (e.g., `notepad $PROFILE`):

```powershell
function prompt {
    $status = mswap status 2>$null
    if ($status) {
        Write-Host "[$status] " -NoNewline -ForegroundColor DarkCyan
    }
    "PS $($executionContext.SessionState.Path.CurrentLocation)$('>' * ($nestedPromptLevel + 1)) "
}
```

---

## Starship Custom Module Snippet

Add this to your `~/.config/starship.toml`:

```toml
[custom.mswap]
command = "mswap status"
when = "command -v mswap"
shell = ["cmd", "/C"]  # Use ["bash", "-c"] on Linux or macOS
format = "[$output]($style) "
style = "bold cyan"
```

---

## Bash / Zsh PS1 Snippet

Add this to your `~/.bashrc` or `~/.zshrc`:

```bash
_mswap_prompt() {
    local s
    s=$(mswap status 2>/dev/null)
    if [ -n "$s" ]; then
        printf "[%s] " "$s"
    fi
}

PS1='$(_mswap_prompt)'"$PS1"
```
