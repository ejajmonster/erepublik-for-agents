"""eRepublik-for-agents engine v3. Deterministic, stdlib-only, no time deps.

Changes vs v2 (engine2.py — kept for replay; v1 is frozen):
- world market: 4 resources (wood, iron, grain, oil) with prices that shift
  once per day (seeded). Citizens buy with credits, sell back for treasury.
- nation stockpiles + buildings (infrastructure action):
    factory    +1 work income per level (cap 3)
    barracks   +1 army per level per day
    mine       +1 random resource per level per day
    university -2 research cost per level
  Building a level needs treasury AND resources from the stockpile.
- espionage:
    spy{target}     -15 cr + 10 grain: reveals the enemy's true treasury+army
                     in /api/state for 1 day
    sabotage{target}-20 cr + 8 oil: enemy army -1..3 (fails if they spent
                     on spies that day)
    spies{}         -15 cr: blocks sabotage against your nation today
- titles: `title{}` -50 cr, +1 title (max 5). Prestige = titles. Work +1 per
  title (max +3). Prestige is the election tie-breaker after votes and credits.
- daily world events (0.45 chance/day): harvest, bandits, trade boom,
  black market, plague
- world power ranking: power = 3*tiles + 2*army + treasury/2 + 4*tech
  + 2*culture + 3*sum(buildings). The season ends at day 40 (or last nation
  standing) and the leader is the highest-power nation. Top power nation gets
  +5 treasury/day ("world leader bonus").

Everything random derives from random.Random(f"{season}-{seed}-{turn}") or
random.Random(f"bot-{cid}-t{turn}-{seed}") (bots) — full replay from seed+log.
Same contract as v1/v2: new_state / apply_action / apply_turn / seal_hash.
"""
import hashlib
import json
import random

NATION_NAMES = ["Aurelia", "Brennia", "Cordovia", "Dalmara", "Estra"]
SPARE_NAMES = ["Ferra", "Galdor", "Helvia", "Ithria", "Jovia", "Kymra", "Lornia"]
PERSONAS = ["merchant", "militarist", "scholar", "diplomat", "populist"]
POLICIES = {
    "militarist": {"train_cost": 7},
    "merchant": {"work_bonus": 3},
    "scholar": {"research_cost": 11},
    "isolation": {"trade_bonus": 5},
}

START_TREASURY = 100
START_TILES = 10
START_CREDITS = 50
TRAIN_AMOUNT = 2
WORK_BASE = 6
TRAIN_COST = 12
RESEARCH_COST = 18
RESEARCH_MAX = 10
TRADE_GAIN = 8
TRADE_PARTNER = 3
CULTURE_COST = 5
CULTURE_MAX = 10
WAR_COST = 20
JOIN_COST = 30
EXPAND_COST = 25
TILE_CAP = 25
TILE_INCOME = 2
WAR_UPKEEP = 5
FOUND_COST = 100
FOUND_TILES = 5
FOUND_TREASURY = 50
MAX_NATIONS = 8
ALLY_MAX = 2
BREAK_ALLY_COST = 20
PEACE_FINE = 10
ELECTION_EVERY_DAYS = 10
MAX_DAYS = 40
SEATS = 20
ARMY_CAP = 200

# ---- v3: market ----
RESOURCES = ["wood", "iron", "grain", "oil"]
BASE_PRICES = {"wood": 6, "iron": 10, "grain": 4, "oil": 12}
START_STOCK = {"wood": 6, "iron": 4, "grain": 10, "oil": 2}

# ---- v3: buildings ----
BUILDINGS = {
    "factory":    {"cost": 30, "resources": {"wood": 4, "iron": 2},  "cap": 3},
    "barracks":   {"cost": 25, "resources": {"wood": 3, "iron": 3},  "cap": 3},
    "mine":       {"cost": 20, "resources": {"wood": 2, "iron": 2},  "cap": 3},
    "university": {"cost": 35, "resources": {"wood": 4, "grain": 2}, "cap": 3},
}

# ---- v3: espionage ----
SPY_COST = 15
SPY_GRAN = 10
SABOTAGE_COST = 20
SABOTAGE_OIL = 8
SPIES_DAY_COST = 15
TITLE_COST = 50
TITLE_MAX = 5
MAX_DAY = 5  # day index cap used in event rng seeds


def _canon(state):
    s = dict(state)
    s.pop("seals", None)
    s.pop("pending", None)
    s.pop("secrets", None)
    return json.dumps(s, sort_keys=True, separators=(",", ":"))


def seal_hash(state):
    return hashlib.sha256(_canon(state).encode()).hexdigest()


def _mk_nation(name, leader):
    return {
        "name": name, "treasury": START_TREASURY, "army": 0,
        "tech": 0, "culture": 0, "tiles": START_TILES, "leader": leader,
        "policy": None, "citizens": [],
        "stock": dict(START_STOCK),
        "buildings": {b: 0 for b in BUILDINGS},
    }


def new_state(seed, season="season3", n_seats=SEATS, citizen_names=None):
    assert n_seats % len(NATION_NAMES) == 0, "seats must divide nations"
    rng = random.Random(seed)
    per = n_seats // len(NATION_NAMES)
    citizens = {}
    nations = {}
    personas = []
    for _ in range(len(NATION_NAMES)):
        personas.extend(PERSONAS)
    rng.shuffle(personas)
    names = citizen_names or [f"bot{i:02d}" for i in range(n_seats)]
    assert len(names) == n_seats
    for i, name in enumerate(names):
        n = i // per
        if n not in nations:
            nations[n] = _mk_nation(NATION_NAMES[n], None)
        nations[n]["citizens"].append(i)
        citizens[i] = {
            "name": name, "model": None, "persona": personas[i],
            "country": n, "credits": START_CREDITS, "independent": False,
            "actions": 0, "voted_turn": -1, "policy_set": False, "titles": 0,
        }
    for n in nations.values():
        n["leader"] = n["citizens"][0]
    return {
        "season": season, "seed": seed, "turn": 0, "nations": nations,
        "citizens": citizens, "war": [], "alliances": [],
        "pending": {}, "log_index": 0,
        "elections": [], "winner": None, "seals": [], "recent": [],
        "market": dict(BASE_PRICES), "spied": {}, "spies_day": {},
        "day_flags": {}, "secrets": {},
    }


def day_of(state):
    return state["turn"] // 2


def is_election_turn(turn):
    d = turn // 2
    return turn % 2 == 1 and d > 0 and d % ELECTION_EVERY_DAYS == 0


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


def _event(state, msg):
    state["recent"].append(f"t{state['turn']:03d} {msg}")
    state["recent"] = state["recent"][-40:]


def _war_pair(state, a, b):
    a, b = min(a, b), max(a, b)
    if [a, b] not in state["war"]:
        state["war"].append([a, b])
        state["war"].sort()
        state["alliances"] = [x for x in state["alliances"] if x != [a, b]]
        _event(state, f"WAR {state['nations'][a]['name']} vs {state['nations'][b]['name']}")


def _at_war(state, a, b):
    return [min(a, b), max(a, b)] in state["war"]


def _allied(state, a, b):
    return [min(a, b), max(a, b)] in state["alliances"]


def _conquer(state, n):
    nat = state["nations"][n]
    _event(state, f"CONQUERED {nat['name']} (0 tiles)")
    for c in nat["citizens"]:
        state["citizens"][c]["country"] = None
        state["citizens"][c]["independent"] = True
    state["war"] = [w for w in state["war"] if n not in w]
    state["alliances"] = [w for w in state["alliances"] if n not in w]
    state["spied"] = {k: v for k, v in state["spied"].items() if n not in v}
    del state["nations"][n]


def _eff_army(state, n):
    nat = state["nations"][n]
    bonus = 0
    if nat["tech"] >= 10:
        bonus += 2
    if nat["culture"] >= 5:
        bonus += 1
    return nat["army"] + bonus


def _resolve_war(state, rng):
    for a, b in list(state["war"]):
        if a not in state["nations"] or b not in state["nations"]:
            state["war"].remove([a, b])
            continue
        na, nb = state["nations"][a], state["nations"][b]
        ja, jb = _eff_army(state, a), _eff_army(state, b)
        if ja == 0 and jb == 0:
            continue
        if ja > jb:
            dmg = max(1, int(ja * 0.25) + rng.randint(0, 2))
            nb["army"] = max(0, nb["army"] - dmg)
            _event(state, f"skirmish {na['name']} wins: {nb['name']} army {nb['army'] + dmg}->{nb['army']}")
        elif jb > ja:
            dmg = max(1, int(jb * 0.25) + rng.randint(0, 2))
            na["army"] = max(0, na["army"] - dmg)
            _event(state, f"skirmish {nb['name']} wins: {na['name']} army {na['army'] + dmg}->{na['army']}")
        else:
            na["army"] = max(0, na["army"] - 1)
            nb["army"] = max(0, nb["army"] - 1)
            _event(state, f"skirmish stalemate {na['name']}/{nb['name']}")
    for n in list(state["nations"]):
        if _eff_army(state, n) == 0 and _enemies(state, n):
            foes = sorted(_enemies(state, n), key=lambda f: -_eff_army(state, f))
            f = foes[0]
            lose = 1 if state["nations"][n]["culture"] >= 3 else 2
            lose = min(lose, state["nations"][n]["tiles"])
            state["nations"][n]["tiles"] -= lose
            state["nations"][f]["tiles"] += lose
            _event(state, f"{state['nations'][n]['name']} loses {lose} tile(s) to {state['nations'][f]['name']} (army 0)")
            if state["nations"][n]["tiles"] == 0:
                _conquer(state, n)


def _elect(state):
    state["elections"].append(state["turn"])
    for n, nat in state["nations"].items():
        votes = {}
        for c in nat["citizens"]:
            act = state["pending"].get(c)
            if act and act.get("action") == "vote":
                cand = act.get("args", {}).get("candidate")
                if cand in nat["citizens"]:
                    votes[cand] = votes.get(cand, 0) + 1
        if votes:
            best = max(votes, key=lambda k: (votes[k], state["citizens"][k]["credits"], state["citizens"][k]["titles"]))
        else:
            best = nat["leader"]
        if best != nat["leader"]:
            _event(state, f"ELECTION {nat['name']}: leader {state['citizens'][nat['leader']]['name']} -> {state['citizens'][best]['name']} ({votes})")
        else:
            _event(state, f"ELECTION {nat['name']}: {state['citizens'][best]['name']} re-elected ({votes})")
        nat["leader"] = best


def world_power(state, n):
    """v3: the e-republika ranking formula."""
    nat = state["nations"][n]
    return (3 * nat["tiles"] + 2 * nat["army"] + nat["treasury"] // 2
            + 4 * nat["tech"] + 2 * nat["culture"] + 3 * sum(nat["buildings"].values()))


def power_ranking(state):
    """[nation_id, ...] sorted by world power, strongest first."""
    return sorted(state["nations"].keys(), key=lambda n: -world_power(state, n))


def _check_end(state):
    if state["winner"] is not None:
        return
    alive = list(state["nations"].keys())
    if len(alive) <= 1:
        n = alive[0] if alive else None
        state["winner"] = n
        _event(state, f"SEASON OVER: {state['nations'][n]['name'] if n else '—'} is the last nation")
        if n:
            for c in state["nations"][n]["citizens"]:
                state["citizens"][c]["credits"] += 100
    # off-by-one fix (2026-09-25): the check runs during turn t's apply_turn,
    # before the counter increments. At the final turn 79 (the 40th day,
    # 0-indexed) the season must end; old `day >= MAX_DAYS` never fired in 80 turns.
    elif state["turn"] >= 2 * MAX_DAYS - 1:
        best = power_ranking(state)[0]
        state["winner"] = best
        _event(state, f"SEASON OVER (turn {state['turn']}): {state['nations'][best]['name']} leads (power {world_power(state, best)})")
        for c in state["nations"][best]["citizens"]:
            state["citizens"][c]["credits"] += 100


def _used_names(state):
    return {state["nations"][n]["name"] for n in state["nations"]} | {
        state["citizens"][c]["name"] for c in state["citizens"]
    }


def apply_action(state, cid, action, args=None):
    """Validate and queue one action for the citizen's current turn."""
    args = args or {}
    cit = state["citizens"][cid]
    if state["winner"] is not None:
        return False, "season over"
    if state["pending"].get(cid):
        return False, "action already queued this turn"
    nat = state["nations"].get(cit["country"]) if not cit["independent"] else None

    if action == "work":
        ok = True
    elif action == "train":
        ok = nat is not None
    elif action == "research":
        ok = nat is not None and nat["tech"] < RESEARCH_MAX
    elif action == "culture":
        ok = nat is not None and nat["culture"] < CULTURE_MAX
    elif action == "trade":
        ok = True
    elif action == "expand":
        ok = (nat is not None and nat["treasury"] >= EXPAND_COST and nat["tiles"] < TILE_CAP)
    elif action == "declare_war":
        t = args.get("target")
        ok = (nat is not None and t is not None and t in state["nations"]
              and t != cit["country"] and not _at_war(state, cit["country"], t)
              and not _allied(state, cit["country"], t))
    elif action == "peace":
        t = args.get("target")
        ok = (nat is not None and t is not None and t in state["nations"]
              and _at_war(state, cit["country"], t))
    elif action == "ally":
        t = args.get("target")
        ok = (nat is not None and t is not None and t in state["nations"]
              and t != cit["country"] and not _at_war(state, cit["country"], t)
              and not _allied(state, cit["country"], t)
              and len(_allies(state, cit["country"])) < ALLY_MAX
              and len(_allies(state, t)) < ALLY_MAX)
    elif action == "break_alliance":
        t = args.get("target")
        ok = (nat is not None and t is not None and t in state["nations"]
              and _allied(state, cit["country"], t)
              and nat["treasury"] >= BREAK_ALLY_COST)
    elif action == "vote":
        cand = args.get("candidate")
        ok = (nat is not None and is_election_turn(state["turn"]) and cand in nat["citizens"])
    elif action == "set_policy":
        ok = (nat is not None and nat["leader"] == cid and args.get("policy") in POLICIES)
    elif action == "join":
        t = args.get("target")
        ok = (cit["independent"] and t is not None and t in state["nations"])
    elif action == "found":
        nm = (args.get("name") or "").strip()
        ok = (cit["independent"] and cit["credits"] >= FOUND_COST
              and len(state["nations"]) < MAX_NATIONS
              and 2 <= len(nm) <= 24
              and nm not in _used_names(state))
    # ---- v3 actions ----
    elif action == "market_buy":
        res = args.get("resource")
        ok = res in RESOURCES and cit["credits"] >= BASE_PRICES[res]
    elif action == "market_sell":
        res = args.get("resource")
        ok = (res in RESOURCES and nat is not None
              and nat["stock"].get(res, 0) >= 1)
    elif action == "infrastructure":
        b = args.get("building")
        ok = (nat is not None and b in BUILDINGS
              and nat["buildings"][b] < BUILDINGS[b]["cap"])
    elif action == "spy":
        t = args.get("target")
        ok = (nat is not None and t is not None and t in state["nations"]
              and t != cit["country"] and not _allied(state, cit["country"], t)
              and cit["credits"] >= SPY_COST
              and nat["stock"].get("grain", 0) >= SPY_GRAN)
    elif action == "sabotage":
        t = args.get("target")
        ok = (nat is not None and t is not None and t in state["nations"]
              and t != cit["country"] and not _allied(state, cit["country"], t)
              and cit["credits"] >= SABOTAGE_COST
              and nat["stock"].get("oil", 0) >= SABOTAGE_OIL)
    elif action == "spies":
        ok = nat is not None and cit["credits"] >= SPIES_DAY_COST
    elif action == "title":
        ok = cit["titles"] < TITLE_MAX and cit["credits"] >= TITLE_COST
    else:
        ok = False
    if not ok:
        return False, f"invalid action {action}"
    state["pending"][cid] = {"action": action, "args": args}
    return True, "queued"


def _day_shift_prices(state, rng):
    """One market move per day (day = turn//2), deterministic from turn."""
    for res in RESOURCES:
        state["market"][res] = max(
            2, min(25, state["market"][res] + rng.randint(-3, 3)))


def _day_events(state, rng):
    """One world event per day, ~45% chance. Flags are visible the next turn (same day)."""
    flags = {"boom": False, "black": False}
    if rng.random() < 0.45:
        kind = rng.choice(["harvest", "bandits", "boom", "black", "plague"])
        if kind == "harvest":
            for n in state["nations"].values():
                n["stock"]["grain"] += 2
            _event(state, "EVENT harvest: +2 grain to every nation's stockpile")
        elif kind == "bandits":
            n = rng.choice(list(state["nations"].keys()))
            nat = state["nations"][n]
            res = rng.choice(RESOURCES)
            take = min(3, nat["stock"].get(res, 0))
            nat["stock"][res] -= take
            _event(state, f"EVENT bandits raided {nat['name']}: -{take} {res}")
        elif kind == "boom":
            flags["boom"] = True
            _event(state, "EVENT trade boom: selling today pays 2x")
        elif kind == "black":
            flags["black"] = True
            _event(state, "EVENT black market: buying today costs half")
        else:
            for nat in state["nations"].values():
                if nat["army"] > 0:
                    nat["army"] -= 1
            _event(state, "EVENT plague: every army -1")
    # reset daily espionage state; keep flags for the rest of the day
    state["spied"] = {}
    state["spies_day"] = {}
    state["day_flags"] = flags
    # daily building effects
    for n, nat in state["nations"].items():
        if nat["buildings"]["barracks"]:
            gained = min(nat["buildings"]["barracks"], ARMY_CAP - nat["army"])
            if gained:
                nat["army"] += gained
                _event(state, f"{nat['name']} barracks trained +{gained} (army {nat['army']})")
        for _ in range(nat["buildings"]["mine"]):
            res = rng.choice(RESOURCES)
            nat["stock"][res] += 1
        if nat["buildings"]["mine"]:
            _event(state, f"{nat['name']} mines yielded +{nat['buildings']['mine']} resource(s)")


def apply_turn(state, log_lines=None):
    """Resolve the current turn, run end-of-turn effects, seal, advance.

    Day roll (prices + event) happens BEFORE actions of the day's first turn,
    so an event flag applies to both turns of the day and spy intel lasts
    until the next day's roll. Deterministic: single rng per turn.
    """
    t = state["turn"]
    rng = random.Random(f"{state['season']}-{state['seed']}-{t}")
    applied = []
    do_election = is_election_turn(t)
    new_day = (t % 2) == 0

    if new_day:
        _day_shift_prices(state, rng)
        _day_events(state, rng)

    for cid in sorted(state["citizens"]):
        act = state["pending"].get(cid)
        if not act:
            continue
        action, args = act["action"], act.get("args", {})
        cit = state["citizens"][cid]
        if action == "vote":
            state["pending"].pop(cid, None)
            continue
        nat = state["nations"].get(cit["country"]) if not cit["independent"] else None
        pol = (nat or {}).get("policy")
        done = None

        if action == "work":
            gain = WORK_BASE
            gain += (POLICIES.get(pol) or {}).get("work_bonus", 0)
            if nat:
                gain += nat["buildings"]["factory"]
                if nat["tech"] >= 2:
                    gain += 1
            gain += min(3, cit["titles"])
            if cit["independent"]:
                gain += 3
            cit["credits"] += gain
            done = f"{cit['name']} work +{gain}"
        elif action == "train" and nat:
            cost = (POLICIES.get(pol) or {}).get("train_cost", TRAIN_COST)
            amt = TRAIN_AMOUNT + (1 if nat["tech"] >= 4 else 0)
            if cit["credits"] >= cost:
                cit["credits"] -= cost
                nat["army"] = min(ARMY_CAP, nat["army"] + amt)
                done = f"{cit['name']} train: {nat['name']} army -> {nat['army']} (+{amt})"
            else:
                done = f"{cit['name']} train failed (credits {cit['credits']} < {cost})"
        elif action == "research" and nat:
            cost = (POLICIES.get(pol) or {}).get("research_cost", RESEARCH_COST)
            if nat["tech"] >= 6:
                cost = max(1, cost - 6)
            cost = max(1, cost - 2 * nat["buildings"]["university"])
            if cit["credits"] >= cost:
                cit["credits"] -= cost
                nat["tech"] += 1
                done = f"{cit['name']} research: {nat['name']} tech -> {nat['tech']}"
            else:
                done = f"{cit['name']} research failed"
        elif action == "culture" and nat:
            if cit["credits"] >= CULTURE_COST:
                cit["credits"] -= CULTURE_COST
                nat["culture"] += 1
                done = f"{cit['name']} culture: {nat['name']} culture -> {nat['culture']}"
            else:
                done = f"{cit['name']} culture failed"
        elif action == "trade":
            tg = args.get("target")
            if tg is None:
                neutrals = [n for n in state["nations"]
                            if (cit["independent"] or n != cit["country"])
                            and not _at_war(state, cit["country"] if not cit["independent"] else -1, n)]
                if cit["independent"]:
                    neutrals = list(state["nations"].keys())
                if not neutrals:
                    done = f"{cit['name']} trade failed (no neutral)"
                    tg = None
                else:
                    tg = neutrals[rng.randrange(len(neutrals))]
            if tg is not None and tg in state["nations"] and not _at_war(state, cit["country"] if not cit["independent"] else -1, tg):
                gain = TRADE_GAIN
                gain += (POLICIES.get((state["nations"].get(cit["country"]) or {}).get("policy")) or {}).get("trade_bonus", 0)
                if not cit["independent"] and state["nations"][cit["country"]]["tech"] >= 8:
                    gain += 4
                cit["credits"] += gain
                state["nations"][tg]["treasury"] += TRADE_PARTNER
                done = f"{cit['name']} trade with {state['nations'][tg]['name']} +{gain}"
            else:
                done = f"{cit['name']} trade failed (no target)"
        elif action == "expand" and nat:
            if nat["treasury"] >= EXPAND_COST and nat["tiles"] < TILE_CAP:
                nat["treasury"] -= EXPAND_COST
                nat["tiles"] += 1
                done = f"{cit['name']} expands {nat['name']} -> {nat['tiles']} tiles"
            else:
                done = f"{cit['name']} expand failed"
        elif action == "declare_war" and nat:
            tg = args.get("target")
            if cit["credits"] >= WAR_COST and tg in state["nations"] and not _at_war(state, cit["country"], tg) and not _allied(state, cit["country"], tg):
                cit["credits"] -= WAR_COST
                _war_pair(state, cit["country"], tg)
                done = f"{cit['name']} declares war {nat['name']} vs {state['nations'][tg]['name']}"
            else:
                done = f"{cit['name']} declare_war failed"
        elif action == "peace" and nat:
            tg = args.get("target")
            pair = [min(cit["country"], tg), max(cit["country"], tg)]
            if pair not in state["war"]:
                done = f"{cit['name']} peace failed (already at peace)"
            else:
                my, their = nat["army"], state["nations"][tg]["army"]
                if my > their * 1.5:
                    state["war"].remove(pair)
                    _event(state, f"PEACE {nat['name']} / {state['nations'][tg]['name']}")
                    done = f"{cit['name']} peace accepted (army {my} > {their}*1.5)"
                elif nat["treasury"] >= PEACE_FINE:
                    nat["treasury"] -= PEACE_FINE
                    state["nations"][tg]["treasury"] += PEACE_FINE
                    state["war"].remove(pair)
                    _event(state, f"PEACE {nat['name']} / {state['nations'][tg]['name']} (fine {PEACE_FINE})")
                    done = f"{cit['name']} peace accepted (fine {PEACE_FINE})"
                else:
                    done = f"{cit['name']} peace rejected (army {my} <= {their}*1.5, treasury {nat['treasury']} < {PEACE_FINE})"
        elif action == "ally" and nat:
            tg = args.get("target")
            pair = [min(cit["country"], tg), max(cit["country"], tg)]
            if pair not in state["alliances"]:
                state["alliances"].append(pair)
                state["alliances"].sort()
                _event(state, f"ALLIANCE {nat['name']} / {state['nations'][tg]['name']}")
            done = f"{cit['name']} allies {nat['name']} with {state['nations'][tg]['name']}"
        elif action == "break_alliance" and nat:
            tg = args.get("target")
            pair = [min(cit["country"], tg), max(cit["country"], tg)]
            if pair in state["alliances"]:
                state["alliances"].remove(pair)
                nat["treasury"] -= BREAK_ALLY_COST
                _event(state, f"BREAK {nat['name']} / {state['nations'][tg]['name']} (fine {BREAK_ALLY_COST})")
                done = f"{cit['name']} breaks alliance with {state['nations'][tg]['name']} (fine {BREAK_ALLY_COST})"
            else:
                done = f"{cit['name']} break_alliance failed"
        elif action == "set_policy" and nat and nat["leader"] == cid:
            nat["policy"] = args.get("policy")
            cit["policy_set"] = True
            done = f"{cit['name']} sets {nat['name']} policy = {nat['policy']}"
        elif action == "join" and cit["independent"]:
            tg = args.get("target")
            if cit["credits"] >= JOIN_COST and tg in state["nations"]:
                cit["credits"] -= JOIN_COST
                cit["independent"] = False
                cit["country"] = tg
                state["nations"][tg]["citizens"].append(cid)
                done = f"{cit['name']} joins {state['nations'][tg]['name']}"
            else:
                done = f"{cit['name']} join failed"
        elif action == "found" and cit["independent"]:
            nm = (args.get("name") or "").strip()
            if cit["credits"] >= FOUND_COST and len(state["nations"]) < MAX_NATIONS and nm not in _used_names(state):
                cit["credits"] -= FOUND_COST
                nid = max(state["nations"].keys()) + 1
                state["nations"][nid] = _mk_nation(nm, cid)
                state["nations"][nid]["treasury"] = FOUND_TREASURY
                state["nations"][nid]["tiles"] = FOUND_TILES
                state["nations"][nid]["citizens"] = [cid]
                cit["independent"] = False
                cit["country"] = nid
                _event(state, f"FOUNDED {nm} by {cit['name']}")
                done = f"{cit['name']} founds {nm}"
            else:
                done = f"{cit['name']} found failed"
        # ---- v3 actions ----
        elif action == "market_buy":
            res = args.get("resource")
            price = BASE_PRICES[res]
            if state.get("day_flags", {}).get("black"):
                price = max(1, price // 2)
            if cit["credits"] >= price:
                # price paid is the market (stable) price; market price affects selling
                cit["credits"] -= price
                if nat is not None:
                    nat["stock"][res] += 1
                    done = f"{cit['name']} bought 1 {res} for {price} -> {nat['name']} stockpile"
                else:
                    cit["credits"] += price  # refund: independents can't stockpile
                    done = f"{cit['name']} market_buy failed (independents can't stockpile)"
            else:
                done = f"{cit['name']} market_buy failed (no credits)"
        elif action == "market_sell" and nat:
            res = args.get("resource")
            if nat["stock"].get(res, 0) >= 1:
                nat["stock"][res] -= 1
                price = state["market"][res]
                if state.get("day_flags", {}).get("boom"):
                    price *= 2
                gain = max(1, price // 2)
                cit["credits"] += gain
                nat["treasury"] += gain
                done = f"{cit['name']} sold 1 {res} for {gain} (price {price})"
            else:
                done = f"{cit['name']} market_sell failed (no {res})"
        elif action == "infrastructure" and nat:
            b = args.get("building")
            spec = BUILDINGS[b]
            if nat["buildings"][b] < spec["cap"]:
                affordable = nat["treasury"] >= spec["cost"]
                for res, amt in spec["resources"].items():
                    affordable = affordable and nat["stock"].get(res, 0) >= amt
                if affordable:
                    nat["treasury"] -= spec["cost"]
                    for res, amt in spec["resources"].items():
                        nat["stock"][res] -= amt
                    nat["buildings"][b] += 1
                    _event(state, f"BUILD {nat['name']} {b} lv{nat['buildings'][b]}")
                    done = f"{cit['name']} built {b} lv{nat['buildings'][b]} in {nat['name']}"
                else:
                    done = f"{cit['name']} build {b} failed (need {spec['cost']} tr + {spec['resources']})"
            else:
                done = f"{cit['name']} build {b} failed (max level)"
        elif action == "spy" and nat:
            tg = args.get("target")
            if cit["credits"] >= SPY_COST and nat["stock"].get("grain", 0) >= SPY_GRAN:
                cit["credits"] -= SPY_COST
                nat["stock"]["grain"] -= SPY_GRAN
                target_n = state["nations"][tg]
                spy_list = state["spied"].setdefault(nat["name"], [])
                if tg not in spy_list:
                    spy_list.append(tg)
                _event(state, f"SPY {nat['name']} spies on {target_n['name']} (treasury {target_n['treasury']}, army {target_n['army']})")
                done = f"{cit['name']} spied on {target_n['name']} — intel: treasury {target_n['treasury']}, army {target_n['army']}"
            else:
                done = f"{cit['name']} spy failed (need {SPY_COST} cr + {SPY_GRAN} grain)"
        elif action == "sabotage" and nat:
            tg = args.get("target")
            if cit["credits"] >= SABOTAGE_COST and nat["stock"].get("oil", 0) >= SABOTAGE_OIL:
                cit["credits"] -= SABOTAGE_COST
                nat["stock"]["oil"] -= SABOTAGE_OIL
                target_n = state["nations"][tg]
                if state["spies_day"].get(target_n["name"]):
                    _event(state, f"SABOTAGE {nat['name']} -> {target_n['name']} FAIL (counter-espionage)")
                    done = f"{cit['name']} sabotage {target_n['name']} failed (their spies blocked it)"
                else:
                    dmg = rng.randint(1, 3)
                    target_n["army"] = max(0, target_n["army"] - dmg)
                    _event(state, f"SABOTAGE {nat['name']} -> {target_n['name']} army -{dmg} (now {target_n['army']})")
                    done = f"{cit['name']} sabotaged {target_n['name']}: army -{dmg}"
            else:
                done = f"{cit['name']} sabotage failed (need {SABOTAGE_COST} cr + {SABOTAGE_OIL} oil)"
        elif action == "spies" and nat:
            if cit["credits"] >= SPIES_DAY_COST:
                cit["credits"] -= SPIES_DAY_COST
                state["spies_day"][nat["name"]] = True
                _event(state, f"{nat['name']} spends on counter-espionage today")
                done = f"{cit['name']} deployed {nat['name']} spies for today"
            else:
                done = f"{cit['name']} spies failed (no credits)"
        elif action == "title":
            if cit["credits"] >= TITLE_COST and cit["titles"] < TITLE_MAX:
                cit["credits"] -= TITLE_COST
                cit["titles"] += 1
                _event(state, f"TITLE {cit['name']} earned title (x{cit['titles']})")
                done = f"{cit['name']} bought a title (x{cit['titles']})"
            else:
                done = f"{cit['name']} title failed"

        if done:
            cit["actions"] += 1
            applied.append({"citizen": cid, "name": cit["name"], "action": done,
                            "raw_action": action, "args": args or {}})
        state["pending"].pop(cid, None)

    if do_election:
        _elect(state)

    # economics: tile income, culture bonus, war upkeep, world leader bonus
    if state["nations"]:
        top = power_ranking(state)[0]
        state["nations"][top]["treasury"] += 5
    for n in state["nations"].values():
        n["treasury"] += n["tiles"] * TILE_INCOME
        if n["culture"]:
            n["treasury"] += n["culture"]
    for pair in state["war"]:
        if pair[0] in state["nations"]:
            state["nations"][pair[0]]["treasury"] = max(0, state["nations"][pair[0]]["treasury"] - WAR_UPKEEP)
        if pair[1] in state["nations"]:
            state["nations"][pair[1]]["treasury"] = max(0, state["nations"][pair[1]]["treasury"] - WAR_UPKEEP)

    _resolve_war(state, rng)
    _check_end(state)

    prev = state["seals"][-1]["sha"] if state["seals"] else "GENESIS"
    sha = seal_hash(state)
    state["seals"].append({
        "turn": t, "day": t // 2, "prev": prev, "sha": sha,
        "actions": len(applied), "winner": state["winner"],
    })
    if log_lines is not None:
        for a in applied:
            log_lines.append({"turn": t, "day": t // 2, "type": "action", **a})
        log_lines.append({"turn": t, "day": t // 2, "type": "turn",
                          "seal": sha, "winner": state["winner"],
                          "recent": list(state["recent"][-10:])})

    state["turn"] = t + 1
    return applied
