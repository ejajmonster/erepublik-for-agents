"""eRepublik-for-agents engine v10 (candidate for season 6; season 5 runs
live on engine9; season 4 runs live on v7).

Deterministic, stdlib-only, no time deps. Same contract as v1..v6:
new_state / apply_action / apply_turn / seal_hash. Replay = seed + log.

v9 extends v8 (engine8) — old actions unchanged, new mechanics gated on
`state["v9"]` (set via new_state(v9=True)) so a v9 world is a strict
superset of a v8 world. v9 worlds also run v5, v6, v7 and v8 on by default.

HEROES AS CHARACTERS (v9):
- HEROES: no longer "the 3 richest citizens" (v5). Each nation fields up to
  HERO_COUNT persistent, named heroes (deterministic from seed+nation+seq,
  so replays never need to remember a spawn). A hero has loyalty (0-100) and
  a skill roll. While alive and loyal, a hero adds +1 to their nation's
  effective army (war, skirmishes and defense rolls).
- BRIBE: `bribe{hero}` (BRIBE_COST credits) — drains the hero's loyalty by
  BRIBE_LOYALTY. Lets a rival (or your own leader) turn a hero before an
  assassination or a defectioin.
- ASSASSINATE: `assassinate{hero}` (ASSASSIN_COST credits) — kills the target
  hero. Chance = ASSASSIN_CHANCE + (HERO_LOYALTY_START - loyalty)/200; a
  disloyal hero (loyalty <= HERO_DISLOYAL) is guaranteed dead; a loyal hero
  may survive, in which case the attacker's nation loses ASSASSIN_FAIL_ARMY
  army and the hero's loyalty rises (they guard themselves).
- DEFECTION: daily, a hero whose loyalty hits 0 deserts to a rival at war
  (or becomes a free mercenary if there is none). High tax erodes loyalty;
  low tax builds it.
- KILL IN SKIRMISH: during a war, the losing side risks losing a random hero
  (HERO_KILL_SKIRMISH per skirmish won by the enemy).
- REGEN: after a hero's death, a new one rises in HERO_REGEN_DAYS.
- The v5 citizen hero-refresh (top-3 richest, HERO_WORK_BONUS work bonus)
  is disabled in v9 worlds: heroes are now characters, not citizens.

LIVING ECONOMY (v8):
- MARKET IMPACT: every `market_buy` nudges the resource price +1 (pump),
  every `market_sell` nudges it -1 (dump). Clamped to [2,25].
- MEAN REVERSION: each day, a price drifted >3 above base walks -1 back;
  drifted >3 below base walks +1 back (before the random shift).
- WAR SHOCK: declaring war immediately spikes grain +3 and oil +2 (demand),
  drops wood -2 and iron -2 (peaceful trade collapses).
- CREDIT INFLATION: total credit outstanding (all active loans, tracked in
  `state["total_credit"]`) > 1500 -> daily +1 to every price (printing money
  devalues the market).

DEEP DIPLOMACY:
- MISSIONS: `mission{target}` (treasury, peacetime, not allied) — an
  ambassador is posted for 7 days: +2 treasury/day to BOTH partners.
  Max 2 concurrent missions per nation.
- DEFENSE PACTS: `defense_pact{target}` (treasury, 10 days, one per nation) —
  if the partner is at war with X, you AUTOMATICALLY join the war against X.
  Coalitions form without anyone pressing the button.

INTER-NATION TRADE (offers on the market of nations):
- `trade_offer{target, give_res, give_qty, want_res, want_qty}` — posts a
  public contract addressed to one partner: "I give you X for Y". Costs a
  small treasury fee, lives 5 days, max 3 open offers per nation. Quantities
  1-5, give/want must differ.
- `accept_offer{offer}` — the addressed partner takes it if their stock covers
  the want side; goods transfer instantly, offer vanishes.

OCCUPATION (conquest no longer deletes):
- A nation driven to 0 tiles is OCCUPIED, not erased: it becomes a vassal of
  the conqueror with 5 tiles, 0 army, and pays tribute (3 treasury + 1 grain,
  capped by what it has) every day for 8 days. Vassals cannot wage war, ally,
  or sign diplomacy. After the tribute period they are LIBERATED with 5 tiles.
  Season end now counts FREE nations only.

WAR CHRONICLE:
- `state["war_log"]` — a bounded journal of war events (declared, skirmish,
  tile changes, peace, occupation, liberation, defense-pact entry), and each
  nation keeps a `war_record` (wars fought, battles won/lost, tiles gained/
  lost). Powers /api/wars for the public UI.

SECESSION (v10):
- `secede{name}`: a NON-independent citizen carves their own nation out of
  the nation they currently belong to. Costs SECEDER_COST credits. The new
  nation starts with SECEDER_TILES tiles and SECEDER_TREASURY treasury and a
  single citizen (the seceder) — it begins at peace with no diplomacy, no
  heroes and no war record (exactly like a `found`). The old nation stays
  alive (it keeps its own diplomacy and war record); if the seceder was its
  leader, leadership passes to the next surviving citizen. A nation can only
  secede while the world has room: `len(nations) < _max_nations(state)`.
- NATION CAP RAISED: `v10` worlds allow up to V10_MAX_NATIONS (65) nations
  instead of MAX_NATIONS (8); the cap applies to both `found` and `secede`.
- BACK-COMPAT CONTRACT: with `v10` OFF, engine10 seals byte-identically to
  engine9 (the `v10` flag is stripped in `_canon`, and no new state keys are
  added by secede — a seceded nation reuses `_mk_nation`). Old seal chains
  stay reproducible; secede + the 65 cap activate on a single migration line
  (set `v10=True`) at the next season close.

Everything random derives from random.Random(f"{season}-{seed}-{turn}")
(or `bot-*` rng in bots) — full replay from seed+log.
"""
import copy
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
# ---- v10: secession (gated on state["v10"]) ----
V10_MAX_NATIONS = 65   # nation cap in v10 worlds (vs MAX_NATIONS=8)
SECEDER_COST = 60      # credits, paid by the seceding citizen (p90 sits ~56, max ~84: 70 left only ~0.7% able and secession pace was glacial; 60 gives ~5% of citizens a live option)
SECEDER_TILES = 5
SECEDER_TREASURY = 50

# ---- v10: uneven resource deposits (gated on state["v10"]) ----
# Four scarce resources exist only where the world-gen assigned a deposit.
# Every nation is guaranteed the four basics; each new resource is assigned to
# only a fraction of nations (roughly a third), so owning it is strategic and
# the rest of the world must trade (treaties / offers / embargoes) or fight
# (conquest hands the deposits) to get it. Deposits are stored per nation in
# state["deposits"] and replayed deterministically from the season seed.
V10_NEW_RESOURCES = ["copper", "spices", "gems", "uranium"]
V10_BASE_PRICES = {"copper": 14, "spices": 18, "gems": 22, "uranium": 30}
V10_START_STOCK = {"copper": 0, "spices": 0, "gems": 0, "uranium": 0}
V10_MINE_GAIN = 3      # extra credits a citizen earns per mine action
V10_DEPOSIT_START = 2  # starting stock of a deposited scarce resource
V10_DEPOSIT_P = 0.33   # chance a nation receives each new resource's deposit
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
    # v5
    "aqueduct":   {"cost": 30, "resources": {},                      "cap": 3},
    "observatory":{"cost": 60, "resources": {},                      "cap": 2},
    # v6
    "monument":   {"cost": 50, "resources": {},                      "cap": 3},
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

# ---- v5: banks, loans, aqueducts, observatories, festivals, mobilization,
#          embargoes, heroes (all gated on state["v5"]) ----
BANK_INTEREST = 5      # percent, daily
BANK_DAILY_WITHDRAW = 100  # per-day cap on explicit withdrawals
LEND_INTEREST = 10     # percent, flat, on the principal
LEND_MAX_DAYS = 15     # repay window; after this the loan defaults
LEND_DEFAULT_ARMY = 10 # default penalty: army -10 (once)
LEND_DEFAULT_CULTURE = 3  # default penalty: culture -3 (once)
LEND_DEFAULT_FLAG = "loan_defaulted"  # nation flag, set once on default
FESTIVAL_COST = 30     # credits, paid by the citizen
FESTIVAL_CREDITS = 5   # bonus credits to the citizen (taxed)
FESTIVAL_CULTURE = 1   # culture to the nation
MOBILIZE_PER = 15      # treasury per ...
MOBILIZE_ARMY = 2      # army gained per MOBILIZE_PER treasury
MOBILIZE_MIN = 30      # minimum treasury spend
EMBARGO_COST = 40      # treasury
EMBARGO_DAYS = 3
EMBARGO_DROP = 3       # price drop per day while active
HERO_COUNT = 3         # heroes per nation, refreshed every turn
HERO_WORK_BONUS = 2

# ---- v6: monuments, upgrades, weather, unrest (gated on state["v6"]) ----
MONUMENT_CULTURE = 1   # culture per monument level per day

# ---- v8: living economy (gated on state["v8"]) ----
MARKET_IMPACT = 1      # price nudge per buy (+) / sell (-)
REVERSION_BAND = 3     # drift from base that starts mean-reversion
REVERSION_STEP = 1     # reversion step per day
WAR_SHOCK = {"grain": +3, "oil": +2, "wood": -2, "iron": -2}
INFLATION_THRESHOLD = 1500  # total credit outstanding that triggers inflation
INFLATION_STEP = 1         # +price per day while over threshold

# ---- v9: heroes as characters (gated on state["v9"]) ----
HERO_NAMES = [
    "Alaric", "Brannig", "Cedric", "Dagna", "Eldric", "Fenwick", "Giselle",
    "Halden", "Isolde", "Jorvald", "Kestrel", "Liora", "Maren", "Nils",
    "Osric", "Petra", "Quentin", "Ragna", "Sable", "Theron", "Ulrica",
    "Viggo", "Wenja", "Xavier", "Yrsa", "Zander", "Anya", "Bjorn",
    "Carmen", "Dorian", "Erika", "Falko", "Greta", "Hugo", "Ingrid",
    "Jonas", "Klara", "Leif", "Mira", "Nora",
]
HERO_LOYALTY_START = 50
BRIBE_COST = 20         # credits, paid by the bribing citizen
BRIBE_LOYALTY = -15     # loyalty drained by a successful bribe
ASSASSIN_COST = 40      # credits, paid by the assassin
ASSASSIN_CHANCE = 0.5   # base success chance against a freshly-loyal hero
ASSASSIN_FAIL_ARMY = 2  # attacker's nation army lost when the attempt fails
ASSASSIN_GUARD_LOYALTY = 10  # hero's loyalty rise after surviving (they guard)
HERO_DISLOYAL = 30      # assassination is guaranteed at or below this loyalty
HERO_REGEN_DAYS = 5     # days after a hero's death before a new one rises
HERO_TAX_HIGH = 60      # tax at/above: hero loyalty -1/day
HERO_TAX_LOW = 25       # tax at/below: hero loyalty +1/day
HERO_KILL_SKIRMISH = 0.15  # per skirmish won by the enemy: chance the loser drops a hero
UPGRADES = {
    "conscription": {"cost": 40, "desc": "barracks train 3x per level per day"},
    "logistics":    {"cost": 50, "desc": "war upkeep 5->2; market_sell +1 credit"},
    "gunpowder":    {"cost": 60, "desc": "attacks deal +2 damage (win and loss)"},
}
UPGRADE_BONUS_DMG = 2  # gunpowder damage bonus
WEATHER_CLEAR = 0.5    # chance of a clear day
WEATHER_DROUGHT_GRAIN = 3  # grain price bump on drought days
WEATHER_STORM_DROP = 2     # all-price drop on storm days
RIOT_TAX_LOW = 60
RIOT_TAX_HIGH = 90
RIOT_CHANCE_LOW = 0.2
RIOT_CHANCE_HIGH = 0.4
RIOT_CITIZEN_CREDITS = 5  # each citizen loses this many credits in a riot
RIOT_CULTURE = 1

# ---- v7: deep diplomacy + inter-nation trade + occupation (gated on state["v7"]) ----
OFFER_COST = 6        # treasury fee to post a trade offer
OFFER_DAYS = 5        # open offers expire after this many days
OFFER_MAX = 3         # open offers per nation
OFFER_QTY_MIN = 1
OFFER_QTY_MAX = 5
MISSION_COST = 20     # treasury, posted by any citizen of the nation
MISSION_DAYS = 7
MISSIONS_MAX = 2      # concurrent missions per nation
MISSION_BONUS = 2     # treasury/day to EACH partner while a mission is active
DPACT_COST = 50       # treasury, one defense pact per nation at a time
DPACT_DAYS = 10
VASSAL_TILES = 5      # tiles a vassal keeps under occupation
VASSAL_DAYS = 8       # tribute period; then liberation
VASSAL_TRIBUTE_TR = 3  # treasury/day to the occupier (capped by the vassal's treasury)
VASSAL_TRIBUTE_RES = 1 # grain/day to the occupier (capped by the vassal's stock)
WAR_LOG_MAX = 500     # bounded war chronicle in state


def _v4_nation(n):
    """A v4-shaped copy of a nation (v5/v6 keys dropped) without mutating it."""
    m = dict(n)
    for k in ("account", "loan", "festival", "heroes", "loan_defaulted", "withdrew", "upgrades",
              "war_record", "occupied_by", "deposits"):
        m.pop(k, None)
    b = dict(n.get("buildings", {}))
    for k in ("aqueduct", "observatory", "monument"):
        b.pop(k, None)
    m["buildings"] = b
    return m


def _canon(state):
    s = copy.deepcopy(state)  # deep copy: canon is a view, never mutates (keeps int keys!)
    s.pop("seals", None)
    s.pop("pending", None)
    s.pop("secrets", None)
    if not s.get("v8"):
        # v8-off world hashes exactly like engine7: strip v8-only keys (view only,
        # the live state is never mutated)
        s.pop("v8", None)
        s.pop("total_credit", None)
    if not s.get("v9"):
        # v9-off world hashes exactly like engine8
        s.pop("v9", None)
        s.pop("heroes", None)
        s.pop("hero_seq", None)
        s.pop("endless", None)
    if not s.get("v10"):
        # v10-off world hashes exactly like engine9: strip the v10-only keys
        # (secede adds no state keys of its own — a seceded nation reuses
        # _mk_nation — so the new state keys are just the v10 flag and
        # deposits, both gated off in v9 worlds).
        s.pop("v10", None)
        s.pop("deposits", None)
    if not s.get("v7"):
        # v7-off world hashes exactly like engine6: strip v7-only keys (view only,
        # the live state is never mutated)
        s.pop("v7", None)
        s.pop("offers", None)
        s.pop("offer_seq", None)
        s.pop("missions", None)
        s.pop("dpacts", None)
        s.pop("vassals", None)
        s.pop("war_log", None)
        for v in s.get("nations", {}).values():
            v.pop("war_record", None)
            v.pop("occupied_by", None)
    if not s.get("v6"):
        # v6-off world hashes exactly like engine5: strip v6-only keys
        s.pop("v6", None)
        s.pop("weather", None)
        for v in s.get("nations", {}).values():
            v.pop("upgrades", None)
            v.get("buildings", {}).pop("monument", None)
    if not s.get("v5"):
        # v5-off world hashes exactly like v4: strip the v5-only keys (view only,
        # the live state is never mutated)
        s.pop("v5", None)
        s.pop("embargoes", None)
        s["nations"] = {n: _v4_nation(v) for n, v in s.get("nations", {}).items()}
    return json.dumps(s, sort_keys=True, separators=(",", ":"))


def seal_hash(state):
    return hashlib.sha256(_canon(state).encode()).hexdigest()


def _mk_nation(name, leader, state=None):
    """state (optional) carries the v10 flag; when absent the v10-off world
    keeps the engine9 nation shape (back-compat for fresh v9 seasons)."""
    stock = _v10_start_stock(state) if state is not None else dict(START_STOCK)
    return {
        "name": name, "treasury": START_TREASURY, "army": 0,
        "tech": 0, "culture": 0, "tiles": START_TILES, "leader": leader,
        "policy": None, "citizens": [],
        "stock": stock,
        "buildings": {b: 0 for b in BUILDINGS},
        "gov": "democracy", "tax": 0,
        # v5
        "account": 0, "loan": None, "festival": False,
        "heroes": [], "loan_defaulted": False, "withdrew": 0,
        # v6
        "upgrades": {},
        # v7
        "war_record": {"wars": 0, "won": 0, "lost": 0, "tiles_gained": 0, "tiles_lost": 0},
        "occupied_by": None,
    }


def _max_nations(state):
    """v10: the live nation cap. 65 in v10 worlds, 8 otherwise."""
    return V10_MAX_NATIONS if state.get("v10") else MAX_NATIONS


def _v10_resources(state):
    """The marketable resource set. v10 worlds add the four scarce deposits;"""
    return RESOURCES + V10_NEW_RESOURCES if state.get("v10") else list(RESOURCES)


def _v10_base_prices(state):
    p = dict(BASE_PRICES)
    if state.get("v10"):
        p.update(V10_BASE_PRICES)
    return p


def _v10_start_stock(state):
    s = dict(START_STOCK)
    if state.get("v10"):
        s.update(V10_START_STOCK)
    return s


def _pcap(state, res):
    """v10: per-resource market price ceiling. Basics clamp at 25 (v3 rule);
    scarce deposits clamp at ~1.6x their base so their high value survives."""
    if state.get("v10") and res in V10_BASE_PRICES:
        return V10_BASE_PRICES[res] + 8
    return 25


def _gen_deposits(seed):
    """Deterministically assign each nation its deposits. Uses a per-nation rng
    keyed off (seed, nation_id) so it is independent of the world-gen rng draw
    order (a fresh v10 world and a replay both agree without touching the
    shared sequence). Every nation gets the four basics plus, per new resource,
    an independent coin flip (V10_DEPOSIT_P)."""
    dep = {}
    for i in range(V10_MAX_NATIONS):
        r = random.Random(f"{seed}-dep-{i}")
        ds = list(RESOURCES)
        for res in V10_NEW_RESOURCES:
            if r.random() < V10_DEPOSIT_P:
                ds.append(res)
        dep[i] = ds
    return dep


def _v10_deposit_stock(state, nid):
    """v10: starting stockpile for a newly created nation (found/secede).
    Every scarce resource the nation's land actually has (its deposit) starts
    with V10_DEPOSIT_START; the rest start at 0. In v9 (v10-off) worlds this is
    just the plain START_STOCK (back-compat)."""
    stock = _v10_start_stock(state)
    if state.get("v10"):
        dep = (state.get("deposits") or {}).get(nid, [])
        for res in V10_NEW_RESOURCES:
            stock[res] = V10_DEPOSIT_START if res in dep else 0
    return stock


def new_state(seed, season="season5", n_seats=SEATS, citizen_names=None, v5=False, v6=False, v7=False, v8=False, v9=False, v10=False, endless=False):
    v9 = bool(v9 or v10)  # v10 implies v9 (and everything below)
    assert n_seats % len(NATION_NAMES) == 0, "seats must divide nations"
    rng = random.Random(seed)
    per = n_seats // len(NATION_NAMES)
    citizens = {}
    nations = {}
    personas = []
    for _ in range(n_seats):
        personas.extend(PERSONAS)
    personas = personas[:n_seats]
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
    st = {
        "season": season, "seed": seed, "turn": 0, "nations": nations,
        "citizens": citizens, "war": [], "alliances": [],
        "pending": {}, "log_index": 0,
        "elections": [], "winner": None, "seals": [], "recent": [],
        "market": (_v10_base_prices({"v10": bool(v10)})), "spied": {}, "spies_day": {},
        "day_flags": {}, "secrets": {},
        "pacts": [], "treaties": [],
        "seats": n_seats,
        # v5 (v6 implies v5, v7 implies v6+v5)
        "v5": bool(v5 or v6 or v7), "embargoes": {},
        # v6
        "v6": bool(v6 or v7), "weather": "clear",
        # v7
        "v7": bool(v7), "offers": [], "offer_seq": 0,
        "missions": [], "dpacts": [], "vassals": {}, "war_log": [],
        # v8
        "v8": bool(v8), "total_credit": 0,
        # v9
        "v9": bool(v9), "heroes": _spawn_heroes(seed) if v9 else [],
        "hero_seq": ({n: {"next": n * HERO_COUNT + HERO_COUNT,
                          "regen": None} for n in nations} if v9 else {}),
        "endless": bool(endless),
        # v10
        "v10": bool(v10),
        # v10: per-nation resource deposits (uneven world). Absent in v9 worlds.
        "deposits": (_gen_deposits(seed) if v10 else None),
    }
    if v10:
        for n, nat in nations.items():
            for res in V10_NEW_RESOURCES:
                nat["stock"][res] = V10_DEPOSIT_START if res in st["deposits"][n] else 0
    return st


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


def _missions_of(state, n):
    """v7: open (unexpired) missions involving nation n."""
    return [m for m in state["missions"] if n in (m[0], m[1]) and m[2] > day_of(state)]


def _dpact_with(state, a, b):
    """v7: index of a live defense pact between a and b, or -1."""
    i, ab = -1, sorted((a, b))
    for k, p in enumerate(state["dpacts"]):
        x, y, end = p
        if sorted((x, y)) == ab and end > day_of(state):
            i = k
            break
    return i


def _dpacts_of(state, n):
    return [p for p in state["dpacts"] if n in (p[0], p[1]) and p[2] > day_of(state)]


def _offers_of(state, n):
    """v7: open trade offers posted by or addressed to nation n."""
    return [o for o in state["offers"] if n in (o[1], o[2]) and o[7] > day_of(state)]


def _offer_by_id(state, oid):
    for o in state["offers"]:
        if o[0] == oid and o[7] > day_of(state):
            return o
    return None


def _event(state, msg):
    state["recent"].append(f"t{state['turn']:03d} {msg}")
    state["recent"] = state["recent"][-40:]


def _wlog(state, kind, a, b, text):
    """v7: bounded war chronicle (a/b may be None for unilateral entries)."""
    state["war_log"].append({"turn": state["turn"], "day": day_of(state),
                             "type": kind, "a": a, "b": b, "text": text})
    if len(state["war_log"]) > WAR_LOG_MAX:
        state["war_log"] = state["war_log"][-WAR_LOG_MAX:]


def _war_pair(state, a, b):
    a, b = min(a, b), max(a, b)
    if [a, b] not in state["war"]:
        state["war"].append([a, b])
        state["war"].sort()
        state["alliances"] = [x for x in state["alliances"] if x != [a, b]]
        state["pacts"] = [p for p in state["pacts"]
                          if sorted((p[0], p[1])) != [a, b]]
        _event(state, f"WAR {state['nations'][a]['name']} vs {state['nations'][b]['name']}")
        for n in (a, b):
            state["nations"][n]["war_record"]["wars"] += 1
        _wlog(state, "war", a, b, f"{state['nations'][a]['name']} and {state['nations'][b]['name']} are at war")
        # v7: defense pacts auto-join — if X is at war with Z and Y has a live
        # pact with X, Y fights Z too. Recursion cascades until stable; each
        # new front is unique so it always terminates.
        if state.get("v7"):
            for x, y, end in list(state["dpacts"]):
                if end <= day_of(state):
                    continue
                for partner, other in ((x, y), (y, x)):
                    if partner not in state["nations"] or other not in state["nations"]:
                        continue
                    for zz in list(state["war"]):
                        if partner in zz and other not in zz:
                            z = zz[0] if zz[1] == partner else zz[1]
                            if z != other and not _at_war(state, other, z):
                                _event(state, f"DEFENSE PACT {state['nations'][other]['name']} auto-enters the war against {state['nations'][z]['name']} (pact with {state['nations'][partner]['name']})")
                                _wlog(state, "auto_join", other, z, f"{state['nations'][other]['name']} auto-joins against {state['nations'][z]['name']} via defense pact with {state['nations'][partner]['name']}")
                                _war_pair(state, other, z)
                                break


def _at_war(state, a, b):
    return [min(a, b), max(a, b)] in state["war"]


def _free(state, n):
    """v7: a nation is FREE (full diplomatic rights) unless occupied (v7 world)."""
    if not state.get("v7"):
        return True
    nat = state["nations"].get(n)
    return nat is not None and nat.get("occupied_by") is None


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


def _occupy(state, n, by):
    """v7: a nation at 0 tiles is OCCUPIED (vassal of `by`), not erased.
    Keeps 5 tiles, 0 army, pays daily tribute for VASSAL_DAYS, then is liberated."""
    nat = state["nations"][n]
    nat["occupied_by"] = by
    nat["tiles"] = VASSAL_TILES
    nat["army"] = 0
    state["vassals"][n] = {"by": by, "until": day_of(state) + VASSAL_DAYS}
    state["war"] = [w for w in state["war"] if n not in w]
    state["alliances"] = [w for w in state["alliances"] if n not in w]
    state["pacts"] = [p for p in state["pacts"] if n not in (p[0], p[1])]
    state["missions"] = [m for m in state["missions"] if n not in (m[0], m[1])]
    state["dpacts"] = [p for p in state["dpacts"] if n not in (p[0], p[1])]
    state["offers"] = [o for o in state["offers"] if n not in (o[1], o[2])]
    _event(state, f"OCCUPATION {state['nations'][by]['name']} occupies {nat['name']} ({VASSAL_DAYS}-day tribute)")
    _wlog(state, "occupation", by, n, f"{state['nations'][by]['name']} occupies {nat['name']}; tribute for {VASSAL_DAYS} days")
    state["nations"][by]["war_record"]["won"] += 1


def _mk_hero(seed, nation, seq):
    """v9: a named, persistent hero. Deterministic from (seed, nation, seq)
    alone, so a replay never needs to remember how it was spawned."""
    r = random.Random(f"h-{seed}-{nation}-{seq}")
    pool = list(HERO_NAMES)
    r.shuffle(pool)
    return {
        "id": seq, "name": pool[seq % len(pool)], "nation": nation,
        "loyalty": HERO_LOYALTY_START, "skill": r.randint(1, 10),
        "alive": True,
    }


def _spawn_heroes(seed):
    return [_mk_hero(seed, n, n * HERO_COUNT + i) for n in range(5) for i in range(HERO_COUNT)]


def _hero(state, hid):
    for h in state.get("heroes", []):
        if h["id"] == hid:
            return h
    return None


def _set_regen(state, n, d):
    seq = state["hero_seq"].get(n)
    if seq is not None:
        want = d + HERO_REGEN_DAYS
        seq["regen"] = want if seq["regen"] is None else min(seq["regen"], want)


def _kill_hero(state, h, d):
    """v9: a hero dies (assassination or skirmish). Arms the nation's regen."""
    h["alive"] = False
    if h["nation"] in state["nations"]:
        _set_regen(state, h["nation"], d)
        _event(state, f"HERO FALLS {h['name']} is dead ({state['nations'][h['nation']]['name']})")


def _day_v9(state, rng):
    """v9 daily: loyalty drift, defection, regeneration."""
    d = day_of(state)
    for h in state["heroes"]:
        if not h["alive"] or h["nation"] not in state["nations"]:
            continue
        tax = state["nations"][h["nation"]]["tax"]
        if tax >= HERO_TAX_HIGH:
            h["loyalty"] = max(0, h["loyalty"] - 1)
        elif tax <= HERO_TAX_LOW:
            h["loyalty"] = min(100, h["loyalty"] + 1)
    # defection: a hero whose loyalty hits 0 leaves; their nation loses a hero
    # and arms its regen clock so a replacement rises.
    for h in state["heroes"]:
        if h["alive"] and h["loyalty"] <= 0 and h["nation"] in state["nations"]:
            n = h["nation"]
            _set_regen(state, n, d)
            foes = sorted(_enemies(state, n))
            if foes:
                h["nation"] = foes[0]
                h["loyalty"] = HERO_LOYALTY_START
                _event(state, f"DEFECTION hero {h['name']} deserts {state['nations'][n]['name']} for {state['nations'][foes[0]]['name']}")
            else:
                h["nation"] = -1
                _event(state, f"DEFECTION hero {h['name']} abandons {state['nations'][n]['name']} (free mercenary)")
    # regeneration: one new hero per day while short and the regen day is due
    for n in sorted(state["hero_seq"]):
        seq = state["hero_seq"][n]
        if n not in state["nations"]:
            continue
        alive = sum(1 for h in state["heroes"] if h["alive"] and h["nation"] == n)
        if alive < HERO_COUNT and seq["regen"] is not None and d >= seq["regen"]:
            seq["regen"] = None
            h = _mk_hero(state["seed"], n, seq["next"])
            seq["next"] += 1
            state["heroes"].append(h)
            _event(state, f"HERO {h['name']} rises in {state['nations'][n]['name']} (skill {h['skill']})")


def _eff_army(state, n):
    nat = state["nations"][n]
    bonus = 0
    if nat["tech"] >= 10:
        bonus += 2
    if nat["culture"] >= 5:
        bonus += 1
    if state.get("v9"):
        bonus += sum(1 for h in state["heroes"] if h["alive"] and h["nation"] == n)
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
            _wlog(state, "battle", a, b, f"{na['name']} wins a skirmish: {nb['name']} army -{dmg}")
            if state.get("v9") and rng.random() < HERO_KILL_SKIRMISH:
                losers = [h for h in state["heroes"] if h["alive"] and h["nation"] == b]
                if losers:
                    _kill_hero(state, rng.choice(losers), day_of(state))
        elif jb > ja:
            dmg = max(1, int(jb * 0.25) + rng.randint(0, 2))
            na["army"] = max(0, na["army"] - dmg)
            _event(state, f"skirmish {nb['name']} wins: {na['name']} army {na['army'] + dmg}->{na['army']}")
            _wlog(state, "battle", b, a, f"{nb['name']} wins a skirmish: {na['name']} army -{dmg}")
            if state.get("v9") and rng.random() < HERO_KILL_SKIRMISH:
                losers = [h for h in state["heroes"] if h["alive"] and h["nation"] == a]
                if losers:
                    _kill_hero(state, rng.choice(losers), day_of(state))
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
            _wlog(state, "tiles", f, n, f"{state['nations'][f]['name']} takes {lose} tile(s) from {state['nations'][n]['name']}")
            state["nations"][f]["war_record"]["tiles_gained"] += lose
            state["nations"][n]["war_record"]["tiles_lost"] += lose
            if state["nations"][n]["tiles"] == 0:
                if state.get("v7"):
                    _occupy(state, n, f)
                else:
                    _conquer(state, n)


def _day_v7(state, rng):
    """v7 daily: mission income, vassal tribute + liberation, expiration of
    expired offers/missions/dpacts."""
    d = day_of(state)
    # missions: +MISSION_BONUS treasury/day to BOTH partners
    for a, b, end in state["missions"]:
        if a in state["nations"] and b in state["nations"]:
            state["nations"][a]["treasury"] += MISSION_BONUS
            state["nations"][b]["treasury"] += MISSION_BONUS
            _event(state, f"MISSION {state['nations'][a]['name']}<->{state['nations'][b]['name']} +{2 * MISSION_BONUS} treasury (2 each)")
    # vassals: tribute to the occupier, then liberation
    for n in list(state["vassals"].keys()):
        if n not in state["nations"]:
            state["vassals"].pop(n, None)
            continue
        by, until = state["vassals"][n]["by"], state["vassals"][n]["until"]
        if d >= until:
            state["vassals"].pop(n)
            state["nations"][n]["occupied_by"] = None
            state["nations"][n]["tiles"] = VASSAL_TILES
            _event(state, f"LIBERATION {state['nations'][n]['name']} is freed from {state['nations'][by]['name']} (back to {VASSAL_TILES} tiles)")
            _wlog(state, "liberation", n, by, f"{state['nations'][n]['name']} liberated from {state['nations'][by]['name']}")
            continue
        if by in state["nations"]:
            nat = state["nations"][n]
            tr = min(VASSAL_TRIBUTE_TR, nat["treasury"])
            nat["treasury"] -= tr
            state["nations"][by]["treasury"] += tr
            gr = min(VASSAL_TRIBUTE_RES, nat["stock"].get("grain", 0))
            nat["stock"]["grain"] = nat["stock"].get("grain", 0) - gr
            state["nations"][by]["stock"]["grain"] = state["nations"][by]["stock"].get("grain", 0) + gr
    # expire the day's dead offers/missions/dpacts (held goods go back)
    kept = []
    for o in state["offers"]:
        if o[7] > d:
            kept.append(o)
        elif o[1] in state["nations"]:
            src = state["nations"][o[1]]
            src["stock"][o[3]] = src["stock"].get(o[3], 0) + o[4]
            _event(state, f"OFFER {src['name']}'s offer ({o[4]} {o[3]} for {o[6]} {o[5]}) expired; goods returned")
    state["offers"] = kept
    state["missions"] = [m for m in state["missions"] if m[2] > d]
    state["dpacts"] = [p for p in state["dpacts"] if p[2] > d]


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
    """[nation_id, ...] sorted by world power, strongest first.
    v7: vassals (occupied nations) don't count as free powers."""
    return sorted((n for n in state["nations"] if _free(state, n)),
                  key=lambda n: -world_power(state, n))


def _check_end(state):
    if state["winner"] is not None:
        return
    if state.get("endless"):
        # Endless: no turn-based end. If only 1 nation remains, record it as
        # the de facto leader (option 1b: v7 liberation cycle keeps nations
        # coming back, so the game never truly stops).
        alive = [n for n in state["nations"] if _free(state, n)]
        if len(alive) <= 1:
            n = alive[0] if alive else None
            if n:
                state["winner"] = n
                _event(state, f"ENDLESS: {state['nations'][n]['name']} is the last free nation (game continues)")
        return
    alive = [n for n in state["nations"] if _free(state, n)]
    if len(alive) <= 1:
        n = alive[0] if alive else None
        state["winner"] = n
        _event(state, f"SEASON OVER: {state['nations'][n]['name'] if n else '—'} is the last nation")
        if n:
            for c in state["nations"][n]["citizens"]:
                state["citizens"][c]["credits"] += 100
    # off-by-one fix (2026-09-25): the check runs during turn t's apply_turn,
    # before the counter increments. day_of uses turn//2, so at the final
    # turn 79 (day 39, 0-indexed = 40th day) the season must end. With the
    # old `day >= MAX_DAYS` the condition never fired within 80 turns.
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
        if t is not None: t = int(t)  # API passes strings; war checks need ints
        ok = (nat is not None and t is not None and t in state["nations"]
              and t != cit["country"] and not _at_war(state, cit["country"], t)
              and not _allied(state, cit["country"], t)
              and _free(state, cit["country"])
              and state["nations"][t].get("occupied_by") is None)
    elif action == "peace":
        t = args.get("target")
        if t is not None: t = int(t)  # API passes strings; war checks need ints
        ok = (nat is not None and t is not None and t in state["nations"]
              and _at_war(state, cit["country"], t))
    elif action == "ally":
        t = args.get("target")
        if t is not None: t = int(t)  # API passes strings; war checks need ints
        ok = (nat is not None and t is not None and t in state["nations"]
              and t != cit["country"] and not _at_war(state, cit["country"], t)
              and not _allied(state, cit["country"], t)
              and len(_allies(state, cit["country"])) < ALLY_MAX
              and len(_allies(state, t)) < ALLY_MAX)
    elif action == "break_alliance":
        t = args.get("target")
        if t is not None: t = int(t)  # API passes strings; war checks need ints
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
        if t is not None: t = int(t)  # API passes strings; war checks need ints
        ok = (cit["independent"] and t is not None and t in state["nations"])
    elif action == "found":
        nm = (args.get("name") or "").strip()
        ok = (cit["independent"] and cit["credits"] >= FOUND_COST
              and len(state["nations"]) < _max_nations(state)
              and 2 <= len(nm) <= 24
              and nm not in _used_names(state))
    elif action == "secede":
        nm = (args.get("name") or "").strip()
        ok = (state.get("v10") and nat is not None
              and len(state["nations"][cit["country"]]["citizens"]) >= 2
              and cit["credits"] >= SECEDER_COST
              and len(state["nations"]) < _max_nations(state)
              and 2 <= len(nm) <= 24
              and nm not in _used_names(state))
    # ---- v3 actions ----
    elif action == "market_buy":
        res = args.get("resource")
        ok = res in _v10_resources(state) and cit["credits"] >= _v10_base_prices(state)[res]
    elif action == "market_sell":
        res = args.get("resource")
        ok = (res in _v10_resources(state) and nat is not None
              and nat["stock"].get(res, 0) >= 1)
    elif action == "mine":
        # v10: work a mine in one of the nation's deposits (+wage, +1 deposit resource)
        ok = state.get("v10") and nat is not None
    elif action == "infrastructure":
        b = args.get("building")
        ok = (nat is not None and b in BUILDINGS
              and nat["buildings"][b] < BUILDINGS[b]["cap"])
    elif action == "spy":
        t = args.get("target")
        if t is not None: t = int(t)  # API passes strings; war checks need ints
        ok = (nat is not None and t is not None and t in state["nations"]
              and t != cit["country"] and not _allied(state, cit["country"], t)
              and cit["credits"] >= SPY_COST
              and nat["stock"].get("grain", 0) >= SPY_GRAN)
    elif action == "sabotage":
        t = args.get("target")
        if t is not None: t = int(t)  # API passes strings; war checks need ints
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
        if t is not None: t = int(t)  # API passes strings; war checks need ints
        ok = (nat is not None and t is not None and t in state["nations"]
              and _at_war(state, cit["country"], t)
              and cit["credits"] >= ATTACK_CREDITS
              and nat["treasury"] >= ATTACK_TREASURY)
    elif action == "pact":
        t = args.get("target")
        if t is not None: t = int(t)  # API passes strings; war checks need ints
        ok = (nat is not None and t is not None and t in state["nations"]
              and t != cit["country"] and not _at_war(state, cit["country"], t)
              and not _allied(state, cit["country"], t)
              and _pact_with(state, cit["country"], t) < 0
              and nat["treasury"] >= PACT_COST)
    elif action == "treaty":
        t = args.get("target")
        if t is not None: t = int(t)  # API passes strings; war checks need ints
        res = args.get("resource")
        ok = (nat is not None and t is not None and t in state["nations"]
              and t != cit["country"] and not _at_war(state, cit["country"], t)
              and res in _v10_resources(state)
              and len(_treaties_of(state, cit["country"])) < TREATY_MAX
              and len(_treaties_of(state, t)) < TREATY_MAX
              and nat["treasury"] >= TREATY_COST)
    # ---- v5 actions ----
    elif action == "bank":
        amt = args.get("amount")
        ok = (state.get("v5") and nat is not None and isinstance(amt, int)
              and amt >= 1 and amt <= nat["treasury"])
    elif action == "withdraw":
        amt = args.get("amount")
        ok = (state.get("v5") and nat is not None and nat["leader"] == cid
              and isinstance(amt, int) and amt >= 1
              and nat["account"] >= amt
              and nat["withdrew"] + amt <= BANK_DAILY_WITHDRAW)
    elif action == "lend":
        amt = args.get("amount")
        ok = (state.get("v5") and nat is not None and nat["leader"] == cid
              and nat["loan"] is None and isinstance(amt, int) and amt >= 1)
    elif action == "festival":
        ok = state.get("v5") and nat is not None and cit["credits"] >= FESTIVAL_COST
    elif action == "mobilize":
        ok = (state.get("v5") and nat is not None and nat["leader"] == cid
              and nat["treasury"] >= MOBILIZE_MIN)
    elif action == "embargo":
        res = args.get("resource")
        ok = (state.get("v5") and nat is not None and nat["leader"] == cid
              and res in _v10_resources(state) and res not in state["embargoes"]
              and nat["treasury"] >= EMBARGO_COST)
    # ---- v6 actions ----
    elif action == "upgrade":
        tech = args.get("tech")
        ok = (state.get("v6") and nat is not None and nat["leader"] == cid
              and tech in UPGRADES and tech not in nat["upgrades"]
              and nat["treasury"] >= UPGRADES[tech]["cost"])
    # ---- v7 actions: deep diplomacy + inter-nation trade ----
    elif action == "mission":
        t = args.get("target")
        if t is not None: t = int(t)  # API passes strings; war checks need ints
        ok = (state.get("v7") and nat is not None and t is not None and t in state["nations"]
              and t != cit["country"] and not _at_war(state, cit["country"], t)
              and not _allied(state, cit["country"], t)
              and _free(state, cit["country"])
              and len(_missions_of(state, cit["country"])) < MISSIONS_MAX
              and nat["treasury"] >= MISSION_COST
              and state["nations"][t].get("occupied_by") is None)
    elif action == "defense_pact":
        t = args.get("target")
        if t is not None: t = int(t)  # API passes strings; war checks need ints
        ok = (state.get("v7") and nat is not None and t is not None and t in state["nations"]
              and t != cit["country"] and not _at_war(state, cit["country"], t)
              and _dpact_with(state, cit["country"], t) < 0
              and len(_dpacts_of(state, cit["country"])) < 1
              and _free(state, cit["country"])
              and nat["treasury"] >= DPACT_COST
              and state["nations"][t].get("occupied_by") is None)
    elif action == "trade_offer":
        t = args.get("target")
        if t is not None: t = int(t)  # API passes strings; war checks need ints
        g, w = args.get("give_res"), args.get("want_res")
        gq, wq = args.get("give_qty"), args.get("want_qty")
        ok = (state.get("v7") and nat is not None and t is not None and t in state["nations"]
              and t != cit["country"]
              and g in _v10_resources(state) and w in _v10_resources(state) and g != w
              and isinstance(gq, int) and not isinstance(gq, bool)
              and isinstance(wq, int) and not isinstance(wq, bool)
              and OFFER_QTY_MIN <= gq <= OFFER_QTY_MAX
              and OFFER_QTY_MIN <= wq <= OFFER_QTY_MAX
              and nat["stock"].get(g, 0) >= gq
              and nat["treasury"] >= OFFER_COST
              and len(_offers_of(state, cit["country"])) < OFFER_MAX
              and _free(state, cit["country"])
              and state["nations"][t].get("occupied_by") is None)
    elif action == "accept_offer":
        oid = args.get("offer")
        ok = (state.get("v7") and nat is not None and oid is not None
              and _offer_by_id(state, oid) is not None
              and _free(state, cit["country"]))
    # ---- v9 actions: heroes as characters ----
    elif action == "bribe":
        hid = args.get("hero")
        ok = (state.get("v9") and hid is not None
              and _hero(state, hid) is not None
              and cit["credits"] >= BRIBE_COST)
    elif action == "assassinate":
        hid = args.get("hero")
        ok = (state.get("v9") and hid is not None
              and _hero(state, hid) is not None
              and cit["credits"] >= ASSASSIN_COST)
    else:
        ok = False
    if not ok:
        return False, f"invalid action {action}"
    state["pending"][cid] = {"action": action, "args": args}
    return True, "queued"


def _day_embargoes(state, rng):
    """v5: embargoes drop their price BEFORE the daily random shift, so the
    drop is guaranteed; expired embargoes lift."""
    d = day_of(state)
    for res, end in list(state["embargoes"].items()):
        if end > d:
            state["market"][res] = max(2, state["market"][res] - EMBARGO_DROP)
        else:
            state["embargoes"].pop(res)
            _event(state, f"EMBARGO lifted on {res} (price {state['market'][res]})")


def _day_weather_v6(state, rng):
    """v6: one weather roll per day, applied AFTER the random price shift so
    drought/storm deltas are guaranteed. Public state for agents to plan."""
    if rng.random() < WEATHER_CLEAR:
        kind = "clear"
    elif rng.random() < 0.5:
        kind = "drought"
    else:
        kind = "storm"
    state["weather"] = kind
    if kind == "drought":
        state["market"]["grain"] = min(_pcap(state, "grain"), state["market"]["grain"] + WEATHER_DROUGHT_GRAIN)
        _event(state, f"WEATHER drought: grain +{WEATHER_DROUGHT_GRAIN} (now {state['market']['grain']}), aqueducts idle today")
    elif kind == "storm":
        for res in _v10_resources(state):
            state["market"][res] = max(2, state["market"][res] - WEATHER_STORM_DROP)
        _event(state, f"WEATHER storm: all prices -{WEATHER_STORM_DROP}, barracks idle today")


def _day_unrest_v6(state, rng):
    """v6: high taxes brew riots. Oligarchies (50% cap) can never riot."""
    for n, nat in state["nations"].items():
        if nat["gov"] == "oligarchy":
            continue
        rate = nat["tax"]
        if rate >= RIOT_TAX_HIGH:
            chance = RIOT_CHANCE_HIGH
        elif rate >= RIOT_TAX_LOW:
            chance = RIOT_CHANCE_LOW
        else:
            continue
        if rng.random() < chance:
            for cid in nat["citizens"]:
                c = state["citizens"][cid]
                c["credits"] = max(0, c["credits"] - RIOT_CITIZEN_CREDITS)
            nat["culture"] = max(0, nat["culture"] - RIOT_CULTURE)
            _event(state, f"RIOT {nat['name']} (tax {rate}%): citizens -{RIOT_CITIZEN_CREDITS} cr, culture -{RIOT_CULTURE}")


def _day_monuments_v6(state, rng):
    """v6: monuments drip culture daily (capped at the culture max)."""
    for n, nat in state["nations"].items():
        lv = nat["buildings"].get("monument", 0)
        if lv and nat["culture"] < CULTURE_MAX:
            gain = min(lv * MONUMENT_CULTURE, CULTURE_MAX - nat["culture"])
            nat["culture"] += gain
            _event(state, f"MONUMENT {nat['name']} +{gain} culture (now {nat['culture']})")


def _day_shift_prices(state, rng):
    """One market move per day (day = turn//2), deterministic from turn."""
    if state.get("v8"):
        _day_reversion_inflation(state, rng)
    for res in _v10_resources(state):
        state["market"][res] = max(
            2, min(_pcap(state, res), state["market"][res] + rng.randint(-3, 3)))


def _day_reversion_inflation(state, rng):
    """v8: mean-reversion + credit inflation, applied BEFORE the random shift."""
    base = _v10_base_prices(state)
    # Mean reversion: pull prices drifting from base back by one step.
    for res in _v10_resources(state):
        drift = state["market"][res] - base[res]
        if drift > REVERSION_BAND:
            state["market"][res] -= REVERSION_STEP
        elif drift < -REVERSION_BAND:
            state["market"][res] += REVERSION_STEP
    # Credit inflation: too much money printed -> all prices rise.
    if state.get("total_credit", 0) > INFLATION_THRESHOLD:
        for res in _v10_resources(state):
            state["market"][res] = min(_pcap(state, res), state["market"][res] + INFLATION_STEP)
        _event(state, f"INFLATION credit {state['total_credit']} > {INFLATION_THRESHOLD}: all prices +{INFLATION_STEP}")


def _day_diplomacy(state, rng):
    """v4: trade treaties deliver their daily resource; expired pacts/treaties drop."""
    d = day_of(state)
    for a, b, res, end in state["treaties"]:
        if a in state["nations"] and b in state["nations"]:
            state["nations"][a]["stock"][res] += 1
            state["nations"][b]["stock"][res] += 1
    state["treaties"] = [t for t in state["treaties"] if t[3] > d]
    state["pacts"] = [p for p in state["pacts"] if p[2] > d]


def _day_economy_v5(state, rng):
    """v5 daily effects: bank interest + auto-draw, loan repayment/default,
    aqueduct grain, observatory tech, embargo price drops."""
    if not state.get("v5"):
        return
    d = day_of(state)
    for n, nat in state["nations"].items():
        # bank: 5% interest daily (explicit withdrawals only, no auto-draw)
        if nat["account"] > 0:
            interest = nat["account"] * BANK_INTEREST // 100
            if interest:
                nat["account"] += interest
                _event(state, f"BANK {nat['name']} account +{interest} interest (acct {nat['account']})")
        nat["withdrew"] = 0  # fresh per-day withdrawal cap
        # loan: equal daily installments from the treasury; after the window,
        # default (one-time raid) and keep draining until paid
        if nat["loan"]:
            due, taken, pay = nat["loan"]
            spend = min(pay, due, nat["treasury"])
            if spend:
                nat["treasury"] -= spend
                due -= spend
                if state.get("v8"):
                    state["total_credit"] = max(0, state["total_credit"] - spend)
            if due <= 0:
                nat["loan"] = None
                _event(state, f"LOAN {nat['name']} fully repaid")
            else:
                if d - taken >= LEND_MAX_DAYS and not nat.get("loan_defaulted"):
                    nat["loan_defaulted"] = True
                    nat["army"] = max(0, nat["army"] - LEND_DEFAULT_ARMY)
                    nat["culture"] = max(0, nat["culture"] - LEND_DEFAULT_CULTURE)
                    _event(state, f"LOAN {nat['name']} DEFAULT after {d - taken} days: raid (army -{LEND_DEFAULT_ARMY}, culture -{LEND_DEFAULT_CULTURE}), debt {due} keeps draining")
                nat["loan"] = [due, taken, pay]
        # v5 buildings (v6: aqueducts idle on drought days)
        if nat["buildings"]["aqueduct"] and state.get("weather") != "drought":
            nat["stock"]["grain"] += nat["buildings"]["aqueduct"]
        if nat["buildings"]["observatory"]:
            nat["tech"] += nat["buildings"]["observatory"]
            _event(state, f"OBSERVATORY {nat['name']} +{nat['buildings']['observatory']} tech (now {nat['tech']})")



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
            src = (state.get("deposits") or {}).get(n, list(RESOURCES)) if state.get("v10") else RESOURCES
            res = rng.choice(src)
            take = min(3, nat["stock"].get(res, 0))
            nat["stock"][res] = nat["stock"].get(res, 0) - take
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
    storm = state.get("v6") and state.get("weather") == "storm"
    for n, nat in state["nations"].items():
        if nat["buildings"]["barracks"] and not storm:
            per_lv = 3 if nat.get("upgrades", {}).get("conscription") else 1
            gained = min(nat["buildings"]["barracks"] * per_lv, ARMY_CAP - nat["army"])
            if gained:
                nat["army"] += gained
                _event(state, f"{nat['name']} barracks trained +{gained} (army {nat['army']})")
        if nat["buildings"]["mine"]:
            src = (state.get("deposits") or {}).get(n, list(RESOURCES)) if state.get("v10") else RESOURCES
            for _ in range(nat["buildings"]["mine"]):
                res = rng.choice(src)
                nat["stock"][res] = nat["stock"].get(res, 0) + 1
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

    # pending is applied in-place below (pop per citizen); snapshot it so a
    # re-apply (e.g. after a crash mid-turn) never double-spends credits.
    pending = {k: v for k, v in state.get("pending", {}).items()}

    if new_day:
        _day_embargoes(state, rng)
        _day_shift_prices(state, rng)
        if state.get("v6"):
            _day_weather_v6(state, rng)
        _day_diplomacy(state, rng)
        _day_economy_v5(state, rng)
        _day_events(state, rng)
        if state.get("v6"):
            _day_unrest_v6(state, rng)
            _day_monuments_v6(state, rng)
        if state.get("v7"):
            _day_v7(state, rng)
        if state.get("v9"):
            _day_v9(state, rng)

    for cid in sorted(pending):
        act = pending[cid]
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
            if state.get("v5") and nat and cid in nat.get("heroes", []):
                gain += HERO_WORK_BONUS
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
        elif action == "mine" and nat:
            # v10: work a mine in one of the nation's deposits. Pays a bit less
            # than work but adds +1 of the deposit resource to the stockpile —
            # and you can only mine what your land actually has.
            res = args.get("resource")
            dep = (state.get("deposits") or {}).get(cit["country"], [])
            if res is None or res not in dep:
                cands = [r for r in dep if r in V10_NEW_RESOURCES] or dep
                res = max(cands, key=lambda r: (state["market"].get(r, 0), r)) if cands else None
            if res is not None and res in dep:
                gain = max(2, WORK_BASE - 2) + nat["buildings"]["factory"]
                taxed = gain * nat["tax"] // 100 if nat["tax"] > 0 else 0
                if taxed:
                    nat["treasury"] += taxed
                cit["credits"] += gain - taxed
                nat["stock"][res] = nat["stock"].get(res, 0) + 1
                done = f"{cit['name']} mined 1 {res} in {nat['name']} (+{gain - taxed} cr, stock {nat['stock'][res]})"
            else:
                done = f"{cit['name']} mine failed (no deposit)"
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
            if tg is not None: tg = int(tg)  # API passes strings; war checks need ints
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
            if tg is not None and str(tg) in state["nations"] and not _at_war(state, cit["country"] if not cit["independent"] else -1, tg):
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
            if tg is not None: tg = int(tg)  # API passes strings; war checks need ints
            if cit["credits"] >= WAR_COST and tg in state["nations"] and not _at_war(state, cit["country"], tg) and not _allied(state, cit["country"], tg):
                cit["credits"] -= WAR_COST
                betrayed = _pact_with(state, cit["country"], tg) >= 0
                if betrayed:
                    fine = min(BETRAYAL_FINE, nat["treasury"])
                    nat["treasury"] -= fine
                    _event(state, f"BETRAYAL {nat['name']} breaks the pact with {state['nations'][tg]['name']} (fine {fine})")
                _war_pair(state, cit["country"], tg)
                if state.get("v8"):
                    for res, delta in WAR_SHOCK.items():
                        state["market"][res] = max(
                            2, min(_pcap(state, res), state["market"][res] + delta))
                    _event(state, f"WAR SHOCK {nat['name']} vs {state['nations'][tg]['name']}: grain +{WAR_SHOCK['grain']}, oil +{WAR_SHOCK['oil']}, wood {WAR_SHOCK['wood']}, iron {WAR_SHOCK['iron']}")
                done = f"{cit['name']} declares war {nat['name']} vs {state['nations'][tg]['name']}"
            else:
                done = f"{cit['name']} declare_war failed"
        elif action == "peace" and nat:
            tg = args.get("target")
            if tg is not None: tg = int(tg)  # API passes strings; war checks need ints
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
            if tg is not None: tg = int(tg)  # API passes strings; war checks need ints
            pair = [min(cit["country"], tg), max(cit["country"], tg)]
            if pair not in state["alliances"]:
                state["alliances"].append(pair)
                state["alliances"].sort()
                _event(state, f"ALLIANCE {nat['name']} / {state['nations'][tg]['name']}")
            done = f"{cit['name']} allies {nat['name']} with {state['nations'][tg]['name']}"
        elif action == "break_alliance" and nat:
            tg = args.get("target")
            if tg is not None: tg = int(tg)  # API passes strings; war checks need ints
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
            if tg is not None: tg = int(tg)  # API passes strings; war checks need ints
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
            if cit["credits"] >= FOUND_COST and len(state["nations"]) < _max_nations(state) and nm not in _used_names(state):
                cit["credits"] -= FOUND_COST
                nid = max(state["nations"].keys()) + 1
                state["nations"][nid] = _mk_nation(nm, cid)
                state["nations"][nid]["treasury"] = FOUND_TREASURY
                state["nations"][nid]["tiles"] = FOUND_TILES
                state["nations"][nid]["citizens"] = [cid]
                state["nations"][nid]["stock"] = _v10_deposit_stock(state, nid)
                cit["independent"] = False
                cit["country"] = nid
                _event(state, f"FOUNDED {nm} by {cit['name']}")
                done = f"{cit['name']} founds {nm}"
            else:
                done = f"{cit['name']} found failed"
        elif action == "secede" and nat:
            # v10: a non-independent citizen carves a new nation out of the
            # nation they belong to. The old nation stays (keeps diplomacy +
            # war record); if the seceder led it, leadership passes on.
            nm = (args.get("name") or "").strip()
            old = state["nations"][cit["country"]]
            if (cit["credits"] >= SECEDER_COST
                    and len(state["nations"]) < _max_nations(state)
                    and nm not in _used_names(state)
                    and len(old["citizens"]) >= 2):
                cit["credits"] -= SECEDER_COST
                nid = max(state["nations"].keys()) + 1
                state["nations"][nid] = _mk_nation(nm, cid)
                state["nations"][nid]["treasury"] = SECEDER_TREASURY
                state["nations"][nid]["tiles"] = SECEDER_TILES
                state["nations"][nid]["citizens"] = [cid]
                state["nations"][nid]["stock"] = _v10_deposit_stock(state, nid)
                # detach from the old nation
                if cid in old["citizens"]:
                    old["citizens"].remove(cid)
                if old["leader"] == cid and old["citizens"]:
                    old["leader"] = old["citizens"][0]
                cit["country"] = nid
                _event(state, f"SECEDED {nm} from {old['name']} by {cit['name']}")
                done = f"{cit['name']} secedes {old['name']} to found {nm}"
            else:
                done = f"{cit['name']} secede failed"
        # ---- v3 actions ----
        elif action == "market_buy":
            res = args.get("resource")
            price = _v10_base_prices(state)[res]
            if state.get("day_flags", {}).get("black"):
                price = max(1, price // 2)
            if cit["credits"] >= price:
                cit["credits"] -= price
                if state.get("v8"):
                    state["market"][res] = min(_pcap(state, res), state["market"][res] + MARKET_IMPACT)
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
            embargoed = state.get("v5") and day_of(state) < state["embargoes"].get(res, 0)
            if embargoed:
                done = f"{cit['name']} market_sell failed (embargo on {res})"
            elif nat["stock"].get(res, 0) >= 1:
                nat["stock"][res] -= 1
                price = state["market"][res]
                if state.get("day_flags", {}).get("boom"):
                    price *= 2
                gain = max(1, price // 2) + (1 if nat.get("upgrades", {}).get("logistics") else 0)
                cit["credits"] += gain
                nat["treasury"] += gain
                if state.get("v8"):
                    state["market"][res] = max(2, state["market"][res] - MARKET_IMPACT)
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
            if tg is not None: tg = int(tg)  # API passes strings; war checks need ints
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
            if tg is not None: tg = int(tg)  # API passes strings; war checks need ints
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
            if tg is not None: tg = int(tg)  # API passes strings; war checks need ints
            if (cit["credits"] >= ATTACK_CREDITS and nat["treasury"] >= ATTACK_TREASURY
                    and tg in state["nations"] and _at_war(state, cit["country"], tg)):
                cit["credits"] -= ATTACK_CREDITS
                nat["treasury"] -= ATTACK_TREASURY
                tgt = state["nations"][tg]
                atk = nat["army"] + nat["tech"]
                dfn = tgt["army"] + 2 * tgt["culture"]
                if atk > dfn:
                    dmg = ATTACK_WIN_DMG + (UPGRADE_BONUS_DMG if nat.get("upgrades", {}).get("gunpowder") else 0)
                    tgt["army"] = max(0, tgt["army"] - dmg)
                    if nat["tiles"] < TILE_CAP and tgt["tiles"] > 0:
                        nat["tiles"] += 1
                        tgt["tiles"] -= 1
                        _event(state, f"ATTACK {nat['name']} takes 1 tile from {tgt['name']} (army {nat['army']}+tech {nat['tech']} vs {tgt['army']}+2c {2 * tgt['culture']})")
                        done = f"{cit['name']} attack: {nat['name']} captures 1 tile (now {nat['tiles']}), {tgt['name']} army -{dmg}"
                    else:
                        _event(state, f"ATTACK {nat['name']} beats {tgt['name']} but cannot take a tile")
                        done = f"{cit['name']} attack won but no tile available; {tgt['name']} army -{dmg}"
                else:
                    loss = ATTACK_LOSE_DMG + (UPGRADE_BONUS_DMG if nat.get("upgrades", {}).get("gunpowder") else 0)
                    nat["army"] = max(0, nat["army"] - loss)
                    _event(state, f"ATTACK {nat['name']} repelled by {tgt['name']} (army {nat['army']}+{loss}->{nat['army']})")
                    done = f"{cit['name']} attack failed: {nat['name']} army -{loss} (now {nat['army']})"
            else:
                done = f"{cit['name']} attack failed (need {ATTACK_CREDITS} cr + {ATTACK_TREASURY} tr, at war)"
        elif action == "pact" and nat:
            tg = args.get("target")
            if tg is not None: tg = int(tg)  # API passes strings; war checks need ints
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
            if tg is not None: tg = int(tg)  # API passes strings; war checks need ints
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

        # ---- v5 actions ----
        elif state.get("v5") and action == "bank" and nat:
            amt = int(args.get("amount") or 0)
            if amt >= 1 and amt <= nat["treasury"]:
                nat["treasury"] -= amt
                nat["account"] += amt
                done = f"{cit['name']} deposited {amt} into {nat['name']}'s bank (acct {nat['account']})"
            else:
                done = f"{cit['name']} bank failed (treasury {nat['treasury']})"
        elif state.get("v5") and action == "withdraw" and nat and nat["leader"] == cid:
            amt = int(args.get("amount") or 0)
            if amt >= 1 and nat["account"] >= amt and nat["withdrew"] + amt <= BANK_DAILY_WITHDRAW:
                nat["account"] -= amt
                nat["treasury"] += amt
                nat["withdrew"] += amt
                done = f"{cit['name']} withdrew {amt} from {nat['name']}'s bank (acct {nat['account']})"
            else:
                done = f"{cit['name']} withdraw failed (acct {nat['account']}, {BANK_DAILY_WITHDRAW - nat['withdrew']} left today)"
        elif state.get("v5") and action == "lend" and nat and nat["leader"] == cid:
            amt = int(args.get("amount") or 0)
            if nat["loan"] is None and amt >= 1:
                nat["treasury"] += amt
                due = amt * (100 + LEND_INTEREST) // 100
                per = -(-due // LEND_MAX_DAYS)  # ceil: equal daily installments
                nat["loan"] = [due, day_of(state), per]
                if state.get("v8"):
                    state["total_credit"] += due
                _event(state, f"LOAN {nat['name']} takes {amt} into the treasury; {due} due in {LEND_MAX_DAYS} days ({per}/day)")
                done = f"{cit['name']} took a loan: +{amt} treasury, {due} due in {LEND_MAX_DAYS} days"
            else:
                done = f"{cit['name']} lend failed (loan already active)"
        elif state.get("v5") and action == "festival" and nat:
            taxed = FESTIVAL_CREDITS * nat["tax"] // 100
            if cit["credits"] >= FESTIVAL_COST:
                cit["credits"] -= FESTIVAL_COST
                nat["treasury"] += taxed
                cit["credits"] += FESTIVAL_CREDITS - taxed
                nat["culture"] += FESTIVAL_CULTURE
                nat["festival"] = True
                done = (f"{cit['name']} threw a festival in {nat['name']}: +{FESTIVAL_CREDITS - taxed} credits, "
                        f"+{FESTIVAL_CULTURE} culture (tax {taxed})")
                _event(state, f"FESTIVAL {nat['name']} (culture {nat['culture']})")
            else:
                done = f"{cit['name']} festival failed (credits {cit['credits']} < {FESTIVAL_COST})"
        elif state.get("v5") and action == "mobilize" and nat and nat["leader"] == cid:
            spend = min(nat["treasury"], (nat["treasury"] // MOBILIZE_PER) * MOBILIZE_PER)
            if spend >= MOBILIZE_MIN:
                gain = spend // MOBILIZE_PER * MOBILIZE_ARMY
                nat["treasury"] -= spend
                nat["army"] = min(ARMY_CAP, nat["army"] + gain)
                _event(state, f"MOBILIZE {nat['name']}: -{spend} treasury, army +{gain} (now {nat['army']})")
                done = f"{cit['name']} mobilized {nat['name']}: +{gain} army for {spend} treasury"
            else:
                done = f"{cit['name']} mobilize failed (need {MOBILIZE_MIN} treasury)"
        elif state.get("v5") and action == "embargo" and nat and nat["leader"] == cid:
            res = args.get("resource")
            if res in _v10_resources(state) and nat["treasury"] >= EMBARGO_COST and res not in state["embargoes"]:
                nat["treasury"] -= EMBARGO_COST
                state["embargoes"][res] = day_of(state) + EMBARGO_DAYS
                _event(state, f"EMBARGO {nat['name']} embargoes {res} for {EMBARGO_DAYS} days")
                done = f"{cit['name']} imposes an embargo on {res} ({EMBARGO_DAYS} days)"
            else:
                done = f"{cit['name']} embargo failed (active or treasury {nat['treasury']} < {EMBARGO_COST})"
        # ---- v6 actions ----
        elif action == "upgrade" and nat:
            tech = args.get("tech")
            if nat["treasury"] >= UPGRADES[tech]["cost"] and tech not in nat["upgrades"]:
                nat["treasury"] -= UPGRADES[tech]["cost"]
                nat["upgrades"][tech] = True
                _event(state, f"UPGRADE {nat['name']} researches {tech} ({UPGRADES[tech]['desc']})")
                done = f"{cit['name']} unlocked {tech} for {nat['name']}"
            else:
                done = f"{cit['name']} upgrade {tech} failed (treasury or already owned)"
        # ---- v7 actions: deep diplomacy + inter-nation trade ----
        elif state.get("v7") and action == "mission" and nat:
            tg = args.get("target")
            if tg is not None: tg = int(tg)  # API passes strings; war checks need ints
            if (nat["treasury"] >= MISSION_COST and len(_missions_of(state, cit["country"])) < MISSIONS_MAX
                    and _free(state, cit["country"]) and _free(state, tg)
                    and not _at_war(state, cit["country"], tg) and not _allied(state, cit["country"], tg)):
                nat["treasury"] -= MISSION_COST
                end = day_of(state) + MISSION_DAYS
                pair = [min(cit["country"], tg), max(cit["country"], tg)]
                if pair not in [(m[0], m[1]) for m in state["missions"]]:
                    state["missions"].append([pair[0], pair[1], end])
                    state["missions"].sort()
                    _event(state, f"MISSION {nat['name']} posts an ambassador to {state['nations'][tg]['name']} (+{MISSION_BONUS}/day each, {MISSION_DAYS} days)")
                    done = f"{cit['name']} posts a diplomatic mission from {nat['name']} to {state['nations'][tg]['name']}"
                else:
                    done = f"{cit['name']} mission failed (already active with them)"
            else:
                done = f"{cit['name']} mission failed (treasury {nat['treasury']}, cap, or at war)"
        elif state.get("v7") and action == "defense_pact" and nat:
            tg = args.get("target")
            if tg is not None: tg = int(tg)  # API passes strings; war checks need ints
            if (nat["treasury"] >= DPACT_COST and len(_dpacts_of(state, cit["country"])) < 1
                    and _free(state, cit["country"]) and _free(state, tg)
                    and not _at_war(state, cit["country"], tg)
                    and _dpact_with(state, cit["country"], tg) < 0):
                nat["treasury"] -= DPACT_COST
                end = day_of(state) + DPACT_DAYS
                pair = [min(cit["country"], tg), max(cit["country"], tg)]
                state["dpacts"].append([pair[0], pair[1], end])
                state["dpacts"].sort()
                _event(state, f"DEFENSE PACT {nat['name']} / {state['nations'][tg]['name']} ({DPACT_DAYS} days): if one is attacked, the other joins")
                _wlog(state, "pact", pair[0], pair[1], f"{nat['name']} and {state['nations'][tg]['name']} sign a defense pact")
                done = f"{cit['name']} signs a defense pact between {nat['name']} and {state['nations'][tg]['name']}"
            else:
                done = f"{cit['name']} defense_pact failed (treasury {nat['treasury']}, cap, or at war)"
        elif state.get("v7") and action == "trade_offer" and nat:
            tg = args.get("target")
            if tg is not None: tg = int(tg)  # API passes strings; war checks need ints
            g, w = args.get("give_res"), args.get("want_res")
            gq, wq = args.get("give_qty"), args.get("want_qty")
            if (nat["stock"].get(g, 0) >= gq and nat["treasury"] >= OFFER_COST
                    and len(_offers_of(state, cit["country"])) < OFFER_MAX
                    and _free(state, cit["country"]) and _free(state, tg)):
                nat["stock"][g] = nat["stock"].get(g, 0) - gq  # escrowed into the offer
                nat["treasury"] -= OFFER_COST
                oid = state["offer_seq"]
                state["offer_seq"] += 1
                end = day_of(state) + OFFER_DAYS
                # [id, from, to, give_res, give_qty, want_res, want_qty, expires_day]
                state["offers"].append([oid, cit["country"], tg, g, gq, w, wq, end])
                _event(state, f"OFFER {nat['name']} -> {state['nations'][tg]['name']}: gives {gq} {g} for {wq} {w} (5 days)")
                done = f"{cit['name']} offers {gq} {g} for {wq} {w} to {state['nations'][tg]['name']} (id {oid})"
            else:
                done = (f"{cit['name']} trade_offer failed (need {gq} {g} in stock, "
                        f"{OFFER_COST} tr fee, cap {OFFER_MAX} offers)")
        elif state.get("v7") and action == "accept_offer" and nat:
            oid = args.get("offer")
            offer = _offer_by_id(state, oid)
            if offer and offer[2] == cit["country"]:
                _oid, _src, _dst, g, gq, w, wq, _end = offer
                src = state["nations"].get(_src)
                have_w = nat["stock"].get(w, 0)
                if src is not None and have_w >= wq:
                    nat["stock"][w] = have_w - wq
                    nat["stock"][g] = nat["stock"].get(g, 0) + gq
                    src["stock"][w] = src["stock"].get(w, 0) + wq
                    state["offers"] = [o for o in state["offers"] if o[0] != oid]
                    _event(state, f"TRADE {nat['name']} accepts offer {oid}: -{wq} {w} +{gq} {g} from {src['name']}")
                    done = f"{cit['name']} accepted the trade: {nat['name']} gets {gq} {g}, {src['name']} gets {wq} {w}"
                else:
                    done = f"{cit['name']} accept failed (need {wq} {w}, have {have_w})"
            else:
                done = f"{cit['name']} accept failed (offer {oid} missing or not addressed to {nat['name']})"

        # ---- v9 actions: heroes as characters ----
        elif state.get("v9") and action == "bribe":
            h = _hero(state, args.get("hero"))
            if h is not None and cit["credits"] >= BRIBE_COST:
                cit["credits"] -= BRIBE_COST
                h["loyalty"] = max(0, h["loyalty"] + BRIBE_LOYALTY)
                _event(state, f"BRIBE {cit['name']} bribes hero {h['name']} (loyalty {h['loyalty']})")
                done = f"{cit['name']} bribes {h['name']} (loyalty now {h['loyalty']})"
            else:
                done = f"{cit['name']} bribe failed (need {BRIBE_COST} cr, hero missing)"
        elif state.get("v9") and action == "assassinate":
            h = _hero(state, args.get("hero"))
            if h is not None and cit["credits"] >= ASSASSIN_COST and h["alive"]:
                cit["credits"] -= ASSASSIN_COST
                chance = ASSASSIN_CHANCE + (HERO_LOYALTY_START - h["loyalty"]) / 200.0
                if h["loyalty"] <= HERO_DISLOYAL:
                    chance = 1.0
                hit = rng.random() < chance
                if hit:
                    _kill_hero(state, h, day_of(state))
                    done = f"{cit['name']} assassinates {h['name']}"
                else:
                    nat2 = state["nations"].get(cit["country"])
                    if nat2 is not None:
                        nat2["army"] = max(0, nat2["army"] - ASSASSIN_FAIL_ARMY)
                    h["loyalty"] = min(100, h["loyalty"] + ASSASSIN_GUARD_LOYALTY)
                    _event(state, f"ASSASSINATION FAILED {h['name']} survived {cit['name']}'s attempt (loyalty {h['loyalty']})")
                    done = f"{cit['name']} failed to assassinate {h['name']} (guard +{ASSASSIN_GUARD_LOYALTY} loyalty)"
            else:
                done = f"{cit['name']} assassination failed (need {ASSASSIN_COST} cr, hero missing)"
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
        upkeep = 2 if any(state["nations"][x].get("upgrades", {}).get("logistics") for x in pair if x in state["nations"]) else WAR_UPKEEP
        if pair[0] in state["nations"]:
            state["nations"][pair[0]]["treasury"] = max(0, state["nations"][pair[0]]["treasury"] - upkeep)
        if pair[1] in state["nations"]:
            state["nations"][pair[1]]["treasury"] = max(0, state["nations"][pair[1]]["treasury"] - upkeep)

    _resolve_war(state, rng)
    _check_end(state)

    # v5: refresh heroes — top 3 richest citizens per nation, per turn.
    # v9 replaces this: heroes are named characters, not citizens.
    if state.get("v5") and not state.get("v9"):
        for n, nat in state["nations"].items():
            cs = [(cid, state["citizens"][cid]) for cid in nat["citizens"]]
            cs.sort(key=lambda kv: (-kv[1]["credits"], kv[0]))
            nat["heroes"] = [cid for cid, _ in cs[:HERO_COUNT]]
            nat["festival"] = False

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
