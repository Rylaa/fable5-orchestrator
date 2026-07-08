#!/usr/bin/env python3
"""SessionEnd hook: remove this session's temp files and sweep stale ones.

The SessionStart injector caches {model, profile} per session, and the
stop guard keeps a per-session reminder sidecar. This hook deletes both
for the session that just ended, then sweeps any fable-orch-*.json in
the temp dir older than 48 hours — SessionEnd doesn't fire for crashed
or killed sessions, so without the sweep the files accumulate (39
observed in a single day in the wild). Best effort — never fails the
session.
"""
import json
import os
import sys
import tempfile
import time

SWEEP_AGE_SECONDS = 48 * 3600


def _tmp_json(prefix, session_id):
    if not session_id:
        return None
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")
    return os.path.join(tempfile.gettempdir(), f"{prefix}-{safe}.json")


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

    session_id = data.get("session_id")
    for prefix in ("fable-orch-model", "fable-orch-stop"):
        path = _tmp_json(prefix, session_id)
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass

    # Age sweep: catch files left behind by sessions that never ended cleanly.
    try:
        tdir = tempfile.gettempdir()
        cutoff = time.time() - SWEEP_AGE_SECONDS
        for name in os.listdir(tdir):
            if name.startswith("fable-orch-") and name.endswith(".json"):
                path = os.path.join(tdir, name)
                try:
                    if os.path.getmtime(path) < cutoff:
                        os.remove(path)
                except OSError:
                    pass
    except Exception:
        pass

    _metric("cleanup", session_id)


if __name__ == "__main__":
    main()
