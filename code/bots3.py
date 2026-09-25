"""Heuristic fill-in citizens, v3. Same contract as bots_v1/bots2:
choose(state, cid) -> (action, args), deterministic from (state, seat, turn, seed).

New vs v2: market play (buy low, sell high), infrastructure investment by
persona, espionage (spy/sabotage/counter-espionage), titles for the rich,
and decisions driven by the world power ranking (attack the weak, ally the strong).
"""
import random

import engine3 as E

POLICY_BY_PERSONA = {
    "merchant": "merchant",
    "militarist": "militarist",
    "scholar": "scholar",
    "diplomat": "isolation",
    "populist": "merchant",
}

PREFERRED_BUILDING = {
    "militarist": "barracks",
    "scholar": "university",
    "merchant": "factory",
    "diplomat": "mine",
    "populist": "mine",
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


def _best_ally(state, me):
    """Strongest nation that is not me, not at war with me, not allied."""
    cands = [n for n in state["nations"]
             if n != me
             and not E._at_war(state, me, n)
             and not E._allied(state, me, n)
             and len(E._allies(state, n)) < E.ALLY_MAX]
    if not cands:
        return None
    return max(cands, key=lambda n: E.world_power(state, n))


def _buy_target(state, nat):
    """Buy the resource that is cheap relative to its base price."""
    best, best_ratio = None, 1.05
    for res in E.RESOURCES:
        m = state["market"][res]
        base = E.BASE_PRICES[res]
        ratio = m / base
        if ratio < best_ratio and nat["stock"].get(res, 0) < 12:
            best, best_ratio = res, ratio
    return best


def _sell_target(state, nat):
    """Sell the resource that is dear relative to its base price."""
    best, best_ratio = None, 1.25
    for res in E.RESOURCES:
        m = state["market"][res]
        base = E.BASE_PRICES[res]
        ratio = m / base
        if ratio > best_ratio and nat["stock"].get(res, 0) >= 2:
            best, best_ratio = res, ratio
    return best


def choose(state, cid):
    cit = state["citizens"][cid]
    nat = state["nations"].get(cit["country"]) if not cit["independent"] else None
    rng = random.Random(f"bot-{cid}-t{state['turn']}-{state['seed']}")
    p = cit["persona"]

    if state["winner"] is not None:
        return "work", {}

    if cit["independent"]:
        if cit["credits"] >= E.FOUND_COST and len(state["nations"]) < E.MAX_NATIONS:
            used = E._used_names(state)
            spare = [n for n in E.SPARE_NAMES if n not in used]
            if spare:
                return "found", {"name": spare[rng.randrange(len(spare))]}
        if cit["credits"] >= E.JOIN_COST and state["nations"]:
            target = E.power_ranking(state)[0]
            return "join", {"target": target}
        return "work", {}

    if nat is None:
        return "work", {}

    foes = _enemies(state, cit["country"])
    flags = state.get("day_flags", {})

    # 1. war response
    if foes:
        worst = min(foes, key=lambda f: state["nations"][f]["army"])
        if nat["army"] <= state["nations"][worst]["army"]:
            if p in ("diplomat", "populist", "scholar") or cit["credits"] < E.TRAIN_COST:
                return "peace", {"target": worst}
            return "train", {}
        # at war and stronger: train, or sabotage the weakest enemy
        if nat["stock"].get("oil", 0) >= E.SABOTAGE_OIL and cit["credits"] >= E.SABOTAGE_COST and rng.random() < 0.45:
            weakest = min(foes, key=lambda f: state["nations"][f]["army"])
            return "sabotage", {"target": weakest}
        if cit["credits"] >= E.TRAIN_COST and rng.random() < 0.5:
            return "train", {}

    # 2. counter-espionage when at war and wealthy (diplomats do it often)
    if foes and cit["credits"] >= E.SPIES_DAY_COST + 15 and rng.random() < (0.5 if p == "diplomat" else 0.2):
        return "spies", {}

    # 3. election turns: vote for the wealthiest candidate
    if E.is_election_turn(state["turn"]) and cit["voted_turn"] != state["turn"]:
        cands = nat["citizens"]
        cand = max(cands, key=lambda c: (state["citizens"][c]["credits"], state["citizens"][c]["titles"]))
        return "vote", {"candidate": cand}

    # 4. leadership: set policy once
    if nat["leader"] == cid and not cit["policy_set"]:
        return "set_policy", {"policy": POLICY_BY_PERSONA.get(p, "merchant")}

    # 5. market: sell high, buy low (merchants always, others sometimes)
    sell = _sell_target(state, nat)
    if sell and rng.random() < (0.85 if p == "merchant" else 0.5):
        return "market_sell", {"resource": sell}
    buy = _buy_target(state, nat)
    if buy and cit["credits"] >= E.BASE_PRICES[buy] + 10 and rng.random() < (0.7 if p == "merchant" else 0.4):
        return "market_buy", {"resource": buy}

    # 6. infrastructure: persona-favored building when we can afford it
    b = PREFERRED_BUILDING[p]
    spec = E.BUILDINGS[b]
    if nat["buildings"][b] < spec["cap"]:
        affordable = nat["treasury"] >= spec["cost"] + 10
        for res, amt in spec["resources"].items():
            affordable = affordable and nat["stock"].get(res, 0) >= amt
        if affordable and rng.random() < (0.8 if p in ("militarist", "scholar") else 0.5):
            return "infrastructure", {"building": b}

    # 7. diplomacy: spy on the weakest rival (for intel) and ally the strongest
    if p in ("diplomat", "populist") and len(_allies(state, cit["country"])) < E.ALLY_MAX:
        best = _best_ally(state, cit["country"])
        if best is not None and rng.random() < 0.3:
            return "ally", {"target": best}
    if cit["credits"] >= E.SPY_COST + 10 and nat["stock"].get("grain", 0) >= E.SPY_GRAN:
        rivals = [n for n in state["nations"]
                  if n != cit["country"] and not E._allied(state, cit["country"], n)]
        if rivals and rng.random() < (0.4 if p in ("diplomat", "militarist") else 0.15):
            weakest = min(rivals, key=lambda n: E.world_power(state, n))
            return "spy", {"target": weakest}

    # 8. titles: rich citizens buy prestige
    if cit["titles"] < E.TITLE_MAX and cit["credits"] >= E.TITLE_COST + 25 and rng.random() < (0.5 if p == "populist" else 0.3):
        return "title", {}

    # 9. economy: expand when the nation is rich and tiles are the point
    if nat["treasury"] >= E.EXPAND_COST * 2 and nat["tiles"] < E.TILE_CAP:
        if rng.random() < 0.4:
            return "expand", {}

    # 10. persona actions
    if p == "militarist":
        if nat["army"] >= 8 and cit["credits"] >= E.WAR_COST:
            targets = [n for n in state["nations"]
                       if n != cit["country"]
                       and not E._at_war(state, cit["country"], n)
                       and not E._allied(state, cit["country"], n)]
            if targets:
                weakest = min(targets, key=lambda n: E.world_power(state, n))
                if state["nations"][weakest]["army"] < nat["army"] and rng.random() < 0.5:
                    return "declare_war", {"target": weakest}
        if cit["credits"] >= E.TRAIN_COST:
            return "train", {}
        return "work", {}
    if p == "merchant":
        if cit["credits"] >= 20 and rng.random() < 0.6:
            return "trade", {}
        return "work", {}
    if p == "scholar":
        if cit["credits"] >= 15 and nat["tech"] < E.RESEARCH_MAX:
            return "research", {}
        if nat["tech"] >= 6 and cit["credits"] >= E.CULTURE_COST and nat["culture"] < E.CULTURE_MAX and rng.random() < 0.4:
            return "culture", {}
        return "work", {}
    if p == "diplomat":
        if cit["credits"] >= E.CULTURE_COST and nat["culture"] < E.CULTURE_MAX and rng.random() < 0.5:
            return "culture", {}
        if rng.random() < 0.4:
            return "trade", {}
        return "work", {}
    # populist
    if rng.random() < 0.5:
        return "work", {}
    return "trade", {}
