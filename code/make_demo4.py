"""Generate the public demo4 artifact for engine v4 (candidate for season 3).

Full 80-turn season (ends on turn 79, day 40), 20 seats, 5 nations, persona bots (bots4.py). Deterministic:
replay from seed + demo4-log.jsonl must reproduce the seal chain in
demo4-state.json. Run: python3 make_demo4.py
"""
import json
import random

import engine4 as E
import bots4 as B


def _queue(st, turn):
    """Deterministic bot queueing via bots4 (persona-driven, v4 mechanics)."""
    for cid in sorted(st["citizens"]):
        if st["pending"].get(cid):
            continue
        act, args = B.choose(st, cid)
        E.apply_action(st, cid, act, args)


def main():
    seed = 20260925
    st, log = None, None
    for turn in range(81):
        st = E.new_state(seed, season="demo4", n_seats=20)
        log = []
        for t in range(81):
            if st["winner"] is not None:
                break
            _queue(st, t)
            E.apply_turn(st, log)
        break

    # independent replay check
    st2 = E.new_state(seed, season="demo4", n_seats=20)
    by_turn = {}
    for entry in log:
        if entry.get("type") == "action":
            by_turn.setdefault(entry["turn"], []).append(entry)
    turn = 0
    while turn < st["turn"]:
        for entry in by_turn.get(turn, []):
            ok, _ = E.apply_action(st2, entry["citizen"], entry["raw_action"], entry.get("args") or {})
            assert ok, f"replay rejected {entry['raw_action']} at t{turn}"
        E.apply_turn(st2, [])
        turn += 1
    assert E.seal_hash(st2) == E.seal_hash(st), "replay diverged"
    assert st2["seals"] == st["seals"]

    with open("demo4-state.json", "w") as f:
        json.dump(st, f, indent=1)
    with open("demo4-log.jsonl", "w") as f:
        for line in log:
            f.write(json.dumps(line) + "\n")

    acts = [l for l in log if l.get("type") == "action"]
    from collections import Counter
    kinds = Counter(l["raw_action"].split(":")[0] for l in acts)
    winner = st["nations"][st["winner"]] if isinstance(st["winner"], int) else None
    print(json.dumps({
        "replay_ok": True,
        "turns": st["turn"], "seals": len(st["seals"]),
        "winner": winner["name"] if winner else None,
        "winner_tiles": winner["tiles"] if winner else None,
        "actions_total": len(acts),
        "kinds": dict(kinds),
        "elections": len(st["elections"]),
        "governments_set": kinds.get("set_government", 0),
        "taxes_set": kinds.get("set_tax", 0),
        "attacks": kinds.get("attack", 0),
        "wars_declared": kinds.get("declare_war", 0),
        "pacts": kinds.get("pact", 0),
        "treaties": kinds.get("treaty", 0),
        "final": {n["name"]: {"tiles": n["tiles"], "treasury": n["treasury"],
                              "army": n["army"], "gov": n["gov"]}
                 for n in st["nations"].values()},
    }, indent=1))


if __name__ == "__main__":
    main()
