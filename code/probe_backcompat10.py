#!/usr/bin/env python3
"""Back-compat gate: engine10 must replay the LIVE chain (seed + log.jsonl)
byte-identically to the live state's seals, using the live chain's version
flags — exactly like server._replay_state / /api/verify.

This is THE contract that lets engine10 sit next to the running world without
touching the live seal chain. Before season 6 the live chain was an engine9
(v10-off) world; from season 6 it is a v10-on world, so the probe reads the
flags off the live state rather than forcing v10=False.

Run: python3 probe_backcompat10.py
Exit 0 = pass. Prints first divergence on failure.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
import engine10 as E

cfg = json.load(open(os.path.join(HERE, "server_config.json")))
live = json.load(open(os.path.join(HERE, "state.json")))

# Mirror server._replay_state: version flags from the LIVE state.
replay = E.new_state(
    seed=live.get("seed", cfg["SEED"]),
    season=live.get("season", cfg["SEASON"]),
    n_seats=live.get("seats", cfg["SEATS"]),
    v5=bool(live.get("v5")), v6=bool(live.get("v6")),
    v7=bool(live.get("v7")), v8=bool(live.get("v8")),
    v9=bool(live.get("v9")), v10=bool(live.get("v10")),
    endless=bool(live.get("endless")),
)
# The replay must carry the same version flags as the live world, or the
# world shape (deposits, resources, caps) differs and seals can't match.
assert bool(replay.get("v10")) == bool(live.get("v10")), \
    "probe must replay with the live chain's v10 flag"

log_lines = []
with open(os.path.join(HERE, "log.jsonl")) as f:
    for line in f:
        try:
            log_lines.append(json.loads(line))
        except Exception:
            pass

joins = 0
replayed_turns = 0
ok = True
note = None
for idx, raw in enumerate(log_lines):
    line = dict(raw)
    typ = line.get("type", "action" if "raw_action" in line else "turn")
    if typ == "join":
        c = replay["citizens"].get(line["citizen"])
        if c is not None:
            c["name"] = line["name"]
            c["model"] = line["model"]
            c["joined_utc"] = line["utc"]
            if line.get("persona") is not None:
                c["persona"] = line["persona"]
            joins += 1
    elif typ == "action":
        r, msg = E.apply_action(replay, line["citizen"], line["raw_action"],
                                line.get("args") or {})
        if not r:
            ok = False
            note = f"log line {idx}: action {line.get('raw_action')} by {line.get('citizen')} rejected ({msg})"
            break
    elif typ == "turn":
        if line["turn"] != replay["turn"]:
            ok = False
            note = f"log line {idx}: turn order mismatch (log {line['turn']} vs replay {replay['turn']})"
            break
        E.apply_turn(replay)
        if not replay["seals"] or replay["seals"][-1]["sha"] != line["seal"]:
            ok = False
            note = f"log line {idx}: SEAL MISMATCH on turn {line['turn']}"
            break
        replayed_turns = replay["turn"]

if ok and not (replay["seals"] == live["seals"] and replay["turn"] == live["turn"]):
    ok = False
    note = "replay diverged from live state (seal chain or turn count mismatch)"

print(json.dumps({
    "replay_ok": ok,
    "joins": joins,
    "live_seals": len(live["seals"]),
    "replayed_turns": replayed_turns,
    "note": note,
}))
sys.exit(0 if ok else 1)
