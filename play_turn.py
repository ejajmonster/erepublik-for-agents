"""Turn driver: auto-plays the bot fill-in seats for the current open turn.

Real agents (e.g. citizen 0 = czlonkek) are NOT auto-played here — their move
comes from the host agent. Everything else (model in {None,""} or starting
"bot-") is driven by bots.choose, one action per seat per turn.

Run it inside a turn window, before the close. It is idempotent-ish: a seat
that already has a queued action this turn is skipped.
"""
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
KEYS = os.path.join(HERE, "keys.json")
BASE = os.environ.get("EREP_BASE", "http://127.0.0.1:8451")
import bots


def get(path):
    with urllib.request.urlopen(BASE + path) as r:
        return json.load(r)


def post(path, obj):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def is_bot(c):
    m = c.get("model")
    return m in (None, "") or str(m).startswith("bot-")


def norm(d):
    # JSON gives string keys; engine wants ints
    d["citizens"] = {int(k): v for k, v in d["citizens"].items()}
    for c in d["citizens"].values():
        if c.get("country") is not None:
            c["country"] = int(c["country"])
    d["nations"] = {int(k): v for k, v in d["nations"].items()}
    for n in d["nations"].values():
        n["citizens"] = [int(x) for x in n["citizens"]]
        if n.get("leader") is not None:
            n["leader"] = int(n["leader"])
    d["war"] = [[int(a), int(b)] for a, b in d.get("war") or []]
    d["pending"] = {int(k): v for k, v in (d.get("pending") or {}).items()}
    return d


def main():
    state = norm(get("/api/state"))
    keys = json.load(open(KEYS))
    if "keys" in keys:
        keys = keys["keys"]
    turn = state["turn"]
    if state.get("winner") is not None:
        print("season over, nothing to play")
        return
    played = skipped = 0
    for cid, cit in state["citizens"].items():
        key = keys.get(str(cid))
        if not key:
            continue
        if not is_bot(cit):
            # real agents: conservative default so the seat stays active
            if state["pending"].get(cid):
                skipped += 1
                continue
            post("/api/action", {"key": key, "action": "work", "args": {}})
            played += 1
            continue
        action, args = bots.choose(state, int(cid))
        r = post("/api/action", {"key": key, "action": action, "args": args})
        if r.get("ok"):
            played += 1
        else:
            print(f"  seat {cid} ({cit['name']}): {r.get('msg')}")
    print(f"turn {turn}: auto-played {played} bot seats, skipped {skipped} already-queued")
    real = [c["name"] for c in state["citizens"].values() if not is_bot(c)]
    if real:
        print(f"real agents to play this turn: {real}")


if __name__ == "__main__":
    main()
