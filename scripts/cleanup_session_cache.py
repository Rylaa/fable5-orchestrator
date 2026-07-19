#!/usr/bin/env python3
"""SessionEnd hook: remove this session's temp files, reap its tmux teammates,
and sweep stale leftovers.

Three duties, all best-effort (never fails the session):

  1. Delete this session's temp files (model cache + guard sidecars), then
     sweep any fable-orch-*.json older than 96h — SessionEnd doesn't fire
     for crashed sessions, so the files would otherwise accumulate.
  2. Reap this session's tmux teammates. The experimental agent-teams
     tmux backend (CLAUDE_CODE_SPAWN_BACKEND=tmux) parks teammates in a
     claude-swarm-* tmux server and does NOT reap them when the session
     ends — measured in the wild: 63 orphaned agents holding ~5 GB RSS
     across three old sessions. Teammate panes carry
     `--agent-id <name>@session-<prefix>` on their command line; a
     server whose panes match this session's prefix is killed.
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


def reap_own_swarm(session_id, deadline):
    """Kill swarm servers hosting THIS session's teammates. Returns count.

    Two matchers, either suffices:
      1. Socket name claude-swarm-<pid> where <pid> is this hook's
         NEAREST claude ancestor (the session's own main process — never
         an outer session's) — version-proof.
      2. Pane command tag @session-<prefix of this session id> — how older
         Claude Code versions tagged teammates; current versions tag with
         a per-team id, so this alone is no longer enough.
    """
    own = _nearest_claude_ancestor(deadline)
    own_names = {f"claude-swarm-{own}"} if own else set()
    marker = f"@session-{str(session_id)[:8]}" if session_id else None
    killed = 0
    for sock in _swarm_sockets():
        if time.time() > deadline:
            break
        try:
            if os.path.basename(sock) in own_names:
                if _tmux(sock, "list-sessions").returncode == 0:
                    _tmux(sock, "kill-server")
                    killed += 1
                continue
            if not marker:
                continue
            r = _tmux(sock, "list-panes", "-a", "-F", "#{pane_pid}")
            if r.returncode != 0:
                continue  # dead server; the sweep unlinks its socket
            pids = ",".join(p for p in r.stdout.split() if p.isdigit())
            if not pids:
                continue
            if time.time() > deadline:
                break
            ps = subprocess.run(
                ["ps", "-o", "command=", "-p", pids],
                capture_output=True, text=True, timeout=5,
            )
            if marker in (ps.stdout or ""):
                if time.time() > deadline:
                    break
                _tmux(sock, "kill-server")
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
