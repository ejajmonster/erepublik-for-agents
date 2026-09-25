#!/usr/bin/env python3
"""Verify the completed season 1: full replay from seed + log, old-format tolerant
(same logic as server.py /api/verify: file order is authoritative; missing
turn/type on early lines are synthesized)."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import engine_v1 as engine

state = json.load(open(os.path.join(HERE, "state.json")))
log_lines = [json.loads(l) for l in open(os.path.join(HERE, "log.jsonl")) if l.strip()]

rep = engine.new_state(seed=state["seed"], season=state["season"])
ok, note, joins, replayed = True, "", 0, 0
for idx, line in enumerate(log_lines):
    if "turn" not in line:
        line["turn"] = rep["turn"]
    if "type" not in line:
        line["type"] = "action" if "raw_action" in line else "turn"
    t = line["type"]
    if t == "join":
        rep["citizens"][line["citizen"]]["name"] = line["name"]
        rep["citizens"][line["citizen"]]["model"] = line["model"]
        rep["citizens"][line["citizen"]]["joined_utc"] = line["utc"]
        joins += 1
    elif t == "action":
        r, msg = engine.apply_action(rep, line["citizen"], line["raw_action"], line.get("args") or {})
        if not r:
            ok, note = False, f"line {idx}: action rejected ({msg})"
            break
    else:
        if line["turn"] != rep["turn"]:
            ok, note = False, f"line {idx}: turn order mismatch"
            break
        engine.apply_turn(rep)
        if not rep["seals"] or rep["seals"][-1]["sha"] != line["seal"]:
            ok, note = False, f"line {idx}: seal mismatch turn {line['turn']}"
            break
        replayed = rep["turn"]
if ok and not (rep["seals"] == state["seals"] and rep["turn"] == state["turn"]):
    ok, note = False, "replay diverged from live state"
print("replay_ok:", ok)
print("seals:", len(state["seals"]), "replayed_turns:", replayed, "joins:", joins, "winner:", state["winner"])
print("note:", note)
sys.exit(0 if ok else 1)
