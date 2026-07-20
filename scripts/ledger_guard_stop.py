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

Piggybacked duty: because this hook fires at every turn end of every
session, it also runs the rate-limited teammate-pane sweep (see
reap_idle_teammates) — finished teammates die within roughly
FABLE_ORCH_TEAMMATE_IDLE_H hours instead of waiting for a SessionEnd
that may be days away.
"""
import json
import os
import re
import subprocess
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
    written by older versions. 5s slack for filesystem timestamp
    granularity. No cache (manual install) → assume ownership rather
    than go silent.
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
        # A future `started` (clock jump, corrupt value) must not silence
        # the guard for the whole session — clamp to now.
        start = min(start, time.time())
        return os.path.getmtime(ledger) >= start - 5.0
    except OSError:
        return True


def _read_blocked(path):
    """The sidecar's blocked map — {} for missing/corrupt/wrong-typed."""
    try:
        with open(path, encoding="utf-8") as f:
            blocked = json.load(f).get("blocked")
        return blocked if isinstance(blocked, dict) else {}
    except Exception:
        return {}


def already_reminded(session_id, ledger):
    path = stop_sidecar_path(session_id)
    if not path or not os.path.isfile(path):
        return False
    return ledger in _read_blocked(path)


def record_reminder(session_id, ledger):
    path = stop_sidecar_path(session_id)
    if not path:
        return
    blocked = _read_blocked(path)
    try:
        blocked[ledger] = round(time.time(), 3)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"blocked": blocked}, f)
    except Exception:
        pass


TEAMMATE_SWEEP_INTERVAL = 1800  # at most one pane sweep per 30 minutes
TEAMMATE_SWEEP_BUDGET = 4       # seconds of wall clock per sweep
# A PARKED teammate is not CPU-silent: it polls its mailbox at roughly
# 0.004 cpu-sec/sec, so "cputime unchanged" never fires on the current
# backend. Idleness is a RATE: below this cpu-sec/wall-sec, the pane is
# considered parked. Working agents (even mostly API-bound) run well
# above it over an hour-long window.
TEAMMATE_IDLE_RATE = 0.01


def _idle_rate():
    raw = os.environ.get("FABLE_ORCH_TEAMMATE_IDLE_RATE")
    if raw is not None:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return TEAMMATE_IDLE_RATE


def _team_sockets():
    """Every tmux socket that may host teammate panes (TMUX_TMPDIR or
    /tmp, NOT $TMPDIR). Current Claude Code opens teammate panes inside
    the USER'S default tmux server; older versions parked them in
    dedicated claude-swarm-* servers — scan every socket and let the
    pane filters (--agent-id + claude root command) protect everything
    that isn't a teammate."""
    try:
        base = os.environ.get("TMUX_TMPDIR") or "/tmp"
        d = os.path.join(base, f"tmux-{os.getuid()}")
        return [os.path.join(d, n) for n in sorted(os.listdir(d))
                if not n.startswith(".")]
    except Exception:
        return []


def _tmux(sock, *args):
    return subprocess.run(["tmux", "-S", sock, *args],
                          capture_output=True, text=True, timeout=5)


def _cpu_seconds(text):
    """Parse a ps cputime ([DD-]HH:MM:SS[.ff] or MM:SS[.ff]) into seconds."""
    text = text.strip()
    days = 0
    if "-" in text:
        day_part, text = text.split("-", 1)
        try:
            days = int(day_part)
        except ValueError:
            return None
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError:
        return None
    if not parts:
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return days * 86400 + seconds


def reap_idle_teammates(session_id):
    """Kill teammate panes whose CPU RATE stayed under the parked
    threshold for FABLE_ORCH_TEAMMATE_IDLE_H hours (default 1; 0
    disables).

    The agent-teams backend parks finished teammates in their tmux
    panes for the whole life of the parent session — on current Claude
    Code those panes sit in the USER'S default tmux server. A parked
    teammate still burns a mailbox-polling heartbeat, so idleness is a
    sustained LOW RATE (see TEAMMATE_IDLE_RATE), measured PER PANE
    against a baseline kept in the state file. A rate at or above the
    threshold re-baselines; a first sighting is never killed. The sweep
    is rate-limited via the state file's mtime. Killing the pane ends
    the teammate process (it exits moments after its pane closes).
    """
    if (os.environ.get("FABLE_ORCH_SWARM_CLEANUP") or "").strip() == "0":
        return
    try:
        idle_h = float(os.environ.get("FABLE_ORCH_TEAMMATE_IDLE_H") or 1)
    except ValueError:
        idle_h = 1.0
    if idle_h <= 0:
        return

    state_path = os.path.join(os.path.expanduser("~"), ".claude",
                              "fable-orch", "swarm-state.json")
    now = time.time()
    try:
        if now - os.path.getmtime(state_path) < TEAMMATE_SWEEP_INTERVAL:
            return
    except OSError:
        pass  # no state yet — first sweep
    try:
        with open(state_path, encoding="utf-8") as f:
            panes = json.load(f).get("panes") or {}
    except Exception:
        panes = {}

    # The budget is checked before EVERY subprocess call, not just per
    # socket: a wedged tmux answers at the 5s timeout each, and one
    # iteration makes up to 3 calls — unchecked, a 4s budget stretches
    # past the 10s hook timeout and the harness SIGKILLs the hook.
    deadline = now + TEAMMATE_SWEEP_BUDGET
    seen = set()
    killed = 0
    expired = False
    for sock in _team_sockets():
        if expired or time.time() > deadline:
            expired = True
            break
        try:
            r = _tmux(sock, "list-panes", "-a", "-F", "#{pane_id} #{pane_pid}")
            if r.returncode != 0:
                continue
            pane_ids = {}
            for line in r.stdout.splitlines():
                bits = line.split()
                if len(bits) == 2 and bits[1].isdigit():
                    pane_ids[bits[1]] = bits[0]
            if not pane_ids:
                continue
            if time.time() > deadline:
                expired = True
                break
            ps = subprocess.run(
                ["ps", "-o", "pid=,cputime=,command=", "-p", ",".join(pane_ids)],
                capture_output=True, text=True, timeout=5,
            )
            for line in ps.stdout.splitlines():
                bits = line.split(None, 2)
                if len(bits) < 3:
                    continue
                pid, cpu_text, command = bits
                if pid not in pane_ids or "--agent-id" not in command:
                    continue  # not a teammate pane — never touch it
                exe = os.path.basename(command.split()[0]) if command.split() else ""
                if not exe.startswith("claude"):
                    # Wrapper-shell root (`sh -c '... && claude ...'`): the
                    # child burns the CPU while the shell's clock stays
                    # frozen — judging idleness by it kills live workers.
                    continue
                cpu = _cpu_seconds(cpu_text)
                if cpu is None:
                    continue
                # Key by socket+pane+pid: a bare pid key survives pid reuse
                # and can hand a NEW busy pane a stale idle baseline.
                key = f"{os.path.basename(sock)}:{pane_ids[pid]}:{pid}"
                seen.add(key)
                prev = panes.get(key)
                try:
                    prev_cpu = float(prev.get("cpu"))
                    since = float(prev.get("since"))
                except (AttributeError, TypeError, ValueError):
                    prev = None
                if not prev:
                    panes[key] = {"cpu": cpu, "since": round(now, 3)}
                    continue  # first sighting: baseline only, never kill
                elapsed = max(now - since, 1.0)
                if (cpu - prev_cpu) / elapsed >= _idle_rate():
                    panes[key] = {"cpu": cpu, "since": round(now, 3)}
                    continue  # genuinely working: restart the idle clock
                if elapsed >= idle_h * 3600:
                    if time.time() > deadline:
                        expired = True
                        break
                    _tmux(sock, "kill-pane", "-t", pane_ids[pid])
                    panes.pop(key, None)
                    seen.discard(key)
                    killed += 1
                # else: parked so far but under the window — keep the
                # old baseline so the idle clock keeps accumulating
        except Exception:
            continue
    if expired:
        seen.update(panes)  # out of budget: keep unvisited samples

    panes = {p: v for p, v in panes.items() if p in seen}
    try:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"panes": panes, "swept": round(now, 3)}, f)
    except Exception:
        pass
    if killed:
        _metric("teammate_reap", session_id, killed=killed)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = None
    if not isinstance(data, dict):
        data = None
    try:
        if data is not None:
            run_guard(data)
    except Exception:
        pass  # the guard fails open; it never crashes the hook pipeline
    finally:
        # The decision above sits in a block-buffered pipe. The pane sweep
        # can outlive the hook timeout on a wedged tmux — flush FIRST so a
        # SIGKILL mid-sweep can't swallow the session's one reminder.
        try:
            sys.stdout.flush()
        except Exception:
            pass
        try:
            reap_idle_teammates((data or {}).get("session_id"))
        except Exception:
            pass  # cleanup is best-effort; the guard's decision already went out


def _outside_fences(text):
    """Drop fenced code blocks — a ``` example checklist is not an open item."""
    kept, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            kept.append(line)
    return "\n".join(kept)


def run_guard(data):
    # Keep the session marker warm: the 96h temp sweep must never eat a
    # LIVE session's marker/sidecars just because SessionStart hasn't
    # re-fired in days. Content (the immutable `started`) is untouched.
    cache = session_model_cache_path(data.get("session_id"))
    if cache and os.path.isfile(cache):
        try:
            os.utime(cache, None)
        except OSError:
            pass

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

    open_items = re.findall(r"^\s*[-*] \[ \](?:\s.*)?$",
                            _outside_fences(text), flags=re.M)
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
