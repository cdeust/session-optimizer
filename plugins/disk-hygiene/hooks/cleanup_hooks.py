"""Shared host lifecycle adapter for the disk-hygiene cleanup protocol."""

# Source: disk-hygiene design: explicit owner markers, no inferred ownership or host file sweep.
import json
import re
import shlex
from pathlib import Path

from cleanup_operations import Protected, dispose, evidence_lost, trees


def scope(cwd):
    listing = trees(cwd)
    if not listing:
        raise Protected("cannot identify main checkout")
    return str(Path(listing[0]["worktree"]).resolve())


def recover(state, owner, cwd):
    ended = state.setdefault("_ended", {})
    ended.pop(owner, None)  # Resuming an owner cancels its end marker.
    eligible = [
        (p, r) for p, r in state.items() if p != "_ended" and r.get("owner") in ended
    ]
    if not eligible:
        ended.clear()  # No registered path left for any ended owner.
        return []
    try:
        repo = scope(cwd)
    except Protected as exc:
        return [{"path": p, "protected": str(exc)} for p, _ in eligible]
    result = []
    for path, record in list(state.items()):
        if path == "_ended" or record.get("owner") not in ended:
            continue
        if record.get("repo") == repo:
            result += dispose(state, record["owner"], path)
    for previous in list(ended):
        if not any(
            r.get("owner") == previous for p, r in state.items() if p != "_ended"
        ):
            del ended[previous]
    return result


def acting_owner(state, owner, path, cwd):
    """The owner a command acts as: the caller, or the ended owner of a path.

    Same rule as startup recovery: the owner's session end is recorded and the
    path is registered for the main checkout the caller runs in. An active or
    unknown owner is never acted for.
    """
    record = state.get(path) if path and path != "_ended" else None
    if not record or record.get("owner") == owner:
        return owner
    if record.get("owner") not in state.get("_ended", {}):
        return owner
    return record["owner"] if record.get("repo") == scope(cwd) else owner


def evidence_owner(state, owner, path, cwd):
    """Evidence of an ended owner is marked again only once its file is gone."""
    actor = acting_owner(state, owner, path, cwd)
    if actor != owner and not evidence_lost(state[path]):
        raise Protected(
            "evidence of an ended session is marked again only once its "
            "recorded file is gone"
        )
    return actor


# Shell-like tools: a push can only come from their command field.
SHELL_TOOLS = ("Bash", "exec_command", "shell", "local_shell")
WRAPPERS = ("env", "command", "time", "sudo", "exec", "nohup")
SHELLS = ("bash", "sh", "zsh")
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z0-9_]+)\1")
GIT_OPTIONS_WITH_VALUE = ("-C", "-c", "--git-dir", "--work-tree", "--namespace")
GH_OPTIONS_WITH_VALUE = ("-R", "--repo")


def command_text(payload):
    """The shell command of a Bash-like tool call, or None for any other tool."""
    if payload.get("tool_name") not in SHELL_TOOLS:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    value = tool_input.get("command", tool_input.get("cmd"))
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return shlex.join(value)
    return value if isinstance(value, str) else None


def without_heredocs(command):
    """Drop heredoc bodies: they are data written to a file or a message."""
    kept, delimiter = [], None
    for line in command.split("\n"):
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue
        kept.append(line)
        found = HEREDOC.search(line)
        if found:
            delimiter = found.group(2)
    return "\n".join(kept)


def segments(command):
    """Split on unquoted ; & | ( ) and newlines."""
    parts, current, quote, escaped = [], [], None, False
    for char in command:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\" and quote != "'":
            current.append(char)
            escaped = True
        elif quote:
            current.append(char)
            quote = None if char == quote else quote
        elif char in "'\"":
            current.append(char)
            quote = char
        elif char in ";&|()\n":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def leading_words(tokens):
    """Drop env assignments and transparent wrappers before the real command."""
    index = 0
    while index < len(tokens) and (
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[index])
        or Path(tokens[index]).name in WRAPPERS
    ):
        index += 1
    return tokens[index:]


def git_subcommand(tokens):
    """(subcommand, arguments) of a git invocation, skipping global options."""
    index = 1
    while index < len(tokens) and tokens[index].startswith("-"):
        step = 2 if tokens[index] in GIT_OPTIONS_WITH_VALUE else 1
        index += step
    if index >= len(tokens):
        return None, []
    return tokens[index], tokens[index + 1 :]


def gh_words(tokens):
    words, index = [], 1
    while index < len(tokens):
        if tokens[index] in GH_OPTIONS_WITH_VALUE:
            index += 2
        elif tokens[index].startswith("-"):
            index += 1
        else:
            words.append(tokens[index])
            index += 1
    return words


def segment_pushes(segment, depth=0):
    try:
        tokens = leading_words(shlex.split(segment))
    except ValueError:
        return False
    if not tokens:
        return False
    name = Path(tokens[0]).name
    if name == "git":
        sub, args = git_subcommand(tokens)
        return sub == "push" and "--dry-run" not in args and "-n" not in args
    if name == "gh":
        return gh_words(tokens)[:2] == ["pr", "create"]
    if name in SHELLS and depth == 0:
        for flag in ("-c", "-lc", "-ic", "-ilc"):
            if flag in tokens[1:-1]:
                return command_pushes(tokens[tokens.index(flag) + 1], 1)
    return False


def command_pushes(command, depth=0):
    return any(
        segment_pushes(part, depth) for part in segments(without_heredocs(command))
    )


def pushed(payload):
    """A real git push / gh pr create command, or an MCP create_pull_request call.

    Text that merely mentions a push (file content, grep patterns, commit
    messages, heredoc bodies, --dry-run) does not count.
    """
    if "create_pull_request" in str(payload.get("tool_name", "")):
        return True
    command = command_text(payload)
    return command is not None and command_pushes(command)


def hook_result(state, owner, args, payload):
    if args.event == "SessionEnd":
        state.setdefault("_ended", {})[owner] = True
        return {"ended": owner}
    if args.event == "SessionStart":
        result = recover(state, owner, payload.get("cwd") or str(Path.cwd()))
        return startup_context(args, result)
    if args.event == "Stop" or (args.event == "PostToolUse" and pushed(payload)):
        result = dispose(state, owner)
        blocked = [r for r in result if "protected" in r]
        if args.event == "Stop" and not payload.get("stop_hook_active") and blocked:
            return {
                "decision": "block",
                "reason": "Owned cleanup remains: " + json.dumps(blocked),
            }
        return result
    return []


def startup_context(args, result):
    entry = str(Path(__file__).with_name("disk_hygiene.py"))
    command = shlex.join(
        [
            "python3",
            entry,
            "--host",
            args.host,
            "--session",
            args.session,
            "register-worktree",
            "--repo",
            "MAIN_CHECKOUT",
            "--path",
            "NEW_WORKTREE",
        ]
    )
    context = (
        "Register each new worktree: "
        + command
        + ". Link its PR and preserve evidence in a project file before "
        + "dispose: a session checkpoint, transcript or scratchpad is refused. "
        + "Transcripts are retained by default. Cleanup: "
        + json.dumps(result)
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }
