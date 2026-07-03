#!/usr/bin/env python3
"""PreToolUse guard (Agent|Task|Workflow): block detailed delegations without a Requirements Ledger.

Dynamic Workflow Rule 1: serious multi-phase delegation requires
a Requirements Ledger at .workflow/LEDGER.md (searched from the
working directory up to the repo root). Short spawn prompts (quick
searches/lookups) pass freely so casual Explore agents are never
blocked.

What is gated:
    Agent / Task  -> length of tool_input.prompt
    Workflow      -> length of tool_input.script (an orchestration
                     script IS the delegation plan; name/scriptPath
                     resume calls carry no new plan text and pass)
Exempt:
    fork subagents (subagent_type == "fork") — a fork inherits the
    full conversation context, so the ledger is already in front
    of it; forcing a file adds nothing.

The threshold is MODEL-AWARE. The Fable profile keeps a strict gate
(small delegations should still carry a ledger, because Fable tokens
are the scarce resource). The lean Opus profile raises the gate: a
large-context chair model fans out with rich agent prompts as normal
practice, and the ledger is meant to be *proportional* — only
large/parallel/high-risk work writes a file.

Profile resolution order:
    1. FABLE_ORCH_PROFILE env override (fable | opus)
    2. top-level `model` string in the hook payload (Claude Code
       includes it on PreToolUse; optional)
    3. per-session cache written by the SessionStart hook
    4. default: lean 'opus' (never over-block)

Configuration (all optional):
    LEGACY hard override, applied to every profile:
        LEDGER_GUARD_THRESHOLD
    Per-profile defaults (used when the hard override is unset):
        LEDGER_GUARD_THRESHOLD_FABLE   default 1500
        LEDGER_GUARD_THRESHOLD_OPUS    default 4000
"""
import json
import os
import sys
import tempfile


def _int_env(name, default):
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def session_model_cache_path(session_id):
    if not session_id:
        return None
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")
    return os.path.join(tempfile.gettempdir(), f"fable-orch-model-{safe}.json")


def profile_from_model(model):
    """'fable' / 'opus' from a model string, or None when absent."""
    if not model:
        return None
    return "fable" if "fable" in str(model).lower() else "opus"


def active_profile(data):
    """Resolve the profile: env override > payload model > cache > lean."""
    override = (os.environ.get("FABLE_ORCH_PROFILE") or "auto").strip().lower()
    if override in ("fable", "opus"):
        return override

    prof = profile_from_model(data.get("model"))
    if prof:
        return prof

    cache = session_model_cache_path(data.get("session_id"))
    if cache and os.path.isfile(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                prof = (json.load(f).get("profile") or "").strip().lower()
            if prof in ("fable", "opus"):
                return prof
        except Exception:
            pass
    return "opus"


def threshold(data):
    # Hard override wins for every profile (backward compatible).
    if "LEDGER_GUARD_THRESHOLD" in os.environ:
        return _int_env("LEDGER_GUARD_THRESHOLD", 1500)
    if active_profile(data) == "fable":
        return _int_env("LEDGER_GUARD_THRESHOLD_FABLE", 1500)
    return _int_env("LEDGER_GUARD_THRESHOLD_OPUS", 4000)


def find_ledger(start_dir):
    """Path of .workflow/LEDGER.md from start_dir up to the repo root.

    Walks parent directories so sessions running in a subdirectory
    still see the project ledger. Stops at the first directory that
    contains .git (a ledger above the repo belongs to another
    project), or at the filesystem root. .git is checked with
    os.path.exists, not isdir: in worktrees and submodules .git is a
    FILE, and treating it as "not a boundary" would let the search
    escape into an unrelated project's ledger.
    """
    d = os.path.abspath(start_dir or os.getcwd())
    while True:
        candidate = os.path.join(d, ".workflow", "LEDGER.md")
        if os.path.isfile(candidate):
            return candidate
        if os.path.exists(os.path.join(d, ".git")):
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

    limit = threshold(data)
    if len(text) <= limit:
        return

    if find_ledger(data.get("cwd")):
        return

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
