#!/usr/bin/env python3
"""SessionStart hook: inject the Dynamic Workflow instructions.

This plugin is built for one chair: Claude Fable 5. Every session gets
the single Fable-in-chair, token-frugal profile
(instructions/dynamic-workflow-fable.md) — heavy delegation discipline
that trades latency to preserve the usage limit of an ultra-scarce top
tier, with Sonnet 5 carrying the work and a two-tier verification /
escalation valve above it.

The hook also maintains the per-session marker file the Stop and
SessionEnd hooks rely on: its immutable `started` timestamp survives
the re-runs SessionStart gets on resume/clear/compact, and the stop
guard compares ledger mtimes against it to decide ownership.
"""
import json
import os
import sys
import tempfile
import time


def session_model_cache_path(session_id):
    """Per-session marker file the stop/cleanup hooks read. None if no id."""
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


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}

    model = data.get("model")  # optional; informational only
    session_id = data.get("session_id")

    root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
    path = os.path.join(root, "instructions", "dynamic-workflow-fable.md")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return  # never break session start

    # Session marker for the guards (best effort; never fatal).
    # `started` marks the session's FIRST start and must survive the
    # re-runs SessionStart gets on resume/clear/compact — the stop guard
    # compares ledger mtimes against it to decide ownership, so it can
    # never move forward.
    try:
        cache = session_model_cache_path(session_id)
        if cache:
            started = time.time()
            try:
                with open(cache, encoding="utf-8") as f:
                    started = float(json.load(f).get("started"))
            except Exception:
                # Marker from an older plugin version (no `started`) or a
                # corrupt write: fall back to the file's mtime — the old
                # heuristic — NEVER to "now", which would disown every
                # ledger touched before this re-injection.
                try:
                    started = os.path.getmtime(cache)
                except OSError:
                    pass
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(
                    {"model": model, "session_id": session_id,
                     "started": round(started, 3)},
                    f,
                )
    except Exception:
        pass

    _metric("inject", session_id, model=model)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }))


if __name__ == "__main__":
    main()
