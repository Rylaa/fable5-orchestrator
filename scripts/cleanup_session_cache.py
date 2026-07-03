#!/usr/bin/env python3
"""SessionEnd hook: remove this session's model/profile cache file.

The SessionStart injector caches {model, profile} to a per-session
file in the temp dir so the PreToolUse guard can fall back to it when
the payload carries no model. Without cleanup those small files
accumulate across sessions; this hook deletes the one belonging to
the session that just ended. Best effort — never fails the session.
"""
import json
import os
import sys
import tempfile


def session_model_cache_path(session_id):
    if not session_id:
        return None
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")
    return os.path.join(tempfile.gettempdir(), f"fable-orch-model-{safe}.json")


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    path = session_model_cache_path(data.get("session_id"))
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass


if __name__ == "__main__":
    main()
