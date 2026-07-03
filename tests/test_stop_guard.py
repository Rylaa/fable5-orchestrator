from conftest import run_hook, write_ledger

SCRIPT = "ledger_guard_stop.py"


def stop_payload(cwd, **extra):
    payload = {"cwd": str(cwd), "session_id": "test-session"}
    payload.update(extra)
    return payload


def test_no_ledger_passes(repo_dir):
    assert run_hook(SCRIPT, stop_payload(repo_dir)) is None


def test_open_items_block(repo_dir):
    write_ledger(repo_dir, "- [ ] 1. first\n- [x] 2. done\n- [ ] 3. third\n")
    result = run_hook(SCRIPT, stop_payload(repo_dir))
    assert result is not None
    assert result["decision"] == "block"
    assert "2 open item(s)" in result["reason"]
    assert "- [ ] 1. first" in result["reason"]


def test_star_bullets_also_count(repo_dir):
    write_ledger(repo_dir, "* [ ] 1. star bullet\n")
    result = run_hook(SCRIPT, stop_payload(repo_dir))
    assert result is not None and result["decision"] == "block"


def test_all_closed_passes(repo_dir):
    write_ledger(repo_dir, "- [x] 1. done\n- [~] 2. deferred: user approved\n")
    assert run_hook(SCRIPT, stop_payload(repo_dir)) is None


def test_loop_guard_lets_second_stop_through(repo_dir):
    write_ledger(repo_dir, "- [ ] 1. still open\n")
    assert run_hook(SCRIPT, stop_payload(repo_dir, stop_hook_active=True)) is None


def test_ledger_in_parent_blocks(repo_dir):
    write_ledger(repo_dir, "- [ ] 1. open\n")
    sub = repo_dir / "pkg" / "inner"
    sub.mkdir(parents=True)
    result = run_hook(SCRIPT, stop_payload(sub))
    assert result is not None and result["decision"] == "block"


def test_upward_search_stops_at_repo_root(tmp_path):
    write_ledger(tmp_path, "- [ ] 1. outside\n")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    assert run_hook(SCRIPT, stop_payload(repo)) is None


def test_malformed_input_passes():
    assert run_hook(SCRIPT, raw="{{{") is None
