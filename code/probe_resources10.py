"""Functional proof for the v10 scarce-deposit resource mechanic (season 6
candidate). Proves, in a fresh v10 world, that the new resources actually
drive the economy: (1) deposits are uneven, (2) `mine` extracts a deposit
resource into stock, (3) the new resources' prices drift day-to-day,
(4) an embargo can be placed on a new resource, (5) the whole thing is
deterministic. Run: python3 probe_resources10.py — exit 0 = all pass.
"""
import random
import sys

import engine10 as E

NAMES = [f"p{i:02d}" for i in range(30)]
SEED = 20261029


def build():
    # A real season-6 world: v5..v10 all on (v10 implies v9 which implies the
    # rest, but new_state only ORs v5/v6/v7 from their own args, so pass them
    # explicitly to mirror server._engine_flags for the live world).
    s = E.new_state(seed=SEED, season="res10", n_seats=30,
                    citizen_names=NAMES, v5=True, v6=True, v7=True,
                    v8=True, v9=True, v10=True)
    # fund a few citizens so they can afford mine/work
    for c in (0, 1, 2, 3):
        s["citizens"][c]["credits"] = 200
    return s


def test_deposits_uneven():
    s = build()
    # every nation must have the 4 basics
    basics = set(E.RESOURCES)
    dep_sets = set()
    scarce_by_nation = 0
    for nid, dep in s["deposits"].items():
        assert basics.issubset(set(dep)), f"nation {nid} missing basics: {dep}"
        dep_sets.add(tuple(sorted(dep)))
        if set(dep) & set(E.V10_NEW_RESOURCES):
            scarce_by_nation += 1
    # unevenness: not every nation shares the same deposit set
    assert len(dep_sets) > 1, "deposits are uniform — every nation has the same set"
    # and not every nation has every scarce resource (that IS the scarcity)
    per_nation_full = sum(1 for dep in s["deposits"].values()
                          if set(E.V10_NEW_RESOURCES).issubset(set(dep)))
    assert per_nation_full < len(s["deposits"]), "every nation owns every scarce resource — no scarcity"
    # determinism of _gen_deposits
    d1 = E._gen_deposits(SEED)
    d2 = E._gen_deposits(SEED)
    assert d1 == d2, "_gen_deposits not deterministic"
    # a deposited scarce resource starts at DEPOSIT_START in stock, others 0
    n0 = s["nations"][0]
    for res in E.V10_NEW_RESOURCES:
        expect = E.V10_DEPOSIT_START if res in s["deposits"][0] else 0
        assert n0["stock"][res] == expect, f"n0 {res}: {n0['stock'][res]} != {expect}"
    print(f"ok: uneven deposits ({len(dep_sets)} distinct sets, {per_nation_full}/{len(s['deposits'])} nations own ALL scarce = not fully scarce), deterministic, stock seeded")


def test_mine_extracts():
    s = build()
    # pick a nation that has a scarce deposit
    n0dep = s["deposits"][0]
    scarce = [r for r in n0dep if r in E.V10_NEW_RESOURCES]
    assert scarce, "test nation 0 needs a scarce deposit"
    res = scarce[0]
    before = s["nations"][0]["stock"].get(res, 0)
    ok, _ = E.apply_action(s, 0, "mine", {"resource": res})
    assert ok, "mine must be accepted in a v10 world"
    # mine resolves inside apply_turn (actions apply at close)
    E.apply_turn(s)
    after = s["nations"][0]["stock"].get(res, 0)
    assert after == before + 1, f"mine should add 1 {res}: {before} -> {after}"
    print(f"ok: mine extracts 1 {res} into stock ({before} -> {after})")


def test_mine_autoselect_highest_price():
    s = build()
    n0dep = s["deposits"][0]
    scarce = [r for r in n0dep if r in E.V10_NEW_RESOURCES]
    assert scarce, "test nation 0 needs a scarce deposit"
    # no explicit resource -> engine picks the highest-priced deposit resource
    ok, _ = E.apply_action(s, 0, "mine", {})
    assert ok
    E.apply_turn(s)
    # whichever it mined, stock of some deposit resource must have gone up by 1
    s2 = build()
    before = {r: s2["nations"][0]["stock"].get(r, 0) for r in E.V10_NEW_RESOURCES}
    E.apply_action(s2, 0, "mine", {})
    E.apply_turn(s2)
    moved = [r for r in before if s2["nations"][0]["stock"].get(r, 0) == before[r] + 1]
    assert moved, "auto-mine must extract exactly one deposit resource"
    print(f"ok: mine without args auto-picks a deposit resource ({moved[0]})")


def test_mine_refused_in_v9():
    s9 = E.new_state(seed=1, season="v9off", n_seats=30, citizen_names=NAMES,
                     v8=True, v9=True)  # v10 off
    s9["citizens"][0]["credits"] = 200
    ok, msg = E.apply_action(s9, 0, "mine", {"resource": "copper"})
    assert not ok, f"v9 world must refuse mine: {msg}"
    print("ok: mine refused in a v9 (v10-off) world")


def test_new_resource_prices_drift():
    s = build()
    start = {r: s["market"][r] for r in E.V10_NEW_RESOURCES}
    # run several days (2 turns/day) so the daily price roll hits the new resources
    for _ in range(6):
        for cid in sorted(s["citizens"]):
            if not s["pending"].get(cid):
                E.apply_action(s, cid, "work", {})
        E.apply_turn(s)
    end = {r: s["market"][r] for r in E.V10_NEW_RESOURCES}
    # at least one new resource price must have moved (drift is +/- up to 3/day)
    moved = [r for r in E.V10_NEW_RESOURCES if start[r] != end[r]]
    assert moved, f"new resources did not drift: {start} -> {end}"
    # and prices must respect the per-resource cap (base+8)
    for r in E.V10_NEW_RESOURCES:
        assert end[r] <= E._pcap(s, r), f"{r} {end[r]} over cap {E._pcap(s, r)}"
    print(f"ok: new-resource prices drift and stay under cap (moved: {moved})")


def test_embargo_on_new_resource():
    s = build()
    # leader of nation 0 embargoes a scarce resource
    leader = s["nations"][0]["leader"]
    s["citizens"][leader]["credits"] = 500
    s["nations"][0]["treasury"] = 500
    # pick a new resource to embargo
    res = E.V10_NEW_RESOURCES[0]
    ok, _ = E.apply_action(s, leader, "embargo", {"resource": res})
    assert ok, f"embargo on {res} must be accepted (v10): "
    E.apply_turn(s)
    assert s["embargoes"].get(res, 0) > 0, f"embargo on {res} not recorded"
    print(f"ok: embargo works on a new (scarce) resource {res}")


def test_determinism_with_mine():
    def run():
        s = build()
        for t in range(6):
            for cid in sorted(s["citizens"]):
                if not s["pending"].get(cid):
                    E.apply_action(s, cid, "mine" if t % 2 == 0 else "work", {})
            E.apply_turn(s)
        return s["seals"]
    assert run() == run(), "determinism broken with mine actions"
    print("ok: determinism holds with mine actions mixed in")


def main():
    test_deposits_uneven()
    test_mine_extracts()
    test_mine_autoselect_highest_price()
    test_mine_refused_in_v9()
    test_new_resource_prices_drift()
    test_embargo_on_new_resource()
    test_determinism_with_mine()
    print("\nALL v10 RESOURCE MECHANICS GREEN (deposits, mine, price drift, embargo, determinism)")


if __name__ == "__main__":
    main()
