# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Clement Deust
"""Codex hook objects. Source: https://learn.chatgpt.com/docs/hooks"""

import json


def protocol_output(args, result):
    if args.host != "codex" or args.command != "hook":
        return result
    if args.event == "SessionStart":
        if not result:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": json.dumps(result),
            }
        }
    if not isinstance(result, list):
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
