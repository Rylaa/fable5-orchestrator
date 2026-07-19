import json
import os
import time

from conftest import run_hook, write_ledger

SCRIPT = "ledger_guard_stop.py"


def stop_payload(cwd, **extra):
    payload = {"cwd": str(cwd), "session_id": "test-session"}
    payload.update(extra)
    return payload


def blocks(result):
    return result is not None and result["decision"] == "block"


def test_no_ledger_passes(repo_dir, tmp_path):
    assert run_hook(SCRIPT, stop_payload(repo_dir), tmpdir=tmp_path) is None


def test_open_items_block(repo_dir, tmp_path):
    write_ledger(repo_dir, "- [ ] 1. first\n- [x] 2. done\n- [ ] 3. third\n")
    result = run_hook(SCRIPT, stop_payload(repo_dir), tmpdir=tmp_path)
    assert blocks(result)
    assert "2 open item(s)" in result["reason"]
    assert "- [ ] 1. first" in result["reason"]


def test_bare_open_checkbox_counts(repo_dir, tmp_path):
    # A placeholder "- [ ]" with no text after it is still an open item.
    write_ledger(repo_dir, "- [ ]\n")
    assert blocks(run_hook(SCRIPT, stop_payload(repo_dir), tmpdir=tmp_path))


def test_star_bullets_also_count(repo_dir, tmp_path):
    write_ledger(repo_dir, "* [ ] 1. star bullet\n")
    assert blocks(run_hook(SCRIPT, stop_payload(repo_dir), tmpdir=tmp_path))


def test_all_closed_passes(repo_dir, tmp_path):
    write_ledger(repo_dir, "- [x] 1. done\n- [~] 2. deferred: user approved\n")
    assert run_hook(SCRIPT, stop_payload(repo_dir), tmpdir=tmp_path) is None


def test_loop_guard_lets_second_stop_through(repo_dir, tmp_path):
    write_ledger(repo_dir, "- [ ] 1. still open\n")
    assert run_hook(
        SCRIPT, stop_payload(repo_dir, stop_hook_active=True), tmpdir=tmp_path
    ) is None


def test_second_stop_same_session_suppressed(repo_dir, tmp_path):
    write_ledger(repo_dir, "- [ ] 1. open\n")
    assert blocks(run_hook(SCRIPT, stop_payload(repo_dir), tmpdir=tmp_path))
    assert run_hook(SCRIPT, stop_payload(repo_dir), tmpdir=tmp_path) is None


def test_other_session_gets_its_own_reminder(repo_dir, tmp_path):
    write_ledger(repo_dir, "- [ ] 1. open\n")
    assert blocks(run_hook(SCRIPT, stop_payload(repo_dir), tmpdir=tmp_path))
    assert blocks(run_hook(
        SCRIPT, stop_payload(repo_dir, session_id="other-session"), tmpdir=tmp_path
    ))


def test_every_turn_mode_blocks_repeatedly(repo_dir, tmp_path):
    write_ledger(repo_dir, "- [ ] 1. open\n")
    env = {"LEDGER_GUARD_STOP_MODE": "every-turn"}
    assert blocks(run_hook(SCRIPT, stop_payload(repo_dir), env_extra=env, tmpdir=tmp_path))
    assert blocks(run_hook(SCRIPT, stop_payload(repo_dir), env_extra=env, tmpdir=tmp_path))


def test_stale_ledger_from_before_session_passes(repo_dir, tmp_path):
    # Ledger predates the session start (cache mtime) -> another session's
    # workflow; this session is not held on it.
    ledger = write_ledger(repo_dir, "- [ ] 1. open\n")
    cache = tmp_path / "fable-orch-model-test-session.json"
    cache.write_text(json.dumps({"profile": "fable"}), encoding="utf-8")
    old = time.time() - 3600
    os.utime(ledger, (old, old))
    assert run_hook(SCRIPT, stop_payload(repo_dir), tmpdir=tmp_path) is None


def test_ledger_touched_this_session_blocks(repo_dir, tmp_path):
    # Session started an hour ago; the ledger was written just now -> owned.
    ledger = write_ledger(repo_dir, "- [ ] 1. open\n")
    cache = tmp_path / "fable-orch-model-test-session.json"
    cache.write_text(json.dumps({"profile": "fable"}), encoding="utf-8")
    old = time.time() - 3600
    os.utime(cache, (old, old))
    assert blocks(run_hook(SCRIPT, stop_payload(repo_dir), tmpdir=tmp_path))


def test_ownership_survives_compact_reinjection(repo_dir, tmp_path):
    # The ledger was touched mid-session; then SessionStart re-fired on a
    # compact and REWROTE the cache (fresh file mtime). The immutable
    # `started` field must keep the ledger owned by this session.
    ledger = write_ledger(repo_dir, "- [ ] 1. open\n")
    mid = time.time() - 1800
    os.utime(ledger, (mid, mid))
    cache = tmp_path / "fable-orch-model-test-session.json"
    cache.write_text(
        json.dumps({"profile": "fable", "started": time.time() - 3600}),
        encoding="utf-8",
    )  # file mtime = now (post-compact rewrite); started = an hour ago
    assert blocks(run_hook(SCRIPT, stop_payload(repo_dir), tmpdir=tmp_path))


def test_ledger_in_parent_blocks(repo_dir, tmp_path):
    write_ledger(repo_dir, "- [ ] 1. open\n")
    sub = repo_dir / "pkg" / "inner"
    sub.mkdir(parents=True)
    assert blocks(run_hook(SCRIPT, stop_payload(sub), tmpdir=tmp_path))


def test_upward_search_stops_at_repo_root(tmp_path):
    write_ledger(tmp_path, "- [ ] 1. outside\n")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    assert run_hook(SCRIPT, stop_payload(repo), tmpdir=tmp_path) is None


def test_upward_search_stops_at_worktree_boundary(tmp_path):
    # .git as a FILE (worktree/submodule) is a boundary too: the open
    # ledger above it must not block a stop inside the worktree.
    write_ledger(tmp_path, "- [ ] 1. outside\n")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
    assert run_hook(SCRIPT, stop_payload(worktree), tmpdir=tmp_path) is None


def test_upward_search_stops_at_home(tmp_path):
    # A ledger ABOVE $HOME belongs to nobody — never holds sessions below.
    write_ledger(tmp_path, "- [ ] 1. above home\n")
    home = tmp_path / "home"
    sub = home / "notes"
    sub.mkdir(parents=True)
    assert run_hook(
        SCRIPT, stop_payload(sub), env_extra={"HOME": str(home)}, tmpdir=tmp_path
    ) is None


def test_ledger_at_home_still_found(tmp_path):
    home = tmp_path / "home"
    (home / "docs").mkdir(parents=True)
    write_ledger(home, "- [ ] 1. home ledger\n")
    assert blocks(run_hook(
        SCRIPT, stop_payload(home / "docs"),
        env_extra={"HOME": str(home)}, tmpdir=tmp_path,
    ))


def test_malformed_input_passes():
    assert run_hook(SCRIPT, raw="{{{") is None


# --- teammate pane reaping (piggybacked on the Stop hook) -------------------

def _pane_env(tmp_path):
    from test_inject_and_cleanup import _swarm_fixture

    env, kill_log = _swarm_fixture(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    env["HOME"] = str(home)
    return env, kill_log, home


PANE_KEY = "claude-swarm-111:%1:12345"  # socket : pane id : pid


def _seed_pane_state(home, cpu=5.0, since_ago=7200, stale_marker=True, key=PANE_KEY):
    d = home / ".claude" / "fable-orch"
    d.mkdir(parents=True, exist_ok=True)
    state = d / "swarm-state.json"
    state.write_text(json.dumps(
        {"panes": {key: {"cpu": cpu, "since": time.time() - since_ago}}}),
        encoding="utf-8")
    if stale_marker:
        old = time.time() - 3600  # let the 30-min rate limit allow a sweep
        os.utime(state, (old, old))
    return state


def test_idle_teammate_pane_reaped(repo_dir, tmp_path):
    # CPU sample matches the previous one (0:05.00) and the baseline is 2h
    # old -> the pane is a finished teammate and gets killed.
    env, kill_log, home = _pane_env(tmp_path)
    _seed_pane_state(home)
    assert run_hook(SCRIPT, stop_payload(repo_dir), env_extra=env, tmpdir=tmp_path) is None
    assert kill_log.is_file()
    log = kill_log.read_text(encoding="utf-8")
    assert "pane" in log
    assert "-t %1" in log  # kill must target the PANE id, not the pid


def test_active_teammate_pane_survives(repo_dir, tmp_path):
    # CPU moved since the last sample -> active worker; re-baseline, no kill.
    env, kill_log, home = _pane_env(tmp_path)
    state = _seed_pane_state(home, cpu=4.0)
    assert run_hook(SCRIPT, stop_payload(repo_dir), env_extra=env, tmpdir=tmp_path) is None
    assert not kill_log.exists()
    rebaselined = json.loads(state.read_text(encoding="utf-8"))["panes"][PANE_KEY]
    assert rebaselined["cpu"] == 5.0  # fresh sample, idle clock restarted


def test_first_sighting_never_reaped(repo_dir, tmp_path):
    # No prior state: the sweep only takes a baseline, never kills.
    env, kill_log, home = _pane_env(tmp_path)
    assert run_hook(SCRIPT, stop_payload(repo_dir), env_extra=env, tmpdir=tmp_path) is None
    assert not kill_log.exists()
    state = home / ".claude" / "fable-orch" / "swarm-state.json"
    assert json.loads(state.read_text(encoding="utf-8"))["panes"][PANE_KEY]["cpu"] == 5.0


def test_pane_sweep_rate_limited(repo_dir, tmp_path):
    # State file written moments ago -> the sweep is skipped entirely.
    env, kill_log, home = _pane_env(tmp_path)
    _seed_pane_state(home, stale_marker=False)
    assert run_hook(SCRIPT, stop_payload(repo_dir), env_extra=env, tmpdir=tmp_path) is None
    assert not kill_log.exists()


def test_legacy_pid_keyed_state_never_kills(repo_dir, tmp_path):
    # Pre-0.10 state was keyed by bare pid — with pid reuse that hands a
    # NEW pane a stale idle baseline. Old keys must not match; the pane
    # is a first sighting and survives.
    env, kill_log, home = _pane_env(tmp_path)
    _seed_pane_state(home, key="12345")
    assert run_hook(SCRIPT, stop_payload(repo_dir), env_extra=env, tmpdir=tmp_path) is None
    assert not kill_log.exists()


def test_wrapper_shell_pane_never_reaped(repo_dir, tmp_path):
    # Pane root is `sh -c '... claude --agent-id ...'`: the shell's CPU
    # clock is frozen while the child works — judging idleness by it
    # would kill a LIVE worker. Non-claude roots are never touched.
    env, kill_log, home = _pane_env(tmp_path)
    env["FAKE_PS_PANE"] = "12345 0:00.01 sh -c cd /repo && claude --agent-id w@session-t"
    _seed_pane_state(home, cpu=0.01)
    assert run_hook(SCRIPT, stop_payload(repo_dir), env_extra=env, tmpdir=tmp_path) is None
    assert not kill_log.exists()


def test_real_ps_cputime_parses():
    # The fakes never exercise the REAL ps output shape; parse our own
    # process's cputime with the actual binary on this OS (macOS + Linux).
    import importlib.util
    import subprocess
    import sys
    from conftest import SCRIPTS

    spec = importlib.util.spec_from_file_location(
        "ledger_guard_stop_real", SCRIPTS / "ledger_guard_stop.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = subprocess.run(["ps", "-o", "cputime=", "-p", str(os.getpid())],
                         capture_output=True, text=True, timeout=10).stdout.strip()
    assert out, "real ps returned nothing"
    assert mod._cpu_seconds(out) is not None


# --- hardening: corrupt sidecars, hostile stdin, ledger dialects ---

def test_wrong_typed_stop_sidecar_recovers(repo_dir, tmp_path):
    # {"blocked": [1]} used to TypeError past the decision print — the
    # guard must still block, exit 0, and rewrite a proper dict.
    write_ledger(repo_dir, "- [ ] 1. open\n")
    sidecar = tmp_path / "fable-orch-stop-test-session.json"
    sidecar.write_text(json.dumps({"blocked": [1]}), encoding="utf-8")
    assert blocks(run_hook(SCRIPT, stop_payload(repo_dir), tmpdir=tmp_path))
    assert isinstance(json.loads(sidecar.read_text())["blocked"], dict)
    assert run_hook(SCRIPT, stop_payload(repo_dir), tmpdir=tmp_path) is None


def test_non_object_stdin_never_crashes():
    assert run_hook(SCRIPT, raw="[1, 2]") is None
    assert run_hook(SCRIPT, raw="42") is None


def test_fenced_checklist_is_not_an_open_item(repo_dir, tmp_path):
    write_ledger(repo_dir,
                 "- [x] 1. done\n```\n- [ ] example inside a code fence\n```\n")
    assert run_hook(SCRIPT, stop_payload(repo_dir), tmpdir=tmp_path) is None


def test_open_item_outside_fence_still_blocks(repo_dir, tmp_path):
    write_ledger(repo_dir,
                 "```\n- [ ] fenced example\n```\n- [ ] 1. real open item\n")
    result = run_hook(SCRIPT, stop_payload(repo_dir), tmpdir=tmp_path)
    assert blocks(result)
    assert "1 open item(s)" in result["reason"]


def test_crlf_ledger_blocks(repo_dir, tmp_path):
    (repo_dir / ".workflow").mkdir(exist_ok=True)
    (repo_dir / ".workflow" / "LEDGER.md").write_bytes(b"- [ ] 1. open\r\n")
    assert blocks(run_hook(SCRIPT, stop_payload(repo_dir), tmpdir=tmp_path))


def test_indented_checkbox_blocks(repo_dir, tmp_path):
    write_ledger(repo_dir, "  - [ ] 1. nested open item\n")
    assert blocks(run_hook(SCRIPT, stop_payload(repo_dir), tmpdir=tmp_path))


def test_plus_bullet_is_out_of_dialect(repo_dir, tmp_path):
    # Documented scope: '- [ ]' and '* [ ]' count; '+ [ ]' does not.
    write_ledger(repo_dir, "+ [ ] 1. plus bullet\n")
    assert run_hook(SCRIPT, stop_payload(repo_dir), tmpdir=tmp_path) is None


def test_future_started_still_owns(repo_dir, tmp_path):
    # A marker `started` in the future (clock jump) must clamp to now —
    # not silently disown every ledger for the whole session.
    write_ledger(repo_dir, "- [ ] 1. open\n")
    cache = tmp_path / "fable-orch-model-test-session.json"
    cache.write_text(json.dumps({"started": time.time() + 3600}), encoding="utf-8")
    assert blocks(run_hook(SCRIPT, stop_payload(repo_dir), tmpdir=tmp_path))


def test_stop_touches_the_session_marker(repo_dir, tmp_path):
    # Every Stop refreshes the marker's mtime so the 96h temp sweep can
    # never eat a LIVE session's files; `started` content is untouched.
    cache = tmp_path / "fable-orch-model-test-session.json"
    cache.write_text(json.dumps({"started": 123.0}), encoding="utf-8")
    old = time.time() - 7200
    os.utime(cache, (old, old))
    assert run_hook(SCRIPT, stop_payload(repo_dir), tmpdir=tmp_path) is None
    assert os.path.getmtime(cache) > time.time() - 300
    assert json.loads(cache.read_text())["started"] == 123.0
