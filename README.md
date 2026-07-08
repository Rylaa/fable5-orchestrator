# Fable Orchestrator

[![CI](https://github.com/Rylaa/fable5-orchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/Rylaa/fable5-orchestrator/actions/workflows/ci.yml)

**Run Claude Fable 5 all day — without watching the usage meter.**

Fable 5 is the best chair a Claude Code session can have: the deepest reasoning, the best arbitration, the longest horizon. It is also the most expensive seat in the house. Let it type every token itself and the session ends the way unmanaged Fable sessions always end: rate-limited, waiting out the reset window.

This plugin makes the obvious split mechanical, so you never have to make that trade. **Fable 5 keeps the chair and spends tokens only on what actually needs Fable-level intelligence** — planning, arbitration, final decisions. Everything else — implementation, research, briefs, review, bulk reading — is routed to **Sonnet 5**, which since the Claude 5 generation is good enough to carry all of it at a fraction of the limit weight. **Opus 4.8** stays on the roster for exactly two jobs: fresh-eyes verification and escalation.

The goal: a Fable 5 session that lasts your whole workday. No limit anxiety, no waiting for resets.

## The division of labor

```
                        ┌─────────────────────────────────┐
                        │         FABLE 5 — chair         │
                        │    plan · arbitrate · decide    │
                        └────────────────┬────────────────┘
                                         │
                specs & ledger down      │      briefs & verdicts up
                                         │
           ┌─────────────────────────────┼─────────────────────────────┐
           ▼                             ▼                             ▼
┌─────────────────────┐       ┌─────────────────────┐       ┌─────────────────────┐
│   SONNET 5 · low    │       │   SONNET 5 · high   │       │   SONNET 5 · max    │
│   mechanical bulk   │       │   implementation    │       │   routine judgment  │
│   grep·fetch·scan   │       │   code · tests      │       │   briefs · review   │
│   format · read     │       │   (xhigh critical)  │       │   filtering         │
└─────────────────────┘       └─────────────────────┘       └──────────┬──────────┘
                                                                       │ uncertain /
                                                                       │ high stakes
                                                            ┌──────────▼──────────┐
                                                            │    OPUS 4.8 · max   │
                                                            │     the valve:      │
                                                            │  fresh-eyes verify  │
                                                            │    + escalation     │
                                                            └─────────────────────┘
```

Fable thinks. Sonnet does. Opus checks. Your limit pays for the thinking only:

```
┌─────────────────────────────────────────┬─────────────────┬─────────────────────┐
│ Work                                    │ Runs on         │ Fable limit pays    │
├─────────────────────────────────────────┼─────────────────┼─────────────────────┤
│ Phase planning, arbitration, decisions  │ Fable 5 (chair) │ yes — and only this │
│ Implementation, tests, refactors        │ Sonnet 5 high   │ nothing             │
│ Source briefs, filtering, code review   │ Sonnet 5 max    │ nothing             │
│ Bulk gathering (fetch, grep, scan)      │ Sonnet 5 low    │ nothing             │
│ Fresh-eyes verification, escalations    │ Opus 4.8 max    │ nothing             │
└─────────────────────────────────────────┴─────────────────┴─────────────────────┘
```

## Why Fable 5 × Sonnet 5 is the right couple

This pairing is deliberate, not a budget compromise.

```
┌───────────┬───────────────────────────────────────────────┐
│ Model     │ Role in this plugin                           │
├───────────┼───────────────────────────────────────────────┤
│ Fable 5   │ The brain — plans, arbitrates, decides        │
│ Opus 4.8  │ The valve — fresh-eyes verify + escalation    │
│ Sonnet 5  │ The body — implementation + routine judgment  │
└───────────┴───────────────────────────────────────────────┘
```

Three facts make the split work:

- **Fable tokens are the heaviest draw on your usage limit.** Every token of bulk work you keep off the chair directly extends how long you can keep Fable in it. The chair's output should be plans, verdicts, and ledger updates — not code diffs and fetched pages.
- **Sonnet 5 closed the gap.** It reaches near-Opus quality on coding and agentic work, and it is the first Sonnet-tier model with the full effort ladder (`low` → `xhigh` → `max`). At `max` effort it carries routine judgment — source briefs, relevance filtering, standard review — that used to require an Opus call.
- **Volume is what drains limits.** So Sonnet carries the judgment volume, and Opus is reserved for where a *different* model genuinely earns its place: the fresh-eyes verification pass (a decorrelated reviewer catches the blind spots Sonnet shares with its own implementation) and the escalation valve (a `sonnet` worker that returns "uncertain" goes up to Opus — never back up to Fable, so your limit stays out of it).

## What the plugin does

Three layers, all mechanical — no CLAUDE.md editing, no manual routing.

### 1 · Model-aware orchestration profiles

A SessionStart hook detects the model in the chair and injects the profile that fits it:

```
┌──────────────────────────┬──────────────────────────────┬──────────────────────────────┐
│                          │ FABLE profile                │ OPUS / lean profile          │
├──────────────────────────┼──────────────────────────────┼──────────────────────────────┤
│ Injected when chair is   │ Fable 5                      │ any other model              │
│ Scarce resource          │ your usage limit             │ wall-clock latency           │
│ Bounded / medium work    │ delegated                    │ done inline by the chair     │
│ Requirements Ledger      │ file, before any delegation  │ proportional to size & risk  │
│ Verification             │ fresh-eyes on every close    │ risk-gated                   │
│ Disk hand-off            │ the default                  │ only for genuinely bulky     │
│ Spawn-guard threshold    │ 1500 chars                   │ 4000 chars                   │
└──────────────────────────┴──────────────────────────────┴──────────────────────────────┘
```

Both profiles route subagents by tier name (`sonnet`, `opus`, `fable` — always resolving to the latest model of each tier) and treat the filesystem as shared memory: gathering agents write raw material to `./.workflow/scratch/` and return paths; consumers read from disk; the chair receives briefs and verdicts, never bulk. Reasoning effort is set per spawn: `low` for mechanical work, `high` for implementation (`xhigh` when correctness is critical), `max` for judgment and verification. Bounded follow-up work that leans on the conversation goes to a **fork** (`subagent_type: "fork"`), which inherits the full context with no spec-writing tax.

### 2 · A Requirements Ledger

Before serious delegation, the chair writes every explicit requirement, implicit expectation, constraint, and edge case as one checkbox line in `./.workflow/LEDGER.md`. Files survive context compaction; conversation context does not. `- [x]` only when addressed *and verified*; `- [~] deferred: <reason>` only with user approval. Detail loss becomes visible instead of silent.

### 3 · Guard hooks — the two most-skipped rules, made mechanical

Workflow instructions are *advice*; hooks are *mechanism*. The two rules that get skipped under pressure — write the ledger, finish it before closing — are enforced:

**Spawn guard** (`PreToolUse` on `Agent|Task|Workflow`):

```
spawn (Agent / Task / Workflow)
  │
  ├─ text ≤ profile threshold ..................... PASS  (short spawns are never taxed)
  │
  └─ text > threshold
       │
       ├─ subagent_type == "fork" ................. PASS  (forks already see the ledger)
       │
       ├─ .workflow/LEDGER.md found ............... PASS  (cite its items per agent)
       │  (searched cwd → repo root)
       │
       └─ no ledger ............................... DENY  → "write the ledger first,
                                                     or do the small task yourself"
```

Gated text: the spawn `prompt` for Agent/Task, the orchestration `script` for Workflow (name/scriptPath resume calls carry no new plan text and pass). The threshold is model-aware: strict on Fable (1500), relaxed on the lean profile (4000) so ordinary fan-out isn't taxed.

**Close guard** (`Stop`):

```
turn ends
  │
  ├─ no ledger on the search path ................. pass
  ├─ every item "- [x]" or "- [~] deferred" ....... pass
  ├─ already blocked once this turn ............... pass  (loop guard)
  │
  └─ open "- [ ]" items remain .................... BLOCK once, listing the items:
       finish them, defer with user approval, or — if the ledger belongs to a
       paused task — acknowledge in one line and stop. Archive paused ledgers
       (rename to LEDGER-<topic>-archive.md) to silence the guard entirely.
```

A fourth hook (`SessionEnd`) removes the session's temp-dir model cache.

### What it looks like in practice

A detailed delegation without a ledger gets bounced back to the model:

```
LEDGER GUARD: this looks like a detailed delegation (spawn prompt > 1500 chars)
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

## Install

```
/plugin marketplace add Rylaa/fable5-orchestrator
/plugin install orchestrator@fable-orchestrator
```

Requires `python3` on PATH (present by default on macOS and most Linux distros). Model auto-detection works out of the box — no configuration needed.

### Manual install (without the plugin system)

Manual install can't auto-inject instructions at session start (the SessionStart injector is plugin-only), so you append the profile that matches your daily driver yourself.

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

3. Append the profile that matches your daily model to `~/.claude/CLAUDE.md`: `instructions/dynamic-workflow-fable.md` (for Fable) or `instructions/dynamic-workflow-opus.md` (for Opus/Sonnet chairs).
4. Without the SessionStart injector there is no per-session cache — and PreToolUse itself carries no model field — so the spawn guard will sit at the relaxed lean gate (4000). If Fable is your daily driver, pin the strict gate with `LEDGER_GUARD_THRESHOLD=1500` or force `FABLE_ORCH_PROFILE=fable` (see Configuration).

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

Set these in `~/.claude/settings.json` under `"env"`.

```
┌───────────────────────────────┬─────────┬───────────────────────────────────────────────┐
│ Env var                       │ Default │ Meaning                                       │
├───────────────────────────────┼─────────┼───────────────────────────────────────────────┤
│ FABLE_ORCH_PROFILE            │ auto    │ auto | fable | opus — force a profile;       │
│                               │         │ honored by the injector and the spawn guard  │
│ LEDGER_GUARD_THRESHOLD        │ (unset) │ hard threshold override, applied to every    │
│                               │         │ profile (chars)                              │
│ LEDGER_GUARD_THRESHOLD_FABLE  │ 1500    │ spawn-guard gate under the Fable profile     │
│ LEDGER_GUARD_THRESHOLD_OPUS   │ 4000    │ spawn-guard gate under the lean profile      │
└───────────────────────────────┴─────────┴───────────────────────────────────────────────┘
```

**How model detection works.** Per the Claude Code hooks reference, the session model is delivered **only in the SessionStart payload** — and it is optional even there; no other hook event receives a `model` field. So the SessionStart injector is the source of truth: it resolves the profile and caches `{model, profile}` to a per-session temp file. The spawn guard then resolves in this order: `FABLE_ORCH_PROFILE` override → a `model` field in its own payload (future-proofing; today PreToolUse carries none) → the per-session cache → the lean default. If the model is unknown everywhere, the plugin defaults to the lean profile — the heavy Fable discipline never runs silently on the wrong chair. The SessionEnd hook deletes the cache file when the session ends.

## Tests

```
python3 -m pytest tests/ -q
```

The hooks are plain stdin/stdout JSON filters, so the tests exercise them end-to-end as subprocesses: threshold selection per model, the fork exemption, Workflow script gating, the upward ledger search stopping at the repo root (including worktree/submodule `.git`-as-file boundaries), stop-guard blocking and its loop guard, profile injection, and cache cleanup.

## What this does NOT do (honest limitations)

- **Hooks check ledger existence and checkbox state — not ledger fidelity.** A shallow ledger passes both gates. Writing a faithful ledger remains a judgment task, which is exactly why the instructions assign it to your smartest model, not a subagent.
- **The spawn gate checks that a ledger exists, not that it is fresh.** A fully closed ledger from last week's task satisfies the gate for today's unrelated delegation. Freshness is left to the model on purpose — a mechanical staleness rule would misfire on legitimately long-running work.
- **Marking `- [x]` without actually verifying is possible.** The instructions say "verify first"; no hook can tell the difference. Mechanizing this further invites ritual compliance — we deliberately stopped here.
- **Detection keys on the model string.** Anything containing `fable` (case-insensitive) gets the Fable profile; everything else gets the lean profile. Force it with `FABLE_ORCH_PROFILE` if your setup needs otherwise.
- **Hook enforcement is only as strong as the host's hook pipeline.** On at least one experimental configuration (async agent-teams spawn backend), we observed an async `Agent` launch proceed even though the guard emits a deny for that exact payload when run in isolation. If you run experimental spawn backends, verify enforcement once: a longer-than-threshold spawn without a ledger should come back denied.
- **The orchestration discipline itself is still prompt-level.** The hooks fence exactly two failure points — the two that, in practice, get skipped the most: delegating without a ledger, and closing without finishing.

## Why these two guards

Because they sit at the two ends of the failure pipeline. Details die *entering* the workflow (task→plan translation, fenced by the spawn guard) and *leaving* it (closing with silently unaddressed items, fenced by the close guard). Everything between those two points is judgment — and judgment belongs to the model, not to a regex.

## License

MIT
