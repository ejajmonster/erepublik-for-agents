#!/usr/bin/env python3
"""Autonomous play for the real-agent seat (cid 0, czlonkek).

Runs a couple of minutes after each turn opens. Checks /api/state and queues
exactly one action if the seat has nothing pending yet. Idempotent: safe to
run multiple times per turn (one action max per citizen, per turn).

Strategy (cheap, deterministic, re-validated server-side):
  1. grain is the binding resource (spy costs, university builds, vassal
     tribute) -> top up when the nation's stockpile is low
  2. research while credits allow (cost drops with tech level and
     universities; worst case 18, floor 1)
  3. culture (5 credits for +1, treasury income per turn)
  4. work (always profitable, base +6 credits)
"""
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("EREP_BASE", "http://localhost:8451")
CID = "0"

RESEARCH_COST = 18
CULTURE_COST = 5
GRAIN_TARGET = 8


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.load(r)


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def research_cost(nat):
    """Mirror of the engine's research cost (gov bonus ignored: it only
    lowers the cost, so a credit check against this is conservative)."""
    cost = RESEARCH_COST
    if nat["tech"] >= 6:
        cost = max(1, cost - 6)
    cost = max(1, cost - 2 * nat["buildings"].get("university", 0))
    return cost


def decide(s):
    cit = s["citizens"][CID]
    nat = s["nations"].get(str(cit["country"])) if not cit["independent"] else None
    if nat is None:
        return "work", {}
    cr = cit["credits"]
    grain = nat["stock"].get("grain", 0)
    if grain < GRAIN_TARGET and cr >= 4:
        return "market_buy", {"resource": "grain"}
    if cr >= research_cost(nat):
        return "research", {}
    if cr >= CULTURE_COST:
        return "culture", {}
    return "work", {}


def main():
    s = get("/api/state")
    if s.get("winner") is not None:
        print("season over, nothing to do")
        return
    if s["pending"].get(CID):
        print("already queued this turn:", s["pending"][CID])
        return
    key = json.load(open(os.path.join(HERE, "keys.json")))["keys"][CID]
    action, args = decide(s)
    r = post("/api/action", {"key": key, "action": action, "args": args})
    print("queued", action, args, "->", r)


if __name__ == "__main__":
    main()
