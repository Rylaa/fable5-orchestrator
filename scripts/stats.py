#!/usr/bin/env python3
"""Summarize the fable-orchestrator metrics log.

Usage:
    python3 scripts/stats.py [path]

Default path: ~/.claude/fable-orch/metrics.jsonl (written by the hooks;
disable collection with FABLE_ORCH_METRICS=0).
"""
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone


def records(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.expanduser("~"), ".claude", "fable-orch", "metrics.jsonl")
    if not os.path.isfile(path):
        print(f"no metrics yet: {path}")
        return

    per_day = defaultdict(Counter)
    events = Counter()
    profiles = Counter()
    ledgers = Counter()
    swarm_reaped = 0

    for rec in records(path):
        event = rec.get("event") or "?"
        events[event] += 1
        try:
            day = datetime.fromtimestamp(
                float(rec.get("ts") or 0), tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            day = "?"
        per_day[day][event] += 1
        if event == "inject":
            profiles[rec.get("profile") or "?"] += 1
        if event == "stop_block":
            ledgers[rec.get("ledger") or "?"] += 1
        if event == "cleanup":
            try:
                swarm_reaped += int(rec.get("swarm_own") or 0)
                swarm_reaped += int(rec.get("swarm_stale") or 0)
            except (TypeError, ValueError):
                pass

    print(f"metrics: {path}\n")
    print("== events per day ==")
    for day in sorted(per_day):
        parts = ", ".join(f"{k}={v}" for k, v in sorted(per_day[day].items()))
        print(f"{day}  {parts}")

    print("\n== totals ==")
    for name, count in sorted(events.items()):
        print(f"{name:26} {count}")

    if profiles:
        print("\n== sessions by profile (inject events) ==")
        for name, count in profiles.most_common():
            print(f"{name:8} {count}")

    denies = events.get("spawn_deny", 0)
    passes = events.get("spawn_pass_over_threshold", 0)
    if denies or passes:
        print(f"\nover-threshold spawns: {passes} passed with a ledger, {denies} denied")

    if swarm_reaped:
        print(f"\ntmux teammate servers reaped: {swarm_reaped}")

    if ledgers:
        print("\n== stop blocks by ledger ==")
        for name, count in ledgers.most_common(5):
            print(f"{count:5}  {name}")


if __name__ == "__main__":
    main()
