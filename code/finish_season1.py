#!/usr/bin/env python3
"""Complete season 1 deterministically (engine v1 frozen + bots_v1).

Closes turn 9 (already fully queued: 19 bots + czlonkek culture) and plays
every remaining turn to day 40 / last nation standing. Pure replay of the
frozen engine — the only sanctioned way to finish the season. Prints the
winner and the final seal; verifies the full chain afterwards.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import engine_v1 as engine
import bots_v1 as bots

STATE = os.path.join(HERE, "state.json")
LOG = os.path.join(HERE, "log.jsonl")


def norm(d):
    d["citizens"] = {int(k): v for k, v in d["citizens"].items()}
    for c in d["citizens"].values():
        if c.get("country") is not None:
            c["country"] = int(c["country"])
    d["nations"] = {int(k): v for k, v in d["nations"].items()}
    for n in d["nations"].values():
        n["citizens"] = [int(x) for x in n["citizens"]]
        if n.get("leader") is not None:
            n["leader"] = int(n["leader"])
    d["war"] = [[int(a), int(b)] for a, b in d.get("war") or []]
    d["pending"] = {int(k): v for k, v in (d.get("pending") or {}).items()}
    return d


def main():
    state = norm(json.load(open(STATE)))
    log_lines = [json.loads(l) for l in open(LOG) if l.strip()]
    start_turn = state["turn"]
    if state["winner"] is not None:
        print("season already over, winner", state["winner"])
        return
    # real-agent seats are NOT auto-played; turn 9 already has czlonkek's move
    steps = 0
    while state["winner"] is None and state["turn"] < 80:
        for cid in sorted(state["citizens"]):
            if cid in state["pending"]:
                continue
            act, args = bots.choose(state, cid)
            engine.apply_action(state, cid, act, args)
        applied = engine.apply_turn(state, log_lines)
        steps += 1
        if steps % 10 == 0:
            print(f"  closed up to turn {state['turn']-1} (day {(state['turn']-1)//2})")
    print(f"closed {steps} turns (from turn {start_turn} to {state['turn']-1})")
    print("winner:", state["winner"],
          state["nations"][state["winner"]]["name"] if state["winner"] is not None else "—")
    last = state["seals"][-1]
    print(f"final seal turn {last['turn']}: {last['sha']} (actions {last['actions']})")
    # persist exactly like server.save()
    json.dump(state, open(STATE, "w"), indent=1)
    with open(LOG, "a") as f:
        for line in log_lines[-steps * 21 - 5:]:
            pass
    # rewrite the full log (log_lines now contains everything)
    with open(LOG, "w") as f:
        for line in log_lines:
            f.write(json.dumps(line, sort_keys=True) + "\n")
    # keys: save() in server writes secrets; do the same
    json.dump(state["secrets"], open(os.path.join(HERE, "keys.json"), "w"))
    # full verification: rebuild from seed and re-apply
    rep = engine.new_state(seed=state["seed"], season=state["season"])
    ok, note = True, ""
    joins = 0
    for line in log_lines:
        if line.get("type") == "join":
            rep["citizens"][line["citizen"]]["name"] = line["name"]
            rep["citizens"][line["citizen"]]["model"] = line["model"]
            rep["citizens"][line["citizen"]]["joined_utc"] = line["utc"]
            joins += 1
        elif line.get("type") == "action":
            r, _ = engine.apply_action(rep, line["citizen"], line["raw_action"], line.get("args") or {})
            if not r:
                ok = False
                note = f"replay rejected action by citizen {line['citizen']} at line"
                break
        else:  # turn
            if line["turn"] != rep["turn"]:
                ok = False
                note = "turn order mismatch"
                break
            engine.apply_turn(rep)
            if rep["seals"][-1]["sha"] != line["seal"]:
                ok = False
                note = f"seal mismatch turn {line['turn']}"
                break
    print("VERIFY:", "OK — full chain replayable from seed + log" if ok else f"FAILED: {note}")
    print("final state:", {k: (v["name"], v["tiles"], v["treasury"], v["army"])
          for k, v in state["nations"].items()} if state["nations"] else "all gone")


if __name__ == "__main__":
    main()
