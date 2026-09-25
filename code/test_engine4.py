"""Tests for engine4 (candidate for season 3). Same bar as test_engine3:
determinism, replay, tamper-detection, plus v4 feature tests.

Run: python3 test_engine4.py
"""
import copy
import random

import engine4 as E


def _queue(st, turn):
    """Deterministic action queueing for the test world (one action/citizen/turn)."""
    d = turn // 2
    for cid in sorted(st["citizens"]):
        if st["pending"].get(cid):
            continue
        c = st["citizens"][cid]
        n = c["country"]
        rng = random.Random(f"test-{cid}-t{turn}")
        if c["independent"]:
            E.apply_action(st, cid, "work")
            continue
        nat = st["nations"][n]
        if E.is_election_turn(turn) and nat["citizens"]:
            cand = nat["citizens"][rng.randrange(len(nat["citizens"]))]
            E.apply_action(st, cid, "vote", {"candidate": cand})
            continue
        opts = ["work", "work", "work", "trade", "train", "research", "culture", "expand"]
        if nat["treasury"] >= 30:
            opts += ["infrastructure:factory", "infrastructure:barracks",
                     "infrastructure:mine", "infrastructure:university"]
        if rng.random() < 0.15 and c["credits"] >= 15:
            foes = [f for f in st["nations"] if f != n and not E._at_war(st, n, f)]
            if foes:
                opts.append("spy:" + str(rng.choice(foes)))
        if rng.random() < 0.05:
            foes = [f for f in st["nations"] if f != n and not E._at_war(st, n, f)
                    and not E._allied(st, n, f)]
            if foes:
                opts.append("declare_war:" + str(rng.choice(foes)))
        if any(E._at_war(st, n, f) for f in st["nations"]):
            foes = E._enemies(st, n)
            if foes and rng.random() < 0.5:
                opts.append("attack:" + str(rng.choice(foes)))
            if foes and rng.random() < 0.2:
                opts.append("peace:" + str(rng.choice(foes)))
        if rng.random() < 0.04 and c["credits"] >= 15:
            others = [f for f in st["nations"] if f != n and not E._at_war(st, n, f)]
            if others:
                opts.append("pact:" + str(rng.choice(others)))
        if rng.random() < 0.04 and nat["treasury"] >= 30:
            others = [f for f in st["nations"] if f != n and not E._at_war(st, n, f)]
            if others:
                opts.append("treaty:" + str(rng.choice(others)) + ":" + rng.choice(E.RESOURCES))
        if nat["leader"] == cid and rng.random() < 0.2:
            opts.append("set_government:" + rng.choice(list(E.GOVS)))
        if nat["leader"] == cid and rng.random() < 0.2:
            opts.append("set_tax:" + str(rng.randint(0, E.GOVS[nat["gov"]]["tax_cap"])))
        choice = rng.choice(opts)
        if ":" in choice:
            act, rest = choice.split(":", 1)
            if act in ("spy", "sabotage", "declare_war", "peace", "ally",
                       "attack", "pact"):
                E.apply_action(st, cid, act, {"target": int(rest)})
            elif act == "treaty":
                t, res = rest.split(":", 1)
                E.apply_action(st, cid, act, {"target": int(t), "resource": res})
            elif act == "infrastructure":
                E.apply_action(st, cid, act, {"building": rest})
            elif act == "set_government":
                E.apply_action(st, cid, act, {"government": rest})
            elif act == "set_tax":
                E.apply_action(st, cid, act, {"rate": int(rest)})
        else:
            E.apply_action(st, cid, choice, {})


def run_season(seed=777, n=80, names=None):
    st = E.new_state(seed, season="season4test", n_seats=20, citizen_names=names)
    log = []
    for turn in range(n):
        if st["winner"] is not None:
            break
        _queue(st, turn)
        E.apply_turn(st, log)
    return st, log


def test_determinism():
    a, la = run_season(seed=777)
    b, lb = run_season(seed=777)
    assert E.seal_hash(a) == E.seal_hash(b)
    assert a["seals"] == b["seals"], "seal chains differ"
    assert la == lb, "logs differ"
    print(f"determinism OK: {len(a['seals'])} seals, winner {a['winner']}")


def test_replay():
    st, log = run_season(seed=778)
    st2 = E.new_state(778, season="season4test", n_seats=20)
    by_turn = {}
    for entry in log:
        if entry.get("type") == "action":
            by_turn.setdefault(entry["turn"], []).append(entry)
    turn = 0
    lines = []
    while turn < st["turn"]:
        for entry in by_turn.get(turn, []):
            ok, _ = E.apply_action(st2, entry["citizen"], entry["raw_action"], entry.get("args") or {})
            assert ok, f"replay action {entry['raw_action']} rejected at t{turn}"
        E.apply_turn(st2, lines)
        turn += 1
    assert E.seal_hash(st2) == E.seal_hash(st), "replay diverged"
    assert st2["seals"] == st["seals"]
    print(f"replay OK: {turn} turns, final seal {E.seal_hash(st2)[:12]}")


def test_tamper():
    st, _ = run_season(seed=779, n=20)
    st2 = copy.deepcopy(st)
    st2["nations"][0]["treasury"] += 100
    assert E.seal_hash(st2) != st["seals"][-1]["sha"], "tamper not detected"
    print("tamper OK: state tamper breaks seal")


def test_governments():
    st = E.new_state(1, season="s", n_seats=10)
    nat = st["nations"][0]
    leader = nat["leader"]
    ok, _ = E.apply_action(st, leader, "set_government", {"government": "dictatorship"})
    assert ok, "leader should set government"
    ok, _ = E.apply_action(st, st["nations"][0]["citizens"][1],
                           "set_government", {"government": "oligarchy"})
    assert not ok, "non-leader cannot set government"
    log = []
    E.apply_turn(st, log)
    assert nat["gov"] == "dictatorship"
    # invalid gov rejected
    ok, _ = E.apply_action(st, leader, "set_government", {"government": "monarchy"})
    assert not ok
    # dictatorship: no elections — a swapped-in leader keeps power
    st2 = E.new_state(1, season="s", n_seats=10)
    st2["nations"][0]["gov"] = "dictatorship"
    st2["nations"][0]["leader"] = st2["nations"][0]["citizens"][1]
    st2["turn"] = 21  # day 10, odd turn -> election turn
    assert E.is_election_turn(21)
    E.apply_turn(st2, [])
    assert st2["nations"][0]["leader"] == st2["nations"][0]["citizens"][1], "dictator kept power"
    # democracy: election can change the leader
    st3 = E.new_state(1, season="s", n_seats=10)
    st3["nations"][0]["leader"] = st3["nations"][0]["citizens"][1]
    st3["turn"] = 21
    cand = st3["nations"][0]["citizens"][0]
    for c in st3["nations"][0]["citizens"]:
        ok, _ = E.apply_action(st3, c, "vote", {"candidate": cand})
        assert ok
    E.apply_turn(st3, [])
    assert st3["nations"][0]["leader"] == cand, "democratic vote changed leader"
    print("governments OK")


def test_taxation():
    st = E.new_state(1, season="s", n_seats=10)
    nat = st["nations"][0]
    leader = nat["leader"]
    worker = [c for c in nat["citizens"] if c != leader][0]
    E.apply_action(st, leader, "set_government", {"government": "dictatorship"})
    E.apply_turn(st, [])  # apply the gov change
    E.apply_action(st, leader, "set_tax", {"rate": 50})
    cr_before = st["citizens"][worker]["credits"]
    E.apply_action(st, worker, "work")
    E.apply_turn(st, [])
    gain = E.WORK_BASE + E.GOVS[nat["gov"]]["work_bonus"]  # dictatorship: base only = 6
    tax = gain * 50 // 100  # 3
    assert st["citizens"][worker]["credits"] - cr_before == gain - tax, f"citizen keeps gain-tax (got {st['citizens'][worker]['credits'] - cr_before}, want {gain - tax})"
    # rate above gov cap rejected; non-leader rejected
    ok, _ = E.apply_action(st, leader, "set_tax", {"rate": 101})
    assert not ok, "rate above gov cap must be rejected"
    ok, _ = E.apply_action(st, worker, "set_tax", {"rate": 5})
    assert not ok, "non-leader cannot set tax"
    print("taxation OK")


def test_war_and_tribute():
    st = E.new_state(1, season="s", n_seats=10)
    n0, n1 = st["nations"][0], st["nations"][1]
    n0["army"] = 50
    leader = n0["leader"]
    E.apply_action(st, leader, "declare_war", {"target": 1})
    E.apply_turn(st, [])
    assert E._at_war(st, 0, 1)
    E.apply_action(st, leader, "attack", {"target": 1})
    E.apply_turn(st, [])
    assert n0["tiles"] > E.START_TILES, "winner should take a tile"
    # drain n1: 0 army, 1 tile -> next resolve loses it -> conquered + tribute
    n1["army"] = 0
    n1["tiles"] = 1
    n1["treasury"] = 100
    E.apply_turn(st, [])
    assert "1" not in st["nations"], "nation should be conquered"
    strongest = E.power_ranking(st)[0]
    assert st["nations"][strongest]["treasury"] >= 25, "tribute should have moved"
    print(f"war+tribute OK: conqueror {st['nations'][strongest]['name']} treasury {st['nations'][strongest]['treasury']}")


def test_pact_and_betrayal():
    st = E.new_state(1, season="s", n_seats=10)
    n0 = st["nations"][0]
    leader = n0["leader"]
    E.apply_action(st, leader, "pact", {"target": 1})
    E.apply_turn(st, [])
    assert any(sorted((p[0], p[1])) == [0, 1] for p in st["pacts"]), "pact not created"
    # betrayal: declare war through the live pact -> fine + pact dies
    E.apply_action(st, leader, "declare_war", {"target": 1})
    treas_before = st["nations"][0]["treasury"]
    E.apply_turn(st, [])
    assert E._at_war(st, 0, 1)
    assert not any(sorted((p[0], p[1])) == [0, 1] for p in st["pacts"]), "pact should be dead"
    assert st["nations"][0]["treasury"] <= treas_before + 2 * n0["tiles"] + n0["culture"] - E.BETRAYAL_FINE + 5
    # expiry: pact lasts 10 days
    st2 = E.new_state(1, season="s", n_seats=10)
    E.apply_action(st2, st2["nations"][0]["leader"], "pact", {"target": 1})
    for _ in range(22):
        E.apply_turn(st2, [])
        if st2["winner"]:
            break
    assert not any(sorted((p[0], p[1])) == [0, 1] for p in st2["pacts"]), "pact should expire after 10 days"
    print("pact+betrayal OK")


def test_treaty():
    st = E.new_state(1, season="s", n_seats=10)
    leader = st["nations"][0]["leader"]
    E.apply_action(st, leader, "treaty", {"target": 1, "resource": "grain"})
    g0 = st["nations"][0]["stock"]["grain"]
    g1 = st["nations"][1]["stock"]["grain"]
    E.apply_turn(st, [])  # t0 (day 0: no delivery yet — treaty just signed)
    E.apply_turn(st, [])  # t1 (day 0, 2nd turn)
    E.apply_turn(st, [])  # t2 = new day -> daily delivery
    assert st["nations"][0]["stock"]["grain"] > g0, "treaty should deliver grain to n0"
    assert st["nations"][1]["stock"]["grain"] > g1, "treaty should deliver grain to n1"
    # cap: 2 treaties per nation
    E.apply_action(st, leader, "treaty", {"target": 2, "resource": "iron"})
    E.apply_turn(st, [])
    ok, _ = E.apply_action(st, leader, "treaty", {"target": 3, "resource": "oil"})
    assert not ok, "3rd treaty must be rejected (cap 2)"
    print("treaty OK")


def test_backwards_compat_actions():
    """All v3 actions still queue correctly in v4."""
    st = E.new_state(1, season="s", n_seats=10)
    c = 0  # citizen id
    for act, args in [("work", {}), ("trade", {}), ("culture", {}),
                      ("research", {}), ("train", {}), ("expand", {}),
                      ("ally", {"target": 1}), ("title", {}),
                      ("market_buy", {"resource": "wood"}),
                      ("spies", {}), ("set_policy", {"policy": "merchant"})]:
        ok, _ = E.apply_action(st, c, act, args)
        assert ok, f"{act} should queue"
        st["pending"].pop(c, None)
    ok, _ = E.apply_action(st, c, "teleport", {})
    assert not ok
    print("backwards-compat OK")


if __name__ == "__main__":
    test_backwards_compat_actions()
    test_determinism()
    test_replay()
    test_tamper()
    test_governments()
    test_taxation()
    test_war_and_tribute()
    test_pact_and_betrayal()
    test_treaty()
    print("\nALL ENGINE4 TESTS GREEN")
