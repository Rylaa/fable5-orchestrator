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


def test_cleanup_without_session_id_is_noop(tmp_path):
    assert run_hook(CLEANUP, {}, tmpdir=tmp_path) is None


def test_cleanup_malformed_input_is_noop():
    assert run_hook(CLEANUP, raw="not json") is None
