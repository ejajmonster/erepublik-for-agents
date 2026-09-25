"""Heuristic fill-in citizens for eRepublik-for-agents.

choose(state, cid) -> (action, args). Pure function of the public state plus a
per-seat, per-turn deterministic rng, so a re-run of the same state+turn gives
the same move. Strategies are deliberately naive and persona-driven: this is
the degeneracy control for season 1 — if the bots are too clever the game is
boring, if they are too dumb it is noise.
"""
import random

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


def _at_war(state, a, b):
    return [min(a, b), max(a, b)] in state["war"]


def choose(state, cid):
    cit = state["citizens"][cid]
    nat = state["nations"].get(cit["country"]) if not cit["independent"] else None
    rng = random.Random(f"bot-{cid}-t{state['turn']}-{state['seed']}")

    if state["winner"] is not None:
        return "work", {}

    if cit["independent"]:
        if cit["credits"] >= 30 and state["nations"]:
            target = max(state["nations"], key=lambda n: (state["nations"][n]["tiles"], state["nations"][n]["treasury"]))
            return "join", {"target": target}
        return "work", {}

    if nat is None:
        return "work", {}

    p = cit["persona"]

    # 1. survival first: if at war and weak, try peace
    foes = _enemies(state, cit["country"])
    if foes and p in ("diplomat", "populist", "scholar"):
        worst = min(foes, key=lambda f: state["nations"][f]["army"])
        if nat["army"] <= state["nations"][worst]["army"]:
            return "peace", {"target": worst}

    # 2. election turns: vote for the wealthiest candidate
    if state["turn"] > 0 and state["turn"] % 2 == 1 and (state["turn"] // 2) % 10 == 0:
        cand = max(nat["citizens"], key=lambda c: state["citizens"][c]["credits"])
        return "vote", {"candidate": cand}

    # 3. leaders set their policy once
    if nat["leader"] == cid and not cit["policy_set"]:
        return "set_policy", {"policy": POLICY_BY_PERSONA.get(p, "merchant")}

    # 4. war: militarists expand when their army is strong and cheap
    if p == "militarist":
        if foes:
            return "train", {}
        neutrals = [n for n in state["nations"]
                    if n != cit["country"] and not any(f == n for f in _enemies(state, n))
                    and n in state["nations"] and not _at_war(state, cit["country"], n)]
        if (neutrals and cit["credits"] >= 20 and nat["army"] >= 6
                and nat["army"] >= max(state["nations"][n]["army"] for n in neutrals) * 1.5
                and rng.random() < 0.25):
            target = min(neutrals, key=lambda n: state["nations"][n]["tiles"] + state["nations"][n]["army"])
            return "declare_war", {"target": target}
        if cit["credits"] >= 12:
            return "train", {}
        return "work", {}
    if p == "populist" and not foes and cit["credits"] >= 30 and nat["army"] >= 10 and rng.random() < 0.1:
        neutrals = [n for n in state["nations"] if n != cit["country"] and not _at_war(state, cit["country"], n)]
        if neutrals:
            target = min(neutrals, key=lambda n: state["nations"][n]["tiles"])
            return "declare_war", {"target": target}
    if p == "merchant":
        if cit["credits"] >= 18 and nat["tech"] < 10 and rng.random() < 0.3:
            return "research", {}
        return "trade", {}
    if p == "scholar":
        if nat["tech"] < 10 and cit["credits"] >= 18:
            return "research", {}
        if cit["credits"] >= 5:
            return "culture", {}
        return "work", {}
    if p == "diplomat":
        if cit["credits"] >= 5 and rng.random() < 0.6:
            return "culture", {}
        return "trade", {}
    if p == "populist":
        return "work", {}

    return "work", {}
