#!/usr/bin/env python3
"""SessionStart hook: inject the Dynamic Workflow orchestration instructions.

Makes the plugin self-contained — no manual CLAUDE.md editing required.
"""
import json
import os
import sys


def main():
    root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
    path = os.path.join(root, "instructions", "dynamic-workflow.md")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return  # never break session start

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }))


if __name__ == "__main__":
    main()
