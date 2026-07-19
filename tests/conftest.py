import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

# Env vars that would leak the host's configuration into the tests.
STRIP_ENV = [
    "LEDGER_GUARD_THRESHOLD",
    "LEDGER_GUARD_TASKS",
    "LEDGER_GUARD_STOP_MODE",
    "FABLE_ORCH_METRICS",
    "FABLE_ORCH_SWARM_CLEANUP",
    "FABLE_ORCH_SWARM_MAX_IDLE_H",
    "FABLE_ORCH_TEAMMATE_IDLE_H",
    "TMUX_TMPDIR",
    "CLAUDE_PLUGIN_ROOT",
]


def run_hook(script, payload=None, raw=None, env_extra=None, tmpdir=None):
    """Run a hook script as a subprocess, exactly as Claude Code would.

    Returns the parsed JSON it printed, or None for empty output.
    `tmpdir` redirects tempfile.gettempdir() inside the subprocess so
    session-cache reads/writes stay inside the test sandbox.
    """
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    env["FABLE_ORCH_METRICS"] = "0"        # keep tests from writing ~/.claude metrics
    env["FABLE_ORCH_SWARM_CLEANUP"] = "0"  # keep tests away from real tmux servers
    if tmpdir is not None:
        env["TMPDIR"] = str(tmpdir)
        env["TEMP"] = str(tmpdir)
        env["TMP"] = str(tmpdir)
    if env_extra:
        env.update(env_extra)
    # Insurance: a test that turns the swarm cleanup ON without pointing
    # tmux at a sandbox would sweep the developer's REAL tmux servers.
    assert env.get("FABLE_ORCH_SWARM_CLEANUP") != "1" or "TMUX_TMPDIR" in env, \
        "FABLE_ORCH_SWARM_CLEANUP=1 requires a sandboxed TMUX_TMPDIR"
    stdin = raw if raw is not None else json.dumps(payload or {})
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / script)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.strip()
    return json.loads(out) if out else None


@pytest.fixture
def repo_dir(tmp_path):
    """A fake repo root: the upward ledger search stops at .git."""
    (tmp_path / ".git").mkdir()
    return tmp_path


def write_ledger(root, body="- [ ] 1. item\n"):
    d = root / ".workflow"
    d.mkdir(parents=True, exist_ok=True)
    (d / "LEDGER.md").write_text(body, encoding="utf-8")
    return d / "LEDGER.md"
