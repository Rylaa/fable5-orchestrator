import json

from conftest import REPO, run_hook

INJECT = "inject_instructions.py"
CLEANUP = "cleanup_session_cache.py"


def context_of(result):
    assert result["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    return result["hookSpecificOutput"]["additionalContext"]


def test_profiles_fit_the_injection_cap():
    # Claude Code caps hook output at 10,000 chars; anything over is
    # dumped to a file and the model sees only a 2KB preview — the
    # profile silently stops reaching the chair (this happened: v0.8.0
    # shipped at 10,069 chars). Keep a safety margin for the JSON wrapper.
    for name in ("dynamic-workflow-fable.md", "dynamic-workflow-opus.md"):
        text = (REPO / "instructions" / name).read_text(encoding="utf-8")
        assert len(text) < 9000, f"{name} is {len(text)} chars — over the 9k safety margin"


def test_injects_the_fable_profile(tmp_path):
    result = run_hook(
        INJECT,
        {"model": "claude-fable-5", "session_id": "s-fable"},
        env_extra={"CLAUDE_PLUGIN_ROOT": str(REPO)},
        tmpdir=tmp_path,
    )
    assert "(FABLE profile)" in context_of(result)
    cache = tmp_path / "fable-orch-model-s-fable.json"
    assert cache.is_file()
    data = json.loads(cache.read_text())
    assert data["model"] == "claude-fable-5"
    assert "started" in data


def test_non_opus_models_get_the_fable_profile(tmp_path):
    # Fable-first: everything that isn't an opus chair (sonnet chairs
    # included) gets the primary profile.
    result = run_hook(
        INJECT,
        {"model": "claude-sonnet-5", "session_id": "s-sonnet"},
        env_extra={"CLAUDE_PLUGIN_ROOT": str(REPO)},
        tmpdir=tmp_path,
    )
    assert "(FABLE profile)" in context_of(result)


def test_opus_chair_gets_the_opus_fallback_profile(tmp_path):
    # The Fable limit ran dry and the chair restarted on Opus 4.8: the
    # matching profile keeps the same discipline with the fable tier
    # resting.
    result = run_hook(
        INJECT,
        {"model": "claude-opus-4-8", "session_id": "s-opus"},
        env_extra={"CLAUDE_PLUGIN_ROOT": str(REPO)},
        tmpdir=tmp_path,
    )
    text = context_of(result)
    assert "(OPUS profile)" in text
    assert "(FABLE profile)" not in text


def test_missing_model_still_injects(tmp_path):
    result = run_hook(
        INJECT,
        {"session_id": "s-nomodel"},
        env_extra={"CLAUDE_PLUGIN_ROOT": str(REPO)},
        tmpdir=tmp_path,
    )
    assert "(FABLE profile)" in context_of(result)
    cache = tmp_path / "fable-orch-model-s-nomodel.json"
    assert "started" in json.loads(cache.read_text())


def test_plugin_root_fallback_to_script_location(tmp_path):
    # No CLAUDE_PLUGIN_ROOT: the script resolves the repo from its own path.
    result = run_hook(
        INJECT,
        {"model": "claude-fable-5", "session_id": "s-fallback"},
        tmpdir=tmp_path,
    )
    assert "(FABLE profile)" in context_of(result)


def test_metrics_written_when_enabled(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    run_hook(
        INJECT,
        {"model": "claude-fable-5", "session_id": "s-metrics"},
        env_extra={
            "CLAUDE_PLUGIN_ROOT": str(REPO),
            "HOME": str(home),
            "FABLE_ORCH_METRICS": "1",
        },
        tmpdir=tmp_path,
    )
    log = home / ".claude" / "fable-orch" / "metrics.jsonl"
    assert log.is_file()
    rec = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[0])
    assert rec["event"] == "inject"
    assert rec["model"] == "claude-fable-5"
    assert rec["profile"] == "fable"


def test_metrics_optout(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    run_hook(
        INJECT,
        {"model": "claude-fable-5", "session_id": "s-nometrics"},
        env_extra={
            "CLAUDE_PLUGIN_ROOT": str(REPO),
            "HOME": str(home),
            "FABLE_ORCH_METRICS": "0",
        },
        tmpdir=tmp_path,
    )
    assert not (home / ".claude" / "fable-orch" / "metrics.jsonl").exists()


def test_inject_preserves_started_across_reruns(tmp_path):
    env = {"CLAUDE_PLUGIN_ROOT": str(REPO)}
    run_hook(INJECT, {"model": "claude-fable-5", "session_id": "s-started"},
             env_extra=env, tmpdir=tmp_path)
    cache = tmp_path / "fable-orch-model-s-started.json"
    data = json.loads(cache.read_text(encoding="utf-8"))
    assert "started" in data
    data["started"] = 123.0  # pretend the session started long ago
    cache.write_text(json.dumps(data), encoding="utf-8")
    # Re-injection (resume/clear/compact) must not move `started` forward.
    run_hook(INJECT, {"model": "claude-fable-5", "session_id": "s-started"},
             env_extra=env, tmpdir=tmp_path)
    assert json.loads(cache.read_text(encoding="utf-8"))["started"] == 123.0


def test_metrics_rotation_caps_the_log(tmp_path):
    home = tmp_path / "home"
    d = home / ".claude" / "fable-orch"
    d.mkdir(parents=True)
    log = d / "metrics.jsonl"
    log.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
    run_hook(CLEANUP, {"session_id": "s-rot"},
             env_extra={"HOME": str(home), "FABLE_ORCH_METRICS": "1"},
             tmpdir=tmp_path)
    assert (d / "metrics.jsonl.old").is_file()
    assert log.is_file() and b"cleanup" in log.read_bytes()


def test_cleanup_removes_cache(tmp_path):
    cache = tmp_path / "fable-orch-model-s-clean.json"
    cache.write_text(json.dumps({"profile": "fable"}), encoding="utf-8")
    assert run_hook(CLEANUP, {"session_id": "s-clean"}, tmpdir=tmp_path) is None
    assert not cache.exists()


def test_cleanup_removes_stop_sidecar_and_sweeps_old(tmp_path):
    import os
    import time

    cache = tmp_path / "fable-orch-model-s-clean.json"
    cache.write_text("{}", encoding="utf-8")
    sidecar = tmp_path / "fable-orch-stop-s-clean.json"
    sidecar.write_text("{}", encoding="utf-8")
    tasks = tmp_path / "fable-orch-tasks-s-clean.json"
    tasks.write_text('{"count": 2}', encoding="utf-8")
    stale = tmp_path / "fable-orch-model-dead-session.json"
    stale.write_text("{}", encoding="utf-8")
    old = time.time() - 120 * 3600  # past the 96h sweep window
    os.utime(stale, (old, old))
    fresh = tmp_path / "fable-orch-model-alive.json"
    fresh.write_text("{}", encoding="utf-8")

    assert run_hook(CLEANUP, {"session_id": "s-clean"}, tmpdir=tmp_path) is None
    assert not cache.exists()
    assert not sidecar.exists()
    assert not tasks.exists()  # the task-gate counter dies with the session
    assert not stale.exists()  # older than the 96h sweep window
    assert fresh.exists()      # other live sessions' files stay


FAKE_TMUX = """#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
sock = args[1] if len(args) > 1 and args[0] == "-S" else ""
cmd = args[2] if len(args) > 2 else ""
if os.environ.get("FAKE_TMUX_DEAD") == "1":
    sys.stderr.write("no server running\\n")
    sys.exit(1)
if os.environ.get("FAKE_TMUX_MISMATCH") == "1":
    sys.stderr.write("protocol version mismatch (client 3.4, server 3.3)\\n")
    sys.exit(1)
if cmd == "list-panes":
    print(os.environ.get("FAKE_PANES", "%1 12345"))
elif cmd == "list-windows":
    print(os.environ.get("FAKE_WINDOW_ACTIVITY", "0"))
elif cmd == "kill-pane":
    with open(os.environ["FAKE_KILL_LOG"], "a") as f:
        f.write("pane " + sock + " " + " ".join(args[3:]) + "\\n")
elif cmd == "kill-server":
    with open(os.environ["FAKE_KILL_LOG"], "a") as f:
        f.write(sock + "\\n")
sys.exit(0)
"""

FAKE_PS = """#!/usr/bin/env python3
import json, os, sys
log = os.environ.get("FAKE_PS_LOG")
if log:
    with open(log, "a") as f:
        f.write(" ".join(sys.argv[1:]) + "\\n")
if "ppid=,command=" in sys.argv:
    # nearest-claude ancestor walk: "<ppid> <command of queried pid>"
    anc = os.environ.get("FAKE_ANCESTRY")
    if anc:
        m = json.loads(anc)
        print(m.get(sys.argv[-1], m.get("default", "1 init")))
    else:
        print(os.environ.get("FAKE_PPID", "1") + " claude")
elif "ppid=" in sys.argv:
    print(os.environ.get("FAKE_PPID", "1"))   # legacy ancestor walk
elif any("cputime" in a for a in sys.argv):
    print(os.environ.get("FAKE_PS_PANE",       # pane idle sampling
          "12345 0:05.00 claude --agent-id w@session-t --agent-name w"))
else:
    print(os.environ.get("FAKE_PS_OUTPUT", ""))  # command lookup
"""


def _swarm_fixture(tmp_path):
    """Fake tmux/ps on PATH + a fake socket dir with one swarm socket."""
    import os as _os

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("tmux", FAKE_TMUX), ("ps", FAKE_PS)):
        p = bin_dir / name
        p.write_text(body, encoding="utf-8")
        _os.chmod(p, 0o755)
    swarm_root = tmp_path / "tmuxroot"
    sock_dir = swarm_root / f"tmux-{_os.getuid()}"
    sock_dir.mkdir(parents=True)
    (sock_dir / "claude-swarm-111").write_text("", encoding="utf-8")
    kill_log = tmp_path / "kills.log"
    env = {
        "PATH": f"{bin_dir}:{_os.environ.get('PATH', '')}",
        "TMUX_TMPDIR": str(swarm_root),
        "FABLE_ORCH_SWARM_CLEANUP": "1",
        "FAKE_KILL_LOG": str(kill_log),
        "FAKE_PS_LOG": str(tmp_path / "ps.log"),
    }
    return env, kill_log


def test_cleanup_reaps_own_swarm(tmp_path):
    env, kill_log = _swarm_fixture(tmp_path)
    env["FAKE_PS_OUTPUT"] = "claude --agent-id worker@session-s-swarm- --agent-name worker"
    import time
    env["FAKE_WINDOW_ACTIVITY"] = str(int(time.time()))  # fresh — sweep must not fire
    assert run_hook(CLEANUP, {"session_id": "s-swarm-123"}, env_extra=env, tmpdir=tmp_path) is None
    assert kill_log.is_file()
    assert "claude-swarm-111" in kill_log.read_text()
    # The tag matcher must actually query ps with the pane pids it collected.
    ps_log = tmp_path / "ps.log"
    assert ps_log.is_file() and "-p 12345" in ps_log.read_text()


def test_cleanup_leaves_other_sessions_swarm(tmp_path):
    env, kill_log = _swarm_fixture(tmp_path)
    env["FAKE_PS_OUTPUT"] = "claude --agent-id worker@session-deadbeef --agent-name worker"
    import time
    env["FAKE_WINDOW_ACTIVITY"] = str(int(time.time()))
    assert run_hook(CLEANUP, {"session_id": "s-swarm-123"}, env_extra=env, tmpdir=tmp_path) is None
    assert not kill_log.exists()


def test_cleanup_sweeps_idle_swarm(tmp_path):
    env, kill_log = _swarm_fixture(tmp_path)
    env["FAKE_PS_OUTPUT"] = "claude --agent-id worker@session-deadbeef --agent-name worker"
    env["FAKE_WINDOW_ACTIVITY"] = "1000"  # ancient — way past the 48h window
    assert run_hook(CLEANUP, {"session_id": "s-swarm-123"}, env_extra=env, tmpdir=tmp_path) is None
    assert kill_log.is_file()
    assert "claude-swarm-111" in kill_log.read_text()


def test_cleanup_reaps_by_ancestor_pid_socket(tmp_path):
    # Current Claude Code tags teammates with a per-team id, not the parent
    # session id — the reaper must still find OUR server via its socket
    # name, claude-swarm-<main pid>, using the hook's ancestor chain.
    import os
    import time

    env, kill_log = _swarm_fixture(tmp_path)
    sock_dir = tmp_path / "tmuxroot" / f"tmux-{os.getuid()}"
    own = sock_dir / f"claude-swarm-{os.getpid()}"  # test process = hook's parent
    own.write_text("", encoding="utf-8")
    env["FAKE_PPID"] = str(os.getpid())
    env["FAKE_PS_OUTPUT"] = "claude --agent-id worker@session-otherteam --agent-name w"
    env["FAKE_WINDOW_ACTIVITY"] = str(int(time.time()))
    assert run_hook(CLEANUP, {"session_id": "s-swarm-123"}, env_extra=env, tmpdir=tmp_path) is None
    text = kill_log.read_text(encoding="utf-8") if kill_log.exists() else ""
    assert f"claude-swarm-{os.getpid()}" in text   # ours: killed via pid match
    assert "claude-swarm-111" not in text          # foreign tag + fresh: untouched


def test_swarm_idle_sweep_disabled_by_zero(tmp_path):
    # MAX_IDLE_H=0 turns off the idle kill even for ancient servers;
    # own-session reaping is a separate switch and stays available.
    import time

    env, kill_log = _swarm_fixture(tmp_path)
    env["FABLE_ORCH_SWARM_MAX_IDLE_H"] = "0"
    env["FAKE_PS_OUTPUT"] = "claude --agent-id worker@session-deadbeef --agent-name w"
    env["FAKE_WINDOW_ACTIVITY"] = "1000"  # ancient, but the sweep is off
    assert run_hook(CLEANUP, {"session_id": "s-swarm-123"}, env_extra=env, tmpdir=tmp_path) is None
    assert not kill_log.exists()


def test_inject_started_falls_back_to_mtime_for_legacy_cache(tmp_path):
    # A cache written by an older plugin version has no `started`. On the
    # next re-injection (compact/resume) the injector must anchor to the
    # file's mtime — never to "now", which would disown the session's
    # pre-compaction ledgers.
    import os
    import time

    cache = tmp_path / "fable-orch-model-s-legacy.json"
    cache.write_text(json.dumps({"profile": "fable"}), encoding="utf-8")
    old = time.time() - 7200
    os.utime(cache, (old, old))
    run_hook(INJECT, {"model": "claude-fable-5", "session_id": "s-legacy"},
             env_extra={"CLAUDE_PLUGIN_ROOT": str(REPO)}, tmpdir=tmp_path)
    started = json.loads(cache.read_text(encoding="utf-8"))["started"]
    assert abs(started - old) < 5


def test_swarm_cleanup_optout(tmp_path):
    env, kill_log = _swarm_fixture(tmp_path)
    env["FABLE_ORCH_SWARM_CLEANUP"] = "0"
    env["FAKE_PS_OUTPUT"] = "claude --agent-id worker@session-s-swarm- --agent-name worker"
    env["FAKE_WINDOW_ACTIVITY"] = "1000"
    assert run_hook(CLEANUP, {"session_id": "s-swarm-123"}, env_extra=env, tmpdir=tmp_path) is None
    assert not kill_log.exists()


def test_cleanup_unlinks_dead_socket(tmp_path):
    env, kill_log = _swarm_fixture(tmp_path)
    env["FAKE_TMUX_DEAD"] = "1"
    sock = tmp_path / "tmuxroot" / f"tmux-{__import__('os').getuid()}" / "claude-swarm-111"
    assert run_hook(CLEANUP, {"session_id": "s-swarm-123"}, env_extra=env, tmpdir=tmp_path) is None
    assert not sock.exists()
    assert not kill_log.exists()


def test_cleanup_without_session_id_is_noop(tmp_path):
    assert run_hook(CLEANUP, {}, tmpdir=tmp_path) is None


def test_cleanup_malformed_input_is_noop():
    assert run_hook(CLEANUP, raw="not json") is None


def test_non_object_stdin_never_crashes(tmp_path):
    # Valid JSON that isn't an object: both hooks stay on the contract.
    assert run_hook(CLEANUP, raw="[1, 2]", tmpdir=tmp_path) is None
    result = run_hook(INJECT, raw="[1, 2]",
                      env_extra={"CLAUDE_PLUGIN_ROOT": str(REPO)},
                      tmpdir=tmp_path)
    assert "(FABLE profile)" in context_of(result)  # still injects


def test_nested_claude_does_not_kill_outer_swarm(tmp_path):
    # A nested `claude -p` (fusion helpers, scripts) ends: its hook's
    # ancestor chain CONTAINS the outer session's claude. Matching every
    # ancestor used to kill the outer session's LIVE team — only the
    # NEAREST claude ancestor (pid 900, the inner session) may match.
    import time

    env, kill_log = _swarm_fixture(tmp_path)
    sock_dir = tmp_path / "tmuxroot" / f"tmux-{__import__('os').getuid()}"
    (sock_dir / "claude-swarm-900").write_text("", encoding="utf-8")  # inner's team
    (sock_dir / "claude-swarm-500").write_text("", encoding="utf-8")  # OUTER's team
    env["FAKE_ANCESTRY"] = json.dumps({
        "default": "900 python3 hook.py",   # hook's parent is the inner claude
        "900": "800 claude -p run this",    # inner claude — NEAREST match
        "800": "500 -bash",                 # shell between the two sessions
        "500": "1 claude",                  # outer interactive claude
    })
    env["FAKE_PS_OUTPUT"] = "claude --agent-id w@session-otherteam --agent-name w"
    env["FAKE_WINDOW_ACTIVITY"] = str(int(time.time()))
    assert run_hook(CLEANUP, {"session_id": "s-nested"}, env_extra=env, tmpdir=tmp_path) is None
    text = kill_log.read_text(encoding="utf-8") if kill_log.exists() else ""
    assert "claude-swarm-900" in text      # the inner session's own team dies
    assert "claude-swarm-500" not in text  # the outer session's team LIVES


def test_session_end_kills_own_panes_in_default_server(tmp_path):
    # Current layout: teammates are panes inside the USER'S default tmux
    # server, tagged with --parent-session-id. SessionEnd must kill the
    # session's own PANES there — and must NEVER kill-server a non-swarm
    # socket.
    import time

    env, kill_log = _swarm_fixture(tmp_path)
    sock_dir = tmp_path / "tmuxroot" / f"tmux-{__import__('os').getuid()}"
    default_sock = sock_dir / "default"
    default_sock.write_text("", encoding="utf-8")
    env["FAKE_PS_OUTPUT"] = (
        "12345 claude --agent-id w@session-team1 --agent-name w "
        "--parent-session-id s-own-full-id"
    )
    env["FAKE_WINDOW_ACTIVITY"] = str(int(time.time()))
    assert run_hook(CLEANUP, {"session_id": "s-own-full-id"}, env_extra=env, tmpdir=tmp_path) is None
    lines = kill_log.read_text(encoding="utf-8").splitlines() if kill_log.exists() else []
    assert any("pane" in l and "default -t %1" in l for l in lines)
    assert not any(l.strip() == str(default_sock) for l in lines)  # no kill-server on default


def test_session_end_leaves_other_sessions_panes(tmp_path):
    import time

    env, kill_log = _swarm_fixture(tmp_path)
    sock_dir = tmp_path / "tmuxroot" / f"tmux-{__import__('os').getuid()}"
    (sock_dir / "default").write_text("", encoding="utf-8")
    env["FAKE_PS_OUTPUT"] = (
        "12345 claude --agent-id w@session-team1 --agent-name w "
        "--parent-session-id another-sessions-id"
    )
    env["FAKE_WINDOW_ACTIVITY"] = str(int(time.time()))
    assert run_hook(CLEANUP, {"session_id": "s-own-full-id"}, env_extra=env, tmpdir=tmp_path) is None
    log = kill_log.read_text(encoding="utf-8") if kill_log.exists() else ""
    assert "default" not in log  # the other session's pane lives


def test_session_id_prefix_collision_does_not_kill(tmp_path):
    # Session "s-own" must not claim a teammate of "s-own-full-id":
    # the --parent-session-id match is token-exact, not substring.
    import time

    env, kill_log = _swarm_fixture(tmp_path)
    sock_dir = tmp_path / "tmuxroot" / f"tmux-{__import__('os').getuid()}"
    (sock_dir / "default").write_text("", encoding="utf-8")
    env["FAKE_PS_OUTPUT"] = (
        "12345 claude --agent-id w@session-team1 --agent-name w "
        "--parent-session-id s-own-full-id"
    )
    env["FAKE_WINDOW_ACTIVITY"] = str(int(time.time()))
    assert run_hook(CLEANUP, {"session_id": "s-own"}, env_extra=env, tmpdir=tmp_path) is None
    log = kill_log.read_text(encoding="utf-8") if kill_log.exists() else ""
    assert "default" not in log


def test_protocol_mismatch_socket_survives(tmp_path):
    # tmux binary upgraded mid-flight: the server answers rc=1 with
    # "protocol version mismatch" but is ALIVE — unlinking its socket
    # would orphan it forever. Only truly dead sockets get removed.
    env, kill_log = _swarm_fixture(tmp_path)
    env["FAKE_TMUX_MISMATCH"] = "1"
    sock = tmp_path / "tmuxroot" / f"tmux-{__import__('os').getuid()}" / "claude-swarm-111"
    assert run_hook(CLEANUP, {"session_id": "s-mismatch"}, env_extra=env, tmpdir=tmp_path) is None
    assert sock.exists()
    assert not kill_log.exists()
