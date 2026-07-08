import json

from conftest import REPO, run_hook

INJECT = "inject_instructions.py"
CLEANUP = "cleanup_session_cache.py"


def context_of(result):
    return result["hookSpecificOutput"]["additionalContext"]


def test_fable_model_injects_fable_profile(tmp_path):
    result = run_hook(
        INJECT,
        {"model": "claude-fable-5", "session_id": "s-fable"},
        env_extra={"CLAUDE_PLUGIN_ROOT": str(REPO)},
        tmpdir=tmp_path,
    )
    assert "(FABLE profile)" in context_of(result)
    cache = tmp_path / "fable-orch-model-s-fable.json"
    assert cache.is_file()
    assert json.loads(cache.read_text())["profile"] == "fable"


def test_1m_suffix_detected_as_fable(tmp_path):
    result = run_hook(
        INJECT,
        {"model": "claude-fable-5[1m]", "session_id": "s-1m"},
        env_extra={"CLAUDE_PLUGIN_ROOT": str(REPO)},
        tmpdir=tmp_path,
    )
    assert "(FABLE profile)" in context_of(result)


def test_missing_model_defaults_to_lean_profile(tmp_path):
    result = run_hook(
        INJECT,
        {"session_id": "s-nomodel"},
        env_extra={"CLAUDE_PLUGIN_ROOT": str(REPO)},
        tmpdir=tmp_path,
    )
    assert "(OPUS / lean profile)" in context_of(result)
    cache = tmp_path / "fable-orch-model-s-nomodel.json"
    assert json.loads(cache.read_text())["profile"] == "opus"


def test_env_override_forces_profile(tmp_path):
    result = run_hook(
        INJECT,
        {"model": "claude-sonnet-5", "session_id": "s-forced"},
        env_extra={"CLAUDE_PLUGIN_ROOT": str(REPO), "FABLE_ORCH_PROFILE": "fable"},
        tmpdir=tmp_path,
    )
    assert "(FABLE profile)" in context_of(result)


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
    stale = tmp_path / "fable-orch-model-dead-session.json"
    stale.write_text("{}", encoding="utf-8")
    old = time.time() - 72 * 3600
    os.utime(stale, (old, old))
    fresh = tmp_path / "fable-orch-model-alive.json"
    fresh.write_text("{}", encoding="utf-8")

    assert run_hook(CLEANUP, {"session_id": "s-clean"}, tmpdir=tmp_path) is None
    assert not cache.exists()
    assert not sidecar.exists()
    assert not stale.exists()  # older than the 48h sweep window
    assert fresh.exists()      # other live sessions' files stay


FAKE_TMUX = """#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
sock = args[1] if len(args) > 1 and args[0] == "-S" else ""
cmd = args[2] if len(args) > 2 else ""
if os.environ.get("FAKE_TMUX_DEAD") == "1":
    sys.stderr.write("no server running\\n")
    sys.exit(1)
if cmd == "list-panes":
    print(os.environ.get("FAKE_PANE_PIDS", "12345"))
elif cmd == "list-windows":
    print(os.environ.get("FAKE_WINDOW_ACTIVITY", "0"))
elif cmd == "kill-server":
    with open(os.environ["FAKE_KILL_LOG"], "a") as f:
        f.write(sock + "\\n")
sys.exit(0)
"""

FAKE_PS = """#!/usr/bin/env python3
import os, sys
log = os.environ.get("FAKE_PS_LOG")
if log:
    with open(log, "a") as f:
        f.write(" ".join(sys.argv[1:]) + "\\n")
if "ppid=" in sys.argv:
    print(os.environ.get("FAKE_PPID", "1"))   # ancestor walk
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
