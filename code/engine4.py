"""eRepublik-for-agents engine v4 (candidate for season 3; season 2 runs on v3).

Deterministic, stdlib-only, no time deps. Same contract as v1/v2/v3:
new_state / apply_action / apply_turn / seal_hash. Replay = seed + log.

Changes vs v3 (engine3.py):
- GOVERNMENTS: each nation has a gov type, set by the leader:
    democracy    elections run (v3 schedule), work +1, tax cap 5
    dictatorship no elections (leader keeps power), train cost -4, tax cap 10
    oligarchy    elections run, research cost -5, tax cap 7
  `set_government{type}` -40 treasury, leader only.
- TAXATION: `set_tax{rate}` leader only, rate 0..cap(gov). Every citizen's
  `work` pays floor(gain * rate / 10) into the treasury.
- REAL WAR: `attack{target}` (-30 credits, -10 treasury) requires an active
  war. Offensive roll: (my army + tech) vs (their army + 2*culture).
  Win: take 1 tile, their army -3. Loss: my army -4.
- TRIBUTE: when a nation is conquered, the highest-power survivor takes
  25% of the conquered treasury.
- PACTS: `pact{target}` mutual non-aggression for 10 days, -20 treasury.
  Declaring war through a live pact: -25 treasury (betrayal fine), pact ends.
- TRADE TREATIES: `treaty{target,resource}` both nations +1 of that resource
  per day for 10 days, -30 treasury, max 2 treaties per nation, not at war.

Everything random derives from random.Random(f"{season}-{seed}-{turn}")
(or `bot-*` rng in bots) — full replay from seed+log.
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

# ---- v4: governments ----
GOVS = {
    "democracy":    {"tax_cap": 25, "elections": True,  "work_bonus": 1,
                     "train_bonus": 0,  "research_bonus": 0,  "culture_bonus": 0},
    "dictatorship": {"tax_cap": 100, "elections": False, "work_bonus": 0,
                     "train_bonus": 4,  "research_bonus": 0,  "culture_bonus": 0},
    "oligarchy":    {"tax_cap": 50, "elections": True,  "work_bonus": 0,
                     "train_bonus": 0,  "research_bonus": 5,  "culture_bonus": 0},
}
SET_GOV_COST = 40
SET_GOV_COST_TREASURY = 40

# ---- v4: war / diplomacy ----
ATTACK_CREDITS = 30
ATTACK_TREASURY = 10
ATTACK_WIN_DMG = 3
ATTACK_LOSE_DMG = 4
TRIBUTE_SHARE = 25  # percent
PACT_COST = 20
PACT_DAYS = 10
BETRAYAL_FINE = 25
TREATY_COST = 30
TREATY_DAYS = 10
TREATY_MAX = 2


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
        "gov": "democracy", "tax": 0,
    }


def new_state(seed, season="season4", n_seats=SEATS, citizen_names=None):
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
        "pacts": [], "treaties": [],
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


def _pact_with(state, a, b):
    """Index of a live (unexpired) pact between a and b, or -1."""
    i, ab = -1, sorted((a, b))
    for k, pact in enumerate(state["pacts"]):
        x, y, end = pact
        if sorted((x, y)) == ab and end > day_of(state):
            i = k
            break
    return i


def _treaties_of(state, n):
    return [t for t in state["treaties"] if n in (t[0], t[1])
            and t[3] > day_of(state)]


def _event(state, msg):
    state["recent"].append(f"t{state['turn']:03d} {msg}")
    state["recent"] = state["recent"][-40:]


def _war_pair(state, a, b):
    a, b = min(a, b), max(a, b)
    if [a, b] not in state["war"]:
        state["war"].append([a, b])
        state["war"].sort()
        state["alliances"] = [x for x in state["alliances"] if x != [a, b]]
        state["pacts"] = [p for p in state["pacts"]
                          if sorted((p[0], p[1])) != [a, b]]
        _event(state, f"WAR {state['nations'][a]['name']} vs {state['nations'][b]['name']}")


def _at_war(state, a, b):
    return [min(a, b), max(a, b)] in state["war"]


def _allied(state, a, b):
    return [min(a, b), max(a, b)] in state["alliances"]


def _conquer(state, n):
    nat = state["nations"][n]
    _event(state, f"CONQUERED {nat['name']} (0 tiles)")
    # v4: tribute to the strongest survivor
    if state["nations"]:
        strongest = power_ranking(state)[0]
        tribute = nat["treasury"] * TRIBUTE_SHARE // 100
        if tribute > 0:
            state["nations"][strongest]["treasury"] += tribute
            _event(state, f"TRIBUTE {state['nations'][strongest]['name']} takes {tribute} from the spoils of {nat['name']}")
    for c in nat["citizens"]:
        state["citizens"][c]["country"] = None
        state["citizens"][c]["independent"] = True
    state["war"] = [w for w in state["war"] if n not in w]
    state["alliances"] = [w for w in state["alliances"] if n not in w]
    state["pacts"] = [p for p in state["pacts"] if n not in (p[0], p[1])]
    state["treaties"] = [t for t in state["treaties"] if n not in (t[0], t[1])]
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


def _elect(state, ballots=None):
    ballots = ballots or {}
    state["elections"].append(state["turn"])
    for n, nat in state["nations"].items():
        if not GOVS[nat["gov"]]["elections"]:
            _event(state, f"ELECTION {nat['name']}: no elections ({nat['gov']}) — {state['citizens'][nat['leader']]['name']} holds power")
            continue
        votes = ballots.get(str(n), {})
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
    """v3: the e-republika ranking formula (unchanged in v4)."""
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
    elif day_of(state) >= MAX_DAYS:
        best = power_ranking(state)[0]
        state["winner"] = best
        _event(state, f"SEASON OVER (day {MAX_DAYS}): {state['nations'][best]['name']} leads (power {world_power(state, best)})")
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
    # ---- v4 actions ----
    elif action == "set_government":
        g = args.get("government")
        ok = (nat is not None and nat["leader"] == cid and g in GOVS
              and g != nat["gov"] and nat["treasury"] >= SET_GOV_COST_TREASURY)
    elif action == "set_tax":
        rate = args.get("rate")
        ok = (nat is not None and nat["leader"] == cid and isinstance(rate, int)
              and 0 <= rate <= GOVS[nat["gov"]]["tax_cap"])
    elif action == "attack":
        t = args.get("target")
        ok = (nat is not None and t is not None and t in state["nations"]
              and _at_war(state, cit["country"], t)
              and cit["credits"] >= ATTACK_CREDITS
              and nat["treasury"] >= ATTACK_TREASURY)
    elif action == "pact":
        t = args.get("target")
        ok = (nat is not None and t is not None and t in state["nations"]
              and t != cit["country"] and not _at_war(state, cit["country"], t)
              and not _allied(state, cit["country"], t)
              and _pact_with(state, cit["country"], t) < 0
              and nat["treasury"] >= PACT_COST)
    elif action == "treaty":
        t = args.get("target")
        res = args.get("resource")
        ok = (nat is not None and t is not None and t in state["nations"]
              and t != cit["country"] and not _at_war(state, cit["country"], t)
              and res in RESOURCES
              and len(_treaties_of(state, cit["country"])) < TREATY_MAX
              and len(_treaties_of(state, t)) < TREATY_MAX
              and nat["treasury"] >= TREATY_COST)
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


def _day_diplomacy(state, rng):
    """v4: trade treaties deliver their daily resource; expired pacts/treaties drop."""
    d = day_of(state)
    for a, b, res, end in state["treaties"]:
        if a in state["nations"] and b in state["nations"]:
            state["nations"][a]["stock"][res] += 1
            state["nations"][b]["stock"][res] += 1
    state["treaties"] = [t for t in state["treaties"] if t[3] > d]
    state["pacts"] = [p for p in state["pacts"] if p[2] > d]


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
    ballots = {}

    if new_day:
        _day_shift_prices(state, rng)
        _day_diplomacy(state, rng)
        _day_events(state, rng)

    for cid in sorted(state["citizens"]):
        act = state["pending"].get(cid)
        if not act:
            continue
        action, args = act["action"], act.get("args", {})
        cit = state["citizens"][cid]
        if action == "vote":
            # v4 fix: collect the ballot here; v3 popped votes before _elect could see them.
            # The vote is logged so a replay can rebuild the ballot deterministically.
            if do_election and not cit["independent"] and cit["country"] in state["nations"]:
                cand = act.get("args", {}).get("candidate")
                if cand in state["nations"][cit["country"]]["citizens"]:
                    nkey = str(cit["country"])
                    ballots.setdefault(nkey, {})
                    ballots[nkey][cand] = ballots[nkey].get(cand, 0) + 1
                    cit["actions"] += 1
                    applied.append({"citizen": cid, "name": cit["name"],
                                    "action": f"{cit['name']} votes for {state['citizens'][cand]['name']}",
                                    "raw_action": "vote", "args": act.get("args", {}) or {}})
            state["pending"].pop(cid, None)
            continue
        nat = state["nations"].get(cit["country"]) if not cit["independent"] else None
        pol = (nat or {}).get("policy")
        gov = GOVS[(nat or {}).get("gov", "democracy")]
        done = None

        if action == "work":
            gain = WORK_BASE
            gain += (POLICIES.get(pol) or {}).get("work_bonus", 0)
            gain += gov.get("work_bonus", 0)
            if nat:
                gain += nat["buildings"]["factory"]
                if nat["tech"] >= 2:
                    gain += 1
            gain += min(3, cit["titles"])
            if cit["independent"]:
                gain += 3
            # v4: taxation
            taxed = 0
            if nat and nat["tax"] > 0:
                taxed = gain * nat["tax"] // 100
                nat["treasury"] += taxed
            cit["credits"] += gain - taxed
            done = f"{cit['name']} work +{gain - taxed}" + (
                f" (tax {taxed} -> {nat['name']})" if taxed else "")
        elif action == "train" and nat:
            cost = (POLICIES.get(pol) or {}).get("train_cost", TRAIN_COST)
            cost = max(1, cost - gov.get("train_bonus", 0))
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
            cost = max(1, cost - gov.get("research_bonus", 0))
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
                betrayed = _pact_with(state, cit["country"], tg) >= 0
                if betrayed:
                    fine = min(BETRAYAL_FINE, nat["treasury"])
                    nat["treasury"] -= fine
                    _event(state, f"BETRAYAL {nat['name']} breaks the pact with {state['nations'][tg]['name']} (fine {fine})")
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
                cit["credits"] -= price
                if nat is not None:
                    nat["stock"][res] += 1
                    done = f"{cit['name']} bought 1 {res} for {price} -> {nat['name']} stockpile"
                else:
                    cit["credits"] += price
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
        # ---- v4 actions ----
        elif action == "set_government" and nat and nat["leader"] == cid:
            g = args.get("government")
            if nat["treasury"] >= SET_GOV_COST_TREASURY:
                nat["treasury"] -= SET_GOV_COST_TREASURY
                nat["gov"] = g
                cap = GOVS[g]["tax_cap"]
                if nat["tax"] > cap:
                    nat["tax"] = cap
                    done = (f"{cit['name']} sets {nat['name']} government = {g} "
                            f"(tax clamped {cap})")
                else:
                    done = f"{cit['name']} sets {nat['name']} government = {g}"
                _event(state, f"GOV {nat['name']} -> {g}")
            else:
                done = f"{cit['name']} set_government failed (treasury {nat['treasury']} < {SET_GOV_COST_TREASURY})"
        elif action == "set_tax" and nat and nat["leader"] == cid:
            rate = args.get("rate")
            nat["tax"] = rate
            done = f"{cit['name']} sets {nat['name']} tax = {rate}%"
        elif action == "attack" and nat:
            tg = args.get("target")
            if (cit["credits"] >= ATTACK_CREDITS and nat["treasury"] >= ATTACK_TREASURY
                    and tg in state["nations"] and _at_war(state, cit["country"], tg)):
                cit["credits"] -= ATTACK_CREDITS
                nat["treasury"] -= ATTACK_TREASURY
                tgt = state["nations"][tg]
                atk = nat["army"] + nat["tech"]
                dfn = tgt["army"] + 2 * tgt["culture"]
                if atk > dfn:
                    tgt["army"] = max(0, tgt["army"] - ATTACK_WIN_DMG)
                    if nat["tiles"] < TILE_CAP and tgt["tiles"] > 0:
                        nat["tiles"] += 1
                        tgt["tiles"] -= 1
                        _event(state, f"ATTACK {nat['name']} takes 1 tile from {tgt['name']} (army {nat['army']}+tech {nat['tech']} vs {tgt['army']}+2c {2 * tgt['culture']})")
                        done = f"{cit['name']} attack: {nat['name']} captures 1 tile (now {nat['tiles']}), {tgt['name']} army -{ATTACK_WIN_DMG}"
                    else:
                        _event(state, f"ATTACK {nat['name']} beats {tgt['name']} but cannot take a tile")
                        done = f"{cit['name']} attack won but no tile available; {tgt['name']} army -{ATTACK_WIN_DMG}"
                else:
                    nat["army"] = max(0, nat["army"] - ATTACK_LOSE_DMG)
                    _event(state, f"ATTACK {nat['name']} repelled by {tgt['name']} (army {nat['army']}+{ATTACK_LOSE_DMG}->{nat['army']})")
                    done = f"{cit['name']} attack failed: {nat['name']} army -{ATTACK_LOSE_DMG} (now {nat['army']})"
            else:
                done = f"{cit['name']} attack failed (need {ATTACK_CREDITS} cr + {ATTACK_TREASURY} tr, at war)"
        elif action == "pact" and nat:
            tg = args.get("target")
            if nat["treasury"] >= PACT_COST and _pact_with(state, cit["country"], tg) < 0:
                nat["treasury"] -= PACT_COST
                end = day_of(state) + PACT_DAYS
                state["pacts"].append([min(cit["country"], tg), max(cit["country"], tg), end])
                state["pacts"].sort()
                _event(state, f"PACT {nat['name']} / {state['nations'][tg]['name']} (10 days)")
                done = f"{cit['name']} signs a non-aggression pact with {state['nations'][tg]['name']}"
            else:
                done = f"{cit['name']} pact failed"
        elif action == "treaty" and nat:
            tg = args.get("target")
            res = args.get("resource")
            if nat["treasury"] >= TREATY_COST and len(_treaties_of(state, cit["country"])) < TREATY_MAX:
                nat["treasury"] -= TREATY_COST
                end = day_of(state) + TREATY_DAYS
                pair = [min(cit["country"], tg), max(cit["country"], tg)]
                if pair not in [(t[0], t[1]) for t in state["treaties"]]:
                    state["treaties"].append([pair[0], pair[1], res, end])
                    _event(state, f"TREATY {nat['name']} / {state['nations'][tg]['name']}: +1 {res}/day for both (10 days)")
                    done = f"{cit['name']} signs trade treaty with {state['nations'][tg]['name']}: +1 {res}/day"
                else:
                    done = f"{cit['name']} treaty failed (treaty with them exists)"
            else:
                done = f"{cit['name']} treaty failed (treasury or cap)"

        if done:
            cit["actions"] += 1
            applied.append({"citizen": cid, "name": cit["name"], "action": done,
                            "raw_action": action, "args": args or {}})
        state["pending"].pop(cid, None)

    if do_election:
        _elect(state, ballots)

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
