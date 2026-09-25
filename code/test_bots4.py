"""Tests for bots4 (season 3 candidate bots). Run: python3 test_bots4.py"""
import engine4 as E
import bots4 as B


def run_season(seed=9001, n_turns=82, names=None):
    st = E.new_state(seed, season="bots4test", n_seats=20, citizen_names=names)
    log = []
    used_actions = set()
    for turn in range(n_turns):
        if st["winner"] is not None:
            break
        for cid in sorted(st["citizens"]):
            act, args = B.choose(st, cid)
            ok, _ = E.apply_action(st, cid, act, args)
            assert ok, f"bot action {act} rejected for cid {cid} at t{turn}"
            used_actions.add(act)
        E.apply_turn(st, log)
    return st, log, used_actions


def test_full_season_finishes():
    st, log, used = run_season(seed=9001)
    assert st["winner"] is not None, f"season did not finish (turn {st['turn']})"
    print(f"full season OK: {st['turn']} turns, winner nation {st['winner']} "
          f"({st['nations'][st['winner']]['name']}), {len(st['seals'])} seals")
    # v4 mechanics must actually be exercised by the bots
    for mech in ("set_government", "set_tax", "attack", "pact", "treaty"):
        assert mech in used, f"bots never used v4 action {mech}"
    print("v4 mechanics exercised:", sorted(used))


def test_determinism():
    a, la, _ = run_season(seed=9002)
    b, lb, _ = run_season(seed=9002)
    assert E.seal_hash(a) == E.seal_hash(b), "seals differ across identical runs"
    assert la == lb, "logs differ across identical runs"
    print(f"determinism OK: {len(a['seals'])} seals, winner {a['winner']}")


def test_replay():
    st, log, _ = run_season(seed=9003)
    st2 = E.new_state(9003, season="bots4test", n_seats=20)
    by_turn = {}
    for entry in log:
        if entry.get("type") == "action":
            by_turn.setdefault(entry["turn"], []).append(entry)
    turn = 0
    while turn < st["turn"]:
        for entry in by_turn.get(turn, []):
            ok, _ = E.apply_action(st2, entry["citizen"], entry["raw_action"],
                                   entry.get("args") or {})
            assert ok, f"replay rejected {entry['raw_action']} at t{turn}"
        E.apply_turn(st2, [])
        turn += 1
    assert E.seal_hash(st2) == E.seal_hash(st), "replay diverged"
    print(f"replay OK: {turn} turns, final seal {E.seal_hash(st2)[:12]}")


def test_stability_multiple_seeds():
    for seed in (9100, 9200, 9300):
        st, log, _ = run_season(seed=seed, n_turns=90)
        assert st["winner"] is not None, f"seed {seed}: no winner"
        assert all(st["nations"][n]["tiles"] >= 0 for n in st["nations"])
        assert all(nat["treasury"] >= 0 for nat in st["nations"].values())
        assert all(st["citizens"][c]["credits"] >= -50 for c in st["citizens"]), \
            f"seed {seed}: a citizen went very poor"
        print(f"seed {seed} OK: winner {st['nations'][st['winner']]['name']} "
              f"(tiles {st['nations'][st['winner']]['tiles']}, tr {st['nations'][st['winner']]['treasury']})")
    print("stability OK: 3 seeds finish with sane states")


if __name__ == "__main__":
    test_full_season_finishes()
    test_determinism()
    test_replay()
    test_stability_multiple_seeds()
    print("ALL BOTS4 TESTS GREEN")
