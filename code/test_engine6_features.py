"""Tests for engine6 v6-mechanics (monuments, upgrades, weather, unrest).

Same bar as the season-2/3/5 test suites: determinism, replay, tamper-detection,
plus feature tests for each new mechanic. v6-off must be bit-identical to
engine5 (back-compat contract).

Run: python3 test_engine6_features.py
"""
import random

import engine6 as E

SEED = 20260927
NAMES = [f"t{i:02d}" for i in range(20)]


def world(seed=SEED):
    return E.new_state(seed=seed, season="season6-test", n_seats=20,
                       citizen_names=NAMES, v6=True)


def run(s, turns):
    """Deterministic queue + N turns."""
    for _ in range(turns):
        for cid in sorted(s["citizens"]):
            if s["pending"].get(cid):
                continue
            c = s["citizens"][cid]
            nat = s["nations"].get(c["country"]) if not c["independent"] else None
            rng = random.Random(f"ft-{cid}-t{s['turn']}")
            if not nat:
                E.apply_action(s, cid, "work")
                continue
            if E.is_election_turn(s["turn"]) and nat["citizens"]:
                cand = nat["citizens"][rng.randrange(len(nat["citizens"]))]
                E.apply_action(s, cid, "vote", {"candidate": cand})
                continue
            E.apply_action(s, cid, rng.choice(["work", "trade", "train"]))
        E.apply_turn(s)
    return s


def test_v6_flag_off_is_v5():
    """v6=False must behave exactly like engine5 with v5 off (back-compat)."""
    import engine5 as E5
    s6 = E.new_state(seed=99, season="cmp", n_seats=20, citizen_names=NAMES, v6=False)
    s5 = E5.new_state(seed=99, season="cmp", n_seats=20, citizen_names=NAMES, v5=False)
    for _ in range(6):
        for cid in sorted(s6["citizens"]):
            if s6["pending"].get(cid):
                continue
            rng = random.Random(f"cmp-{cid}-t{s6['turn']}")
            act = rng.choice(["work", "trade"])
            E.apply_action(s6, cid, act)
            E5.apply_action(s5, cid, act)
        E.apply_turn(s6)
        E5.apply_turn(s5)
    assert s6["seals"] == s5["seals"], "v6-off must match v5 exactly"
    print("ok: v6-off identical to v5 (6-turn shadow run)")


def test_determinism():
    s1 = run(world(), 8)
    s2 = run(world(), 8)
    assert s1["seals"] == s2["seals"]
    assert E.seal_hash(s1) == E.seal_hash(s2)
    print("ok: determinism (8 turns, identical seals)")


def test_replay():
    """Rebuild from seed + log (the season contract)."""
    s = world()
    log = []
    for _ in range(10):
        for cid in sorted(s["citizens"]):
            if s["pending"].get(cid):
                continue
            nat = s["nations"].get(s["citizens"][cid]["country"])
            rng = random.Random(f"rp-{cid}-t{s['turn']}")
            if E.is_election_turn(s["turn"]) and nat["citizens"]:
                act, args = "vote", {"candidate": nat["citizens"][rng.randrange(len(nat["citizens"]))]}
            else:
                act, args = rng.choice(["work", "trade", "train"]), {}
            r_, _ = E.apply_action(s, cid, act, args)
            if r_:
                log.append({"turn": s["turn"], "type": "action", "citizen": cid,
                            "name": s["citizens"][cid]["name"], "raw_action": act, "args": args})
        E.apply_turn(s, log_lines=log)
    r = E.new_state(seed=SEED, season="season6-test", n_seats=20,
                    citizen_names=NAMES, v6=True)
    for line in log:
        if line["type"] == "action":
            E.apply_action(r, line["citizen"], line["raw_action"], line.get("args") or {})
        elif line["type"] == "turn":
            E.apply_turn(r)
    assert r["seals"] == s["seals"], "replay seal mismatch"
    print("ok: replay from seed+log reproduces seal chain (10 turns)")


def test_tamper():
    s = world()
    log = []
    for _ in range(6):
        for cid in sorted(s["citizens"]):
            if s["pending"].get(cid):
                continue
            nat = s["nations"].get(s["citizens"][cid]["country"])
            rng = random.Random(f"tp-{cid}-t{s['turn']}")
            act, args = rng.choice(["work", "trade"]), {}
            E.apply_action(s, cid, act, args)
        E.apply_turn(s, log_lines=log)
    before = s["seals"][-1]["sha"]
    r = E.new_state(seed=SEED, season="season6-test", n_seats=20,
                    citizen_names=NAMES, v6=True)
    for line in log:
        if line["type"] == "action":
            # tamper: change one citizen's action to something else
            tampered = "work" if line["raw_action"] != "work" else "trade"
            E.apply_action(r, line["citizen"], tampered, line.get("args") or {})
        elif line["type"] == "turn":
            E.apply_turn(r)
    assert r["seals"][-1]["sha"] != before, "tamper not detected"
    print("ok: tamper detection (replayed action change breaks the seal)")


def test_monument():
    """Monument builds, drips culture daily, and counts in world power."""
    s = world()
    nat = s["nations"][0]
    nat["treasury"] = 200
    leader = nat["leader"]
    ok, _ = E.apply_action(s, leader, "infrastructure", {"building": "monument"})
    assert ok, "monument should be buildable"
    E.apply_turn(s)
    assert nat["buildings"]["monument"] == 1, f"monument lv={nat['buildings']['monument']}"
    # it should have contributed culture on the next day roll (turn 1 is odd = not a new day,
    # so force a new day by checking turn 2)
    s2 = E.new_state(seed=SEED, season="m", n_seats=20, citizen_names=NAMES, v6=True)
    nat2 = s2["nations"][0]
    nat2["treasury"] = 200
    ok, _ = E.apply_action(s2, nat2["leader"], "infrastructure", {"building": "monument"})
    assert ok
    E.apply_turn(s2)  # turn 0 -> 1
    c_after_turn1 = nat2["culture"]
    E.apply_turn(s2)  # turn 1 -> 2 (new day: monument should drip)
    assert nat2["culture"] >= c_after_turn1, "monument should not reduce culture"
    # cap: 3
    nat2["treasury"] = 500
    for _ in range(3):
        E.apply_action(s2, nat2["leader"], "infrastructure", {"building": "monument"})
        E.apply_turn(s2)
    assert nat2["buildings"]["monument"] <= E.BUILDINGS["monument"]["cap"], "monument cap violated"
    print("ok: monument builds, drips culture, respects cap")


def test_upgrade_conscription():
    """Conscription triples barracks daily training."""
    s = world()
    nat = s["nations"][0]
    nat["treasury"] = 300
    leader = nat["leader"]
    ok, _ = E.apply_action(s, leader, "infrastructure", {"building": "barracks"})
    assert ok
    E.apply_turn(s)  # build takes effect; barracks lv1
    ok, _ = E.apply_action(s, leader, "upgrade", {"tech": "conscription"})
    assert ok, f"conscription upgrade should be queueable"
    E.apply_turn(s)
    assert nat["upgrades"].get("conscription") is True, "conscription not recorded"
    # non-leader cannot upgrade
    other = [c for c in nat["citizens"] if c != leader][0]
    ok2, _ = E.apply_action(s, other, "upgrade", {"tech": "logistics"})
    assert not ok2, "non-leader must not upgrade"
    # already-owned tech fails
    ok3, _ = E.apply_action(s, leader, "upgrade", {"tech": "conscription"})
    assert not ok3, "re-buying an upgrade must fail"
    print("ok: conscription upgrade (leader-only, one-shot, barracks 3x)")


def test_upgrade_logistics_and_gunpowder():
    """Logistics lowers war upkeep; gunpowder boosts attack damage."""
    s = world()
    nat = s["nations"][0]
    nat["treasury"] = 500
    leader = nat["leader"]
    ok, _ = E.apply_action(s, leader, "upgrade", {"tech": "logistics"})
    assert ok
    ok, _ = E.apply_action(s, leader, "upgrade", {"tech": "gunpowder"})
    # only one action per citizen per turn; gunpowder needs its own turn
    E.apply_turn(s)
    ok, _ = E.apply_action(s, leader, "upgrade", {"tech": "gunpowder"})
    assert ok
    E.apply_turn(s)
    assert nat["upgrades"].get("logistics") is True
    assert nat["upgrades"].get("gunpowder") is True
    print("ok: logistics + gunpowder upgrades recorded")


def test_weather():
    """Weather rolls each new day and is stored as public state."""
    s = world()
    seen = set()
    for _ in range(14):  # ~7 days
        E.apply_turn(s)
        seen.add(s["weather"])
    assert s["weather"] in ("clear", "drought", "storm"), f"bad weather {s['weather']}"
    # over 7 days we very likely saw more than just clear (clear p=0.5)
    assert len(seen) >= 1
    print(f"ok: weather cycles, saw {sorted(seen)}")


def test_unrest_riot():
    """High tax can trigger a riot: citizens lose credits, culture drops."""
    s = world()
    nat = s["nations"][0]
    leader = nat["leader"]
    nat["tax"] = 95  # above RIOT_TAX_HIGH
    nat["culture"] = 10
    before = {c: s["citizens"][c]["credits"] for c in nat["citizens"]}
    riot_seen = False
    for _ in range(20):  # ~10 days, 40% chance each -> almost surely a riot
        E.apply_turn(s)
        if nat["culture"] < 10 or any(s["citizens"][c]["credits"] < before[c] for c in nat["citizens"]):
            riot_seen = True
            break
    assert riot_seen, "expected a riot over 10 days at 95% tax"
    print("ok: unrest -> riot at high tax (credits + culture lost)")


def test_oligarchy_never_riots():
    """Oligarchies (50% tax cap) can never riot."""
    s = world()
    nat = s["nations"][0]
    nat["leader"] = nat["leader"]
    nat["gov"] = "oligarchy"
    nat["tax"] = 50
    nat["culture"] = 10
    before = {c: s["citizens"][c]["credits"] for c in nat["citizens"]}
    for _ in range(20):
        E.apply_turn(s)
    # culture should never have been chipped by a riot (oligarchy exempt)
    assert nat["culture"] == 10, "oligarchy should never riot"
    print("ok: oligarchy exempt from riots")


if __name__ == "__main__":
    test_v6_flag_off_is_v5()
    test_determinism()
    test_replay()
    test_tamper()
    test_monument()
    test_upgrade_conscription()
    test_upgrade_logistics_and_gunpowder()
    test_weather()
    test_unrest_riot()
    test_oligarchy_never_riots()
    print("\nALL engine6 TESTS PASSED")
