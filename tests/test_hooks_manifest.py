"""The registration contract: every other test drives the scripts
directly, so a broken hooks.json (dropped matcher entry, mistyped
script path) would ship green. This file pins the manifest itself."""
import json
import re

from conftest import REPO


def _manifest():
    with open(REPO / "hooks" / "hooks.json", encoding="utf-8") as f:
        return json.load(f)["hooks"]


def test_all_four_events_registered():
    assert set(_manifest()) == {"SessionStart", "PreToolUse", "Stop", "SessionEnd"}


def test_every_hook_command_script_exists():
    for entries in _manifest().values():
        for entry in entries:
            for hook in entry["hooks"]:
                cmd = hook["command"]
                assert cmd.startswith('python3 "${CLAUDE_PLUGIN_ROOT}/')
                rel = cmd.split("${CLAUDE_PLUGIN_ROOT}/", 1)[1].rstrip('"')
                assert (REPO / rel).is_file(), f"missing script: {rel}"
                assert isinstance(hook.get("timeout"), int)


def test_pretooluse_matcher_covers_the_gated_tools():
    matcher = _manifest()["PreToolUse"][0]["matcher"]
    pattern = re.compile(matcher)
    for tool in ("Agent", "Task", "Workflow", "TaskCreate"):
        assert pattern.search(tool), f"matcher misses {tool}"
    for tool in ("TaskUpdate", "TaskList", "AgentOutput", "WorkflowX"):
        assert not pattern.search(tool), f"matcher over-matches {tool}"
