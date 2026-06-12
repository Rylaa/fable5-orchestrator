#!/usr/bin/env python3
"""PreToolUse guard (Agent|Task): block detailed delegations without a Requirements Ledger.

Dynamic Workflow Rule 1: serious multi-phase delegation requires
./.workflow/LEDGER.md. Short spawn prompts (quick searches/lookups)
pass freely so casual Explore agents are never blocked.

Configuration:
    LEDGER_GUARD_THRESHOLD  spawn-prompt length (chars) above which a
                            delegation requires a ledger. Default: 1500.
"""
import json
import os
import sys


def threshold():
    try:
        return int(os.environ.get("LEDGER_GUARD_THRESHOLD", "1500"))
    except ValueError:
        return 1500


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return  # malformed input -> never block

    prompt = (data.get("tool_input") or {}).get("prompt") or ""
    if len(prompt) <= threshold():
        return

    cwd = data.get("cwd") or os.getcwd()
    ledger = os.path.join(cwd, ".workflow", "LEDGER.md")
    if os.path.isfile(ledger):
        return

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "LEDGER GUARD: this looks like a detailed delegation "
                f"(spawn prompt > {threshold()} chars) but ./.workflow/LEDGER.md "
                "does not exist. Per Dynamic Workflow Rule 1, first write the "
                "numbered Requirements Ledger to ./.workflow/LEDGER.md (checkbox "
                "format: '- [ ] N. <item>'), then re-spawn citing which ledger "
                "items each agent covers. If this is genuinely a small one-off "
                "task, do it directly yourself instead of delegating."
            ),
        }
    }))


if __name__ == "__main__":
    main()
