#!/usr/bin/env python3
"""SessionEnd hook: remove this session's temp files, reap its tmux teammates,
and sweep stale leftovers.

Three duties, all best-effort (never fails the session):

  1. Delete this session's temp files (model cache + guard sidecars), then
     sweep any fable-orch-*.json older than 96h — SessionEnd doesn't fire
     for crashed sessions, so the files would otherwise accumulate.
  2. Reap this session's tmux teammates. The agent-teams backend parks
     teammates in tmux panes and does NOT reap them when the session
     ends — measured in the wild: 63 orphaned agents holding ~5 GB RSS.
     Current Claude Code opens the panes inside the USER'S default tmux
     server (killed PANE by pane via `--parent-session-id`); older
     versions used dedicated claude-swarm-* servers (killed whole, via
     the nearest-claude ancestor pid or the @session-<prefix> tag).
  3. Sweep swarm servers with no window activity for
     FABLE_ORCH_SWARM_MAX_IDLE_H hours (default 48; 0 disables) —
     catches teams orphaned by crashed sessions. Dead sockets are
     unlinked.

FABLE_ORCH_SWARM_CLEANUP=0 disables duties 2 and 3 entirely.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

# Temp-file sweep is 96h (not 48h): a session left open-but-idle for two
# days would otherwise lose its marker/sidecar to another session's sweep
# and with them the stop guard's ownership scoping. Files are tiny.
SWEEP_AGE_SECONDS = 96 * 3600
METRICS_MAX_BYTES = 5 * 1024 * 1024
# Hard wall-clock budget for all tmux work at SessionEnd — wedged tmux
# servers answer at the 5s subprocess timeout each, and the hook itself
# is killed at 20s; stop early rather than mid-sweep.
SWARM_BUDGET_SECONDS = 12


def _rotate_metrics():
    """Cap the metrics log: past ~5MB the current file becomes .old
    (replacing the previous .old), so the pair never exceeds ~10MB."""
    try:
        path = os.path.join(os.path.expanduser("~"), ".claude",
                            "fable-orch", "metrics.jsonl")
        if os.path.isfile(path) and os.path.getsize(path) > METRICS_MAX_BYTES:
            os.replace(path, path + ".old")
    except Exception:
        pass


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


def _swarm_dir():
    """tmux socket directory (tmux uses TMUX_TMPDIR or /tmp, NOT $TMPDIR)."""
    base = os.environ.get("TMUX_TMPDIR") or "/tmp"
    return os.path.join(base, f"tmux-{os.getuid()}")


def _swarm_sockets():
    try:
        d = _swarm_dir()
        return [os.path.join(d, n) for n in sorted(os.listdir(d))
                if n.startswith("claude-swarm-")]
    except OSError:
        return []


def _team_sockets():
    """Every tmux socket that may host teammate panes. Current Claude
    Code opens teammate panes inside the USER'S default tmux server;
    only the pane filters decide what dies — a non-swarm server itself
    is NEVER killed."""
    try:
        d = _swarm_dir()
        return [os.path.join(d, n) for n in sorted(os.listdir(d))
                if not n.startswith(".")]
    except OSError:
        return []


def _tmux(sock, *args):
    return subprocess.run(
        ["tmux", "-S", sock, *args],
        capture_output=True, text=True, timeout=5,
    )


def _is_claude_command(command):
    """True when an argv string IS the claude CLI — the native `claude`
    binary or the npm cli under a claude-code path. Deliberately NOT a
    bare substring test: hook command lines contain `.claude/plugins/...`
    paths, which must never match."""
    for tok in command.split():
        base = os.path.basename(tok.strip("\"'"))
        if base == "claude" or "claude-code" in tok:
            return True
    return False


def _nearest_claude_ancestor(deadline, max_hops=12):
    """PID of the CLOSEST ancestor that is the claude CLI, or None.

    The tmux backend names our swarm server claude-swarm-<main pid>.
    Matching only the NEAREST claude ancestor keeps a nested claude
    session (a `claude -p` run from Bash — fusion helpers, scripts)
    from claiming — and killing — the OUTER session's live team when
    the inner one ends."""
    self_pid = pid = os.getpid()
    for _ in range(max_hops):
        if time.time() > deadline:
            return None
        try:
            out = subprocess.run(
                ["ps", "-o", "ppid=,command=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            bits = (out.splitlines()[0] if out else "").split(None, 1)
            ppid = int(bits[0])
        except Exception:
            return None
        command = bits[1] if len(bits) > 1 else ""
        if pid != self_pid and _is_claude_command(command):
            return pid
        if ppid <= 1:
            return None
        pid = ppid
    return None


def _owns_pane(command, session_id):
    """True when the pane's argv carries `--parent-session-id <EXACTLY
    this id>`. Token-exact on purpose: substring matching would let
    session "abc" claim — and kill — a teammate of session "abc-def"."""
    toks = command.split()
    for i, t in enumerate(toks[:-1]):
        if t == "--parent-session-id":
            return toks[i + 1] == str(session_id)
    return False


def reap_own_swarm(session_id, deadline):
    """Kill THIS session's teammates so they die with the session.

    Three matchers:
      1. Whole-server: socket claude-swarm-<pid> where <pid> is this
         hook's NEAREST claude ancestor — legacy dedicated-server
         layout; version-proof for it.
      2. Whole-server: a claude-swarm-* server whose panes carry the
         @session-<prefix> tag (older teammate tagging).
      3. Pane-level, EVERY socket: panes whose command carries
         `--parent-session-id <this session id>` — the current layout
         parks teammates inside the USER'S default tmux server, so only
         the matching PANES are killed there, never the server.
    Returns the number of servers+panes killed.
    """
    own = _nearest_claude_ancestor(deadline)
    own_names = {f"claude-swarm-{own}"} if own else set()
    tag = f"@session-{str(session_id)[:8]}" if session_id else None
    killed = 0
    for sock in _team_sockets():
        if time.time() > deadline:
            break
        is_swarm = os.path.basename(sock).startswith("claude-swarm-")
        try:
            if is_swarm and os.path.basename(sock) in own_names:
                if _tmux(sock, "list-sessions").returncode == 0:
                    _tmux(sock, "kill-server")
                    killed += 1
                continue
            r = _tmux(sock, "list-panes", "-a", "-F", "#{pane_id} #{pane_pid}")
            if r.returncode != 0:
                continue  # dead server; the sweep unlinks swarm sockets
            pane_by_pid = {}
            for line in r.stdout.splitlines():
                bits = line.split()
                if len(bits) == 2 and bits[1].isdigit():
                    pane_by_pid[bits[1]] = bits[0]
            if not pane_by_pid:
                continue
            if time.time() > deadline:
                break
            ps = subprocess.run(
                ["ps", "-o", "pid=,command=", "-p", ",".join(pane_by_pid)],
                capture_output=True, text=True, timeout=5,
            )
            out = ps.stdout or ""
            if is_swarm and tag and tag in out:
                if time.time() > deadline:
                    break
                _tmux(sock, "kill-server")
                killed += 1
                continue
            if not session_id:
                continue
            for line in out.splitlines():
                bits = line.split(None, 1)
                if len(bits) < 2:
                    continue
                pid, command = bits
                if pid not in pane_by_pid or "--agent-id" not in command:
                    continue
                if not _owns_pane(command, session_id):
                    continue
                if time.time() > deadline:
                    break
                _tmux(sock, "kill-pane", "-t", pane_by_pid[pid])
                killed += 1
        except Exception:
            continue
    return killed


def sweep_stale_swarms(max_idle_h, deadline):
    """Kill swarm servers idle for max_idle_h+ hours; unlink dead sockets."""
    killed = 0
    cutoff = time.time() - max_idle_h * 3600
    for sock in _swarm_sockets():
        if time.time() > deadline:
            break
        try:
            r = _tmux(sock, "list-windows", "-a", "-F", "#{window_activity}")
            if r.returncode != 0:
                # Only unlink when the server is REALLY gone. A tmux
                # binary upgrade answers with "protocol version mismatch"
                # (also rc != 0) while the server lives — unlinking then
                # makes it a permanently unreachable orphan.
                err = (r.stderr or "").lower()
                if "no server running" in err or "error connecting" in err:
                    try:
                        os.remove(sock)  # server already gone; socket is litter
                    except OSError:
                        pass
                continue
            if max_idle_h <= 0:
                continue
            acts = [int(x) for x in r.stdout.split() if x.isdigit()]
            if acts and max(acts) < cutoff:
                _tmux(sock, "kill-server")
                killed += 1
        except Exception:
            continue
    return killed


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    session_id = data.get("session_id")
    for prefix in ("fable-orch-model", "fable-orch-stop", "fable-orch-tasks"):
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

    swarm_own = swarm_stale = 0
    if (os.environ.get("FABLE_ORCH_SWARM_CLEANUP") or "").strip() != "0":
        try:
            max_idle_h = float(os.environ.get("FABLE_ORCH_SWARM_MAX_IDLE_H") or 48)
        except ValueError:
            max_idle_h = 48.0
        try:
            deadline = time.time() + SWARM_BUDGET_SECONDS
            swarm_own = reap_own_swarm(session_id, deadline)
            swarm_stale = sweep_stale_swarms(max_idle_h, deadline)
        except Exception:
            pass  # no tmux, or no os.getuid (Windows) — never fail the hook

    _rotate_metrics()
    _metric("cleanup", session_id, swarm_own=swarm_own, swarm_stale=swarm_stale)


if __name__ == "__main__":
    main()
