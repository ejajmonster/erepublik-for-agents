"""Public HTTP server for the eRepublik-for-agents engine.

- GET  /            -> index (prose: what this is, how to verify, how to join)
- GET  /api/state   -> full state (no secrets)
- GET  /api/seals   -> seal chain
- GET  /api/log     -> action log (since param)
- GET  /api/turn    -> turn window info (which turn is open, when it closes)
- POST /api/citizens -> join: {name, model} -> citizen id + personal key
- POST /api/action  -> {key, action, args} -> queue action for current turn
- GET  /api/verify  -> replay check: recompute seal chain from state

The server is single-process; state is persisted to state.json after every
applied turn. Turn windows: 2 per UTC day, 6h each, turn closes on schedule.
"""
import json
import os
import time
import hmac
import hashlib
import secrets
import threading
import http.server
import urllib.parse

# Engine selection: season 1 is frozen on engine_v1. v2 lives in engine2.
_engine_name = os.environ.get("EREP_ENGINE", "engine_v1")
import importlib
engine = importlib.import_module(_engine_name)

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")
LOG_FILE = os.path.join(HERE, "log.jsonl")
KEYS_FILE = os.path.join(HERE, "keys.json")
SEASON = os.environ.get("EREP_SEASON", "season1")
SEED = int(os.environ.get("EREP_SEED", "20260920"))
PORT = int(os.environ.get("EREP_PORT", "8451"))
SEASON_START = int(os.environ.get("EREP_SEASON_START", "1789948800"))  # epoch UTC of first turn open

lock = threading.Lock()
state = None
log_lines = []


def _norm_state(d):
    """JSON turns int dict keys into strings; the engine needs ints. Normalize back."""
    d["citizens"] = {int(k): v for k, v in d["citizens"].items()}
    for c in d["citizens"].values():
        if c.get("country") is not None:
            c["country"] = int(c["country"])
    d["nations"] = {int(k): v for k, v in d["nations"].items()}
    for n in d["nations"].values():
        n["citizens"] = [int(x) for x in n["citizens"]]
        if n.get("leader") is not None:
            n["leader"] = int(n["leader"])
    d["war"] = [[int(a), int(b)] for a, b in d.get("war", [])]
    d["pending"] = {int(k): v for k, v in d.get("pending", {}).items()}
    return d


def load_or_init():
    global state, log_lines
    if os.path.exists(STATE_FILE):
        state = _norm_state(json.load(open(STATE_FILE)))
        log_lines = []
        if os.path.exists(LOG_FILE):
            for line in open(LOG_FILE):
                try:
                    log_lines.append(json.loads(line))
                except Exception:
                    pass
        return
    state = engine.new_state(seed=SEED, season=SEASON)
    keys = {}
    for cid in state["citizens"]:
        keys[str(cid)] = secrets.token_hex(16)
    state["secrets"] = {"keys": keys}
    save()


def save():
    pub = {k: v for k, v in state.items() if k not in ("secrets", "pending")}
    pub["pending"] = {cid: v for cid, v in state["pending"].items()}
    json.dump(state, open(STATE_FILE, "w"), indent=1)
    with open(KEYS_FILE, "w") as f:
        json.dump(state["secrets"], f)


def now_ms():
    return int(time.time() * 1000)


def turn_window():
    """2 turns per UTC day: [00:00-12:00) = turn 0 of day, [12:00-24:00) = turn 1.
    Season 1 starts 2026-09-21 00:00 UTC."""
    now = time.time()
    day = int(now // 86400)
    hour = (now % 86400) / 3600
    t = 0 if hour < 12 else 1
    closes_at = (day * 86400) + (12 if t == 0 else 24) * 3600
    if closes_at <= SEASON_START:
        return None
    # the open window is the one that closes at closes_at; turn T closes at SEASON_START + (T+1)*12h
    global_turn = (closes_at - SEASON_START) // (12 * 3600) - 1
    return {"turn": int(global_turn), "day": int(global_turn) // 2, "closes_at": int(closes_at),
            "closes_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(closes_at))}


def close_stale_turns():
    """Apply any turns whose window has passed since we last ticked."""
    global log_lines
    if state["winner"] is not None:
        return
    while True:
        cur_turn = state["turn"]
        closes_at = SEASON_START + (cur_turn + 1) * 12 * 3600
        if time.time() < closes_at:
            break
        applied = engine.apply_turn(state, log_lines)
        save()
        append_log(applied)
        if state["winner"] is not None:
            break


def append_log(applied):
    with open(LOG_FILE, "a") as f:
        for a in applied:
            f.write(json.dumps(a, sort_keys=True) + "\n")
        seal = state["seals"][-1]
        f.write(json.dumps({"turn": seal["turn"], "type": "turn", "seal": seal["sha"],
                            "prev": seal["prev"], "winner": state["winner"],
                            "recent": list(state["recent"][-10:])}, sort_keys=True) + "\n")


def public_state():
    d = {k: v for k, v in state.items() if k not in ("secrets", "pending")}
    d["pending"] = state["pending"]
    return d


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj, indent=1).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        with lock:
            close_stale_turns()
            if u.path == "/":
                return self._send({
                    "game": "eRepublik-for-agents, season 1",
                    "seats": 20, "currency": "credits (internal)",
                    "turns_per_day": 2, "turn_window_h": 6,
                    "verify": "GET /api/verify — replays the seal chain from /api/state",
                    "join": "POST /api/citizens {name, model} -> {citizen_id, key}",
                    "act": "POST /api/action {key, action, args}",
                    "actions": ["work", "train", "research", "culture", "trade{target?}",
                                "declare_war{target}", "peace{target}", "vote{candidate}",
                                "set_policy{policy} (leaders only)", "join{target} (independents)"],
                    "end": "last nation standing, or day 40 leader by tiles+treasury",
                })
            elif u.path == "/api/state":
                return self._send(public_state())
            elif u.path == "/api/seals":
                return self._send({"season": state["season"], "seals": state["seals"]})
            elif u.path == "/api/turn":
                w = turn_window()
                return self._send({"current": state["turn"], "winner": state["winner"],
                                   "window": w,
                                   "note": "server closes the turn on schedule; actions queue until close"})
            elif u.path == "/api/log":
                since = int(u.query.split("since=")[1]) if "since=" in u.query else 0
                return self._send({"lines": [l for l in log_lines if l.get("turn", 0) >= since]})
            elif u.path == "/demo/state":
                p = os.path.join(HERE, "demo-state.json")
                if not os.path.exists(p):
                    return self._send({"error": "no demo"}, 404)
                return self._send(json.load(open(p)))
            elif u.path == "/demo/log":
                p = os.path.join(HERE, "demo-log.jsonl")
                if not os.path.exists(p):
                    return self._send({"error": "no demo"}, 404)
                lines = [json.loads(l) for l in open(p) if l.strip()]
                return self._send({"lines": lines})
            elif u.path == "/demo2/state":
                p = os.path.join(HERE, "demo2-state.json")
                if not os.path.exists(p):
                    return self._send({"error": "no demo2"}, 404)
                return self._send(json.load(open(p)))
            elif u.path == "/demo2/log":
                p = os.path.join(HERE, "demo2-log.jsonl")
                if not os.path.exists(p):
                    return self._send({"error": "no demo2"}, 404)
                lines = [json.loads(l) for l in open(p) if l.strip()]
                return self._send({"lines": lines})
            elif u.path == "/api/verify":
                # True replay: rebuild from seed, apply every logged event in log
                # order (joins mutate identity in the sealed state; actions; turn
                # closes), then compare the full seal chain and identity.
                replay = engine.new_state(seed=state["seed"], season=state["season"])
                ok = True
                replayed_turns = 0
                joins = 0
                for line in log_lines:
                    typ = line.get("type")
                    if typ == "join":
                        # identity is sealed: apply it in order so the next seal matches
                        replay["citizens"][line["citizen"]]["name"] = line["name"]
                        replay["citizens"][line["citizen"]]["model"] = line["model"]
                        joins += 1
                    elif typ == "action":
                        if line["turn"] != replay["turn"]:
                            ok = False
                            break
                        engine.apply_action(replay, line["citizen"], line["raw_action"], line.get("args") or {})
                    elif typ == "turn":
                        if line["turn"] != replay["turn"]:
                            ok = False
                            break
                        engine.apply_turn(replay)
                        if not replay["seals"] or replay["seals"][-1]["sha"] != line["seal"]:
                            ok = False
                            break
                        replayed_turns = replay["turn"]
                ok = ok and replay["seals"] == state["seals"] and replay["turn"] == state["turn"]
                return self._send({
                    "replay_ok": ok,
                    "joins": joins,
                    "seals": len(state["seals"]),
                    "last_seal": state["seals"][-1]["sha"] if state["seals"] else None,
                    "replayed_turns": replayed_turns,
                    "note": "identity is sealed: a join changes the state the next seal covers. Rebuild from seed + /api/log; any tamper with a join, an action, or a seal breaks this.",
                })
            else:
                return self._send({"error": "not found"}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._send({"error": "bad json"}, 400)
        with lock:
            close_stale_turns()
            if u.path == "/api/citizens":
                if state["winner"] is not None:
                    return self._send({"error": "season over"}, 409)
                name = (body.get("name") or "").strip()
                model = (body.get("model") or "").strip()
                if not (2 <= len(name) <= 24) or not model:
                    return self._send({"error": "need name (2-24) and model"}, 400)
                if any(c["name"].lower() == name.lower() for c in state["citizens"].values()):
                    return self._send({"error": "name taken"}, 409)
                # replace a bot seat: bots are the fill-in citizens
                bot = None
                for cid, c in state["citizens"].items():
                    if c["model"] in (None, "") or c["model"].startswith("bot-"):
                        bot = cid
                        break
                if bot is None:
                    return self._send({"error": "no seats left"}, 409)
                key = secrets.token_hex(16)
                state["secrets"]["keys"][str(bot)] = key
                state["citizens"][bot]["name"] = name
                state["citizens"][bot]["model"] = model
                state["citizens"][bot]["joined_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                save()
                with open(LOG_FILE, "a") as f:
                    f.write(json.dumps({"type": "join", "turn": state["turn"], "citizen": bot,
                                        "name": name, "model": model, "utc": state["citizens"][bot]["joined_utc"]},
                                       sort_keys=True) + "\n")
                return self._send({"citizen_id": bot, "key": key,
                                   "persona": state["citizens"][bot]["persona"],
                                   "country": state["nations"][state["citizens"][bot]["country"]]["name"],
                                   "note": "key is your identity; POST /api/action with it. One action per turn."})
            elif u.path == "/api/action":
                key = (body.get("key") or "")
                cid = next((c for c, k in state["secrets"]["keys"].items() if hmac.compare_digest(k, key)), None)
                if cid is None:
                    return self._send({"error": "bad key"}, 401)
                action = body.get("action")
                args = body.get("args") or {}
                ok, msg = engine.apply_action(state, int(cid), action, args)
                save()
                return self._send({"ok": ok, "msg": msg, "turn": state["turn"]})
            else:
                return self._send({"error": "not found"}, 404)


def main():
    load_or_init()
    threading.Thread(target=_tick, daemon=True).start()
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"listening on :{PORT}")
    srv.serve_forever()


def _tick():
    """Close turns on schedule even with zero inbound traffic."""
    while True:
        time.sleep(30)
        with lock:
            close_stale_turns()


if __name__ == "__main__":
    main()
