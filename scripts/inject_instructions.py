#!/usr/bin/env python3
"""SessionStart hook: inject the model-appropriate Dynamic Workflow instructions.

Two profiles ship side by side:

  instructions/dynamic-workflow-fable.md  Fable-in-chair, token-frugal.
      Heavy delegation discipline that trades latency to preserve the
      usage limit of an ultra-scarce top tier.

  instructions/dynamic-workflow-opus.md   Opus-class-in-chair, latency-lean.
      A 1M-context judgment+implementation model is in the chair, so the
      scarce resource flips to wall-clock. Same quality guarantees, but
      ceremony (ledger, verification, disk hand-off) is proportional.

Which one is injected depends on the SESSION MODEL, reported by Claude
Code in the SessionStart payload as a top-level `model` string. This hook
also caches the resolved model+profile to a per-session file so the
PreToolUse/Stop guards (which do NOT receive the model) can adapt.

Override the auto-detection with FABLE_ORCH_PROFILE = auto | fable | opus.
"""
import json
import os
import sys
import tempfile


def session_model_cache_path(session_id):
    """Per-session cache file the spawn/stop guards read. None if no id."""
    if not session_id:
        return None
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")
    return os.path.join(tempfile.gettempdir(), f"fable-orch-model-{safe}.json")


def resolve_profile(model):
    """Return 'fable' or 'opus' (lean). Default lean unless clearly Fable.

    Defaulting unknown/non-Fable to the lean profile matches intent: the
    heavy Fable profile must not silently run on Opus/Sonnet/unknown.
    """
    override = (os.environ.get("FABLE_ORCH_PROFILE") or "auto").strip().lower()
    if override in ("fable", "opus"):
        return override
    if model and "fable" in str(model).lower():
        return "fable"
    return "opus"


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}

    model = data.get("model")  # optional; may be absent
    session_id = data.get("session_id")
    profile = resolve_profile(model)

    root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
    filename = f"dynamic-workflow-{profile}.md"
    path = os.path.join(root, "instructions", filename)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return  # never break session start

    # Cache model+profile for the guards (best effort; never fatal).
    try:
        cache = session_model_cache_path(session_id)
        if cache:
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(
                    {"model": model, "profile": profile, "session_id": session_id},
                    f,
                )
    except Exception:
        pass

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }))


if __name__ == "__main__":
    main()
