#!/usr/bin/env python3
"""PreToolUse guard (Agent|Task|Workflow): block detailed delegations without a Requirements Ledger.

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
    2. top-level `model` string in the hook payload (future-proofing:
       per the Claude Code hooks reference, `model` is delivered only
       on SessionStart today — PreToolUse carries none)
    3. per-session cache written by the SessionStart hook
    4. default: lean 'opus' (never over-block)

Configuration (all optional):
    LEGACY hard override, applied to every profile:
        LEDGER_GUARD_THRESHOLD
    Per-profile defaults (used when the hard override is unset):
        LEDGER_GUARD_THRESHOLD_FABLE   default 1500
        LEDGER_GUARD_THRESHOLD_OPUS    default 4000
    FABLE_ORCH_METRICS=0 disables the local metrics log.
"""
import json
import os
import sys
import tempfile
import time


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
    # Hard override wins for every profile (backward compatible); a value
    # that doesn't parse falls through to the per-profile defaults.
    raw = os.environ.get("LEDGER_GUARD_THRESHOLD")
    if raw is not None:
        try:
            return int(raw)
        except ValueError:
            pass
    if active_profile(data) == "fable":
        return _int_env("LEDGER_GUARD_THRESHOLD_FABLE", 1500)
    return _int_env("LEDGER_GUARD_THRESHOLD_OPUS", 4000)


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
