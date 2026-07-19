# Fable Orchestrator

[![CI](https://github.com/Rylaa/fable5-orchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/Rylaa/fable5-orchestrator/actions/workflows/ci.yml)

**Run Claude Fable 5 all day — without watching the usage meter.**

Fable 5 is the best chair a Claude Code session can have — and the most expensive seat in the house. Let it type every token itself and the session ends rate-limited, waiting out the reset window.

This plugin makes the split mechanical. **Fable 5 keeps the chair** and spends tokens only on planning, arbitration, and final decisions. Everything else — implementation, research, briefs, review, bulk reading — goes to **Sonnet 5**. **Opus 4.8** stays on the roster as the escalation lane (security reviews pinned there), and the close gets its fresh-eyes verification from **Fable 5** itself — one bounded call per workflow.

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
│   SONNET 5 · max    │       │   SONNET 5 · max    │       │   SONNET 5 · max    │
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
│ Bulk gathering (fetch, grep, scan)      │ Sonnet 5 max    │ nothing             │
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

### 1 · The Fable profile

A SessionStart hook injects the Fable-in-chair profile ([`instructions/dynamic-workflow-fable.md`](instructions/dynamic-workflow-fable.md)) into every session — the plugin is built for one chair, Claude Fable 5, and there is no mode to configure:

```
┌──────────────────────────┬──────────────────────────────┐
│ Scarce resource          │ your usage limit             │
│ Bounded / medium work    │ delegated                    │
│ Requirements Ledger      │ file, before any delegation  │
│ Verification             │ fresh-eyes on every close    │
│ Disk hand-off            │ the default                  │
│ Spawn-guard threshold    │ 1500 chars                   │
│ Task-list gate           │ 3rd task needs the ledger    │
└──────────────────────────┴──────────────────────────────┘
```

The profile routes subagents by tier name (`sonnet`, `opus`, `fable`), keeps bulk material on disk (`./.workflow/scratch/` — the chair receives briefs and verdicts, never dumps), and runs every delegated agent at `max` effort — savings come from the tier, never from dialing effort down. Context-heavy follow-ups go to a **fork** (`subagent_type: "fork"`), which inherits the full conversation with no spec-writing tax.

### 2 · A Requirements Ledger

Before serious delegation the chair writes every requirement, constraint, and edge case as one checkbox line in `./.workflow/LEDGER.md`. Files survive context compaction; conversation context does not.

```markdown
- [ ] 1. Every explicit requirement, one line each
- [ ] 2. Implicit expectations and constraints too
- [x] 3. Marked done only after verification confirms it
- [~] 4. deferred: user approved postponing this
```

### 3 · Guard hooks

Instructions are *advice*; hooks are *mechanism*. The failure points that get skipped under pressure are fenced:

**Spawn guard** (`PreToolUse` on `Agent|Task|Workflow`) — gates the spawn `prompt`, or the Workflow `script`:

```
spawn (Agent / Task / Workflow)
  │
  ├─ text ≤ threshold (default 1500) .............. PASS  (short spawns are never taxed)
  │
  └─ text > threshold
       │
       ├─ subagent_type == "fork" ................. PASS  (forks already see the ledger)
       │
       ├─ ACTIVE .workflow/LEDGER.md found ........ PASS  (cite its items per agent)
       │  (cwd → repo root / $HOME)
       │
       └─ no ledger — or only a stale one ......... DENY  → "write the ledger first;
                                                     small single-phase → do directly"
```

A ledger is *stale* when every item is closed AND it was last touched before this session started — last week's finished ledger doesn't disarm the gates for a new task. Open items, or any touch during this session, keep it active.

**Task guard** (`PreToolUse` on `TaskCreate`) — the solo path the spawn guard can't see. A session that never spawns agents never meets the spawn guard; it just quietly implements a six-phase plan solo on the most expensive model (measured in the wild). The tracker tasks it creates for itself are the tell:

```
TaskCreate (tracker task)
  │
  ├─ ACTIVE .workflow/LEDGER.md found .............. PASS
  ├─ fewer than 3 ledgerless tasks this session .... PASS  (small task lists are fine)
  │
  └─ 3rd ledgerless task ........................... DENY once → "multi-phase work:
                                                     write the ledger, delegate the
                                                     phases to workers" — then quiet
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

A fourth hook (`SessionEnd`) cleans up after the session: its temp files, **its tmux teammates** — the experimental agent-teams backend parks teammates in `claude-swarm-*` tmux servers and never reaps them (measured in the wild: 63 orphaned agents holding ~5 GB across three old sessions); the hook kills the server named with the session's own process id (`claude-swarm-<pid>`, found via the hook's ancestor chain) or whose panes carry its `@session-<id>` tag — and any swarm server idle for 48h+, which catches teams orphaned by crashed sessions. Finished teammates don't wait for a SessionEnd that may be days away: a rate-limited sweep piggybacked on the Stop hook samples every teammate pane's CPU and kills panes whose clock hasn't moved for `FABLE_ORCH_TEAMMATE_IDLE_H` hours (default 2) — pane-level, so working siblings are untouched.

## Install

```
/plugin marketplace add Rylaa/fable5-orchestrator
/plugin install orchestrator@fable-orchestrator
```

Requires `python3` on PATH; macOS and Linux only — the hooks also shell out to `tmux` for teammate reaping, and Windows is not supported. No configuration needed.

### Manual install (without the plugin system)

1. Copy `scripts/ledger_guard_spawn.py`, `scripts/ledger_guard_stop.py`, and `scripts/cleanup_session_cache.py` to `~/.claude/hooks/`.
2. Merge this into `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "^(Agent|Task|Workflow|TaskCreate)$",
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
          { "type": "command", "command": "python3 ~/.claude/hooks/cleanup_session_cache.py", "timeout": 20 }
        ]
      }
    ]
  }
}
```

3. Append `instructions/dynamic-workflow-fable.md` to `~/.claude/CLAUDE.md`.
4. Without the SessionStart injector there is no per-session `started` marker, so the stop guard can't tell another session's ledger from yours (every open ledger costs one reminder per session instead of zero) and the spawn/task gates can't ignore stale fully-closed ledgers.

> Don't run the plugin AND the manual install side by side — you'd get every guard twice.

## Configuration

Set these in `~/.claude/settings.json` under `"env"`.

```
┌───────────────────────────────┬────────────────────┬────────────────────────────────────────────┐
│ Env var                       │ Default            │ Meaning                                    │
├───────────────────────────────┼────────────────────┼────────────────────────────────────────────┤
│ LEDGER_GUARD_THRESHOLD        │ 1500               │ spawn-guard gate (chars)                   │
│ LEDGER_GUARD_TASKS            │ 3                  │ 3rd ledgerless tracker task denied; 0 off  │
│ LEDGER_GUARD_STOP_MODE        │ once-per-session   │ every-turn restores per-turn blocking      │
│ FABLE_ORCH_METRICS            │ (on)               │ 0 disables local metrics logging           │
│ FABLE_ORCH_SWARM_CLEANUP      │ (on)               │ 0 disables all teammate reaping            │
│ FABLE_ORCH_SWARM_MAX_IDLE_H   │ 48                 │ sweep swarms idle ≥ N hours; 0 disables    │
│ FABLE_ORCH_TEAMMATE_IDLE_H    │ 2                  │ kill teammate panes idle ≥ N hours; 0 off  │
└───────────────────────────────┴────────────────────┴────────────────────────────────────────────┘
```

**The session marker.** The SessionStart injector writes a per-session temp file whose immutable `started` timestamp survives resume/clear/compact re-injections — the stop guard compares ledger mtimes against it to decide ownership, and the SessionEnd reaper anchors its cleanup to it. The SessionEnd hook removes the session's temp files and sweeps any older than 96 hours.

**Metrics.** Every hook appends one event line to `~/.claude/fable-orch/metrics.jsonl` (events only — never prompt content): injections per model, spawn/task denies and passes, stop blocks and suppressions, reaps. `python3 scripts/stats.py` prints the summary, so the next "how is this performing?" question is answered with data. Disable with `FABLE_ORCH_METRICS=0`.

## Tests

```
python3 -m pytest tests/ -q
```

The hooks are plain stdin/stdout JSON filters; the tests run them end-to-end as subprocesses — the spawn threshold and its env override, the fork exemption, Workflow script gating, the task-list gate (counting, one deny per session, session isolation), the upward ledger search and its repo-root/worktree/$HOME boundaries, stop-guard session scoping and ownership, metrics emission and opt-out, injection, cache cleanup, and teammate reaping (against a fake tmux/ps on PATH).

## Honest limitations

- Hooks check ledger **existence and checkbox state**, not fidelity — a shallow ledger passes; writing a faithful one stays a judgment task.
- Freshness is only half-checked: a fully-closed ledger from a previous session re-arms the gates, but a stale ledger with OPEN items still satisfies them (it looks like active work).
- Marking `- [x]` without actually verifying is possible; mechanizing further would invite ritual compliance.
- The plugin assumes a Fable 5 chair — the same profile is injected regardless of the session model, and the 1500-char spawn gate applies everywhere.
- Enforcement is only as strong as the host's hook pipeline — on at least one experimental spawn backend we observed an async `Agent` launch proceed despite the guard's deny; verify once on your setup.
- Pane idleness is a CPU heuristic: a teammate silently blocked for hours inside one external command (a long build) looks idle and can be reaped — raise or disable `FABLE_ORCH_TEAMMATE_IDLE_H` for such workloads. A reaped teammate can no longer be resumed with SendMessage.
- The task guard counts tracker tasks, not work size: a solo multi-phase session that never creates tasks still slips through, and the deny is a single nudge per session, not a wall.
- The orchestration discipline itself is prompt-level; the hooks fence exactly three failure points — the ones that get skipped the most.

## Why these guards

Details die *entering* the workflow (task→plan translation, fenced by the spawn guard) and *leaving* it (closing with silently unaddressed items, fenced by the close guard). The third measured failure is the workflow never starting at all: the chair quietly implementing a multi-phase plan solo on the most expensive model (fenced by the task guard). Everything between is judgment — and judgment belongs to the model, not to a regex.

## License

MIT
