"""Codex hook objects. Source: https://learn.chatgpt.com/docs/hooks"""

import json


def protocol_output(args, result):
    if args.host != "codex" or args.command != "hook" or not isinstance(result, list):
        return result
    if not result:
        return {}
    report = json.dumps(result)
    if args.event == "PostToolUse":
        return {
            "hookSpecificOutput": {
                "hookEventName": args.event,
                "additionalContext": report,
            }
        }
    return {"systemMessage": report}
