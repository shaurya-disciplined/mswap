# Shell completions

`mswap completions SHELL` prints a completion script for PowerShell, bash, zsh or fish. The script is generated from mswap's own command line grammar, so it always matches the version you have installed: every command, alias, flag and flag value is there, and nothing is maintained by hand.

Account selectors (`mswap switch`, `remove`, `alias`, `disable`, `enable`) complete dynamically to your saved accounts' slot numbers, emails and aliases. They come from `mswap __complete selectors`, an internal helper that reads only `accounts.json`: no credential store access, no network, no locks. If the file is missing or unreadable it prints nothing and exits `0`, so a tab press never produces an error.

## Install

### PowerShell

Add this line to your profile (`notepad $PROFILE`, or `New-Item -Force $PROFILE` first if it does not exist):

```powershell
mswap completions powershell | Out-String | Invoke-Expression
```

Open a new session, then try `mswap sw<Tab>` or `mswap switch <Tab>`.

### bash

Add this line to `~/.bashrc`:

```bash
eval "$(mswap completions bash)"
```

### zsh

Add this line to `~/.zshrc`, **after** `compinit`:

```zsh
eval "$(mswap completions zsh)"
```

Or install it once as a completion function, in any directory on your `fpath`:

```zsh
mswap completions zsh > "${fpath[1]}/_mswap"
```

### fish

```fish
mswap completions fish > ~/.config/fish/completions/mswap.fish
```

## After upgrading mswap

Re-run the install step if you used a file (zsh `fpath`, fish). The `eval` and `Invoke-Expression` forms regenerate the script every time a shell starts, so they need nothing.

## What gets completed

| You type | You get |
|---|---|
| `mswap <Tab>` | every command and alias |
| `mswap switch <Tab>` | your slots, emails and aliases |
| `mswap auto --strategy <Tab>` | `best`, `consume-first` |
| `mswap schedule <Tab>` | `install`, `remove`, `status` |
| `mswap completions <Tab>` | `powershell`, `bash`, `zsh`, `fish` |
| `mswap list --<Tab>` | the flags for `list`, with descriptions where the shell shows them |

## Troubleshooting

- **Nothing completes in PowerShell:** check `$PROFILE` ran (`Get-Command mswap`), and that `mswap` is on `PATH`. PowerShell falls back to file names when a position has no suggestions, for example the `NAME` of `mswap alias SELECTOR NAME`.
- **zsh says `command not found: compdef`:** the `eval` line runs before `compinit`. Move it below `autoload -Uz compinit && compinit`.
- **Selectors are empty:** run `mswap list`. Completion only offers accounts already saved with `mswap add`.
