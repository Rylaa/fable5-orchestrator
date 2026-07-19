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

Staleness: a ledger satisfies the gates unless it is STALE-COMPLETE —
every item closed AND untouched since before this session started
(session start read from the injector's marker). Without that rule,
last week's finished ledger would silence the gates in a repo forever.
A ledger with open items, or one touched this session, always
satisfies; without a marker (manual install) existence alone wins.

Configuration (all optional):
    LEDGER_GUARD_THRESHOLD   gate in chars (default 1500; unparseable
                             values fall back to it, negatives clamp
                             to 0)
    LEDGER_GUARD_TASKS       deny fires AT the Nth ledgerless tracker
                             task (default 3 — two tasks pass free;
                             0 or negative disables the task gate)
    FABLE_ORCH_METRICS=0     disables the local metrics log
"""
import json
import os
import re
import sys
import tempfile
import time

try:
    import fcntl
except ImportError:  # non-POSIX: run unlocked, best effort
    fcntl = None

DEFAULT_THRESHOLD = 1500
DEFAULT_TASK_LIMIT = 3
OPEN_ITEM_RE = r"^\s*[-*] \[ \](?:\s.*)?$"


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
            return max(0, int(raw))
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


def _bump_task_count(path):
    """Read-increment-write the sidecar under an exclusive lock.

    Parallel TaskCreate hooks race on this file; without the lock the
    deny can be skipped or fired twice (proven under forced
    concurrency). Valid-JSON-but-wrong-typed content must coerce, not
    crash — the hook contract is exit 0 always. Returns
    (count, denied_before) or None when the file can't be used.
    """
    try:
        f = open(path, "a+", encoding="utf-8")
    except OSError:
        return None
    try:
        if fcntl is not None:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            except OSError:
                pass
        f.seek(0)
        try:
            state = json.load(f)
        except Exception:
            state = {}
        if not isinstance(state, dict):
            state = {}
        try:
            count = int(state.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        count += 1
        denied_before = bool(state.get("denied"))
        deny_now = count >= task_limit() and not denied_before
        try:
            f.seek(0)
            f.truncate()
            json.dump({"count": count, "denied": denied_before or deny_now}, f)
            f.flush()
        except (OSError, ValueError):
            pass
        return count, denied_before
    finally:
        f.close()


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
    session_id = data.get("session_id")
    ledger = find_ledger(data.get("cwd"))
    stale = bool(ledger) and not ledger_satisfies(ledger, session_id)
    if ledger and not stale:
        return

    path = _task_sidecar(session_id)
    if path is None:
        return
    bumped = _bump_task_count(path)
    if bumped is None:
        return
    count, denied_before = bumped

    if count < limit:
        return
    if denied_before:
        _metric("tasks_suppressed", session_id, count=count)
        return

    _metric("tasks_deny", session_id, count=count, threshold=limit, stale=stale)
    stale_note = (
        f" (a fully-closed ledger from a previous session was found at {ledger} "
        "and ignored — archive it as LEDGER-<topic>-archive.md or write a "
        "fresh one)" if stale else ""
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"LEDGER GUARD: this is tracker task #{count} this session — "
                "multi-phase work — but no active .workflow/LEDGER.md exists "
                f"from the working directory up to the repo root{stale_note}. "
                "Rule 0's hard cap: work that needs a task list of 3+ items is "
                "OVER the orchestration threshold, and an approved plan is NOT "
                "an exemption. Write the numbered Requirements Ledger to "
                "./.workflow/LEDGER.md now, then delegate implementation to "
                "sonnet workers citing ledger items instead of implementing "
                "the phases yourself. Re-issue this task afterwards — this "
                "reminder fires once per session."
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
    root. realpath, not abspath: a symlinked cwd must climb the REAL
    project tree, or a legitimate ledger next to it is never found.
    """
    if not isinstance(start_dir, str) or not start_dir:
        start_dir = os.getcwd()
    d = os.path.realpath(start_dir)
    home = os.path.realpath(os.path.expanduser("~"))
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


def _session_started(session_id):
    """The session's immutable start time from the injector marker, or None."""
    if not session_id:
        return None
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")
    path = os.path.join(tempfile.gettempdir(), f"fable-orch-model-{safe}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f).get("started")
        if raw is not None:
            return float(raw)
    except Exception:
        pass
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def ledger_satisfies(ledger, session_id):
    """False only for a STALE-COMPLETE ledger: all items closed AND
    untouched since before this session started. Open items or a
    this-session touch keep it armed; no marker → existence wins."""
    try:
        with open(ledger, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return True
    if re.findall(OPEN_ITEM_RE, text, flags=re.M):
        return True
    started = _session_started(session_id)
    if started is None:
        return True
    try:
        return os.path.getmtime(ledger) >= min(started, time.time()) - 5.0
    except OSError:
        return True


def _guard(data):
    if (data.get("tool_name") or "") == "TaskCreate":
        guard_task_create(data)
        return

    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    # Forks inherit the full conversation context — ledger already visible.
    if str(tool_input.get("subagent_type") or "").strip().lower() == "fork":
        return

    if (data.get("tool_name") or "") == "Workflow":
        text = tool_input.get("script")
        what = "orchestration script"
    else:
        text = tool_input.get("prompt")
        what = "spawn prompt"
    if not isinstance(text, str):
        text = ""

    limit = threshold()
    if len(text) <= limit:
        return

    session_id = data.get("session_id")
    ledger = find_ledger(data.get("cwd"))
    stale = bool(ledger) and not ledger_satisfies(ledger, session_id)
    if ledger and not stale:
        _metric("spawn_pass_over_threshold", session_id,
                chars=len(text), threshold=limit,
                tool=data.get("tool_name") or "")
        return

    _metric("spawn_deny", session_id,
            chars=len(text), threshold=limit,
            tool=data.get("tool_name") or "", stale=stale)
    stale_note = (
        f" (a fully-closed ledger from a previous session was found at {ledger} "
        "and ignored — archive it as LEDGER-<topic>-archive.md or write a "
        "fresh one)" if stale else ""
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"LEDGER GUARD: this looks like a detailed delegation "
                f"({what} > {limit} chars) but no active .workflow/LEDGER.md "
                f"exists from the working directory up to the repo root"
                f"{stale_note}. Per Dynamic Workflow Rule 1, first write the "
                "numbered Requirements Ledger to ./.workflow/LEDGER.md "
                "(checkbox format: '- [ ] N. <item>'), then re-spawn citing "
                "which ledger items each agent covers. If this is genuinely a "
                "small single-phase task, do it directly; if it is "
                "multi-phase, write the ledger and delegate — never keep "
                "multi-phase work solo."
            ),
        }
    }))


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return  # malformed input -> never block
    if not isinstance(data, dict):
        return
    try:
        _guard(data)
    except Exception:
        return  # a guard fails open; it never crashes the hook pipeline


if __name__ == "__main__":
    main()
