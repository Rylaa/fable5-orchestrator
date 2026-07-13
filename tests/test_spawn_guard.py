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
        and result["hookSpecificOutput"]["permissionDecision"] == "deny"
    )


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
