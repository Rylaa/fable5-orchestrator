# Fable Orchestrator

Keep **Claude Fable 5** (or Opus) as your daily driver without draining your usage limit. Token-frugal multi-agent orchestration for Claude Code — model-tier routing, a **Requirements Ledger** against silent detail loss, and **guard hooks** that enforce the discipline mechanically.

## The problem

Running a top-tier model (Opus, Fable) as your daily driver burns through context and usage limits fast — and most of those tokens go to bulk work (reading files, fetching pages, grinding logs) that a cheaper tier handles just as well. The obvious fix is delegating to subagents.

But naive delegation has a failure mode worse than the cost it saves: **silent detail loss**. Requirements vanish at the task→plan translation step, subagents return confident summaries, and nobody notices what got dropped until it ships broken.

And the deeper issue: workflow instructions in CLAUDE.md are *advice*, not *mechanism*. The model follows them probabilistically — and the two rules that matter most are exactly the ones that get skipped under pressure.

## What this plugin does

Three layers:

1. **Orchestration instructions** — injected at every session start (no CLAUDE.md editing needed). Your session model plans, delegates by model tier (`haiku` → mechanical, `sonnet` → implementation, `opus` → judgment), verifies with fresh eyes, and arbitrates. It stops spending its own context on bulk work. See [`instructions/dynamic-workflow.md`](instructions/dynamic-workflow.md).

2. **A Requirements Ledger** — before any delegation, the orchestrator writes every explicit requirement, implicit expectation, constraint, and edge case as a checkbox line to `./.workflow/LEDGER.md`. Files survive context compaction; conversation context does not. Detail loss becomes *visible* instead of silent.

3. **Two guard hooks** — they convert the two most-skipped rules from "should" into "must":

| Guard | Trigger | Effect |
|---|---|---|
| **Spawn guard** (`PreToolUse` on `Agent\|Task`) | A detailed delegation (spawn prompt > 1500 chars) while `./.workflow/LEDGER.md` doesn't exist | The spawn is denied, with instructions fed back to the model: write the ledger first, or do small tasks directly. Short prompts (quick search agents) always pass. |
| **Close guard** (`Stop`) | The turn ends while the ledger has open `- [ ]` items | The stop is blocked once and the open items are listed. `- [x]` (done + verified) and `- [~] deferred: <reason>` count as closed. A `stop_hook_active` check prevents infinite loops. |

### What it looks like in practice

A detailed delegation without a ledger gets bounced back to the model:

```
LEDGER GUARD: this looks like a detailed delegation (spawn prompt > 1500 chars)
but ./.workflow/LEDGER.md does not exist. Per Dynamic Workflow Rule 1, first
write the numbered Requirements Ledger ...
```

A turn ending with unfinished ledger items gets held once:

```
LEDGER GUARD: ./.workflow/LEDGER.md still has 8 open item(s):
- [ ] A1. ...
- [ ] A2. ...
If you are CLOSING a workflow: address each item and mark it '- [x]' ...
```

## Using a top-tier model (Fable 5, Opus) without draining your limit

This setup was born from a specific pain: running **Claude Fable 5** as the session model and watching the usage limit evaporate — not on hard decisions, but on bulk work the top tier never needed to do itself.

With the plugin installed, the division of labor becomes:

- **Your session model (Fable/Opus) spends tokens only on judgment** — writing the Requirements Ledger, speccing phases, arbitrating conflicts, judging subagent returns, final synthesis.
- **Everything bulky runs on cheaper tiers** — `haiku` fetches and scans, `sonnet` implements from specs, `opus` handles delegated judgment. Those tokens drain your limit far more slowly than top-tier tokens do.
- **Your session context stays small**, so long sessions avoid the compaction cliff where details silently vanish — a quality win on top of the limit win.

Two honest caveats:

- **Total tokens across all models go up.** Delegation duplicates some reading (subagents re-read material from disk). What drops — dramatically — is the *top-tier share* of consumption, which is what your limit actually cares about.
- This is not a "make everything cheap" tool; it's a "spend the scarce resource only where being smartest matters" tool. For small bounded tasks the instructions tell the orchestrator to skip delegation entirely (Rule 0) — orchestration overhead would exceed the work itself.

## Install

```
/plugin marketplace add Rylaa/fable-orchestrator
/plugin install orchestrator@fable-orchestrator
```

Requires `python3` on PATH (present by default on macOS and most Linux distros).

### Manual install (without the plugin system)

1. Copy `scripts/ledger_guard_spawn.py` and `scripts/ledger_guard_stop.py` to `~/.claude/hooks/`.
2. Merge this into `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Agent|Task",
        "hooks": [
          { "type": "command", "command": "python3 ~/.claude/hooks/ledger_guard_spawn.py", "timeout": 10 }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "python3 ~/.claude/hooks/ledger_guard_stop.py", "timeout": 10 }
        ]
      }
    ]
  }
}
```

3. Append `instructions/dynamic-workflow.md` to your `~/.claude/CLAUDE.md`.

> Don't run the plugin AND the manual install side by side — you'd get every guard twice.

## The ledger format

```markdown
- [ ] 1. Every explicit requirement, one line each
- [ ] 2. Implicit expectations and constraints too
- [x] 3. Marked done only after verification confirms it
- [~] 4. deferred: user approved postponing this
```

A paused project keeps its open ledger, so the close guard adds one acknowledgment line per turn. Archive paused ledgers (rename to `LEDGER-<topic>-archive.md`) to silence it; restore the name when you resume.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `LEDGER_GUARD_THRESHOLD` | `1500` | Spawn-prompt length (chars) above which a delegation requires a ledger. Raise it if legitimate quick agents get blocked; lower it if big delegations slip under. |

Set it in `~/.claude/settings.json` under `"env"`.

## What this does NOT do (honest limitations)

- **Hooks check ledger existence and checkbox state — not ledger fidelity.** A shallow ledger passes both gates. Writing a faithful ledger remains a judgment task, which is exactly why the instructions assign it to your smartest model, not a subagent.
- **Marking `- [x]` without actually verifying is possible.** The instructions say "verify first"; no hook can tell the difference. Mechanizing this further (e.g. mandatory verification stamps) invites ritual compliance — we deliberately stopped here.
- **The orchestration discipline itself is still prompt-level.** The hooks fence exactly two failure points — the two that, in practice, get skipped the most: delegating without a ledger, and closing without finishing.

## Why these two hooks

Because they sit at the two ends of the failure pipeline. Details die *entering* the workflow (task→plan translation, fenced by the spawn guard) and *leaving* it (closing with silently unaddressed items, fenced by the close guard). Everything between those two points is judgment — and judgment belongs to the model, not to a regex.

## License

MIT
