# Fable Orchestrator

Model-aware multi-agent orchestration for Claude Code. It detects the model in the session chair and injects the orchestration profile that fits it: **token-frugal** when you're on **Fable**, **latency-lean** when you're on **Opus 4.8** (or any other model). Both profiles keep the same quality guarantees — a **Requirements Ledger** against silent detail loss and a verification pass — and both are backed by **guard hooks** that enforce the discipline mechanically.

## The problem

Delegating bulk work to subagents is the right move when your session model is an ultra-scarce top tier (Fable): it preserves the usage limit and keeps the session context small. But that same heavy discipline — mandatory ledger before any delegation, sequential research relays, a fresh re-read verification pass on every close, disk hand-offs between every stage — is tuned to trade **wall-clock latency** for **top-tier token frugality**.

Run it under **Opus 4.8**, with its large context window and top-tier judgment+implementation in the chair, and the trade inverts. Opus tokens aren't the scarce resource you're protecting; the **clock** is. The ceremony that saves Fable's limit just makes Opus *slow* — for a benefit it no longer needs. The fix isn't "less discipline," it's "the right discipline for who's in the chair."

And the deeper issue stays the same across both: workflow instructions in CLAUDE.md are *advice*, not *mechanism*. The two rules that matter most — write the ledger, finish it before closing — are exactly the ones that get skipped under pressure. Hooks make them mechanical.

## What this plugin does

Three layers:

1. **Model-aware orchestration instructions** — injected at every session start (no CLAUDE.md editing needed). The SessionStart hook reads the session model and injects one of two profiles:

   | Profile | Injected when | Optimizes for | Behavior |
   |---|---|---|---|
   | [`dynamic-workflow-fable.md`](instructions/dynamic-workflow-fable.md) | session model is **Fable** | usage limit | Delegate aggressively; bulk work to cheaper tiers; mandatory ledger + fresh-eyes verification; research runs as ONE `Workflow` pipeline so intermediate material never enters the chair's context. Accepts latency to keep the top-tier token share low. |
   | [`dynamic-workflow-opus.md`](instructions/dynamic-workflow-opus.md) | **any other** model (Opus 4.8, Sonnet…) | wall-clock latency | Do bounded/medium work **inline**; delegate only to buy **parallelism** or to keep bulk out of context; ledger and verification **proportional** to size and risk; parallel fan-out (`Workflow` `pipeline()`/`parallel()`) over sequential relays. |

   Both profiles route subagents by tier and treat the filesystem as shared memory. Tier names resolve to the latest model of each tier: `sonnet` → Sonnet 5 (mechanical work AND standard implementation — the old haiku tier is retired), `opus` → Opus 4.8 (judgment, fresh-eyes verification), `fable` → Fable 5 (available to the Opus profile as the quality-ceiling verifier for the highest-stakes work). Reasoning **effort** is set per spawn (agent frontmatter `effort:` / Workflow `agent()` `effort:`): `max` for judgment and verification, `high` for implementation, `low` for mechanical gathering. Both profiles also use **forks** (`subagent_type: "fork"`) for bounded context-heavy work: a fork inherits the full conversation (no spec-writing tax) and keeps its tool churn out of the chair's window.

2. **A Requirements Ledger** — every explicit requirement, implicit expectation, constraint, and edge case as a checkbox line. Files survive context compaction; conversation context does not. On Fable the ledger is written before *any* delegation. On Opus it's **proportional**: an inline checklist for small/medium work, a `./.workflow/LEDGER.md` file for large/multi-session/high-risk work. Either way, detail loss becomes *visible* instead of silent.

3. **Guard hooks** — they convert the two most-skipped rules from "should" into "must":

| Guard | Trigger | Effect |
|---|---|---|
| **Spawn guard** (`PreToolUse` on `Agent\|Task\|Workflow`) | A detailed delegation while no `.workflow/LEDGER.md` exists from the working directory up to the repo root | The spawn is denied; the model is told to write the ledger first, or do small tasks directly. Gated text: the spawn `prompt` for Agent/Task, the orchestration `script` for Workflow. **Forks are exempt** — they inherit the full conversation, so the ledger is already in front of them. The threshold is **model-aware**: strict on Fable (`1500`), relaxed on Opus (`4000`) so ordinary fan-out isn't taxed. Short prompts always pass. |
| **Close guard** (`Stop`) | The turn ends while the ledger (same upward search) has open `- [ ]` items | The stop is blocked once and the open items are listed. `- [x]` (done + verified) and `- [~] deferred: <reason>` count as closed. A `stop_hook_active` check prevents infinite loops. On Opus, small tasks have no file ledger, so this simply never fires for them. |

A fourth hook (`SessionEnd`) removes the session's temp-dir model cache so the files don't accumulate.

### What it looks like in practice

A detailed delegation without a ledger gets bounced back to the model (threshold shown reflects the active profile):

```
LEDGER GUARD: this looks like a detailed delegation (spawn prompt > 4000 chars)
but no .workflow/LEDGER.md exists from the working directory up to the repo
root. Per Dynamic Workflow Rule 1, first write the numbered Requirements
Ledger ...
```

A turn ending with unfinished ledger items gets held once:

```
LEDGER GUARD: /path/to/.workflow/LEDGER.md still has 8 open item(s):
- [ ] A1. ...
- [ ] A2. ...
If you are CLOSING a workflow: address each item and mark it '- [x]' ...
```

## Why the Opus profile is faster (at the same quality)

The Fable profile's latency sinks, and what the Opus profile does about each — without dropping a quality guarantee:

| Fable profile (kept on Fable) | Opus profile (latency-lean) |
|---|---|
| Mandatory ledger file before any delegation | **Proportional**: inline checklist for small/medium, file for large/high-risk |
| Research as one token-frugal `Workflow` pipeline (intermediates never touch the chair) | **Parallel** `pipeline()` too — plus the chair synthesizes inline instead of delegating synthesis |
| Mandatory fresh-eyes re-read on every close | **Risk-gated**: fresh-eyes pass (opus, or `fable` for the very highest stakes) for high-risk work; inline tests + self-review for ordinary changes |
| Disk hand-off (write all to scratch, then read all) between stages | **Conditional**: only genuinely bulky material goes to disk; everything else returns inline |
| Offload everything to "protect" the chair | **Inline-first**: the chair is Opus-class — it does judgment and bounded implementation directly; delegate only for parallelism or bulk |

The anti-detail-loss ledger and the verification pass both survive — they're just spent *proportionally* instead of flat on every task.

## Install

```
/plugin marketplace add Rylaa/fable5-orchestrator
/plugin install orchestrator@fable-orchestrator
```

Requires `python3` on PATH (present by default on macOS and most Linux distros). Model auto-detection works out of the box — no configuration needed.

### Manual install (without the plugin system)

Manual install can't auto-inject instructions at session start (the SessionStart injector is plugin-only), so you pick the profile that matches your daily driver.

1. Copy `scripts/ledger_guard_spawn.py`, `scripts/ledger_guard_stop.py`, and `scripts/cleanup_session_cache.py` to `~/.claude/hooks/`.
2. Merge this into `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Agent|Task|Workflow",
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
    ],
    "SessionEnd": [
      {
        "hooks": [
          { "type": "command", "command": "python3 ~/.claude/hooks/cleanup_session_cache.py", "timeout": 10 }
        ]
      }
    ]
  }
}
```

3. Append the profile that matches your daily model to `~/.claude/CLAUDE.md`: `instructions/dynamic-workflow-opus.md` (recommended for Opus 4.8) or `instructions/dynamic-workflow-fable.md` (for Fable).
4. Without the SessionStart injector there is no per-session cache, but the spawn guard still adapts: it reads the session model directly from the `PreToolUse` payload when Claude Code provides it, and only falls back to the relaxed Opus threshold (`4000`) when it doesn't. To pin the strict Fable gate regardless, set `LEDGER_GUARD_THRESHOLD=1500` (see Configuration).

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
| `FABLE_ORCH_PROFILE` | `auto` | Which profile to use: `auto` (detect from the session model), `fable` (force token-frugal), or `opus` (force latency-lean). Honored by both the injector and the spawn guard. |
| `LEDGER_GUARD_THRESHOLD` | _(unset)_ | Hard override for the spawn-guard threshold (chars), applied to **every** profile. Set it to pin a single value regardless of model. |
| `LEDGER_GUARD_THRESHOLD_FABLE` | `1500` | Spawn-guard threshold under the Fable profile (used when the hard override is unset). |
| `LEDGER_GUARD_THRESHOLD_OPUS` | `4000` | Spawn-guard threshold under the Opus profile (used when the hard override is unset). |

Set these in `~/.claude/settings.json` under `"env"`.

**How model detection works.** Claude Code passes the selected model to hooks as a top-level `model` string. The spawn guard resolves the profile in this order: `FABLE_ORCH_PROFILE` override → the `model` field in its own payload → the per-session cache written by the SessionStart injector → the lean Opus default. The injector resolves the same way from the SessionStart payload and caches `{model, profile}` for the fallback path. If the model is absent or unknown everywhere, the plugin defaults to the lean Opus profile — the heavy Fable discipline never runs silently on a non-Fable model. The SessionEnd hook deletes the cache file when the session ends.

## Tests

```
python3 -m pytest tests/ -q
```

The hooks are plain stdin/stdout JSON filters, so the tests exercise them end-to-end as subprocesses: threshold selection per model, the fork exemption, Workflow script gating, the upward ledger search stopping at the repo root, stop-guard blocking and its loop guard, profile injection, and cache cleanup.

## What this does NOT do (honest limitations)

- **Hooks check ledger existence and checkbox state — not ledger fidelity.** A shallow ledger passes both gates. Writing a faithful ledger remains a judgment task, which is exactly why the instructions assign it to your smartest model, not a subagent.
- **Marking `- [x]` without actually verifying is possible.** The instructions say "verify first"; no hook can tell the difference. Mechanizing this further (e.g. mandatory verification stamps) invites ritual compliance — we deliberately stopped here.
- **Detection keys on the model string.** Anything containing `fable` (case-insensitive) gets the Fable profile; everything else gets the lean profile. Force it with `FABLE_ORCH_PROFILE` if your setup needs otherwise.
- **The orchestration discipline itself is still prompt-level.** The hooks fence exactly two failure points — the two that, in practice, get skipped the most: delegating without a ledger, and closing without finishing.

## Why these two guards

Because they sit at the two ends of the failure pipeline. Details die *entering* the workflow (task→plan translation, fenced by the spawn guard) and *leaving* it (closing with silently unaddressed items, fenced by the close guard). Everything between those two points is judgment — and judgment belongs to the model, not to a regex.

## License

MIT
