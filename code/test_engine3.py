#!/usr/bin/env python3
"""Test harness for engine3: determinism, full bot season, tamper detection."""
import json
import sys
import copy

sys.path.insert(0, "/home/piotr/.openclaw/workspace/erepublik")
import engine3
import bots3


def run_full(seed=777, turns=80, seed_season="season3"):
    st = engine3.new_state(seed=seed, season=seed_season)
    log = []
    for _ in range(turns):
        for cid in sorted(st["citizens"]):
            if st["winner"] is not None:
                break
            act, args = bots3.choose(st, cid)
            r, _ = engine3.apply_action(st, cid, act, args)
            assert r, f"bot action rejected: {act} {args} at turn {st['turn']}"
        applied = engine3.apply_turn(st, log)
        if st["winner"] is not None:
            break
    return st, log


def test_determinism():
    st1, log1 = run_full(seed=777)
    st2, log2 = run_full(seed=777)
    assert [s["sha"] for s in st1["seals"]] == [s["sha"] for s in st2["seals"]], "seals differ!"
    assert log1 == log2, "logs differ!"
    print(f"OK determinism: {len(st1['seals'])} turns, winner={st1['winner']} "
          f"({st1['nations'][st1['winner']]['name'] if st1['winner'] is not None else '—'})")


def test_replay():
    st, log = run_full(seed=4242)
    # rebuild from seed and re-apply log actions in order
    rep = engine3.new_state(seed=4242, season=st["season"])
    turn_lines = []
    act_lines = []
    for line in log:
        if line["type"] == "turn":
            turn_lines.append(line)
        else:
            act_lines.append(line)
    # replay: apply actions of each turn then close the turn
    ti = 0
    ai = 0
    ok = True
    for tl in turn_lines:
        while ai < len(act_lines) and act_lines[ai]["turn"] <= tl["turn"]:
            a = act_lines[ai]
            r, msg = engine3.apply_action(rep, a["citizen"], a["raw_action"], a.get("args") or {})
            if not r:
                ok = False
                print(f"replay rejected action: {msg}")
                break
            ai += 1
        if not ok:
            break
        engine3.apply_turn(rep)
        if rep["seals"][-1]["sha"] != tl["seal"]:
            ok = False
            print(f"seal mismatch at turn {tl['turn']}")
            break
    assert ok, "replay failed"
    assert rep["seals"] == st["seals"]
    print("OK replay: full seal chain rebuilds from seed + log")


def test_tamper():
    st, log = run_full(seed=99)
    # tamper: change a bot action in the log, replay must fail
    tampered = copy.deepcopy(log)
    changed = False
    for line in tampered:
        if line["type"] == "action":
            line["raw_action"] = "work"
            line["args"] = {}
            changed = True
            break
    assert changed
    rep = engine3.new_state(seed=99, season=st["season"])
    ti, ai, ok = 0, 0, True
    turn_lines = [l for l in tampered if l["type"] == "turn"]
    act_lines = [l for l in tampered if l["type"] == "action"]
    for tl in turn_lines:
        while ai < len(act_lines) and act_lines[ai]["turn"] <= tl["turn"]:
            a = act_lines[ai]
            r, _ = engine3.apply_action(rep, a["citizen"], a["raw_action"], a.get("args") or {})
            ai += 1
        engine3.apply_turn(rep)
        if rep["seals"][-1]["sha"] != tl["seal"]:
            ok = False
            break
    assert not ok, "tampered log should NOT replay cleanly"
    print("OK tamper: modified action breaks the seal chain")


def test_v3_features():
    st = engine3.new_state(seed=1)
    n0 = st["nations"][0]
    # market buy/sell
    r, _ = engine3.apply_action(st, 0, "market_buy", {"resource": "wood"})
    assert r
    r, _ = engine3.apply_action(st, 1, "market_buy", {"resource": "wood"})
    assert r
    engine3.apply_turn(st)
    r, _ = engine3.apply_action(st, 1, "market_sell", {"resource": "wood"})
    assert r
    engine3.apply_turn(st)
    assert "wood" in n0["stock"]
    print("OK market: buy/sell work, stockpile updated")

    # infrastructure
    n0["treasury"] = 500
    n0["stock"] = {"wood": 50, "iron": 50, "grain": 50, "oil": 50}
    r, _ = engine3.apply_action(st, 0, "infrastructure", {"building": "barracks"})
    assert r, "barracks build should work"
    engine3.apply_turn(st)
    assert n0["buildings"]["barracks"] == 1
    print("OK infrastructure: barracks built")

    # spy + sabotage
    st2 = engine3.new_state(seed=2)
    st2["nations"][0]["stock"] = {"wood": 50, "iron": 50, "grain": 50, "oil": 50}
    st2["nations"][1]["stock"] = {"wood": 50, "iron": 50, "grain": 50, "oil": 50}
    st2["nations"][0]["army"] = 10
    r, _ = engine3.apply_action(st2, 0, "spy", {"target": 1})
    assert r
    r, _ = engine3.apply_action(st2, 4, "sabotage", {"target": 0})
    assert r
    engine3.apply_turn(st2)  # turn 0: intel applied during the day, still active
    assert st2["spied"].get("Aurelia") == [1], f"spy intel should last the day: {st2['spied']}"
    engine3.apply_turn(st2)  # turn 1: still same day
    assert st2["spied"].get("Aurelia") == [1]
    engine3.apply_turn(st2)  # turn 2: new day -> intel expires
    assert st2["spied"] == {}, f"spy intel should expire next day: {st2['spied']}"
    print("OK espionage: spy + sabotage executed, intel expires daily")

    # counter-espionage blocks sabotage
    st3 = engine3.new_state(seed=3)
    st3["nations"][0]["stock"] = {"wood": 50, "iron": 50, "grain": 50, "oil": 50}
    st3["nations"][1]["stock"] = {"wood": 50, "iron": 50, "grain": 50, "oil": 50}
    st3["nations"][0]["army"] = 10
    engine3.apply_turn(st3)
    r, _ = engine3.apply_action(st3, 0, "spies", {})
    assert r
    r, _ = engine3.apply_action(st3, 4, "sabotage", {"target": 0})
    assert r
    before = st3["nations"][0]["army"]
    engine3.apply_turn(st3)
    # sabotage should have failed (army untouched by sabotage; battle may still hit)
    evs = [e for e in st3["recent"] if "SABOTAGE" in e]
    assert any("FAIL" in e for e in evs), f"counter-espionage should block: {evs}"
    print("OK counter-espionage: sabotage blocked")

    # titles
    st4 = engine3.new_state(seed=4)
    st4["citizens"][0]["credits"] = 200
    r, _ = engine3.apply_action(st4, 0, "title", {})
    assert r
    engine3.apply_turn(st4)
    assert st4["citizens"][0]["titles"] == 1
    print("OK titles")

    # power ranking
    st5 = engine3.new_state(seed=5)
    st5["nations"][0]["army"] = 50
    st5["nations"][0]["treasury"] = 500
    rank = engine3.power_ranking(st5)
    assert rank[0] == 0
    print("OK power ranking")

    print()
    print("ALL ENGINE3 TESTS PASSED")


if __name__ == "__main__":
    test_v3_features()
    test_determinism()
    test_replay()
    test_tamper()
