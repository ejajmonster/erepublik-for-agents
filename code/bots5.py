"""Heuristic fill-in citizens, v5 (season 4 candidate). Same contract as bots4:
choose(state, cid) -> (action, args), deterministic from (state, seat, turn, seed).

New vs bots4 — the v5 mechanics actually get used:
- BANK: leaders park surplus treasury in the state bank (5% interest/day,
  explicit 100/day withdrawals) when it would otherwise sit idle.
- LOANS: a leader takes a loan when the treasury can't cover a planned big
  spend but the installment (due/15 per day) is serviceable.
- AQUIEDUCT/OBSERVATORY: persona-favored v5 buildings — aqueducts for
  merchants (grain is spy/trade food), observatories for scholars (tech
  without credits).
- FESTIVALS: populists throw festivals when rich — cheap culture + morale.
- MOBILIZATION: at war with a losing army, the leader converts treasury into
  army faster than training can.
- EMBARGO: at war, leaders embargo the resource the enemy has most of
  (their exports stop, the price collapses for everyone).

Robustness: the engine applies all queued actions at turn end and deducts
treasury then, so two citizens of one nation can both queue an expensive action
and the second one fails. `choose` tracks the nation's queued treasury
commitments (v4 + v5 actions) so it never double-spends.
"""
import random

import engine5 as E

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

# v5: second building each persona wants (free grain/tech streams): scholar ->
# observatory (tech without credits), merchant/diplomat -> aqueduct (grain for
# spies and food), militarist/populist -> none (they live off barracks/mines)
V5_BUILDING = {
    "militarist": None,
    "scholar": "observatory",
    "merchant": "aqueduct",
    "diplomat": "aqueduct",
    "populist": None,
}

# which government each persona wants (leader only)
GOV_BY_PERSONA = {
    "merchant": "democracy",
    "militarist": "dictatorship",
    "scholar": "oligarchy",
    "diplomat": "democracy",
    "populist": "democracy",
}

# how hard the leader pushes tax toward the gov cap (0..100 percent of cap)
TAX_PUSH = {
    "merchant": 10,     # keep the people productive
    "militarist": 100,  # squeeze everything for the army
    "scholar": 40,
    "diplomat": 10,
    "populist": 0,      # no taxation, popularity
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


def _pacted_with(state, n):
    out = []
    d = E.day_of(state)
    for a, b, end in state["pacts"]:
        if end > d and (a == n or b == n):
            out.append(b if a == n else a)
    return out


def _treaties_with(state, n):
    """Partners in this nation's live (unexpired) treaties."""
    out = []
    d = E.day_of(state)
    for a, b, res, end in state["treaties"]:
        if end > d and (a == n or b == n):
            out.append(b if a == n else a)
    return out


def _pending_treasury_cost(state, n):
    """Treasury already committed by this nation's queued actions this turn.

    The engine deducts treasury only at turn end, so a second citizen would see
    the same live treasury and queue an action that then fails. Subtract the
    queued commitments so the nation never double-spends.
    """
    cost = 0
    for cid, act in state["pending"].items():
        c = state["citizens"][cid]
        if c["independent"] or c["country"] != n:
            continue
        a = act["action"]
        if a == "set_government":
            cost += E.SET_GOV_COST_TREASURY
        elif a == "treaty":
            cost += E.TREATY_COST
        elif a == "pact":
            cost += E.PACT_COST
        elif a == "expand":
            cost += E.EXPAND_COST
        elif a == "attack":
            cost += E.ATTACK_TREASURY
        elif a == "infrastructure":
            b = act.get("args", {}).get("building")
            if b in E.BUILDINGS:
                cost += E.BUILDINGS[b]["cost"]
        elif a == "embargo":
            cost += E.EMBARGO_COST
        elif a == "set_government":
            pass  # already counted above
    return cost

def _pending_bank(state, n):
    """Treasury already queued for the bank by this nation's citizens this turn."""
    total = 0
    for cid, act in state["pending"].items():
        c = state["citizens"][cid]
        if c["independent"] or c["country"] != n or act["action"] != "bank":
            continue
        total += int(act.get("args", {}).get("amount") or 0)
    return total

def _pending_v5_leader(state, n):
    """True if the nation's leader already queued a v5 leader action this turn
    (lend / mobilize / embargo / withdraw — one per leader per turn anyway)."""
    for cid, act in state["pending"].items():
        c = state["citizens"][cid]
        if c["independent"] or c["country"] != n:
            continue
        if act["action"] in ("lend", "mobilize", "embargo", "withdraw"):
            return True
    return False


def _pending_treaties(state, n):
    """Partners this nation already has a treaty queued for this turn."""
    out = []
    for cid, act in state["pending"].items():
        c = state["citizens"][cid]
        if c["independent"] or c["country"] != n or act["action"] != "treaty":
            continue
        t = act.get("args", {}).get("target")
        if t is not None and t != n:
            out.append(t)
    return out


def _best_ally(state, me):
    """Strongest nation that is not me, not at war, not allied, has ally slots."""
    cands = [n for n in state["nations"]
             if n != me
             and not E._at_war(state, me, n)
             and not E._allied(state, me, n)
             and len(E._allies(state, n)) < E.ALLY_MAX]
    if not cands:
        return None
    return max(cands, key=lambda n: E.world_power(state, n))


def _buy_target(state, nat):
    best, best_ratio = None, 1.05
    for res in E.RESOURCES:
        m = state["market"][res]
        base = E.BASE_PRICES[res]
        ratio = m / base
        if ratio < best_ratio and nat["stock"].get(res, 0) < 12:
            best, best_ratio = res, ratio
    return best


def _sell_target(state, nat):
    best, best_ratio = None, 1.25
    for res in E.RESOURCES:
        m = state["market"][res]
        base = E.BASE_PRICES[res]
        ratio = m / base
        if ratio > best_ratio and nat["stock"].get(res, 0) >= 2:
            best, best_ratio = res, ratio
    return best


def _attack_win(state, me, t):
    """True if the offensive roll wins: my army+tech > their army+2*culture."""
    nat, tgt = state["nations"][me], state["nations"][t]
    return (nat["army"] + nat["tech"]) > (tgt["army"] + 2 * tgt["culture"])


def _treaty_candidates(state, nat, me, free_tr, pending_tr):
    """Peaceful partners with live treaty capacity, not already queued this turn."""
    if free_tr < E.TREATY_COST + 10:
        return []
    if len(_treaties_with(state, me)) + len(pending_tr) >= E.TREATY_MAX:
        return []
    return [n for n in state["nations"]
            if n != me
            and not E._at_war(state, me, n)
            and n not in _treaties_with(state, me)
            and n not in pending_tr
            and len(E._treaties_of(state, n)) < E.TREATY_MAX]


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

    me = cit["country"]
    foes = _enemies(state, me)
    flags = state.get("day_flags", {})
    free_tr = nat["treasury"] - _pending_treasury_cost(state, me)
    pending_tr = _pending_treaties(state, me)

    # ---- v4: leader governance (government first, then tax) ----
    if nat["leader"] == cid:
        want_gov = GOV_BY_PERSONA.get(p, "democracy")
        if (nat["gov"] != want_gov
                and nat["treasury"] >= E.SET_GOV_COST_TREASURY + 20
                and free_tr >= E.SET_GOV_COST_TREASURY + 10):
            return "set_government", {"government": want_gov}
        cap = E.GOVS[nat["gov"]]["tax_cap"]
        want_tax = cap * TAX_PUSH.get(p, 10) // 100
        # only raise tax toward the target; clamping happens on gov switch
        if want_tax > nat["tax"] and want_tax - nat["tax"] >= 5:
            return "set_tax", {"rate": want_tax}

    # ---- v5: leader financials (bank / loan / mobilize / embargo) ----
    if state.get("v5") and nat["leader"] == cid and not _pending_v5_leader(state, me):
        foes = _enemies(state, me)
        # MOBILIZE: at war and losing the roll against the worst enemy
        if foes:
            worst = max(foes, key=lambda f: E.world_power(state, f))
            tgt = state["nations"][worst]
            if ((nat["army"] + nat["tech"]) <= (tgt["army"] + 2 * tgt["culture"])
                    and nat["treasury"] >= 2 * E.MOBILIZE_MIN
                    and p in ("militarist", "diplomat") or (p == "militarist" and nat["treasury"] >= E.MOBILIZE_MIN)):
                return "mobilize", {}
        # EMBARGO: at war, block the resource the worst enemy stocks most
        if foes and nat["treasury"] >= E.EMBARGO_COST + 10 and rng.random() < 0.4:
            worst = max(foes, key=lambda f: E.world_power(state, f))
            tgt = state["nations"][worst]
            res = max(E.RESOURCES, key=lambda r: tgt["stock"].get(r, 0))
            if tgt["stock"].get(res, 0) >= 3 and res not in state["embargoes"]:
                return "embargo", {"resource": res}
        # LOAN: can't service a planned big spend? borrow if installments are small
        if nat["loan"] is None and nat["treasury"] < 50 and free_tr < 30:
            amt = 150
            due = amt * (100 + E.LEND_INTEREST) // 100
            per = -(-due // E.LEND_MAX_DAYS)  # ~15/day
            if nat["tiles"] * E.TILE_INCOME >= per:  # tile income alone services it
                return "lend", {"amount": amt}
        # BANK: park deep surplus (leaders of rich, peaceful nations)
        if (free_tr >= 300 and nat["account"] < 400
                and nat["treasury"] - _pending_bank(state, me) >= 300
                and (not foes or p in ("merchant", "diplomat"))):
            amt = min(150, free_tr - 100)
            return "bank", {"amount": amt}

    # 1. war response
    if foes:
        worst = min(foes, key=lambda f: state["nations"][f]["army"])
        if nat["army"] <= state["nations"][worst]["army"]:
            if p in ("diplomat", "populist", "scholar") or cit["credits"] < E.TRAIN_COST:
                return "peace", {"target": worst}
            return "train", {}
        # v4: at war and winning the roll — attack to take tiles
        if p == "militarist" or rng.random() < 0.4:
            targets = [f for f in foes if _attack_win(state, me, f)]
            if (targets and nat["tiles"] < E.TILE_CAP
                    and cit["credits"] >= E.ATTACK_CREDITS
                    and free_tr >= E.ATTACK_TREASURY + 5):
                best_t = max(targets, key=lambda f: state["nations"][f]["tiles"])
                return "attack", {"target": best_t}
        # at war and stronger: sabotage the weakest enemy, else train
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

    # ---- v4: diplomacy (pacts + trade treaties) ----
    if p in ("diplomat", "populist"):
        # pact with a strong non-ally we are not at war with
        if (free_tr >= E.PACT_COST + 10
                and len(_pacted_with(state, me)) < 2):
            cands = [n for n in state["nations"]
                     if n != me
                     and not E._at_war(state, me, n)
                     and not E._allied(state, me, n)
                     and n not in _pacted_with(state, me)]
            if cands and rng.random() < 0.35:
                strong = max(cands, key=lambda n: E.world_power(state, n))
                return "pact", {"target": strong}
        # trade treaty with a peaceful neighbour that has capacity
        cands = _treaty_candidates(state, nat, me, free_tr, pending_tr)
        if cands and rng.random() < 0.3:
            partner = min(cands, key=lambda n: E.world_power(state, n))
            res = max(E.RESOURCES, key=lambda r: nat["stock"].get(r, 0))
            return "treaty", {"target": partner, "resource": res}
    if p == "merchant":
        cands = _treaty_candidates(state, nat, me, free_tr, pending_tr)
        if cands and rng.random() < 0.2:
            partner = rng.choice(cands)
            res = max(E.RESOURCES, key=lambda r: nat["stock"].get(r, 0))
            return "treaty", {"target": partner, "resource": res}

    # 5. market: sell high, buy low (merchants always, others sometimes)
    sell = _sell_target(state, nat)
    if sell and (flags.get("boom") or rng.random() < (0.85 if p == "merchant" else 0.5)):
        return "market_sell", {"resource": sell}
    buy = _buy_target(state, nat)
    if buy and cit["credits"] >= E.BASE_PRICES[buy] + 10 and (flags.get("black") or rng.random() < (0.7 if p == "merchant" else 0.4)):
        return "market_buy", {"resource": buy}

    # 6. infrastructure: persona-favored building when we can afford it
    b = PREFERRED_BUILDING[p]
    spec = E.BUILDINGS[b]
    if nat["buildings"][b] < spec["cap"]:
        affordable = free_tr >= spec["cost"] + 10
        for res, amt in spec["resources"].items():
            affordable = affordable and nat["stock"].get(res, 0) >= amt
        if affordable and rng.random() < (0.8 if p in ("militarist", "scholar") else 0.5):
            return "infrastructure", {"building": b}
    # 6b. v5: free-stream building (aqueduct/observatory) when affordable
    b5 = V5_BUILDING.get(p)
    if state.get("v5") and b5 and nat["buildings"][b5] < E.BUILDINGS[b5]["cap"]:
        spec5 = E.BUILDINGS[b5]
        if free_tr >= spec5["cost"] + 10 and rng.random() < 0.6:
            return "infrastructure", {"building": b5}

    # 7. diplomacy: spy on the weakest rival (for intel) and ally the strongest
    if p in ("diplomat", "populist") and len(_allies(state, me)) < E.ALLY_MAX:
        best = _best_ally(state, me)
        if best is not None and rng.random() < 0.3:
            return "ally", {"target": best}
    if cit["credits"] >= E.SPY_COST + 10 and nat["stock"].get("grain", 0) >= E.SPY_GRAN:
        rivals = [n for n in state["nations"]
                  if n != me and not E._allied(state, me, n)]
        if rivals and rng.random() < (0.4 if p in ("diplomat", "militarist") else 0.15):
            weakest = min(rivals, key=lambda n: E.world_power(state, n))
            return "spy", {"target": weakest}

    # 8. titles: rich citizens buy prestige
    if cit["titles"] < E.TITLE_MAX and cit["credits"] >= E.TITLE_COST + 25 and rng.random() < (0.5 if p == "populist" else 0.3):
        return "title", {}

    # 9. economy: expand when the nation is rich and tiles are the point
    if free_tr >= E.EXPAND_COST * 2 and nat["tiles"] < E.TILE_CAP:
        if rng.random() < 0.4:
            return "expand", {}

    # 10. persona actions
    if p == "militarist":
        if nat["army"] >= 8 and cit["credits"] >= E.WAR_COST:
            targets = [n for n in state["nations"]
                       if n != me
                       and not E._at_war(state, me, n)
                       and not E._allied(state, me, n)]
            if targets:
                weakest = min(targets, key=lambda n: E.world_power(state, n))
                # v4: a militarist with a fat army breaks pacts when it pays
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
        return "work", {}
    # populist
    if state.get("v5") and cit["credits"] >= E.FESTIVAL_COST + 20 and rng.random() < 0.4:
        return "festival", {}
    if cit["credits"] >= E.CULTURE_COST and nat["culture"] < E.CULTURE_MAX and rng.random() < 0.5:
        return "culture", {}
    return "work", {}


if __name__ == "__main__":
    # quick smoke: one season of bots5 (v5 mechanics on), print the final leaderboard
    st = E.new_state(777, season="bots5smoke", n_seats=20, v5=True)
    log = []
    rejected = 0
    used = set()
    for turn in range(E.MAX_DAYS * 2):
        if st["winner"] is not None:
            break
        for cid in sorted(st["citizens"]):
            act, args = choose(st, cid)
            ok, _ = E.apply_action(st, cid, act, args)
            if ok:
                used.add(act)
            else:
                rejected += 1
        E.apply_turn(st, log)
    print(f"turns: {st['turn']}, winner: {st['winner']}, rejected: {rejected}")
    print("actions queued:", sorted(used))
    for n in E.power_ranking(st):
        nat = st["nations"][n]
        print(f"  {nat['name']:10s} tiles={nat['tiles']:2d} army={nat['army']:3d} "
              f"tech={nat['tech']:2d} tr={nat['treasury']:4d} gov={nat['gov']} tax={nat['tax']}%")
