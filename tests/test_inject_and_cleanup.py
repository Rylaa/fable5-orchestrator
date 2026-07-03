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


def test_cleanup_removes_cache(tmp_path):
    cache = tmp_path / "fable-orch-model-s-clean.json"
    cache.write_text(json.dumps({"profile": "fable"}), encoding="utf-8")
    assert run_hook(CLEANUP, {"session_id": "s-clean"}, tmpdir=tmp_path) is None
    assert not cache.exists()


def test_cleanup_without_session_id_is_noop(tmp_path):
    assert run_hook(CLEANUP, {}, tmpdir=tmp_path) is None


def test_cleanup_malformed_input_is_noop():
    assert run_hook(CLEANUP, raw="not json") is None
