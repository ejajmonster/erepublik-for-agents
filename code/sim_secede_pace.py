#!/usr/bin/env python3
"""Measure secession pace on a full 5000-seat v10 world driven by bots10.
Prints per-10-turn nation count + secede/found events. Run: python3 sim_secede_pace.py [turns]"""
import sys, time
sys.path.insert(0, ".")
import engine10 as E
import bots10 as B

TURNS = int(sys.argv[1]) if len(sys.argv) > 1 else 60
SEED = 20261004

s = E.new_state(seed=SEED, season="sim-pace", n_seats=5000,
                v5=True, v6=True, v7=True, v8=True, v9=True, v10=True,
                endless=True)
t0 = time.time()
last_n = len(s["nations"])
for turn in range(TURNS):
    for cid in sorted(s["citizens"]):
        if s["pending"].get(cid):
            continue
        act, args = B.choose(s, cid)
        E.apply_action(s, cid, act, args)
    before = len(s["nations"])
    E.apply_turn(s)
    grew = len(s["nations"]) - before
    if turn % 10 == 0 or grew:
        print(f"t{turn:3d}: nations {len(s["nations"])} (+{grew})  [{time.time()-t0:5.1f}s]", flush=True)
print(f"done {TURNS} turns in {time.time()-t0:.1f}s; nations {last_n}->{len(s["nations"])}")
