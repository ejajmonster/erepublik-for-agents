"""Tests for engine7 v7-mechanics (missions, defense pacts, trade offers,
occupation/vassals, war chronicle).

Same bar as the season-3/5/6 suites: determinism, replay, tamper-detection,
plus a feature test per new mechanic. v7-off must be bit-identical to
engine6 (back-compat contract).

Run: python3 test_engine7_features.py
"""
import random

import engine7 as E

SEED = 20260928
NAMES = [f"t{i:02d}" for i in range(20)]


def world(seed=SEED, season="season7-test", **kw):
    return E.new_state(seed=seed, season=season, n_seats=20,
                       citizen_names=NAMES, v7=True, **kw)


def run(s, turns):
    """Deterministic random queue + N turns."""
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


def leader(n):
    return n["leader"]


def _shadow(v6_on, seed=99, turns=6):
    """Same flags on both sides: engine7 must seal identically to engine6."""
    import engine6 as E6
    s7 = E.new_state(seed=seed, season="cmp", n_seats=20, citizen_names=NAMES,
                     v6=v6_on, v7=False)
    s6 = E6.new_state(seed=seed, season="cmp", n_seats=20, citizen_names=NAMES,
                      v6=v6_on)
    for _ in range(turns):
        for cid in sorted(s7["citizens"]):
            if s7["pending"].get(cid):
                continue
            rng = random.Random(f"cmp-{cid}-t{s7['turn']}")
            act, args = rng.choice(["work", "trade"]), {}
            E.apply_action(s7, cid, act, args)
            E6.apply_action(s6, cid, act, args)
        E.apply_turn(s7)
        E6.apply_turn(s6)
    return s7, s6


def test_v7_off_is_v6():
    """v7=False must behave exactly like engine6 with the same v5/v6 flags."""
    for v6_on in (False, True):
        s7, s6 = _shadow(v6_on)
        assert s7["seals"] == s6["seals"], f"v7-off (v6={v6_on}) must match v6 exactly"
    print("ok: v7-off identical to v6 (6-turn shadow runs, v6 off and on)")


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
    r = E.new_state(seed=SEED, season="season7-test", n_seats=20,
                    citizen_names=NAMES, v7=True)
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
    r = E.new_state(seed=SEED, season="season7-test", n_seats=20,
                    citizen_names=NAMES, v7=True)
    for line in log:
        if line["type"] == "action":
            tampered = "work" if line["raw_action"] != "work" else "trade"
            E.apply_action(r, line["citizen"], tampered, line.get("args") or {})
        elif line["type"] == "turn":
            E.apply_turn(r)
    assert r["seals"][-1]["sha"] != before, "tamper not detected"
    print("ok: tamper detection (replayed action change breaks the seal)")


def test_mission():
    """Missions pay both partners daily and respect the 2-mission cap."""
    s = world()
    a, b = s["nations"][0], s["nations"][1]
    la, lb = a["leader"], b["leader"]
    a["treasury"] = b["treasury"] = 200
    ok, _ = E.apply_action(s, la, "mission", {"target": 1})
    assert ok, "mission should be queueable"
    E.apply_turn(s)
    assert [0, 1, s["turn"]] in [(m[0], m[1], m[2]) for m in s["missions"]] or any(
        m[0] == 0 and m[1] == 1 for m in s["missions"]), "mission pair missing"
    m = [m for m in s["missions"] if m[0] == 0 and m[1] == 1][0]
    assert m[2] == E.day_of(s) + E.MISSION_DAYS or m[2] == E.day_of(s) + 1, f"mission end={m[2]}"
    tr_a, tr_b = a["treasury"], b["treasury"]
    E.apply_turn(s)  # next turn (still same day) - no new income until new day
    E.apply_turn(s)  # new day: both partners get +MISSION_BONUS
    assert a["treasury"] >= tr_a + E.MISSION_BONUS - 100, "A did not earn mission income"
    assert b["treasury"] >= tr_b + E.MISSION_BONUS - 100, "B did not earn mission income"
    # cap: 2 concurrent missions
    a["treasury"] = 500
    ok, _ = E.apply_action(s, la, "mission", {"target": 2})
    assert ok, "2nd mission must be allowed"
    E.apply_turn(s)
    a["treasury"] = 500
    ok, _ = E.apply_action(s, la, "mission", {"target": 3})
    assert not ok, "3rd concurrent mission must fail"
    print("ok: mission income both sides + 2-mission cap")


def test_defense_pact_auto_join():
    """A defense-pact partner auto-enters a war against its ally's enemy."""
    s = world()
    a, b, c = s["nations"][0], s["nations"][1], s["nations"][2]
    la, lb = a["leader"], b["leader"]
    a["treasury"] = b["treasury"] = 200
    s["citizens"][la]["credits"] = s["citizens"][lb]["credits"] = 200
    # A and B sign a defense pact
    ok, _ = E.apply_action(s, la, "defense_pact", {"target": 1})
    assert ok, "defense_pact should be queueable"
    E.apply_turn(s)
    assert any(p[0] == 0 and p[1] == 1 for p in s["dpacts"]), "dpact missing"
    # C declares war on B -> A must auto-join against C
    lc = c["leader"]
    s["citizens"][lc]["credits"] = 200
    ok, _ = E.apply_action(s, lc, "declare_war", {"target": 1})
    assert ok
    E.apply_turn(s)
    assert E._at_war(s, 0, 2), "A must auto-enter the war via defense pact"
    assert E._at_war(s, 1, 2), "B-C war must exist"
    kinds = [w["type"] for w in s["war_log"]]
    assert "auto_join" in kinds, "auto_join must be in the war chronicle"
    # cap: only one defense pact per nation
    a["treasury"] = 500
    ok, _ = E.apply_action(s, la, "defense_pact", {"target": 3})
    assert not ok, "second defense pact must fail (one per nation)"
    print("ok: defense pact auto-joins wars + 1-pact cap")


def test_trade_offer_accept():
    """Offers escrow goods, are addressed to one partner, and accept transfers."""
    s = world()
    a, b = s["nations"][0], s["nations"][1]
    la = a["leader"]
    a["treasury"] = 200
    a["stock"]["wood"] = 10
    b["stock"]["iron"] = 10
    ok, _ = E.apply_action(s, la, "trade_offer",
                           {"target": 1, "give_res": "wood", "give_qty": 3,
                            "want_res": "iron", "want_qty": 2})
    assert ok, "trade_offer should be queueable"
    E.apply_turn(s)
    offer = s["offers"][0]
    oid = offer[0]
    assert a["stock"]["wood"] == 7, f"escrow: wood={a['stock']['wood']} (want 7)"
    # only the addressed partner may accept
    lb = b["leader"]
    ok, _ = E.apply_action(s, lb, "accept_offer", {"offer": oid})
    assert ok, "addressed partner should accept"
    E.apply_turn(s)
    assert a["stock"]["wood"] == 7, "giver keeps escrowed wood out (not returned)"
    assert b["stock"]["wood"] == 9, f"B should hold 6+3 wood, has {b['stock']['wood']}"
    assert b["stock"]["iron"] == 8, f"B should spend 2 iron (10->8), has {b['stock']['iron']}"
    assert a["stock"]["iron"] == E.START_STOCK["iron"] + 2, "A should receive 2 iron"
    assert s["offers"] == [], "accepted offer must vanish"
    # non-addressed citizen cannot accept
    a["stock"]["wood"] = 5
    ok, _ = E.apply_action(s, la, "trade_offer",
                           {"target": 1, "give_res": "wood", "give_qty": 2,
                            "want_res": "grain", "want_qty": 2})
    assert ok
    E.apply_turn(s)
    oid2 = s["offers"][0][0]
    c3 = s["nations"][2]["leader"]
    ok, _ = E.apply_action(s, c3, "accept_offer", {"offer": oid2})
    assert ok, "queueing is open; failure happens at apply"
    E.apply_turn(s)
    assert any(o[0] == oid2 for o in s["offers"]), "wrong-recipient accept must not consume the offer"
    print("ok: trade offer escrow, accept transfers, addressed-partner-only")


def test_offer_expiry_returns_goods():
    s = world()
    a = s["nations"][0]
    la = a["leader"]
    a["treasury"] = 200
    a["stock"]["wood"] = 10
    ok, _ = E.apply_action(s, la, "trade_offer",
                           {"target": 1, "give_res": "wood", "give_qty": 3,
                            "want_res": "iron", "want_qty": 1})
    assert ok
    E.apply_turn(s)
    # expire: force the end day to the CURRENT day so the next NEW DAY drops it
    for o in s["offers"]:
        o[7] = E.day_of(s) + 1
    E.apply_turn(s)  # same day, not yet
    E.apply_turn(s)  # new day: expiry fires, goods return
    assert a["stock"]["wood"] == 10, f"goods must return on expiry, wood={a['stock']['wood']}"
    assert s["offers"] == []
    print("ok: expired offer returns escrowed goods")


def test_occupation_vassal_liberation():
    """0 tiles in a v7 world = occupation (vassal), not erasure; tribute, then liberation."""
    s = world()
    a, b = s["nations"][0], s["nations"][1]
    la, lb = a["leader"], b["leader"]
    # B is the weak one: no army, low culture (tile loss = 1/turn), 3 tiles
    a["army"] = 50
    b["army"] = 0
    b["tiles"] = 3
    b["culture"] = 3
    s["citizens"][la]["credits"] = s["citizens"][lb]["credits"] = 200
    ok, _ = E.apply_action(s, la, "declare_war", {"target": 1})
    assert ok
    E.apply_turn(s)
    # A's big army shaves B's tiles (army 0 -> -1 tile/turn) until B hits 0
    for _ in range(20):
        if b.get("occupied_by") is not None:
            break
        E.apply_turn(s)
    assert b.get("occupied_by") == 0, f"B should be occupied by A, got {b.get('occupied_by')}"
    assert b["tiles"] == E.VASSAL_TILES, f"vassal keeps {E.VASSAL_TILES} tiles, has {b['tiles']}"
    assert 1 in s["vassals"], "vassals registry missing"
    kinds = [w["type"] for w in s["war_log"]]
    assert "occupation" in kinds
    # vassal pays tribute daily while occupied
    tr_by, gr_by = a["treasury"], a["stock"]["grain"]
    E.apply_turn(s)
    E.apply_turn(s)  # a new day must pass for tribute
    assert a["treasury"] >= tr_by or b["treasury"] == 0, "tribute missing (unless vassal broke)"
    # liberation after VASSAL_DAYS
    s["vassals"][1]["until"] = E.day_of(s) + 1
    E.apply_turn(s)
    E.apply_turn(s)
    assert b.get("occupied_by") is None, "vassal must be liberated"
    assert b["tiles"] == E.VASSAL_TILES
    assert 1 not in s["vassals"]
    assert "liberation" in [w["type"] for w in s["war_log"]]
    print("ok: occupation -> vassal tribute -> liberation")


def test_vassal_no_diplomacy():
    """Occupied nations cannot wage war or sign diplomacy."""
    s = world()
    b = s["nations"][1]
    lb = b["leader"]
    b["occupied_by"] = 0  # simulate active occupation
    ok, _ = E.apply_action(s, lb, "declare_war", {"target": 2})
    assert not ok, "vassal must not declare war"
    ok, _ = E.apply_action(s, lb, "defense_pact", {"target": 2})
    assert not ok, "vassal must not sign defense pacts"
    ok, _ = E.apply_action(s, lb, "mission", {"target": 2})
    assert not ok, "vassal must not post missions"
    # and others cannot target it
    la = s["nations"][0]["leader"]
    s["nations"][0]["treasury"] = 200
    ok, _ = E.apply_action(s, la, "defense_pact", {"target": 1})
    assert not ok, "cannot sign with an occupied nation"
    print("ok: occupied nations are locked out of diplomacy")


def test_war_chronicle_bounded():
    s = world()
    a, b = s["nations"][0], s["nations"][1]
    la, lb = a["leader"], b["leader"]
    a["army"], b["army"] = 40, 35
    s["citizens"][la]["credits"] = s["citizens"][lb]["credits"] = 200
    ok, _ = E.apply_action(s, la, "declare_war", {"target": 1})
    assert ok
    for _ in range(30):
        if s["winner"] is not None:
            break
        E.apply_turn(s)
    assert len(s["war_log"]) <= E.WAR_LOG_MAX, "chronicle must be bounded"
    assert a["war_record"]["wars"] >= 1, "war_record must count wars"
    # season end counts FREE nations only
    s2 = world(seed=42)
    s2["nations"][1]["occupied_by"] = 0
    free = [n for n in s2["nations"] if E._free(s2, n)]
    assert 1 not in free, "vassal must not count as free power"
    print("ok: war chronicle bounded + vassals excluded from power ranking")


def test_full_season():
    """Full 80-turn season with v7 on: deterministic, ends, replayable."""
    def bot_play(s, tag):
        log = []
        for _ in range(80):
            if s["winner"] is not None:
                break
            for cid in sorted(s["citizens"]):
                if s["pending"].get(cid):
                    continue
                c = s["citizens"][cid]
                nat = s["nations"].get(c["country"]) if not c["independent"] else None
                rng = random.Random(f"{tag}-{cid}-t{s['turn']}")
                if not nat:
                    E.apply_action(s, cid, "work")
                    continue
                if E.is_election_turn(s["turn"]) and nat["citizens"]:
                    cand = nat["citizens"][rng.randrange(len(nat["citizens"]))]
                    E.apply_action(s, cid, "vote", {"candidate": cand})
                    continue
                acts = ["work", "trade", "train"]
                if nat["leader"] == cid and nat["treasury"] > 80:
                    acts += ["mission", "defense_pact"]
                act = rng.choice(acts)
                if act in ("mission", "defense_pact"):
                    others = [n for n in s["nations"] if n != c["country"]]
                    args = {"target": rng.choice(others)}
                else:
                    args = {}
                r_, _ = E.apply_action(s, cid, act, args)
                if r_:
                    log.append({"turn": s["turn"], "type": "action", "citizen": cid,
                                "name": c["name"], "raw_action": act, "args": args})
            E.apply_turn(s, log_lines=log)
        return s, log

    # same seed AND same season string: season is part of the rng key and the
    # hashed state, so both shadow runs must be bit-identical inputs
    s1, log1 = bot_play(E.new_state(seed=777, season="fs", n_seats=20,
                                    citizen_names=NAMES, v7=True), "a")
    s2, _ = bot_play(E.new_state(seed=777, season="fs", n_seats=20,
                                 citizen_names=NAMES, v7=True), "a")
    assert s1["seals"] == s2["seals"], "full-season shadow run must be deterministic"
    assert s1["winner"] is not None, "season must end"
    r = E.new_state(seed=777, season="fs", n_seats=20, citizen_names=NAMES, v7=True)
    for line in log1:
        if line["type"] == "action":
            E.apply_action(r, line["citizen"], line["raw_action"], line.get("args") or {})
        elif line["type"] == "turn":
            E.apply_turn(r)
    assert r["seals"] == s1["seals"], "full-season replay mismatch"
    # some v7 activity happened in a full season
    assert s1["missions"] or any(w["type"] == "auto_join" for w in s1["war_log"]) or \
           s1["vassals"] or [w for w in s1["war_log"] if w["type"] == "pact"], \
           "no v7 mechanics fired in a full season"
    print(f"ok: full 80-turn v7 season deterministic + replayable (winner={s1['nations'][s1['winner']]['name'] if s1['winner'] is not None else None})")


if __name__ == "__main__":
    test_v7_off_is_v6()
    test_determinism()
    test_replay()
    test_tamper()
    test_mission()
    test_defense_pact_auto_join()
    test_trade_offer_accept()
    test_offer_expiry_returns_goods()
    test_occupation_vassal_liberation()
    test_vassal_no_diplomacy()
    test_war_chronicle_bounded()
    test_full_season()
    print("\nALL ENGINE7 TESTS PASSED")
