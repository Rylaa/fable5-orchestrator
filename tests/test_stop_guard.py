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
