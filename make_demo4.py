"""Generate the public demo4 artifact for engine v4 (candidate for season 3).

Full 80-turn-max season, 20 seats, 5 nations, heuristic bots. Deterministic:
replay from seed + demo4-log.jsonl must reproduce the seal chain in
demo4-state.json. Run: python3 make_demo4.py
"""
import json
import random

import engine4 as E


def _queue(st, turn):
    """Deterministic bot queueing (same policy as test_engine4)."""
    for cid in sorted(st["citizens"]):
        if st["pending"].get(cid):
            continue
        c = st["citizens"][cid]
        n = c["country"]
        rng = random.Random(f"demo4-{cid}-t{turn}")
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
