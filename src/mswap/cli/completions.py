"""Shell completion script generation for PowerShell, bash, zsh and fish.

Owns the introspection of the argparse grammar and the four script renderers.
Must never keep a hand-maintained list of commands or flags: everything is read from the parser.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass

SHELLS: tuple[str, ...] = ("powershell", "bash", "zsh", "fish")

SELECTOR_DEST = "selector"
"""Positional dest whose values are account selectors (slot, email or alias)."""

SELECTOR_MARK = "@selector"
"""Slot value meaning "ask `mswap __complete selectors`"."""

_HIDDEN_PREFIX = "__"

_COMMAND_NAME = "mswap"


@dataclass(frozen=True, slots=True)
class Flag:
    """One option, with every spelling it has (for example `-q` and `--quiet`)."""

    names: tuple[str, ...]
    help: str
    takes_value: bool
    choices: tuple[str, ...]
    metavar: str


@dataclass(frozen=True, slots=True)
class Slot:
    """One positional argument."""

    name: str
    help: str
    choices: tuple[str, ...]
    selector: bool
    required: bool


@dataclass(frozen=True, slots=True)
class Node:
    """The root parser or one (nested) subcommand."""

    path: tuple[str, ...]
    help: str
    aliases: tuple[str, ...]
    flags: tuple[Flag, ...]
    slots: tuple[Slot, ...]
    children: tuple[Node, ...]

    @property
    def key(self) -> str:
        """Space-joined path, empty for the root."""
        return " ".join(self.path)

    @property
    def child_words(self) -> tuple[str, ...]:
        """Every spelling of every subcommand (names and aliases)."""
        words: list[str] = []
        for child in self.children:
            words.append(child.path[-1])
            words.extend(child.aliases)
        return tuple(words)

    def positionals(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Positional kinds by index: ("words", choices), ("selector", ()) or ("free", ())."""
        if self.children:
            return (("words", self.child_words),)
        out: list[tuple[str, tuple[str, ...]]] = []
        for slot in self.slots:
            if slot.selector:
                out.append(("selector", ()))
            elif slot.choices:
                out.append(("words", slot.choices))
            else:
                out.append(("free", ()))
        return tuple(out)

    def value_flags(self) -> tuple[tuple[str, Flag], ...]:
        """Every (spelling, flag) pair for flags that take a value."""
        return tuple((n, f) for f in self.flags if f.takes_value for n in f.names)


def _help_of(action: argparse.Action) -> str:
    if action.help is None or action.help == argparse.SUPPRESS:
        return ""
    return " ".join(str(action.help).split())


def _flag_from(action: argparse.Action) -> Flag:
    metavar = action.metavar if isinstance(action.metavar, str) else action.dest
    return Flag(
        names=tuple(action.option_strings),
        help=_help_of(action),
        takes_value=action.nargs != 0,
        choices=tuple(str(c) for c in (action.choices or ())),
        metavar=str(metavar).lower(),
    )


def _slot_from(action: argparse.Action) -> Slot:
    return Slot(
        name=action.dest,
        help=_help_of(action),
        choices=tuple(str(c) for c in (action.choices or ())),
        selector=action.dest == SELECTOR_DEST,
        required=action.nargs not in ("?", "*"),
    )


def _walk(
    parser: argparse.ArgumentParser,
    path: tuple[str, ...],
    help_text: str,
    aliases: tuple[str, ...],
) -> Node:
    flags: list[Flag] = []
    slots: list[Slot] = []
    children: list[Node] = []
    for action in parser._actions:  # introspection is the point of this module
        if isinstance(action, argparse._SubParsersAction):
            children.extend(_walk_children(action, path))
        elif action.option_strings:
            if action.help != argparse.SUPPRESS:
                flags.append(_flag_from(action))
        else:
            slots.append(_slot_from(action))
    return Node(path, help_text, aliases, tuple(flags), tuple(slots), tuple(children))


def _walk_children(
    action: argparse._SubParsersAction[argparse.ArgumentParser], path: tuple[str, ...]
) -> list[Node]:
    helps = {a.dest: _help_of(a) for a in action._choices_actions}
    spellings: dict[int, list[str]] = {}
    parsers: dict[int, argparse.ArgumentParser] = {}
    for name, sub in action.choices.items():
        spellings.setdefault(id(sub), []).append(name)
        parsers[id(sub)] = sub
    nodes: list[Node] = []
    for pid, names in spellings.items():
        name = names[0]
        if name.startswith(_HIDDEN_PREFIX):
            continue
        nodes.append(_walk(parsers[pid], (*path, name), helps.get(name, ""), tuple(names[1:])))
    return nodes


def build_tree(parser: argparse.ArgumentParser) -> Node:
    """Read the whole command grammar out of an argparse parser."""
    return _walk(parser, (), "", ())


def flatten(root: Node) -> list[Node]:
    """Return the root and every descendant, parents before children."""
    out = [root]
    for child in root.children:
        out.extend(flatten(child))
    return out


def _path_word_spellings(root: Node) -> list[tuple[Node, tuple[str, ...]]]:
    """Each non-root node with every spelling of its last path word."""
    return [(n, (n.path[-1], *n.aliases)) for n in flatten(root)[1:]]


def _parent_key(node: Node) -> str:
    return " ".join(node.path[:-1])


def _all_flag_names(node: Node) -> list[str]:
    return [n for f in node.flags for n in f.names]


def _sq(text: str) -> str:
    """Quote text for POSIX shells (bash and zsh)."""
    return "'" + text.replace("'", "'\\''") + "'"


# --------------------------------------------------------------------------- bash


def render_bash(root: Node) -> str:
    """Render the bash completion script."""
    nodes = flatten(root)
    out: list[str] = [
        "# bash completion for mswap, generated by `mswap completions bash`.",
        "# Regenerate it after upgrading mswap instead of editing it.",
        "",
        "_mswap_child() {",
        '    case "$1|$2" in',
    ]
    for node, words in _path_word_spellings(root):
        pats = "|".join(_sq(f"{_parent_key(node)}|{w}") for w in words)
        out.append(f"        {pats}) printf '%s\\n' {_sq(node.key)} ;;")
    out += ["    esac", "}", "", "_mswap_flags() {", '    case "$1" in']
    for node in nodes:
        out.append(
            f"        {_sq(node.key)}) printf '%s\\n' {_sq(' '.join(_all_flag_names(node)))} ;;"
        )
    out += ["    esac", "}", "", "_mswap_flag_values() {", '    case "$1|$2" in']
    for node in nodes:
        for name, flag in node.value_flags():
            choices = " ".join(flag.choices) or "-"
            out.append(f"        {_sq(f'{node.key}|{name}')}) printf '%s\\n' {_sq(choices)} ;;")
    out += ["    esac", "}", "", "_mswap_slot() {", '    case "$1|$2" in']
    for node in nodes:
        for idx, (kind, words) in enumerate(node.positionals()):
            if kind == "free":
                continue
            value = SELECTOR_MARK if kind == "selector" else " ".join(words)
            out.append(f"        {_sq(f'{node.key}|{idx}')}) printf '%s\\n' {_sq(value)} ;;")
    out += [
        "    esac",
        "}",
        "",
        "_mswap() {",
        '    local cur="${COMP_WORDS[COMP_CWORD]}" key="" npos=0 skip="" i w child vals',
        "    COMPREPLY=()",
        "    for ((i = 1; i < COMP_CWORD; i++)); do",
        '        w="${COMP_WORDS[i]}"',
        '        if [[ -n "$skip" ]]; then',
        '            skip=""',
        "            continue",
        "        fi",
        '        if [[ "$w" == -* ]]; then',
        '            if [[ -n "$(_mswap_flag_values "$key" "$w")" ]]; then',
        '                skip="$w"',
        "            fi",
        "            continue",
        "        fi",
        '        child="$(_mswap_child "$key" "$w")"',
        '        if [[ $npos -eq 0 && -n "$child" ]]; then',
        '            key="$child"',
        "        else",
        "            npos=$((npos + 1))",
        "        fi",
        "    done",
        '    if [[ -n "$skip" ]]; then',
        '        vals="$(_mswap_flag_values "$key" "$skip")"',
        '        if [[ "$vals" != "-" ]]; then',
        '            COMPREPLY=($(compgen -W "$vals" -- "$cur"))',
        "        fi",
        "        return 0",
        "    fi",
        '    if [[ "$cur" == -* ]]; then',
        '        COMPREPLY=($(compgen -W "$(_mswap_flags "$key")" -- "$cur"))',
        "        return 0",
        "    fi",
        '    vals="$(_mswap_slot "$key" "$npos")"',
        f'    if [[ "$vals" == "{SELECTOR_MARK}" ]]; then',
        '        vals="$(mswap __complete selectors 2>/dev/null)"',
        "    fi",
        '    COMPREPLY=($(compgen -W "$vals" -- "$cur"))',
        "}",
        "",
        "complete -F _mswap mswap",
        "",
    ]
    return "\n".join(out)


# --------------------------------------------------------------------------- zsh


def _zsh_text(text: str) -> str:
    """Escape text that sits inside `[...]` of an _arguments spec (before shell quoting)."""
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _zsh_flag_spec(flag: Flag) -> str:
    desc = _zsh_text(flag.help)
    if flag.takes_value:
        action = f"({' '.join(flag.choices)})" if flag.choices else " "
        tail = f"[{desc}]:{flag.metavar}:{action}"
    else:
        tail = f"[{desc}]"
    if len(flag.names) == 1:
        suffix = "=" if flag.names[0].startswith("--") else "+"
        name = flag.names[0] + (suffix if flag.takes_value else "")
        return _sq(f"{name}{tail}")
    spellings = ",".join(
        n + (("=" if n.startswith("--") else "+") if flag.takes_value else "") for n in flag.names
    )
    return f"{_sq('(' + ' '.join(flag.names) + ')')}{{{spellings}}}{_sq(tail)}"


def _zsh_slot_spec(index: int, slot: Slot) -> str:
    colons = ":" if slot.required else "::"
    if slot.selector:
        action = "_mswap_selectors"
    elif slot.choices:
        action = f"({' '.join(slot.choices)})"
    else:
        action = " "
    return _sq(f"{index}{colons}{slot.name}:{action}")


def _zsh_fn(node: Node) -> str:
    return "_mswap" + "".join("_" + p.replace("-", "_") for p in node.path)


def _describe_label(node: Node) -> str:
    return " ".join(node.path or (_COMMAND_NAME,)) + " command"


def _zsh_function(node: Node) -> list[str]:
    fn = _zsh_fn(node)
    specs = [_zsh_flag_spec(f) for f in node.flags]
    out = [f"{fn}() {{"]
    if not node.children:
        specs += [_zsh_slot_spec(i, s) for i, s in enumerate(node.slots, start=1)]
        out.append("    _arguments \\")
        out.append(" \\\n".join(f"        {s}" for s in specs))
        out.append("}")
        return out
    specs += [_sq("1: :->sub"), _sq("*:: :->args")]
    out += [
        '    local curcontext="$curcontext" state line ret=1',
        "    typeset -A opt_args",
        "    _arguments -C \\",
        " \\\n".join(f"        {s}" for s in specs) + " && ret=0",
        "    case $state in",
        "        sub)",
        "            local -a subs",
        "            subs=(",
    ]
    for child in node.children:
        for word in (child.path[-1], *child.aliases):
            out.append(f"                {_sq(f'{word}:{child.help}'.replace(chr(10), ' '))}")
    out += [
        "            )",
        f"            _describe -t commands {_sq(_describe_label(node))} subs && ret=0",
        "            ;;",
        "        args)",
        "            case $line[1] in",
    ]
    for child in node.children:
        pats = "|".join((child.path[-1], *child.aliases))
        out.append(f"                {pats}) {_zsh_fn(child)} && ret=0 ;;")
    out += ["            esac", "            ;;", "    esac", "    return ret", "}"]
    return out


def render_zsh(root: Node) -> str:
    """Render the zsh completion script."""
    out: list[str] = [
        "#compdef mswap",
        "# zsh completion for mswap, generated by `mswap completions zsh`.",
        "# Regenerate it after upgrading mswap instead of editing it.",
        "",
        "_mswap_selectors() {",
        "    local -a sel",
        '    sel=(${(f)"$(mswap __complete selectors 2>/dev/null)"})',
        "    compadd -a sel",
        "}",
        "",
    ]
    for node in reversed(flatten(root)):
        out += _zsh_function(node)
        out.append("")
    out += [
        'if [ "$funcstack[1]" = "_mswap" ]; then',
        '    _mswap "$@"',
        "else",
        "    compdef _mswap mswap",
        "fi",
        "",
    ]
    return "\n".join(out)


# --------------------------------------------------------------------------- fish


def _fq(text: str) -> str:
    """Quote text for fish."""
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _fish_key(node: Node) -> str:
    return " ".join((_COMMAND_NAME, *node.path))


def _fish_scan(root: Node) -> list[str]:
    """Render the helper that works out the typed command path and positional count."""
    value_names = sorted({n for node in flatten(root) for n, _ in node.value_flags()})
    out = [
        "function __mswap_scan",
        f"    set -l key {_COMMAND_NAME}",
        "    set -l npos 0",
        "    set -l skip 0",
        "    set -l words (commandline -opc)",
        "    set -e words[1]",
        "    for w in $words",
        "        if test $skip -eq 1",
        "            set skip 0",
        "        else if string match -q -- '-*' $w",
        f"            if contains -- $w {' '.join(value_names)}".rstrip(),
        "                set skip 1",
        "            end",
        "        else",
        "            set -l child ''",
        "            if test $npos -eq 0",
        '                switch "$key|$w"',
    ]
    for node, words in _path_word_spellings(root):
        parent = _fish_key(Node(node.path[:-1], "", (), (), (), ()))
        pats = " ".join(_fq(f"{parent}|{w}") for w in words)
        out.append(f"                    case {pats}")
        out.append(f"                        set child {_fq(_fish_key(node))}")
    out += [
        "                end",
        "            end",
        '            if test -n "$child"',
        "                set key $child",
        "            else",
        "                set npos (math $npos + 1)",
        "            end",
        "        end",
        "    end",
        "    echo $key",
        "    echo $npos",
        "end",
        "",
        "function __mswap_at",
        "    set -l s (__mswap_scan)",
        '    test "$s[1]" = "$argv[1]"; or return 1',
        "    test (count $argv) -ge 2; or return 0",
        '    test "$s[2]" = "$argv[2]"',
        "end",
        "",
    ]
    return out


def _fish_flag_lines(flags: tuple[Flag, ...], cond: str | None) -> list[str]:
    out: list[str] = []
    for flag in flags:
        parts = ["complete -c mswap"]
        if cond is not None:
            parts.append(f"-n {_fq(cond)}")
        for name in flag.names:
            parts.append(f"-l {name[2:]}" if name.startswith("--") else f"-s {name[1:]}")
        if flag.takes_value:
            parts.append("-r -f")
            if flag.choices:
                parts.append(f"-a {_fq(' '.join(flag.choices))}")
        if flag.help:
            parts.append(f"-d {_fq(flag.help)}")
        out.append(" ".join(parts))
    return out


def _common_flags(nodes: list[Node]) -> tuple[Flag, ...]:
    """Flags every node has (the global options), so they are emitted once."""
    first, *rest = nodes
    return tuple(f for f in first.flags if all(f in n.flags for n in rest))


def render_fish(root: Node) -> str:
    """Render the fish completion script."""
    nodes = flatten(root)
    common = _common_flags(nodes)
    out: list[str] = [
        "# fish completion for mswap, generated by `mswap completions fish`.",
        "# Regenerate it after upgrading mswap instead of editing it.",
        "",
        *_fish_scan(root),
        *_fish_flag_lines(common, None),
    ]
    for node in nodes:
        key = _fish_key(node)
        own = tuple(f for f in node.flags if f not in common)
        out += _fish_flag_lines(own, f"__mswap_at {_fq(key)}")
        if node.children:
            for child in node.children:
                for word in (child.path[-1], *child.aliases):
                    desc = f" -d {_fq(child.help)}" if child.help else ""
                    cond = f"__mswap_at {_fq(key)} 0"
                    out.append(f"complete -c mswap -n {_fq(cond)} -f -a {_fq(word)}{desc}")
            continue
        for idx, (kind, words) in enumerate(node.positionals()):
            if kind == "free":
                continue
            cond = f"__mswap_at {_fq(key)} {idx}"
            arg = " ".join(words)
            if kind == "selector":
                arg = "(mswap __complete selectors 2>/dev/null)"
            out.append(f"complete -c mswap -n {_fq(cond)} -f -a {_fq(arg)}")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------- PowerShell


def _pq(text: str) -> str:
    """Quote text for PowerShell."""
    return "'" + text.replace("'", "''") + "'"


def _ps_array(items: list[str]) -> str:
    return "@(" + ", ".join(_pq(i) for i in items) + ")"


def _ps_key(path: tuple[str, ...]) -> str:
    return " ".join((_COMMAND_NAME, *path))


def _ps_node(node: Node) -> list[str]:
    values = [x for n, f in node.value_flags() for x in (n, " ".join(f.choices))]
    slots: list[str] = []
    for kind, words in node.positionals():
        slots.append(SELECTOR_MARK if kind == "selector" else " ".join(words))
    helps: dict[str, str] = {}
    for flag in node.flags:
        for name in flag.names:
            helps[name] = flag.help
    for child in node.children:
        for word in (child.path[-1], *child.aliases):
            helps[word] = child.help
    help_pairs = [x for k, v in helps.items() if v for x in (k, v)]
    return [
        f"        {_pq(_ps_key(node.path))} = @{{",
        f"            Flags = {_ps_array(_all_flag_names(node))}",
        f"            Values = & $map {_ps_array(values)}",
        f"            Slots = {_ps_array(slots)}",
        f"            Help = & $map {_ps_array(help_pairs)}",
        "        }",
    ]


def render_powershell(root: Node) -> str:
    """Render the PowerShell completion script."""
    out: list[str] = [
        "# PowerShell completion for mswap, generated by `mswap completions powershell`.",
        "# Regenerate it after upgrading mswap instead of editing it.",
        "Register-ArgumentCompleter -Native -CommandName mswap -ScriptBlock {",
        "    param($wordToComplete, $commandAst, $cursorPosition)",
        "",
        "    # Flag names differ only by case (-v and -V), so these maps must be case-sensitive.",
        "    $map = {",
        "        param([string[]]$kv)",
        "        $h = [hashtable]::new([StringComparer]::Ordinal)",
        "        for ($i = 0; $i -lt $kv.Count; $i += 2) { $h[$kv[$i]] = $kv[$i + 1] }",
        "        $h",
        "    }",
        "    $nodes = @{",
    ]
    for node in flatten(root):
        out += _ps_node(node)
    alias_pairs = [
        f"{_pq(_ps_key((*n.path[:-1], a)))} = {_pq(_ps_key(n.path))}"
        for n in flatten(root)[1:]
        for a in n.aliases
    ]
    out += [
        "    }",
        f"    $aliases = @{{ {'; '.join(alias_pairs)} }}",
        "",
        "    $words = @($commandAst.CommandElements | Select-Object -Skip 1 |",
        "        Where-Object { $_.Extent.EndOffset -lt $cursorPosition } |",
        "        ForEach-Object { $_.ToString() })",
        "    $key = 'mswap'",
        "    $slot = 0",
        "    $pending = $null",
        "    foreach ($w in $words) {",
        "        if ($null -ne $pending) { $pending = $null; continue }",
        "        if ($w.StartsWith('-')) {",
        "            if ($nodes[$key].Values.ContainsKey($w)) { $pending = $w }",
        "            continue",
        "        }",
        "        if ($slot -eq 0) {",
        '            $child = "$key $w"',
        "            if ($aliases.ContainsKey($child)) { $child = $aliases[$child] }",
        "            if ($nodes.ContainsKey($child)) { $key = $child; continue }",
        "        }",
        "        $slot++",
        "    }",
        "",
        "    $node = $nodes[$key]",
        "    $kind = 'ParameterValue'",
        "    $candidates = @()",
        "    if ($null -ne $pending) {",
        "        $choices = $node.Values[$pending]",
        "        if ($choices) { $candidates = $choices -split ' ' }",
        "    } elseif ($wordToComplete.StartsWith('-')) {",
        "        $kind = 'ParameterName'",
        "        $candidates = $node.Flags",
        "    } elseif ($slot -lt $node.Slots.Count) {",
        "        $spec = $node.Slots[$slot]",
        f"        if ($spec -eq {_pq(SELECTOR_MARK)}) {{",
        "            $exe = $commandAst.CommandElements[0].ToString()",
        "            $candidates = @(& $exe __complete selectors 2>$null)",
        "        } elseif ($spec) {",
        "            $candidates = $spec -split ' '",
        "        }",
        "    }",
        "    foreach ($c in $candidates) {",
        "        $cmp = [StringComparison]::OrdinalIgnoreCase",
        "        if ($c -and $c.StartsWith($wordToComplete, $cmp)) {",
        "            $tip = $c",
        "            if ($node.Help.ContainsKey($c)) { $tip = $node.Help[$c] }",
        "            [System.Management.Automation.CompletionResult]::new($c, $c, $kind, $tip)",
        "        }",
        "    }",
        "}",
        "",
    ]
    return "\n".join(out)


_RENDERERS: dict[str, Callable[[Node], str]] = {
    "powershell": render_powershell,
    "bash": render_bash,
    "zsh": render_zsh,
    "fish": render_fish,
}


def generate(shell: str, parser: argparse.ArgumentParser | None = None) -> str:
    """Return the completion script for `shell`, built from `parser` (default: mswap's own)."""
    renderer = _RENDERERS[shell]
    if parser is None:
        from mswap.cli.parser import build_parser

        parser = build_parser()
    return renderer(build_tree(parser))
