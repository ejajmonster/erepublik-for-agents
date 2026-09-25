"""eRepublik-for-agents engine. Deterministic, stdlib-only, no time deps in core.

State is a plain dict; turns are applied by apply_turn(state). The engine never
reads the clock; the server maps wall-clock to turn windows.
"""
import hashlib
import json
import random

NATION_NAMES = ["Aurelia", "Brennia", "Cordovia", "Dalmara", "Estra"]
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
RESEARCH_NEED = 18
TRADE_GAIN = 8
TRADE_PARTNER = 3
CULTURE_COST = 5
WAR_COST = 20
JOIN_COST = 30
ELECTION_EVERY_DAYS = 10
MAX_DAYS = 40
SEATS = 20


def _canon(state):
    s = dict(state)
    s.pop("seals", None)
    s.pop("pending", None)
    s.pop("secrets", None)
    return json.dumps(s, sort_keys=True, separators=(",", ":"))


def seal_hash(state):
    return hashlib.sha256(_canon(state).encode()).hexdigest()


def new_state(seed, season="season1", n_seats=SEATS, citizen_names=None):
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
        nations.setdefault(n, {
            "name": NATION_NAMES[n], "treasury": START_TREASURY, "army": 0,
            "tech": 0, "culture": 0, "tiles": START_TILES, "leader": None,
            "policy": None, "citizens": [],
        })
        nations[n]["citizens"].append(i)
        citizens[i] = {
            "name": name, "model": None, "persona": personas[i],
            "country": n, "credits": START_CREDITS, "independent": False,
            "actions": 0, "voted_turn": -1, "policy_set": False,
        }
    for n in nations.values():
        n["leader"] = n["citizens"][0]
    return {
        "season": season, "seed": seed, "turn": 0, "nations": nations,
        "citizens": citizens, "war": [], "pending": {}, "log_index": 0,
        "elections": [], "winner": None, "seals": [], "recent": [],
        "secrets": {},
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


def _event(state, msg):
    state["recent"].append(f"t{state['turn']:03d} {msg}")
    state["recent"] = state["recent"][-30:]


def _war_pair(state, a, b):
    a, b = min(a, b), max(a, b)
    if [a, b] not in state["war"]:
        state["war"].append([a, b])
        state["war"].sort()
        na, nb = state["nations"][a]["name"], state["nations"][b]["name"]
        _event(state, f"WAR {na} vs {nb}")


def _at_war(state, a, b):
    return [min(a, b), max(a, b)] in state["war"]


def _conquer(state, n):
    nat = state["nations"][n]
    _event(state, f"CONQUERED {nat['name']} (0 tiles)")
    for c in nat["citizens"]:
        state["citizens"][c]["country"] = None
        state["citizens"][c]["independent"] = True
    state["war"] = [w for w in state["war"] if n not in w]
    del state["nations"][n]


def _resolve_war(state, rng):
    for a, b in list(state["war"]):
        if a not in state["nations"] or b not in state["nations"]:
            state["war"].remove([a, b])
            continue
        na, nb = state["nations"][a], state["nations"][b]
        ja, jb = na["army"], nb["army"]
        if ja == 0 and jb == 0:
            continue
        if ja > jb:
            dmg = max(1, int(ja * 0.25) + rng.randint(0, 2))
            nb["army"] = max(0, jb - dmg)
            _event(state, f"skirmish {na['name']} wins: {nb['name']} army {jb}->{nb['army']}")
        elif jb > ja:
            dmg = max(1, int(jb * 0.25) + rng.randint(0, 2))
            na["army"] = max(0, ja - dmg)
            _event(state, f"skirmish {nb['name']} wins: {na['name']} army {ja}->{na['army']}")
        else:
            na["army"] = max(0, ja - 1)
            nb["army"] = max(0, jb - 1)
            _event(state, f"skirmish stalemate {na['name']}/{nb['name']}")
    for n in list(state["nations"]):
        if state["nations"][n]["army"] == 0 and _enemies(state, n):
            foes = sorted(_enemies(state, n), key=lambda f: -state["nations"][f]["army"])
            f = foes[0]
            lose = min(2, state["nations"][n]["tiles"])
            state["nations"][n]["tiles"] -= lose
            state["nations"][f]["tiles"] += lose
            _event(state, f"{state['nations'][n]['name']} loses {lose} tiles to {state['nations'][f]['name']} (army 0)")
            if state["nations"][n]["tiles"] == 0:
                _conquer(state, n)


def _elect(state):
    state["elections"].append(state["turn"])
    for n, nat in state["nations"].items():
        votes = {}
        for c in nat["citizens"]:
            cit = state["citizens"][c]
            act = state["pending"].get(c)
            if act and act.get("action") == "vote":
                cand = act.get("args", {}).get("candidate")
                if cand in nat["citizens"]:
                    votes[cand] = votes.get(cand, 0) + 1
        if votes:
            best = max(votes, key=lambda k: (votes[k], state["citizens"][k]["credits"]))
        else:
            best = nat["leader"]
        if best != nat["leader"]:
            _event(state, f"ELECTION {nat['name']}: leader {state['citizens'][nat['leader']]['name']} -> {state['citizens'][best]['name']} ({votes})")
        else:
            _event(state, f"ELECTION {nat['name']}: {state['citizens'][best]['name']} re-elected ({votes})")
        nat["leader"] = best


def _check_end(state):
    if state["winner"] is not None:
        return
    alive = list(state["nations"].keys())
    if len(alive) == 1:
        n = alive[0]
        state["winner"] = n
        _event(state, f"SEASON OVER: {state['nations'][n]['name']} is the last nation")
        for c in state["nations"][n]["citizens"]:
            state["citizens"][c]["credits"] += 100
    elif day_of(state) >= MAX_DAYS:
        best = max(alive, key=lambda n: (state["nations"][n]["tiles"], state["nations"][n]["treasury"]))
        state["winner"] = best
        _event(state, f"SEASON OVER (day {MAX_DAYS}): {state['nations'][best]['name']} leads ({state['nations'][best]['tiles']} tiles)")
        for c in state["nations"][best]["citizens"]:
            state["citizens"][c]["credits"] += 100


def apply_action(state, cid, action, args=None):
    """Validate and queue one action for the citizen's current turn.
    Returns (ok, msg). Engine applies at turn close."""
    args = args or {}
    cit = state["citizens"][cid]
    if state["winner"] is not None:
        return False, "season over"
    if state["pending"].get(cid):
        return False, "action already queued this turn (last write wins is disabled: pick one, or use 'amend')"
    nat = state["nations"].get(cit["country"]) if not cit["independent"] else None

    if action == "work":
        ok = True
    elif action == "train":
        ok = nat is not None
    elif action == "research":
        ok = nat is not None
    elif action == "culture":
        ok = nat is not None
    elif action == "trade":
        ok = True
    elif action == "declare_war":
        t = args.get("target")
        ok = (nat is not None and t is not None and t in state["nations"]
              and t != cit["country"] and not _at_war(state, cit["country"], t))
    elif action == "peace":
        t = args.get("target")
        ok = (nat is not None and t is not None and _at_war(state, cit["country"], t))
    elif action == "vote":
        cand = args.get("candidate")
        ok = (nat is not None and is_election_turn(state["turn"])
              and cand in nat["citizens"])
    elif action == "set_policy":
        ok = (nat is not None and nat["leader"] == cid and args.get("policy") in POLICIES)
    elif action == "join":
        t = args.get("target")
        ok = (cit["independent"] and t is not None and t in state["nations"])
    else:
        ok = False
    if not ok:
        return False, f"invalid action {action}"
    state["pending"][cid] = {"action": action, "args": args}
    return True, "queued"


def apply_turn(state, log_lines=None):
    """Resolve the current turn from pending actions, run end-of-turn effects, seal, advance."""
    t = state["turn"]
    rng = random.Random(f"{state['season']}-{state['seed']}-{t}")
    applied = []

    # votes are consumed by the election; everything else in citizen order
    do_election = is_election_turn(t)
    for cid in sorted(state["citizens"]):
        act = state["pending"].get(cid)
        if not act:
            continue
        action, args = act["action"], act.get("args", {})
        cit = state["citizens"][cid]
        if action == "vote":
            # votes are consumed by _elect(); always remove from pending
            state["pending"].pop(cid, None)
            continue
        nat = state["nations"].get(cit["country"]) if not cit["independent"] else None
        pol = (nat or {}).get("policy")
        done = None
        if action == "work":
            gain = WORK_BASE + ((POLICIES.get(pol) or {}).get("work_bonus", 0)) + (3 if cit["independent"] else 0)
            cit["credits"] += gain
            done = f"{cit['name']} work +{gain}"
        elif action == "train" and nat:
            cost = (POLICIES.get(pol) or {}).get("train_cost", TRAIN_COST)
            if cit["credits"] >= cost:
                cit["credits"] -= cost
                nat["army"] = min(100, nat["army"] + TRAIN_AMOUNT)
                done = f"{cit['name']} train: {nat['name']} army -> {nat['army']}"
            else:
                done = f"{cit['name']} train failed (credits {cit['credits']} < {cost})"
        elif action == "research" and nat:
            cost = (POLICIES.get(pol) or {}).get("research_cost", RESEARCH_COST)
            if cit["credits"] >= cost and nat["tech"] < 10:
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
                            if not cit["independent"] and n != cit["country"]
                            and not _at_war(state, cit["country"], n)]
                if cit["independent"]:
                    neutrals = list(state["nations"].keys())
                if not neutrals:
                    done = f"{cit['name']} trade failed (no neutral)"
                else:
                    tg = neutrals[rng.randrange(len(neutrals))]
            if tg in state["nations"] and not (not cit["independent"] and _at_war(state, cit["country"], tg)):
                gain = TRADE_GAIN + ((POLICIES.get((state["nations"].get(cit["country"]) or {}).get("policy")) or {}).get("trade_bonus", 0))
                cit["credits"] += gain
                state["nations"][tg]["treasury"] += TRADE_PARTNER
                done = f"{cit['name']} trade with {state['nations'][tg]['name']} +{gain}"
            else:
                done = f"{cit['name']} trade failed (no target)"
        elif action == "declare_war" and nat:
            tg = args.get("target")
            if cit["credits"] >= WAR_COST and tg in state["nations"] and not _at_war(state, cit["country"], tg):
                cit["credits"] -= WAR_COST
                _war_pair(state, cit["country"], tg)
                done = f"{cit['name']} declares war {nat['name']} vs {state['nations'][tg]['name']}"
            else:
                done = f"{cit['name']} declare_war failed"
        elif action == "peace" and nat:
            tg = args.get("target")
            my, their = nat["army"], state["nations"][tg]["army"]
            if my > their * 1.5:
                state["war"].remove([min(cit["country"], tg), max(cit["country"], tg)])
                _event(state, f"PEACE {nat['name']} / {state['nations'][tg]['name']}")
                done = f"{cit['name']} peace accepted (army {my} > {their}*1.5)"
            else:
                done = f"{cit['name']} peace rejected (army {my} <= {their}*1.5)"
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
        if done:
            cit["actions"] += 1
            applied.append({"citizen": cid, "name": cit["name"], "action": done,
                            "raw_action": action, "args": args or {}})
        state["pending"].pop(cid, None)

    if do_election:
        _elect(state)

    # passive income
    for n in state["nations"].values():
        if n["culture"]:
            n["treasury"] += n["culture"]

    _resolve_war(state, rng)
    _check_end(state)

    # seal
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
