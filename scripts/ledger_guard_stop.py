#!/usr/bin/env python3
"""Stop guard: hold a turn-end while the session's Requirements Ledger has open items.

Open item = a line matching '- [ ]'. Closed: '- [x]' (done+verified) or
'- [~] deferred: <reason>' (user-approved).

Blocking is SCOPED so the reminder doesn't tax every conversational turn
(measured in the wild: hundreds of per-turn blocks per project):

  1. OWNERSHIP — block only if the ledger was modified during THIS
     session. Session start is approximated by the mtime of the
     per-session model cache written by the SessionStart injector;
     without a cache (manual install), ownership is assumed.
  2. CADENCE — once per session per ledger. A sidecar file in the
     temp dir records the ledgers this session was already held on.

LEDGER_GUARD_STOP_MODE=every-turn restores the legacy per-turn blocking.

The ledger is searched from the working directory upward, stopping at
the first directory containing .git (a FILE in worktrees/submodules —
still a boundary) or at $HOME, so a ledger above the home directory can
never hold unrelated sessions.
"""
import json
import os
import re
import sys
import tempfile
import time


def _tmp_json(prefix, session_id):
    if not session_id:
        return None
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")
    return os.path.join(tempfile.gettempdir(), f"{prefix}-{safe}.json")


def session_model_cache_path(session_id):
    return _tmp_json("fable-orch-model", session_id)


def stop_sidecar_path(session_id):
    return _tmp_json("fable-orch-stop", session_id)


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


def find_ledger(start_dir):
    """Path of .workflow/LEDGER.md from start_dir up to the repo root or $HOME.

    Stops at the first directory that contains .git (checked with
    os.path.exists, not isdir — in worktrees and submodules .git is a
    FILE), at the home directory (a ledger above $HOME belongs to
    nobody), or at the filesystem root.
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


def owned_by_session(ledger, session_id):
    """True if the ledger was modified during this session.

    Session start is the injector cache's immutable `started` field —
    the cache FILE is rewritten on resume/clear/compact re-injections,
    so its mtime moves and serves only as the fallback for caches
    written by older versions. 60s slack for clock jitter. No cache
    (manual install) → assume ownership rather than go silent.
    """
    cache = session_model_cache_path(session_id)
    if not cache or not os.path.isfile(cache):
        return True
    try:
        start = None
        try:
            with open(cache, encoding="utf-8") as f:
                raw = json.load(f).get("started")
            start = float(raw) if raw is not None else None
        except Exception:
            start = None
        if start is None:
            start = os.path.getmtime(cache)
        return os.path.getmtime(ledger) >= start - 60.0
    except OSError:
        return True


def already_reminded(session_id, ledger):
    path = stop_sidecar_path(session_id)
    if not path or not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            return ledger in (json.load(f).get("blocked") or {})
    except Exception:
        return False


def record_reminder(session_id, ledger):
    path = stop_sidecar_path(session_id)
    if not path:
        return
    blocked = {}
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                blocked = json.load(f).get("blocked") or {}
    except Exception:
        blocked = {}
    blocked[ledger] = round(time.time(), 3)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"blocked": blocked}, f)
    except Exception:
        pass


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    # Loop guard: we already blocked this stop once; let it through now.
    if data.get("stop_hook_active"):
        return

    ledger = find_ledger(data.get("cwd"))
    if not ledger:
        return

    try:
        with open(ledger, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return

    open_items = re.findall(r"^\s*[-*] \[ \] .*$", text, flags=re.M)
    if not open_items:
        return

    session_id = data.get("session_id")
    mode = (os.environ.get("LEDGER_GUARD_STOP_MODE") or "once-per-session").strip().lower()
    if mode != "every-turn":
        if not owned_by_session(ledger, session_id):
            _metric("stop_suppressed", session_id, reason="not-owned", ledger=ledger)
            return
        if already_reminded(session_id, ledger):
            _metric("stop_suppressed", session_id, reason="already-reminded", ledger=ledger)
            return
        record_reminder(session_id, ledger)

    _metric("stop_block", session_id, open=len(open_items), ledger=ledger)
    preview = "\n".join(line.strip()[:200] for line in open_items[:10])
    more = f"\n(+{len(open_items) - 10} more)" if len(open_items) > 10 else ""
    print(json.dumps({
        "decision": "block",
        "reason": (
            f"LEDGER GUARD: {ledger} still has {len(open_items)} "
            f"open item(s):\n{preview}{more}\n\n"
            "If you are CLOSING a workflow: address each item and mark it '- [x]' "
            "(only after verification confirms it), or '- [~] deferred: <reason>' "
            "with user approval, and run the fresh-agent verification phase if you "
            "haven't. If you are NOT closing a workflow, acknowledge the open-item "
            "count in one short line and stop — this reminder fires once per "
            "session. Archive the ledger (rename to LEDGER-<topic>-archive.md) "
            "if the task is truly abandoned."
        ),
    }))


if __name__ == "__main__":
    main()
