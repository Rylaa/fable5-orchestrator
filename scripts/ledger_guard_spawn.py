#!/usr/bin/env python3
"""PreToolUse guard (Agent|Task): block detailed delegations without a Requirements Ledger.

Dynamic Workflow Rule 1: serious multi-phase delegation requires
./.workflow/LEDGER.md. Short spawn prompts (quick searches/lookups)
pass freely so casual Explore agents are never blocked.

The threshold is MODEL-AWARE. The Fable profile keeps a strict gate
(small delegations should still carry a ledger, because Fable tokens are
the scarce resource). The lean Opus profile raises the gate: a 1M-context
chair model fans out with rich agent prompts as normal practice, and the
ledger is meant to be *proportional* — only large/parallel/high-risk work
writes a file. A flat 1500-char gate would tax ordinary Opus fan-out.

The active profile is read from the per-session cache written by the
SessionStart hook (PreToolUse payloads do not carry the model). Missing
cache -> assume the lean profile, to avoid over-blocking.

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


def active_profile(session_id):
    """'fable' or 'opus' from the session cache; default 'opus' (lean)."""
    cache = session_model_cache_path(session_id)
    if cache and os.path.isfile(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                prof = (json.load(f).get("profile") or "").strip().lower()
            if prof in ("fable", "opus"):
                return prof
        except Exception:
            pass
    return "opus"


def threshold(session_id):
    # Hard override wins for every profile (backward compatible).
    if "LEDGER_GUARD_THRESHOLD" in os.environ:
        return _int_env("LEDGER_GUARD_THRESHOLD", 1500)
    if active_profile(session_id) == "fable":
        return _int_env("LEDGER_GUARD_THRESHOLD_FABLE", 1500)
    return _int_env("LEDGER_GUARD_THRESHOLD_OPUS", 4000)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return  # malformed input -> never block

    limit = threshold(data.get("session_id"))
    prompt = (data.get("tool_input") or {}).get("prompt") or ""
    if len(prompt) <= limit:
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
                f"(spawn prompt > {limit} chars) but ./.workflow/LEDGER.md "
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
