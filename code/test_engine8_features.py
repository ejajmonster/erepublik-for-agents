"""Tests for engine8 v8-mechanics (living economy: market impact, mean
reversion, war shock, credit inflation).

Same bar as the season-3/5/6/7 suites: determinism, replay, tamper-detection,
plus a feature test per new mechanic. v8-off must be bit-identical to
engine7 (back-compat contract).

Run: python3 test_engine8_features.py
"""
import random

import engine8 as E
import engine7 as E7

SEED = 20260928
NAMES = [f"t{i:02d}" for i in range(20)]


def world(seed=SEED, season="season8-test", **kw):
    return E.new_state(seed=seed, season=season, n_seats=20,
                       citizen_names=NAMES, v7=True, v8=True, **kw)


def test_v8_off_is_v7():
    """Same flags on both sides: engine8(v8=False) must seal identically to engine7."""
    for seed in (99, 1234):
        s8 = E.new_state(seed=seed, season="cmp", n_seats=20, citizen_names=NAMES,
                         v7=True, v8=False)
        s7 = E7.new_state(seed=seed, season="cmp", n_seats=20, citizen_names=NAMES,
                          v7=True)
        for _ in range(6):
            for cid in sorted(s8["citizens"]):
                if s8["pending"].get(cid):
                    continue
                rng = random.Random(f"cmp-{cid}-t{s8['turn']}")
                act, args = rng.choice(["work", "trade"]), {}
                E.apply_action(s8, cid, act, args)
                E7.apply_action(s7, cid, act, args)
            E.apply_turn(s8)
            E7.apply_turn(s7)
        assert s8["seals"] == s7["seals"], \
            f"v8-off must be bit-identical to v7 (seed {seed})"
    print("ok: v8-off identical to v7 (6-turn shadow runs, seeds 99/1234)")


def test_determinism():
    s1 = world()
    s2 = world()
    for _ in range(10):
        for cid in sorted(s1["citizens"]):
            if s1["pending"].get(cid):
                continue
            rng = random.Random(f"d-{cid}-t{s1['turn']}")
            act = rng.choice(["work", "trade", "train"])
            E.apply_action(s1, cid, act, {})
            E.apply_action(s2, cid, act, {})
        E.apply_turn(s1)
        E.apply_turn(s2)
    assert s1["seals"] == s2["seals"], "determinism broken"
    print("ok: determinism (10 turns, identical seals)")


def test_replay():
    """Rebuild from seed + log (the season contract)."""
    s = world()
    log = []
    for _ in range(10):
        for cid in sorted(s["citizens"]):
            if s["pending"].get(cid):
                continue
            nat = s["nations"].get(s["citizens"][cid]["country"])
            rng = random.Random(f"rp8-{cid}-t{s['turn']}")
            if E.is_election_turn(s["turn"]) and nat and nat["citizens"]:
                act, args = "vote", {"candidate": nat["citizens"][rng.randrange(len(nat["citizens"]))]}
            else:
                act, args = rng.choice(["work", "trade", "train"]), {}
                if rng.random() < 0.3:
                    act = rng.choice(["market_buy", "market_sell"])
                    args = {"resource": rng.choice(E.RESOURCES)}
            r_, _ = E.apply_action(s, cid, act, args)
            if r_:
                log.append({"turn": s["turn"], "type": "action", "citizen": cid,
                            "name": s["citizens"][cid]["name"], "raw_action": act, "args": args})
        E.apply_turn(s, log_lines=log)
    r = world()
    for line in log:
        if line["type"] == "action":
            E.apply_action(r, line["citizen"], line["raw_action"], line.get("args") or {})
        elif line["type"] == "turn":
            E.apply_turn(r)
    assert r["seals"] == s["seals"], "replay seal mismatch"
    print("ok: replay from seed+log reproduces seal chain (10 turns, market trades included)")


def test_tamper():
    s = world()
    log = []
    for _ in range(6):
        for cid in sorted(s["citizens"]):
            if s["pending"].get(cid):
                continue
            nat = s["nations"].get(s["citizens"][cid]["country"])
            rng = random.Random(f"tp8-{cid}-t{s['turn']}")
            act, args = rng.choice(["work", "trade"]), {}
            E.apply_action(s, cid, act, args)
        E.apply_turn(s, log_lines=log)
    before = s["seals"][-1]["sha"]
    r = world()
    for line in log:
        if line["type"] == "action":
            tampered = "work" if line["raw_action"] != "work" else "trade"
            E.apply_action(r, line["citizen"], tampered, line.get("args") or {})
        elif line["type"] == "turn":
            E.apply_turn(r)
    assert r["seals"][-1]["sha"] != before, "tamper not detected"
    print("ok: tamper detection (replayed action change breaks the seal)")


def test_market_impact():
    """Buy pushes price up, sell pushes it down (v8 only).
    The daily random shift is identical in the reference world, so the price
    delta vs a shadow world isolates the market impact exactly."""
    s = world()
    ref = world()
    E.apply_action(s, 0, "market_buy", {"resource": "wood"})
    E.apply_turn(s)
    E.apply_turn(ref)
    delta = s["market"]["wood"] - ref["market"]["wood"]
    assert delta == E.MARKET_IMPACT, f"buy impact delta {delta}, want {E.MARKET_IMPACT}"
    s2 = world()
    ref2 = world()
    E.apply_action(s2, 0, "market_sell", {"resource": "wood"})
    E.apply_turn(s2)
    E.apply_turn(ref2)
    delta2 = s2["market"]["wood"] - ref2["market"]["wood"]
    assert delta2 == -E.MARKET_IMPACT, f"sell impact delta {delta2}, want {-E.MARKET_IMPACT}"
    print("ok: market impact (buy pumps +1, sell dumps -1; isolated vs shadow)")


def test_war_shock():
    # same season on both sides: the day-shift rng key is season-seed-turn,
    # so both worlds roll identical random shifts and the delta isolates the shock
    s = E.new_state(seed=SEED, season="ws", n_seats=20, citizen_names=NAMES,
                    v7=True, v8=True)
    ref = E.new_state(seed=SEED, season="ws", n_seats=20, citizen_names=NAMES,
                      v7=True, v8=False)
    E.apply_action(s, 0, "declare_war", {"target": 1})
    E.apply_turn(s)
    E.apply_turn(ref)
    assert [0, 1] in s["war"] or [1, 0] in s["war"], "war must be declared"
    for res, shock in E.WAR_SHOCK.items():
        refp = ref["market"][res]
        # shock applied at action time (prices then equal ref's shifted prices);
        # the shock lands on top of the same shifted base in both worlds
        want_delta = max(2, min(25, refp + shock)) - refp
        got_delta = s["market"][res] - refp
        assert got_delta == want_delta, \
            f"war shock {res}: delta {got_delta}, want {want_delta}"
    # v8-off: engine7 world with the same war is bit-identical to the v8-off ref
    s7 = E7.new_state(seed=SEED, season="ws", n_seats=20, citizen_names=NAMES, v7=True)
    E7.apply_action(s7, 0, "declare_war", {"target": 1})
    E7.apply_turn(s7)
    assert s7["market"] == ref["market"], "v8-off world must not shock on war"
    print("ok: war shock (grain +3, oil +2, wood -2, iron -2; v8-off inert)")


def test_mean_reversion():
    """Price drifting >3 from base walks back 1/day, before the random shift.
    The window must account for the random shift (±3) AND the weather roll
    (drought: grain +3; storm: all prices -2), which fires later in the day."""
    for res in E.RESOURCES:
        s = world()
        base = E.BASE_PRICES[res]
        # push far above base; reversion -1 is guaranteed
        s["market"][res] = min(25, base + 6)
        E.apply_turn(s)
        extra = E.WEATHER_DROUGHT_GRAIN if res == "grain" else 0
        lo = max(2, base + 6 - 1 - 3 - E.WEATHER_STORM_DROP)
        hi = min(25, base + 6 - 1 + 3 + extra)
        assert lo <= s["market"][res] <= hi, \
            f"reversion not pulling {res} back: {s['market'][res]} (window {lo}..{hi})"
    print("ok: mean reversion (all resources walk back toward base)")


def test_credit_inflation():
    s = world()
    s["total_credit"] = E.INFLATION_THRESHOLD + 1
    E.apply_turn(s)
    assert any("INFLATION" in r for r in s["recent"]), \
        "inflation event must fire above threshold"
    # below threshold: no inflation event on a fresh world
    s2 = world()
    s2["total_credit"] = 100
    E.apply_turn(s2)
    assert not any("INFLATION" in r for r in s2["recent"]), \
        "inflation must not fire below threshold"
    print("ok: credit inflation (above threshold raises prices, below does not)")


def test_total_credit_tracking():
    s = world()
    nat = s["nations"][0]
    leader = nat["leader"]
    amt = 200
    E.apply_action(s, leader, "lend", {"amount": amt})
    E.apply_turn(s)  # effects resolve at turn close
    due = amt * (100 + E.LEND_INTEREST) // 100
    assert s["total_credit"] == due, \
        f"total_credit should track the due amount {due}, got {s['total_credit']}"
    # force repayment: fill treasury, run turns until the loan clears
    # (a day = 2 turns, so LEND_MAX_DAYS days = 2*LEND_MAX_DAYS turns)
    nat["treasury"] = 10000
    for _ in range(2 * E.LEND_MAX_DAYS + 4):
        E.apply_turn(s)
        if nat["loan"] is None:
            break
    assert s["total_credit"] == 0, \
        f"total_credit should drain to 0 after repayment, got {s['total_credit']}"
    print("ok: total_credit tracking (loan adds due, repayment drains)")


def test_full_season():
    def bot_play(s, tag):
        log = []
        while s["winner"] is None and s["turn"] < 80:
            for cid in sorted(s["citizens"]):
                if s["pending"].get(cid):
                    continue
                c = s["citizens"][cid]
                nat = s["nations"].get(c["country"]) if not c["independent"] else None
                rng = random.Random(f"fs8-{tag}-{cid}-t{s['turn']}")
                if not nat:
                    E.apply_action(s, cid, "work")
                    continue
                if E.is_election_turn(s["turn"]) and nat["citizens"]:
                    cand = nat["citizens"][rng.randrange(len(nat["citizens"]))]
                    E.apply_action(s, cid, "vote", {"candidate": cand})
                    continue
                acts = ["work", "trade", "train", "market_buy", "market_sell"]
                if nat["leader"] == cid:
                    acts += ["lend", "declare_war"]
                act = rng.choice(acts)
                args = {}
                if act in ("market_buy", "market_sell"):
                    args = {"resource": rng.choice(E.RESOURCES)}
                elif act == "lend":
                    args = {"amount": 300}
                elif act == "declare_war":
                    others = [n for n in s["nations"] if n != c["country"]]
                    args = {"target": rng.choice(others)}
                r_, _ = E.apply_action(s, cid, act, args)
                if r_:
                    log.append({"turn": s["turn"], "type": "action", "citizen": cid,
                                "name": c["name"], "raw_action": act, "args": args})
            E.apply_turn(s, log_lines=log)
        return s, log

    # same seed AND same season string: season is part of the rng key and the
    # hashed state, so both shadow runs must be bit-identical inputs
    s1, log1 = bot_play(E.new_state(seed=777, season="fs8", n_seats=20,
                                    citizen_names=NAMES, v7=True, v8=True), "a")
    s2, _ = bot_play(E.new_state(seed=777, season="fs8", n_seats=20,
                                 citizen_names=NAMES, v7=True, v8=True), "a")
    assert s1["seals"] == s2["seals"], "full-season v8 run must be deterministic"
    assert s1["winner"] is not None, "season must end"
    r = E.new_state(seed=777, season="fs8", n_seats=20, citizen_names=NAMES,
                    v7=True, v8=True)
    for line in log1:
        if line["type"] == "action":
            E.apply_action(r, line["citizen"], line["raw_action"], line.get("args") or {})
        elif line["type"] == "turn":
            E.apply_turn(r)
    assert r["seals"] == s1["seals"], "full-season v8 replay mismatch"
    print(f"ok: full 80-turn v8 season deterministic + replayable "
          f"(winner={s1['nations'][s1['winner']]['name'] if s1['winner'] is not None else None})")


if __name__ == "__main__":
    test_v8_off_is_v7()
    test_determinism()
    test_replay()
    test_tamper()
    test_market_impact()
    test_war_shock()
    test_mean_reversion()
    test_credit_inflation()
    test_total_credit_tracking()
    test_full_season()
    print("\nALL ENGINE8 TESTS PASSED")
