"""Heuristic fill-in citizens, v2. Same contract as bots_v1:
choose(state, cid) -> (action, args), deterministic from (state, seat, turn, seed).

New vs v1: expand when wealthy, found a nation when independent and rich,
ally the strongest non-enemy when a diplomat is threatened, break alliances
that no longer pay, and use culture/tech thresholds from engine2.
"""
import random

import engine2

POLICY_BY_PERSONA = {
    "merchant": "merchant",
    "militarist": "militarist",
    "scholar": "scholar",
    "diplomat": "isolation",
    "populist": "merchant",
}


def _enemies(state, n):
    out = []
    for a, b in state["war"]:
        if a == n:
            out.append(b)
        elif b == n:
            out.append(a)
    return out


def _allies(state, n):
    out = []
    for a, b in state["alliances"]:
        if a == n:
            out.append(b)
        elif b == n:
            out.append(a)
    return out


def choose(state, cid):
    cit = state["citizens"][cid]
    nat = state["nations"].get(cit["country"]) if not cit["independent"] else None
    rng = random.Random(f"bot-{cid}-t{state['turn']}-{state['seed']}")

    if state["winner"] is not None:
        return "work", {}

    if cit["independent"]:
        # founders: rich independents start their own nation
        if cit["credits"] >= engine2.FOUND_COST and len(state["nations"]) < engine2.MAX_NATIONS:
            used = engine2._used_names(state)
            spare = [n for n in engine2.SPARE_NAMES if n not in used]
            if spare:
                return "found", {"name": spare[rng.randrange(len(spare))]}
        if cit["credits"] >= engine2.JOIN_COST and state["nations"]:
            target = max(state["nations"], key=lambda n: (state["nations"][n]["tiles"], state["nations"][n]["treasury"]))
            return "join", {"target": target}
        return "work", {}

    if nat is None:
        return "work", {}

    p = cit["persona"]
    foes = _enemies(state, cit["country"])

    # 1. war response
    if foes:
        worst = min(foes, key=lambda f: state["nations"][f]["army"])
        if nat["army"] <= state["nations"][worst]["army"]:
            if p in ("diplomat", "populist", "scholar") or cit["credits"] < engine2.TRAIN_COST:
                return "peace", {"target": worst}
            return "train", {}
        # at war and stronger: keep training if cheap
        if cit["credits"] >= engine2.TRAIN_COST and rng.random() < 0.5:
            return "train", {}

    # 2. election turns: vote for the wealthiest candidate
    if engine2.is_election_turn(state["turn"]) and cit["voted_turn"] != state["turn"]:
        cands = nat["citizens"]
        cand = max(cands, key=lambda c: state["citizens"][c]["credits"])
        return "vote", {"candidate": cand}

    # 3. leadership: set policy once
    if nat["leader"] == cid and not cit["policy_set"]:
        return "set_policy", {"policy": POLICY_BY_PERSONA.get(p, "merchant")}

    # 4. diplomacy: build one useful alliance (diplomats especially)
    if p in ("diplomat", "populist") and len(_allies(state, cit["country"])) < engine2.ALLY_MAX:
        candidates = [n for n in state["nations"]
                      if n != cit["country"]
                      and not engine2._at_war(state, cit["country"], n)
                      and not engine2._allied(state, cit["country"], n)
                      and len(engine2._allies(state, n)) < engine2.ALLY_MAX]
        if candidates:
            best = max(candidates, key=lambda n: state["nations"][n]["treasury"])
            if rng.random() < 0.3:
                return "ally", {"target": best}

    # 5. economy: expand when the nation is rich and tiles are the point
    if nat["treasury"] >= engine2.EXPAND_COST * 2 and nat["tiles"] < engine2.TILE_CAP:
        if rng.random() < 0.4:
            return "expand", {}

    # 6. persona actions
    if p == "militarist":
        # aggressive branch: with a real army and a weak non-allied target, sometimes attack
        if nat["army"] >= 8 and cit["credits"] >= engine2.WAR_COST:
            targets = [n for n in state["nations"]
                       if n != cit["country"]
                       and not engine2._at_war(state, cit["country"], n)
                       and not engine2._allied(state, cit["country"], n)]
            if targets:
                weakest = min(targets, key=lambda n: (state["nations"][n]["army"], state["nations"][n]["tiles"]))
                if state["nations"][weakest]["army"] < nat["army"] and rng.random() < 0.5:
                    return "declare_war", {"target": weakest}
        if cit["credits"] >= engine2.TRAIN_COST:
            return "train", {}
        return "work", {}
    if p == "merchant":
        if cit["credits"] >= 20 and rng.random() < 0.6:
            return "trade", {}
        return "work", {}
    if p == "scholar":
        if cit["credits"] >= 15 and nat["tech"] < engine2.RESEARCH_MAX:
            return "research", {}
        if nat["tech"] >= 6 and cit["credits"] >= engine2.CULTURE_COST and nat["culture"] < engine2.CULTURE_MAX and rng.random() < 0.4:
            return "culture", {}
        return "work", {}
    if p == "diplomat":
        if cit["credits"] >= engine2.CULTURE_COST and nat["culture"] < engine2.CULTURE_MAX and rng.random() < 0.5:
            return "culture", {}
        if rng.random() < 0.4:
            return "trade", {}
        return "work", {}
    # populist
    if rng.random() < 0.5:
        return "work", {}
    return "trade", {}
