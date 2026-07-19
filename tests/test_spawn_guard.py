import json
import time

from conftest import run_hook, write_ledger

SCRIPT = "ledger_guard_spawn.py"
LONG = "x" * 2000       # above the default 1500 gate
VERY_LONG = "x" * 5000


def spawn_payload(repo, prompt=LONG, tool="Agent", **extra):
    tool_input = {"prompt": prompt}
    tool_input.update(extra.pop("tool_input", {}))
    payload = {
        "tool_name": tool,
        "tool_input": tool_input,
        "cwd": str(repo),
        "session_id": "test-session",
    }
    payload.update(extra)
    return payload


def is_deny(result):
    return (
        result is not None
        and result["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        and result["hookSpecificOutput"]["permissionDecision"] == "deny"
    )


def write_marker(tmp, started, session="test-session"):
    """The injector's session marker — arms the stale-ledger check."""
    marker = tmp / f"fable-orch-model-{session}.json"
    marker.write_text(json.dumps({"started": started}), encoding="utf-8")
    return marker


def test_short_prompt_passes(repo_dir):
    assert run_hook(SCRIPT, spawn_payload(repo_dir, prompt="find the config file")) is None


def test_long_prompt_without_ledger_denied(repo_dir):
    assert is_deny(run_hook(SCRIPT, spawn_payload(repo_dir)))


def test_ledger_present_passes(repo_dir):
    write_ledger(repo_dir)
    assert run_hook(SCRIPT, spawn_payload(repo_dir, prompt=VERY_LONG)) is None


def test_fork_exempt(repo_dir):
    payload = spawn_payload(repo_dir, prompt=VERY_LONG, tool_input={"subagent_type": "fork"})
    assert run_hook(SCRIPT, payload) is None


def test_workflow_script_gated(repo_dir):
    payload = spawn_payload(repo_dir, tool="Workflow")
    payload["tool_input"] = {"script": "y" * 5000}
    result = run_hook(SCRIPT, payload)
    assert is_deny(result)
    reason = result["hookSpecificOutput"]["permissionDecisionReason"]
    assert "orchestration script" in reason


def test_workflow_by_name_passes(repo_dir):
    payload = spawn_payload(repo_dir, tool="Workflow")
    payload["tool_input"] = {"name": "review-changes"}
    assert run_hook(SCRIPT, payload) is None


def test_threshold_env_raises_gate(repo_dir):
    assert run_hook(
        SCRIPT, spawn_payload(repo_dir),
        env_extra={"LEDGER_GUARD_THRESHOLD": "3000"},
    ) is None


def test_threshold_env_lowers_gate(repo_dir):
    assert is_deny(run_hook(
        SCRIPT, spawn_payload(repo_dir, prompt="z" * 200),
        env_extra={"LEDGER_GUARD_THRESHOLD": "100"},
    ))


def test_malformed_threshold_falls_back_to_default(repo_dir):
    # "abc" can't break the gate: the 1500 default applies and LONG is denied.
    assert is_deny(run_hook(
        SCRIPT, spawn_payload(repo_dir),
        env_extra={"LEDGER_GUARD_THRESHOLD": "abc"},
    ))


def test_ledger_found_in_parent_directory(repo_dir):
    write_ledger(repo_dir)
    sub = repo_dir / "src" / "app"
    sub.mkdir(parents=True)
    assert run_hook(SCRIPT, spawn_payload(sub, prompt=VERY_LONG)) is None


def test_upward_search_stops_at_repo_root(tmp_path):
    # Ledger lives ABOVE the repo root -> must not be visible inside it.
    write_ledger(tmp_path)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    assert is_deny(run_hook(SCRIPT, spawn_payload(repo)))


def test_upward_search_stops_at_worktree_boundary(tmp_path):
    # In a git worktree .git is a FILE, not a directory. The search must
    # stop there too — not escape into an unrelated project's ledger.
    write_ledger(tmp_path)
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
    assert is_deny(run_hook(SCRIPT, spawn_payload(worktree)))


def test_upward_search_stops_at_home(tmp_path):
    # A ledger above $HOME must not satisfy the gate for sessions below it.
    write_ledger(tmp_path)
    home = tmp_path / "home"
    wd = home / "project"
    wd.mkdir(parents=True)
    assert is_deny(run_hook(SCRIPT, spawn_payload(wd), env_extra={"HOME": str(home)}))


def test_malformed_input_never_blocks():
    assert run_hook(SCRIPT, raw="this is not json") is None


# --- TaskCreate gate: the solo path the spawn gates can't see ---

def task_payload(repo, session="task-guard-session"):
    payload = {
        "tool_name": "TaskCreate",
        "tool_input": {"subject": "Faz N", "description": "d", "activeForm": "a"},
        "cwd": str(repo),
    }
    if session is not None:
        payload["session_id"] = session
    return payload


def run_tasks(repo, tmp, n, session="task-guard-session", env_extra=None):
    """n TaskCreate calls sharing one sidecar tempdir; returns the outputs."""
    return [
        run_hook(SCRIPT, task_payload(repo, session), env_extra=env_extra, tmpdir=tmp)
        for _ in range(n)
    ]


def test_first_two_tasks_pass_third_denied_once(repo_dir, tmp_path):
    results = run_tasks(repo_dir, tmp_path, 4)
    assert results[0] is None and results[1] is None
    assert is_deny(results[2])
    reason = results[2]["hookSpecificOutput"]["permissionDecisionReason"]
    assert "task #3" in reason and "LEDGER.md" in reason
    assert results[3] is None  # one reminder per session, then quiet


def test_tasks_pass_freely_with_ledger(repo_dir, tmp_path):
    write_ledger(repo_dir)
    assert run_tasks(repo_dir, tmp_path, 5) == [None] * 5


def test_ledger_written_after_deny_unblocks(repo_dir, tmp_path):
    assert is_deny(run_tasks(repo_dir, tmp_path, 3)[2])
    write_ledger(repo_dir)
    assert run_tasks(repo_dir, tmp_path, 2) == [None, None]


def test_task_limit_env_lowers_gate(repo_dir, tmp_path):
    results = run_tasks(repo_dir, tmp_path, 1, env_extra={"LEDGER_GUARD_TASKS": "1"})
    assert is_deny(results[0])


def test_task_gate_disabled_by_zero(repo_dir, tmp_path):
    results = run_tasks(repo_dir, tmp_path, 6, env_extra={"LEDGER_GUARD_TASKS": "0"})
    assert results == [None] * 6


def test_malformed_task_limit_falls_back_to_default(repo_dir, tmp_path):
    results = run_tasks(repo_dir, tmp_path, 3, env_extra={"LEDGER_GUARD_TASKS": "many"})
    assert results[:2] == [None, None] and is_deny(results[2])


def test_task_counts_do_not_leak_across_sessions(repo_dir, tmp_path):
    assert run_tasks(repo_dir, tmp_path, 2, session="session-a") == [None, None]
    assert run_tasks(repo_dir, tmp_path, 2, session="session-b") == [None, None]


def test_task_without_session_id_passes(repo_dir, tmp_path):
    # No session_id -> nothing safe to scope the count to -> never block.
    assert run_tasks(repo_dir, tmp_path, 4, session=None) == [None] * 4


# --- hardening: corrupt state, hostile stdin, races ---

def _seed_sidecar(tmp, body, session="task-guard-session"):
    path = tmp / f"fable-orch-tasks-{session}.json"
    path.write_text(body, encoding="utf-8")
    return path


def test_wrong_typed_sidecar_count_recovers(repo_dir, tmp_path):
    # Valid JSON, wrong member type: must coerce to 0 and keep exit 0 —
    # this exact shape used to crash the hook on every TaskCreate.
    path = _seed_sidecar(tmp_path, '{"count": "x"}')
    assert run_tasks(repo_dir, tmp_path, 1) == [None]
    assert json.loads(path.read_text())["count"] == 1


def test_non_dict_sidecar_recovers(repo_dir, tmp_path):
    path = _seed_sidecar(tmp_path, "[1, 2]")
    assert run_tasks(repo_dir, tmp_path, 1) == [None]
    assert json.loads(path.read_text())["count"] == 1


def test_non_object_stdin_never_crashes():
    # Valid JSON that isn't an object must fail open, not traceback.
    assert run_hook(SCRIPT, raw="[1, 2]") is None
    assert run_hook(SCRIPT, raw="42") is None
    assert run_hook(SCRIPT, raw="null") is None


def test_type_hostile_payload_fails_open(repo_dir):
    # Wrong-typed fields anywhere in the payload: exit 0, never a crash.
    assert run_hook(SCRIPT, spawn_payload(repo_dir, prompt=12345)) is None
    payload = spawn_payload(repo_dir)
    payload["tool_input"] = "not a dict"
    assert run_hook(SCRIPT, payload) is None
    payload = spawn_payload(repo_dir)
    payload["cwd"] = 123  # run_hook asserts exit 0; output shape is free
    run_hook(SCRIPT, payload)


def test_parallel_task_creates_deny_exactly_once(repo_dir, tmp_path):
    # 8 concurrent hooks race on the sidecar; the flock serializes the
    # read-modify-write: no lost counts, exactly one deny, valid JSON.
    import os
    import subprocess
    import sys
    from conftest import SCRIPTS, STRIP_ENV

    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    env.update({"FABLE_ORCH_METRICS": "0", "FABLE_ORCH_SWARM_CLEANUP": "0",
                "TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)})
    payload = json.dumps(task_payload(repo_dir))
    procs = [
        subprocess.Popen([sys.executable, str(SCRIPTS / SCRIPT)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, env=env)
        for _ in range(8)
    ]
    outs = [p.communicate(payload, timeout=30) for p in procs]
    assert all(p.returncode == 0 for p in procs)
    denies = sum(1 for out, _ in outs if out.strip())
    assert denies == 1
    state = json.loads((tmp_path / "fable-orch-tasks-task-guard-session.json").read_text())
    assert state == {"count": 8, "denied": True}


# --- stale-ledger re-arm: last week's finished ledger must not disarm ---

def _stale_completed_ledger(repo, age=3600):
    import os
    ledger = write_ledger(repo, "- [x] 1. done\n- [x] V. verified\n")
    old = time.time() - age
    os.utime(ledger, (old, old))
    return ledger


def test_stale_completed_ledger_rearms_spawn_gate(repo_dir, tmp_path):
    write_marker(tmp_path, time.time())          # session started NOW
    _stale_completed_ledger(repo_dir)            # finished long before
    result = run_hook(SCRIPT, spawn_payload(repo_dir), tmpdir=tmp_path)
    assert is_deny(result)
    assert "previous session" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_stale_completed_ledger_rearms_task_gate(repo_dir, tmp_path):
    write_marker(tmp_path, time.time(), session="task-guard-session")
    _stale_completed_ledger(repo_dir)
    results = run_tasks(repo_dir, tmp_path, 3)
    assert results[:2] == [None, None] and is_deny(results[2])


def test_old_ledger_with_open_items_still_satisfies(repo_dir, tmp_path):
    import os
    write_marker(tmp_path, time.time())
    ledger = write_ledger(repo_dir, "- [ ] 1. still open\n")
    old = time.time() - 3600
    os.utime(ledger, (old, old))
    assert run_hook(SCRIPT, spawn_payload(repo_dir, prompt=VERY_LONG), tmpdir=tmp_path) is None


def test_fresh_completed_ledger_still_satisfies(repo_dir, tmp_path):
    # Closed THIS session (mtime after started): follow-up spawns pass.
    write_marker(tmp_path, time.time() - 3600)
    write_ledger(repo_dir, "- [x] 1. done\n")
    assert run_hook(SCRIPT, spawn_payload(repo_dir, prompt=VERY_LONG), tmpdir=tmp_path) is None


def test_completed_ledger_without_marker_satisfies(repo_dir, tmp_path):
    # Manual install (no injector marker): existence keeps winning.
    _stale_completed_ledger(repo_dir)
    assert run_hook(SCRIPT, spawn_payload(repo_dir, prompt=VERY_LONG), tmpdir=tmp_path) is None


def test_symlinked_cwd_finds_the_real_ledger(tmp_path):
    # cwd is a symlink into the project: the walk must climb the REAL
    # tree (realpath), or the project ledger is invisible -> false deny.
    import os
    real = tmp_path / "real"
    (real / ".git").mkdir(parents=True)
    (real / "sub").mkdir()
    write_ledger(real, "- [ ] 1. open\n")
    link = tmp_path / "link"
    os.symlink(real / "sub", link)
    assert run_hook(SCRIPT, spawn_payload(link, prompt=VERY_LONG)) is None


def test_negative_threshold_clamps_to_zero(repo_dir):
    # Negative used to gate even EMPTY prompts; clamped to 0 it must not.
    assert run_hook(
        SCRIPT, spawn_payload(repo_dir, prompt=""),
        env_extra={"LEDGER_GUARD_THRESHOLD": "-5"},
    ) is None


# --- metrics + stats coverage for the task gate ---

def test_task_metrics_and_stats_summary(repo_dir, tmp_path):
    import os
    import subprocess
    import sys
    from conftest import REPO

    home = tmp_path / "home"
    home.mkdir()
    env = {"FABLE_ORCH_METRICS": "1", "HOME": str(home)}
    for _ in range(4):
        run_hook(SCRIPT, task_payload(repo_dir), env_extra=env, tmpdir=tmp_path)
    log = home / ".claude" / "fable-orch" / "metrics.jsonl"
    events = [json.loads(l) for l in log.read_text().splitlines()]
    deny = [e for e in events if e["event"] == "tasks_deny"]
    supp = [e for e in events if e["event"] == "tasks_suppressed"]
    assert len(deny) == 1 and deny[0]["count"] == 3 and deny[0]["threshold"] == 3
    assert len(supp) == 1 and supp[0]["count"] == 4
    stats = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "stats.py"), str(log)],
        capture_output=True, text=True, timeout=30,
    )
    assert stats.returncode == 0, stats.stderr
    assert "solo multi-phase nudges: 1 denied" in stats.stdout
