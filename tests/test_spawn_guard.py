import json

from conftest import run_hook, write_ledger

SCRIPT = "ledger_guard_spawn.py"
LONG = "x" * 2000       # above the fable gate (1500), below the opus gate (4000)
VERY_LONG = "x" * 5000  # above both gates


def spawn_payload(repo, prompt=LONG, model="claude-fable-5", tool="Agent", **extra):
    tool_input = {"prompt": prompt}
    tool_input.update(extra.pop("tool_input", {}))
    payload = {
        "tool_name": tool,
        "tool_input": tool_input,
        "cwd": str(repo),
        "session_id": "test-session",
    }
    if model is not None:
        payload["model"] = model
    payload.update(extra)
    return payload


def is_deny(result):
    return (
        result is not None
        and result["hookSpecificOutput"]["permissionDecision"] == "deny"
    )


def test_short_prompt_passes(repo_dir):
    assert run_hook(SCRIPT, spawn_payload(repo_dir, prompt="find the config file")) is None


def test_long_prompt_without_ledger_denied_on_fable(repo_dir):
    assert is_deny(run_hook(SCRIPT, spawn_payload(repo_dir)))


def test_same_prompt_passes_on_opus_threshold(repo_dir):
    assert run_hook(SCRIPT, spawn_payload(repo_dir, model="claude-opus-4-8")) is None


def test_very_long_prompt_denied_on_opus(repo_dir):
    assert is_deny(run_hook(SCRIPT, spawn_payload(repo_dir, prompt=VERY_LONG, model="claude-opus-4-8")))


def test_1m_suffix_still_detected_as_fable(repo_dir):
    assert is_deny(run_hook(SCRIPT, spawn_payload(repo_dir, model="claude-fable-5[1m]")))


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


def test_env_profile_override_beats_payload_model(repo_dir):
    result = run_hook(
        SCRIPT,
        spawn_payload(repo_dir, model="claude-opus-4-8"),
        env_extra={"FABLE_ORCH_PROFILE": "fable"},
    )
    assert is_deny(result)


def test_hard_threshold_override(repo_dir):
    result = run_hook(
        SCRIPT,
        spawn_payload(repo_dir, prompt="z" * 200, model="claude-opus-4-8"),
        env_extra={"LEDGER_GUARD_THRESHOLD": "100"},
    )
    assert is_deny(result)


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


def test_cache_fallback_when_payload_has_no_model(repo_dir, tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "fable-orch-model-test-session.json").write_text(
        json.dumps({"model": "claude-fable-5", "profile": "fable"}),
        encoding="utf-8",
    )
    payload = spawn_payload(repo_dir, model=None)
    assert is_deny(run_hook(SCRIPT, payload, tmpdir=cache_dir))


def test_no_model_no_cache_defaults_to_lean(repo_dir, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    payload = spawn_payload(repo_dir, model=None)
    assert run_hook(SCRIPT, payload, tmpdir=empty) is None


def test_malformed_input_never_blocks():
    assert run_hook(SCRIPT, raw="this is not json") is None
