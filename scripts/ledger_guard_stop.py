#!/usr/bin/env python3
"""Stop guard: don't let a turn end silently while ./.workflow/LEDGER.md has open items.

Dynamic Workflow verification rule: the workflow cannot close while any
ledger item is unaddressed. Open item = a line matching '- [ ]'.
Closed: '- [x]' (done+verified) or '- [~] deferred: <reason>' (user-approved).
"""
import json
import os
import re
import sys


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    # Loop guard: we already blocked this stop once; let it through now.
    if data.get("stop_hook_active"):
        return

    cwd = data.get("cwd") or os.getcwd()
    ledger = os.path.join(cwd, ".workflow", "LEDGER.md")
    if not os.path.isfile(ledger):
        return

    try:
        with open(ledger, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return

    open_items = re.findall(r"^\s*[-*] \[ \] .*$", text, flags=re.M)
    if not open_items:
        return

    preview = "\n".join(line.strip()[:200] for line in open_items[:10])
    more = f"\n(+{len(open_items) - 10} more)" if len(open_items) > 10 else ""
    print(json.dumps({
        "decision": "block",
        "reason": (
            f"LEDGER GUARD: ./.workflow/LEDGER.md still has {len(open_items)} "
            f"open item(s):\n{preview}{more}\n\n"
            "If you are CLOSING a workflow: address each item and mark it '- [x]' "
            "(only after verification confirms it), or '- [~] deferred: <reason>' "
            "with user approval, and run the fresh-agent verification phase if you "
            "haven't. If you are NOT closing a workflow (mid-conversation answer, "
            "or this ledger belongs to a paused/abandoned task), reply with one "
            "short line acknowledging the open-item count, then stop. Archive the "
            "ledger (e.g. rename to LEDGER-<topic>-archive.md) if the task is "
            "truly abandoned."
        ),
    }))


if __name__ == "__main__":
    main()
