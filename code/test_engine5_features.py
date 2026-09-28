"""Tests for engine5 v5-mechanics (banks, loans, aqueducts, observatories,
festivals, mobilization, embargoes, heroes).

Same bar as the season-2/3 test suites: determinism, replay, tamper-detection,
plus feature tests for each new mechanic.

Run: python3 test_engine5_features.py
"""
import copy
import random

import engine5 as E

SEED = 20260927
NAMES = [f"t{i:02d}" for i in range(20)]


def world(seed=SEED):
    return E.new_state(seed=seed, season="season5-test", n_seats=20,
                       citizen_names=NAMES, v5=True)


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


def test_v5_flag_off_is_v4():
    """v5=False must behave exactly like engine4 (back-compat)."""
    import engine4 as E4
    s5 = E.new_state(seed=99, season="cmp", n_seats=20, citizen_names=NAMES, v5=False)
    s4 = E4.new_state(seed=99, season="cmp", n_seats=20, citizen_names=NAMES)
    for _ in range(6):
        for cid in sorted(s5["citizens"]):
            if s5["pending"].get(cid):
                continue
            rng = random.Random(f"cmp-{cid}-t{s5['turn']}")
            act = rng.choice(["work", "trade"])
            E.apply_action(s5, cid, act)
            E4.apply_action(s4, cid, act)
        a5 = E.apply_turn(s5)
        a4 = E4.apply_turn(s4)
    assert s5["seals"] == s4["seals"], "v5-off must match v4 exactly"
    print("ok: v5-off identical to v4 (6-turn shadow run)")


def test_determinism():
    s1 = run(world(), 8)
    s2 = run(world(), 8)
    assert s1["seals"] == s2["seals"]
    assert E.seal_hash(s1) == E.seal_hash(s2)
    print("ok: determinism (8 turns, identical seals)")


def test_replay():
    """Rebuild from seed + log (the season-2/3 contract: actions logged by the
    client, turn lines appended by apply_turn)."""
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
    # replay from seed + log
    r = E.new_state(seed=SEED, season="season5-test", n_seats=20,
                    citizen_names=NAMES, v5=True)
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
    for _ in range(5):
        for cid in sorted(s["citizens"]):
            if s["pending"].get(cid):
                continue
            E.apply_action(s, cid, "work")
            log.append({"turn": s["turn"], "type": "action", "citizen": cid,
                        "name": "x", "raw_action": "work", "args": {}})
        E.apply_turn(s, log_lines=log)
    r = E.new_state(seed=SEED, season="season5-test", n_seats=20,
                    citizen_names=NAMES, v5=True)
    cur = 0
    for i, line in enumerate(log):
        if line["type"] == "action" and line["turn"] == cur:
            if i == 10:  # tamper: replay this entry as a DIFFERENT action (train, not work)
                E.apply_action(r, line["citizen"], "train")
                continue
            E.apply_action(r, line["citizen"], line["raw_action"], line.get("args") or {})
        elif line["type"] == "turn":
            E.apply_turn(r)
            cur += 1
    assert r["seals"] != s["seals"], "tampered replay must diverge"
    print("ok: tamper detection (diverged seals)")


def test_bank():
    s = world()
    nat = s["nations"][0]
    nat["treasury"] = 500
    ok, _ = E.apply_action(s, 0, "bank", {"amount": 300})
    assert ok
    tr0 = nat["treasury"]
    E.apply_turn(s)   # turn 0 (new day, economy ran first): deposit resolves
    assert nat["account"] == 300, nat["account"]
    assert nat["treasury"] < tr0, "deposit must reduce the treasury"
    E.apply_turn(s)   # turn 1: nothing yet
    E.apply_turn(s)   # turn 2 (new day): +5% interest -> 315
    assert nat["account"] == 315, nat["account"]
    # explicit withdrawal by the leader, capped at 100/day
    ok, _ = E.apply_action(s, nat["leader"], "withdraw", {"amount": 100})
    assert ok
    E.apply_turn(s)
    assert nat["account"] == 215, nat["account"]  # turn 3 is not a new day: no interest
    print("ok: bank deposit / interest / explicit withdraw (300 -> 315 -> 215)")


def test_loan():
    s = world()
    nat = s["nations"][0]
    tr0 = nat["treasury"]
    ok, _ = E.apply_action(s, 0, "lend", {"amount": 200})
    assert ok
    E.apply_turn(s)  # loan resolves (economy ran before it, no repayment yet)
    assert nat["treasury"] >= tr0 + 200, (nat["treasury"], tr0)  # +200 loan, end-of-turn income adds more
    due = 200 * 110 // 100  # 220
    assert nat["loan"][0] == due and nat["loan"][1] == 0, nat["loan"]
    # healthy treasury: the installment schedule pays it off within the window
    for _ in range(E.LEND_MAX_DAYS * 2):
        for cid in sorted(s["citizens"]):
            if s["pending"].get(cid):
                continue
            E.apply_action(s, cid, "work")
        E.apply_turn(s)
        if nat["loan"] is None:
            break
    assert nat["loan"] is None, nat["loan"]
    assert not nat["loan_defaulted"]
    print("ok: loan auto-repay in installments (200 -> 220 due, paid within window)")


def test_loan_default():
    s = world()
    nat = s["nations"][0]
    ok, _ = E.apply_action(s, 0, "lend", {"amount": 1000})
    assert ok
    E.apply_turn(s)
    assert nat["loan"] is not None, nat["loan"]
    army0, cult0 = None, None
    # starve the treasury every day so the installments can't pay
    for _ in range((E.LEND_MAX_DAYS + 2) * 2):
        nat["treasury"] = 0  # creditors' leverage: no money to service
        for cid in sorted(s["citizens"]):
            if s["pending"].get(cid):
                continue
            E.apply_action(s, cid, "work")
        E.apply_turn(s)
        if army0 is None and nat["loan_defaulted"]:
            army0, cult0 = nat["army"] + E.LEND_DEFAULT_ARMY, nat["culture"] + E.LEND_DEFAULT_CULTURE
    assert nat["loan_defaulted"], "starved loan must default"
    assert nat["army"] < army0 or army0 is None
    print("ok: loan default after %d days (raid once, debt keeps draining)" % E.LEND_MAX_DAYS)


def test_aqueduct_observatory():
    s = world()
    nat = s["nations"][0]
    nat["treasury"] = 100
    grain0 = nat["stock"]["grain"]
    tech0 = nat["tech"]
    ok, _ = E.apply_action(s, 0, "infrastructure", {"building": "aqueduct"})
    assert ok, "aqueduct should build without resources"
    ok, _ = E.apply_action(s, 1, "infrastructure", {"building": "observatory"})
    assert ok
    E.apply_turn(s)
    assert nat["buildings"]["aqueduct"] == 1 and nat["buildings"]["observatory"] == 1
    # after 2 full days
    for _ in range(4):
        for cid in sorted(s["citizens"]):
            if s["pending"].get(cid):
                continue
            E.apply_action(s, cid, "work")
        E.apply_turn(s)
    assert nat["stock"]["grain"] >= grain0 + 2, (nat["stock"]["grain"], grain0)
    assert nat["tech"] >= tech0 + 2
    print("ok: aqueduct +1 grain/day, observatory +1 tech/day")


def test_festival():
    s = world()
    cit, nat = s["citizens"][0], s["nations"][0]
    nat["tax"] = 0
    c0, cult0, cr0 = cit["credits"], nat["culture"], 0
    ok, _ = E.apply_action(s, 0, "festival")
    assert ok
    E.apply_turn(s)
    assert nat["culture"] == cult0 + E.FESTIVAL_CULTURE
    print("ok: festival +1 culture")


def test_mobilize():
    s = world()
    nat = s["nations"][0]
    nat["treasury"] = 90
    nat["army"] = 0
    ok, _ = E.apply_action(s, 0, "mobilize")
    assert ok, "leader mobilize"
    E.apply_turn(s)
    assert nat["army"] == 12, nat["army"]  # 90/15*2
    assert nat["treasury"] >= 0, nat["treasury"]  # 0 after spend; end-of-turn tile income may add back
    print("ok: mobilize 90 treasury -> +12 army")


def test_mobilize_nonleader():
    s = world()
    ok, msg = E.apply_action(s, 1, "mobilize")
    assert not ok
    print("ok: mobilize rejected for non-leader")


def test_embargo():
    s = world()
    nat = s["nations"][0]
    nat["treasury"] = 100
    p0 = s["market"]["iron"]
    ok, _ = E.apply_action(s, 0, "embargo", {"resource": "iron"})
    assert ok
    E.apply_turn(s)  # turn 0 resolves; embargo queued, active from day 1
    assert s["embargoes"].get("iron"), s["embargoes"]
    E.apply_turn(s)  # turn 1
    start_of_day2 = s["market"]["iron"]  # price entering turn 2's day
    E.apply_turn(s)  # turn 2 (new day): embargo -3 then random shift (<= +3)
    assert s["market"]["iron"] <= start_of_day2, (s["market"]["iron"], start_of_day2)
    # selling an embargoed resource must fail
    other = s["nations"][1]
    other["stock"]["iron"] = 5
    ok, _ = E.apply_action(s, 4, "market_sell", {"resource": "iron"})
    assert ok  # queued; resolution must fail
    E.apply_turn(s)
    assert other["stock"]["iron"] == 5, "embargoed resource must not sell"
    # embargo expires after 3 days
    for _ in range(6):
        for cid in sorted(s["citizens"]):
            if s["pending"].get(cid):
                continue
            E.apply_action(s, cid, "work")
        E.apply_turn(s)
    assert "iron" not in s["embargoes"]
    print("ok: embargo blocks sales, drops price, expires")


def test_heroes():
    s = world()
    # make citizen 1 the richest of nation 0
    for cid in s["nations"][0]["citizens"]:
        s["citizens"][cid]["credits"] = 10
    s["citizens"][1]["credits"] = 999
    E.apply_turn(s)
    assert 1 in s["nations"][0]["heroes"], s["nations"][0]["heroes"]
    # hero work bonus
    base = E.WORK_BASE  # no policy, democracy +1
    E.apply_action(s, 1, "work")
    E.apply_turn(s)
    got = s["citizens"][1]["credits"] - 999
    # next refresh happened before work; 1 is still a hero (richest)
    assert got >= base + 1 + E.HERO_WORK_BONUS - 1, got
    print(f"ok: heroes refreshed per turn, +{E.HERO_WORK_BONUS} work (got +{got} over base)")


def main():
    test_v5_flag_off_is_v4()
    test_determinism()
    test_replay()
    test_tamper()
    test_bank()
    test_loan()
    test_loan_default()
    test_aqueduct_observatory()
    test_festival()
    test_mobilize()
    test_mobilize_nonleader()
    test_embargo()
    test_heroes()
    print("\nALL ENGINE5 FEATURE TESTS PASSED")


if __name__ == "__main__":
    main()
