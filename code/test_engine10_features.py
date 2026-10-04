"""Tests for engine10 v10 (secession + 65-nation cap).

Same bar as the season-4..9 suites: determinism, replay, tamper-detection,
plus a feature test per new mechanic. THE critical contract: with `v10` OFF,
engine10 must seal byte-identically to engine9 (back-compat so the live
season-5 seal chain stays reproducible on a shared codebase).

Run: python3 test_engine10_features.py
"""
import random

import engine10 as E
import engine9 as E9

SEED = 20260930
NAMES = [f"t{i:02d}" for i in range(20)]


def world(seed=SEED, season="season10-test", rich=(), **kw):
    """v10 test world. `rich` = citizen ids to fund to 500 credits so they can
    afford to secede (secede costs SECEDER_COST=60; fresh citizens start with 50)."""
    s = E.new_state(seed=seed, season=season, n_seats=20,
                   citizen_names=NAMES, v8=True, v9=True,
                   v10=True, **kw)
    for c in rich:
        s["citizens"][c]["credits"] = 500
    return s


def test_v10_off_is_v9():
    """v10-off: engine10 must seal identically to engine9 (back-compat)."""
    for seed in (99, 1234):
        s10 = E.new_state(seed=seed, season="cmp", n_seats=20, citizen_names=NAMES,
                          v8=True, v9=True)  # v10 off
        s9 = E9.new_state(seed=seed, season="cmp", n_seats=20, citizen_names=NAMES,
                          v8=True, v9=True)
        for _ in range(8):
            for cid in sorted(s10["citizens"]):
                if s10["pending"].get(cid):
                    continue
                rng = random.Random(f"cmp-{cid}-t{s10['turn']}")
                act, args = rng.choice(["work", "trade", "train"]), {}
                E.apply_action(s10, cid, act, args)
                E9.apply_action(s9, cid, act, args)
            E.apply_turn(s10)
            E9.apply_turn(s9)
        assert s10["seals"] == s9["seals"], \
            f"v10-off must be bit-identical to v9 (seed {seed})"
    print("ok: v10-off identical to v9 (8-turn shadow runs, seeds 99/1234)")


def test_secede_creates_nation():
    """secede carves a new nation out of an existing one; old nation stays."""
    s = world(rich=(1,))
    before = len(s["nations"])
    ok, _ = E.apply_action(s, 1, "secede", {"name": "Newfolk"})
    assert ok, "secede must be accepted (credits, room, unique name)"
    E.apply_turn(s)
    assert len(s["nations"]) == before + 1, "a new nation must appear"
    nid = max(s["nations"].keys())
    assert s["nations"][nid]["name"] == "Newfolk"
    assert s["citizens"][1]["country"] == nid, "seceder must move to the new nation"
    assert s["nations"][nid]["citizens"] == [1]
    assert 1 not in s["nations"][0]["citizens"]
    assert len(s["nations"][0]["citizens"]) == 3
    assert s["nations"][nid]["tiles"] == E.SECEDER_TILES
    # treasury is set to SECEDER_TREASURY at creation, then the daily economy
    # tick (in the same apply_turn) adjusts it by production/upkeep — so check
    # it's a sane non-negative int, not the pre-tick exact value.
    t = s["nations"][nid]["treasury"]
    assert isinstance(t, int) and t >= 0, f"new nation treasury must be sane: {t!r}"
    print("ok: secede (splits a nation, new nation starts fresh, old stays)")


def test_secede_leadership_passes():
    """If the seceder was the leader, leadership passes to the next citizen."""
    s = world(rich=(0,))
    assert s["nations"][0]["leader"] == 0
    ok, _ = E.apply_action(s, 0, "secede", {"name": "Ledown"})
    assert ok
    E.apply_turn(s)
    assert s["nations"][0]["leader"] != 0, "leadership must pass when the leader secedes"
    assert s["nations"][0]["leader"] in s["nations"][0]["citizens"]
    print("ok: secede leadership passes to next citizen")


def test_secede_guards():
    """secede is refused for: independent citizens, short/dup names, v10-off."""
    s = world(rich=(0, 1))
    s["citizens"][0]["independent"] = True
    ok, msg = E.apply_action(s, 0, "secede", {"name": "X1"})
    assert not ok, f"secede must refuse an independent citizen: {msg}"
    ok, msg = E.apply_action(s, 1, "secede", {"name": "X"})
    assert not ok, "short name must be refused"
    ok, msg = E.apply_action(s, 1, "secede", {"name": "Aurelia"})
    assert not ok, "duplicate nation name must be refused"
    s2 = E.new_state(seed=1, season="s2", n_seats=20, citizen_names=NAMES,
                     v8=True, v9=True)  # v10 off
    ok, msg = E.apply_action(s2, 1, "secede", {"name": "Nope"})
    assert not ok, f"v10-off must refuse secede: {msg}"
    print("ok: secede guards (independent / short / dup / v10-off all refused)")


def test_cap_65_in_v10():
    assert E._max_nations(world()) == E.V10_MAX_NATIONS == 65
    s9 = E9.new_state(seed=1, season="s9", n_seats=20, citizen_names=NAMES,
                      v8=True, v9=True)
    assert E._max_nations(s9) == E.MAX_NATIONS == 8, "v9-off cap must stay 8"
    print(f"ok: cap ({E.V10_MAX_NATIONS} in v10, {E.MAX_NATIONS} in v9)")


def _drive(s, secede_on=(0, 1, "Det1"), rich=()):
    """Run 8 turns on s, seceding the given (turn, cid, name) once; return the
    per-turn applied-log: a list of 8 flat action lists (exactly what
    apply_turn returns each turn: {citizen,name,action,raw_action,args}).
    A faithful replay re-applies each turn's actions then calls apply_turn."""
    turns = []
    if secede_on:
        s["citizens"][secede_on[1]]["credits"] = 500
    for t in range(8):
        for cid in sorted(s["citizens"]):
            if s["pending"].get(cid):
                continue
            rng = random.Random(f"drv-{cid}-t{s['turn']}")
            if secede_on and t == secede_on[0] and cid == secede_on[1]:
                act, args = "secede", {"name": secede_on[2]}
            else:
                act, args = rng.choice(["work", "trade", "train"]), {}
            E.apply_action(s, cid, act, args)
        turns.append(E.apply_turn(s))
    return turns


def _replay(turns, tamper_secede_to=None):
    """Rebuild a fresh world from the per-turn applied logs. If
    tamper_secede_to is set, swap any secede for that action (tamper test)."""
    r = world()
    if secede := next((l for tl in turns for l in tl
                       if l.get("raw_action") == "secede"), None):
        r["citizens"][secede["citizen"]]["credits"] = 500
    for turn_log in turns:
        for line in turn_log:
            act = line["raw_action"]
            if tamper_secede_to and act == "secede":
                act = tamper_secede_to
            E.apply_action(r, line["citizen"], act, line.get("args") or {})
        E.apply_turn(r)
    return r


def test_determinism():
    def run():
        s = world()
        _drive(s)
        return s["seals"]
    assert run() == run(), "determinism broken"
    print("ok: determinism (8 turns incl. a secede, identical seals)")


def test_replay():
    """Rebuild from seed+log with a secede in the log; seals must match."""
    s = world()
    turns = _drive(s)
    assert any(l.get("raw_action") == "secede" for tl in turns for l in tl), \
        "secede must be in the log"
    r = _replay(turns)
    assert r["seals"] == s["seals"], "replay seal mismatch"
    print("ok: replay from seed+log reproduces seal chain (secede in the log)")


def test_tamper():
    s = world()
    turns = _drive(s)
    before = s["seals"][-1]["sha"]
    r = _replay(turns, tamper_secede_to="work")
    assert r["seals"][-1]["sha"] != before, "tamper not detected"
    print("ok: tamper detection (swapping a secede for work breaks the seal)")


def main():
    test_v10_off_is_v9()
    test_secede_creates_nation()
    test_secede_leadership_passes()
    test_secede_guards()
    test_cap_65_in_v10()
    test_determinism()
    test_replay()
    test_tamper()
    print("\nALL engine10 TESTS GREEN (v10-off == v9, secede, cap 65, replay, tamper)")


if __name__ == "__main__":
    main()
