#!/usr/bin/env python3
"""PreToolUse guard (Agent|Task|Workflow|TaskCreate): keep multi-phase work on the ledger.

Dynamic Workflow Rule 1: serious multi-phase delegation requires
a Requirements Ledger at .workflow/LEDGER.md (searched from the
working directory up to the repo root or $HOME). Short spawn
prompts (quick searches/lookups) pass freely so casual Explore
agents are never blocked.

What is gated:
    Agent / Task  -> length of tool_input.prompt
    Workflow      -> length of tool_input.script (an orchestration
                     script IS the delegation plan; name/scriptPath
                     resume calls carry no new plan text and pass)
    TaskCreate    -> the SOLO path the spawn gates can't see: a chair
                     that never delegates never trips them, but the
                     tracker tasks it creates for itself are the tell.
                     The Nth TaskCreate of a session (default 3rd)
                     with no ledger draws ONE deny — "multi-phase
                     work: write the ledger, delegate to workers" —
                     then stays quiet (measured in the wild: a
                     6-phase plan implemented entirely on the chair).
Exempt:
    fork subagents (subagent_type == "fork") — a fork inherits the
    full conversation context, so the ledger is already in front
    of it; forcing a file adds nothing.

The threshold defaults to 1500 chars — strict on purpose. This
plugin is built for a Claude Fable 5 chair, where even small
delegations should carry a ledger: Fable tokens are the scarce
resource, and detail loss at task->plan translation is exactly
what the ledger exists to catch.

Configuration (all optional):
    LEDGER_GUARD_THRESHOLD   gate in chars (default 1500;
                             unparseable values fall back to it)
    LEDGER_GUARD_TASKS       tracker tasks allowed without a ledger
                             (default 3; 0 disables the task gate)
    FABLE_ORCH_METRICS=0     disables the local metrics log
"""
import json
import os
import sys
import tempfile
import time

DEFAULT_THRESHOLD = 1500
DEFAULT_TASK_LIMIT = 3


def _metric(event, session_id=None, **extra):
    """Append one event line to ~/.claude/fable-orch/metrics.jsonl (best effort)."""
    if (os.environ.get("FABLE_ORCH_METRICS") or "").strip() == "0":
        return
    try:
        d = os.path.join(os.path.expanduser("~"), ".claude", "fable-orch")
        os.makedirs(d, exist_ok=True)
        rec = {"ts": round(time.time(), 3), "event": event}
        if session_id:
            rec["session"] = str(session_id)[:8]
        rec.update(extra)
        with open(os.path.join(d, "metrics.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def threshold():
    raw = os.environ.get("LEDGER_GUARD_THRESHOLD")
    if raw is not None:
        try:
            return int(raw)
        except ValueError:
            pass
    return DEFAULT_THRESHOLD


def task_limit():
    raw = os.environ.get("LEDGER_GUARD_TASKS")
    if raw is not None:
        try:
            return int(raw)
        except ValueError:
            pass
    return DEFAULT_TASK_LIMIT


def _task_sidecar(session_id):
    if not session_id:
        return None
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")
    return os.path.join(tempfile.gettempdir(), f"fable-orch-tasks-{safe}.json")


def guard_task_create(data):
    """Deny the Nth ledgerless tracker task of a session — once.

    Counting lives in a per-session sidecar so the gate never leaks
    across sessions; without a session_id there is nothing safe to
    scope to, so the call passes. The denied call creates no task and
    may simply be re-issued once the ledger exists.
    """
    limit = task_limit()
    if limit <= 0:
        return
    if find_ledger(data.get("cwd")):
        return

    session_id = data.get("session_id")
    path = _task_sidecar(session_id)
    if path is None:
        return

    state = {}
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict):
            state = {}
    except Exception:
        state = {}

    count = int(state.get("count") or 0) + 1
    denied_before = bool(state.get("denied"))
    deny_now = count >= limit and not denied_before
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"count": count, "denied": denied_before or deny_now}, f)
    except OSError:
        pass

    if count < limit:
        return
    if denied_before:
        _metric("tasks_suppressed", session_id, count=count)
        return

    _metric("tasks_deny", session_id, count=count, threshold=limit)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"LEDGER GUARD: this is tracker task #{count} this session — "
                "multi-phase work — but no .workflow/LEDGER.md exists from the "
                "working directory up to the repo root. Rule 0's hard cap: work "
                "that needs a task list of 3+ items is OVER the orchestration "
                "threshold, and an approved plan is NOT an exemption. Write the "
                "numbered Requirements Ledger to ./.workflow/LEDGER.md now, then "
                "delegate implementation to sonnet workers citing ledger items "
                "instead of implementing the phases yourself. Re-issue this task "
                "afterwards — this reminder fires once per session."
            ),
        }
    }))


def find_ledger(start_dir):
    """Path of .workflow/LEDGER.md from start_dir up to the repo root or $HOME.

    Walks parent directories so sessions running in a subdirectory
    still see the project ledger. Stops at the first directory that
    contains .git (checked with os.path.exists, not isdir — in
    worktrees and submodules .git is a FILE), at the home directory
    (a ledger above $HOME belongs to nobody), or at the filesystem
    root.
    """
    d = os.path.abspath(start_dir or os.getcwd())
    home = os.path.abspath(os.path.expanduser("~"))
    while True:
        candidate = os.path.join(d, ".workflow", "LEDGER.md")
        if os.path.isfile(candidate):
            return candidate
        if os.path.exists(os.path.join(d, ".git")) or d == home:
            return None
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return  # malformed input -> never block

    if (data.get("tool_name") or "") == "TaskCreate":
        guard_task_create(data)
        return

    tool_input = data.get("tool_input") or {}

    # Forks inherit the full conversation context — ledger already visible.
    if str(tool_input.get("subagent_type") or "").strip().lower() == "fork":
        return

    if (data.get("tool_name") or "") == "Workflow":
        text = tool_input.get("script") or ""
        what = "orchestration script"
    else:
        text = tool_input.get("prompt") or ""
        what = "spawn prompt"

    limit = threshold()
    if len(text) <= limit:
        return

    session_id = data.get("session_id")
    if find_ledger(data.get("cwd")):
        _metric("spawn_pass_over_threshold", session_id,
                chars=len(text), threshold=limit,
                tool=data.get("tool_name") or "")
        return

    _metric("spawn_deny", session_id,
            chars=len(text), threshold=limit,
            tool=data.get("tool_name") or "")
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"LEDGER GUARD: this looks like a detailed delegation "
                f"({what} > {limit} chars) but no .workflow/LEDGER.md exists "
                "from the working directory up to the repo root. Per Dynamic "
                "Workflow Rule 1, first write the numbered Requirements Ledger "
                "to ./.workflow/LEDGER.md (checkbox format: '- [ ] N. <item>'), "
                "then re-spawn citing which ledger items each agent covers. If "
                "this is genuinely a small one-off task, do it directly "
                "yourself instead of delegating."
            ),
        }
    }))


if __name__ == "__main__":
    main()
