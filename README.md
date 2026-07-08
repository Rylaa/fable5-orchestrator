# Fable Orchestrator

[![CI](https://github.com/Rylaa/fable5-orchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/Rylaa/fable5-orchestrator/actions/workflows/ci.yml)

**Run Claude Fable 5 all day — without watching the usage meter.**

Fable 5 is the best chair a Claude Code session can have — and the most expensive seat in the house. Let it type every token itself and the session ends rate-limited, waiting out the reset window.

This plugin makes the split mechanical. **Fable 5 keeps the chair** and spends tokens only on planning, arbitration, and final decisions. Everything else — implementation, research, briefs, review, bulk reading — goes to **Sonnet 5**. **Opus 4.8** stays on the roster for exactly two jobs: fresh-eyes verification and escalation.

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
│   SONNET 5 · low    │       │   SONNET 5 · max    │       │   SONNET 5 · max    │
│   mechanical bulk   │       │   implementation    │       │   routine judgment  │
│   grep·fetch·scan   │       │   code · tests      │       │   briefs · review   │
│   format · read     │       │   debug · refactor  │       │   filtering         │
└─────────────────────┘       └─────────────────────┘       └──────────┬──────────┘
                                                                       │ uncertain /
                                                                       │ high stakes
                                                            ┌──────────▼──────────┐
                                                            │      the valve      │
                                                            │  verify: FABLE 5    │
                                                            │  escalate: OPUS 4.8 │
                                                            │  (→ fable ceiling)  │
                                                            └─────────────────────┘
```

Fable thinks. Sonnet does. Fable checks the close. Your limit pays for the thinking plus one verification per close:

```
┌─────────────────────────────────────────┬─────────────────┬─────────────────────┐
│ Work                                    │ Runs on         │ Fable limit pays    │
├─────────────────────────────────────────┼─────────────────┼─────────────────────┤
│ Phase planning, arbitration, decisions  │ Fable 5 (chair) │ yes                 │
│ Implementation, tests, refactors        │ Sonnet 5 max    │ nothing             │
│ Source briefs, filtering, code review   │ Sonnet 5 max    │ nothing             │
│ Bulk gathering (fetch, grep, scan)      │ Sonnet 5 low    │ nothing             │
│ Fresh-eyes verification (the close)     │ Fable 5 max     │ one call per close  │
│ Escalations (hard judgment, security)   │ Opus → Fable    │ mostly nothing      │
└─────────────────────────────────────────┴─────────────────┴─────────────────────┘
```

## Why Fable 5 × Sonnet 5 is the right couple

- **Fable tokens are the heaviest draw on your limit.** Every token of bulk work kept off the chair extends how long Fable stays in it.
- **Sonnet 5 closed the gap.** Near-Opus quality on coding and agentic work, with the full effort ladder (`low` → `xhigh` → `max`). At `max` it carries the routine judgment — briefs, filtering, standard review — that used to need an Opus call.
- **The valve is two-tier.** Fresh-eyes verification of the close runs on Fable 5 — the strongest model at the single highest-stakes moment, once per close. Escalations climb sonnet → opus → fable, with security reviews pinned to Opus (Fable's safety classifiers can refuse even benign security work). A worker that returns "uncertain" never bounces back to the chair.

## What the plugin does

Three layers, all mechanical — no CLAUDE.md editing, no manual routing.

### 1 · Model-aware profiles

A SessionStart hook detects the chair model and injects the profile that fits it:

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

Both profiles route subagents by tier name (`sonnet`, `opus`, `fable`), keep bulk material on disk (`./.workflow/scratch/` — the chair receives briefs and verdicts, never dumps), and set effort per spawn: `max` for implementation, judgment, and verification; `low` for mechanical gathering. Context-heavy follow-ups go to a **fork** (`subagent_type: "fork"`), which inherits the full conversation with no spec-writing tax.

### 2 · A Requirements Ledger

Before serious delegation the chair writes every requirement, constraint, and edge case as one checkbox line in `./.workflow/LEDGER.md`. Files survive context compaction; conversation context does not.

```markdown
- [ ] 1. Every explicit requirement, one line each
- [ ] 2. Implicit expectations and constraints too
- [x] 3. Marked done only after verification confirms it
- [~] 4. deferred: user approved postponing this
```

### 3 · Guard hooks

Instructions are *advice*; hooks are *mechanism*. The two rules that get skipped under pressure are enforced:

**Spawn guard** (`PreToolUse` on `Agent|Task|Workflow`) — gates the spawn `prompt`, or the Workflow `script`:

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

**Close guard** (`Stop`):

```
turn ends
  │
  ├─ no ledger on the search path (cwd → repo root / $HOME) ... pass
  ├─ every item "- [x]" or "- [~] deferred" ................... pass
  ├─ ledger untouched by this session ......................... pass
  ├─ this session already got its reminder .................... pass
  │
  └─ open items, touched here, first time ..................... BLOCK once,
       listing the items: finish them, defer with user approval, or
       acknowledge in one line and move on — one reminder per session.
       Archive paused ledgers (LEDGER-<topic>-archive.md) to silence for
       good; LEDGER_GUARD_STOP_MODE=every-turn restores per-turn blocking.
```

A fourth hook (`SessionEnd`) removes the session's temp-dir model cache.

## Install

```
/plugin marketplace add Rylaa/fable5-orchestrator
/plugin install orchestrator@fable-orchestrator
```

Requires `python3` on PATH. Model auto-detection works out of the box.

### Manual install (without the plugin system)

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

3. Append `instructions/dynamic-workflow-fable.md` (Fable chair) or `instructions/dynamic-workflow-opus.md` (any other chair) to `~/.claude/CLAUDE.md`.
4. Without the SessionStart injector there is no per-session cache, so the spawn guard sits at the lean gate (4000). If Fable is your daily driver, pin `LEDGER_GUARD_THRESHOLD=1500` or `FABLE_ORCH_PROFILE=fable`.

> Don't run the plugin AND the manual install side by side — you'd get every guard twice.

## Configuration

Set these in `~/.claude/settings.json` under `"env"`.

```
┌───────────────────────────────┬────────────────────┬────────────────────────────────────────────┐
│ Env var                       │ Default            │ Meaning                                    │
├───────────────────────────────┼────────────────────┼────────────────────────────────────────────┤
│ FABLE_ORCH_PROFILE            │ auto               │ auto | fable | opus — force a profile     │
│ LEDGER_GUARD_THRESHOLD        │ (unset)            │ hard spawn threshold, every profile       │
│ LEDGER_GUARD_THRESHOLD_FABLE  │ 1500               │ spawn gate under the Fable profile        │
│ LEDGER_GUARD_THRESHOLD_OPUS   │ 4000               │ spawn gate under the lean profile         │
│ LEDGER_GUARD_STOP_MODE        │ once-per-session   │ every-turn restores per-turn blocking     │
│ FABLE_ORCH_METRICS            │ (on)               │ 0 disables local metrics logging          │
└───────────────────────────────┴────────────────────┴────────────────────────────────────────────┘
```

**Model detection.** Claude Code delivers the session model only in the SessionStart payload (optional even there — no other hook event receives it). The injector caches `{model, profile}` per session; the spawn guard resolves `FABLE_ORCH_PROFILE` → payload model (future-proofing) → session cache → lean default. Unknown model means lean profile — the heavy Fable discipline never runs silently on the wrong chair. The SessionEnd hook removes the session's temp files and sweeps any older than 48 hours.

**Metrics.** Every hook appends one event line to `~/.claude/fable-orch/metrics.jsonl` (events only — never prompt content): injections per profile, spawn denies/passes, stop blocks and suppressions. `python3 scripts/stats.py` prints the summary, so the next "how is this performing?" question is answered with data. Disable with `FABLE_ORCH_METRICS=0`.

## Tests

```
python3 -m pytest tests/ -q
```

The hooks are plain stdin/stdout JSON filters; the tests run them end-to-end as subprocesses — thresholds per model, the fork exemption, Workflow script gating, the upward ledger search and its repo-root/worktree/$HOME boundaries, stop-guard session scoping and ownership, metrics emission and opt-out, injection, and cache cleanup.

## Honest limitations

- Hooks check ledger **existence and checkbox state**, not fidelity — a shallow ledger passes; writing a faithful one stays a judgment task.
- Existence, not freshness: a fully closed ledger from an old task satisfies the gate for a new one.
- Marking `- [x]` without actually verifying is possible; mechanizing further would invite ritual compliance.
- Detection keys on the model string — anything containing `fable` gets the Fable profile; force with `FABLE_ORCH_PROFILE` otherwise.
- Enforcement is only as strong as the host's hook pipeline — on at least one experimental spawn backend we observed an async `Agent` launch proceed despite the guard's deny; verify once on your setup.
- The orchestration discipline itself is prompt-level; the hooks fence exactly two failure points — the two that get skipped the most.

## Why these two guards

Details die *entering* the workflow (task→plan translation, fenced by the spawn guard) and *leaving* it (closing with silently unaddressed items, fenced by the close guard). Everything between is judgment — and judgment belongs to the model, not to a regex.

## License

MIT
