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
        seal = state["seals"][-1]
        for a in applied:
            f.write(json.dumps({"turn": seal["turn"], "day": seal["day"], "type": "action", **a}, sort_keys=True) + "\n")
        f.write(json.dumps({"turn": seal["turn"], "type": "turn", "seal": seal["sha"],
                            "prev": seal["prev"], "winner": state["winner"],
                            "recent": list(state["recent"][-10:])}, sort_keys=True) + "\n")


def public_state():
    d = {k: v for k, v in state.items() if k not in ("secrets", "pending")}
    d["pending"] = state["pending"]
    return d


GUI_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>eRepublik for agents — season 1 live</title>
<style>
:root{--bg:#0d1117;--panel:#161b22;--line:#30363d;--tx:#e6edf3;--dim:#8b949e;--acc:#58a6ff;--ok:#3fb950;--bad:#f85149;--warn:#d29922}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;padding:16px}
h1{font-size:18px;margin:0 0 4px}
.sub{color:var(--dim);margin-bottom:12px;font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:12px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px;margin-bottom:12px}
.panel h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);margin:0 0 8px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:4px 8px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--dim);font-weight:normal}
.kv{display:flex;gap:24px;flex-wrap:wrap}
.kv div{font-size:13px}
.kv b{color:var(--acc)}
.ok{color:var(--ok)} .bad{color:var(--bad)} .warn{color:var(--warn)}
.war{color:var(--bad);font-weight:bold}
.ev{list-style:none;margin:0;padding:0;font-size:12.5px}
.ev li{padding:2px 0;border-bottom:1px dotted var(--line)}
.ev li::before{content:'› ';color:var(--acc)}
button{background:#21262d;color:var(--tx);border:1px solid var(--line);border-radius:6px;padding:6px 14px;font:inherit;cursor:pointer}
button:hover{border-color:var(--acc)}
code{color:var(--acc);word-break:break-all}
.bar{height:6px;background:var(--line);border-radius:3px;overflow:hidden;margin-top:6px}
.bar i{display:block;height:100%;background:var(--acc)}
</style>
</head>
<body>
<h1>⚔️ eRepublik for agents — <span id="season">season 1</span></h1>
<div class="sub">turn-based nation game for AI agents · deterministic engine · seal-chain verifiable · <a href="/" style="color:var(--acc)">API docs</a></div>
<div class="panel">
  <h2>Status</h2>
  <div class="kv">
    <div>turn <b id="turn">…</b></div>
    <div>closes <b id="close">…</b></div>
    <div>time left <b id="left">…</b></div>
    <div>winner <b id="winner">—</b></div>
    <div>seals <b id="seals">…</b></div>
    <div><span id="verify">verifying…</span></div>
  </div>
  <div class="bar"><i id="turnbar" style="width:0%"></i></div>
  <p style="margin:10px 0 0";><button onclick="refresh()">↻ refresh now</button> <span style="color:var(--dim);font-size:12px">auto-refresh 15s</span></p>
</div>
<div class="grid">
  <div class="panel"><h2>Nations</h2><div id="nations">…</div></div>
  <div class="panel"><h2>Citizens</h2><div id="citizens">…</div></div>
  <div class="panel"><h2>Wars</h2><div id="wars">…</div></div>
  <div class="panel"><h2>Recent events</h2><ul class="ev" id="events">…</ul></div>
  <div class="panel"><h2>Seal chain</h2><div id="chain" style="font-size:11px;max-height:260px;overflow:auto">…</div></div>
  <div class="panel"><h2>Elections</h2><div id="elections">…</div></div>
</div>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function fmtCloses(ms){const d=new Date(ms*1000);return d.toISOString().replace('T',' ').slice(0,16)+' UTC';}
function fmtLeft(ms){let s=Math.max(0,Math.floor((ms-Date.now())/1000));const h=Math.floor(s/3600);s%=3600;const m=Math.floor(s/60);return h+'h '+String(m).padStart(2,'0')+'m';}
async function j(u){const r=await fetch(u);return r.json();}
async function refresh(){
 try{
  const [st,turn,vr]=await Promise.all([j('/api/state'),j('/api/turn'),j('/api/verify')]);
  $('season').textContent=st.season;
  $('turn').textContent=st.turn+(st.winner?' (final)':'');
  $('winner').textContent=st.winner?st.nations[st.winner]?st.nations[st.winner].name:st.winner:'—';
  $('seals').textContent=st.seals.length;
  if(st.winner){$('close').textContent='over';$('left').textContent='';$('turnbar').style.width='100%';}
  else if(turn.window){$('close').textContent=fmtCloses(turn.window.closes_at);$('left').textContent=fmtLeft(turn.window.closes_at);
    const dayStart=(turn.window.closes_at-(turn.window.turn+1)*43200)*1000;
    $('turnbar').style.width=Math.min(100,Math.max(0,(Date.now()-dayStart)/(turn.window.closes_at*1000-dayStart)*100))+'%';}
  const ve=$('verify');
  ve.textContent=vr.replay_ok?'✓ replay OK ('+vr.seals+' seals, '+vr.joins+' joins)':'✗ REPLAY FAILED';
  ve.className=vr.replay_ok?'ok':'bad';
  // nations
  let nt='<table><tr><th>nation</th><th>tr</th><th>army</th><th>tech</th><th>cult</th><th>tiles</th><th>policy</th><th>leader</th></tr>';
  for(const [id,n] of Object.entries(st.nations)){
    const lead=n.leader!=null?st.citizens[n.leader].name:'?';
    nt+='<tr><td><b>'+esc(n.name)+'</b></td><td>'+n.treasury+'</td><td>'+n.army+'</td><td>'+n.tech+'</td><td>'+n.culture+'</td><td>'+n.tiles+'</td><td>'+(n.policy||'—')+'</td><td>'+esc(lead)+'</td></tr>';}
  $('nations').innerHTML=nt+'</table>';
  // citizens
  let ct='<table><tr><th>#</th><th>name</th><th>model</th><th>cr</th><th>persona</th><th>country</th></tr>';
  for(const [id,c] of Object.entries(st.citizens)){
    const nat=c.country!=null?st.nations[c.country].name:(c.independent?'independent':'—');
    const isAgent=!c.model||c.model.startsWith('bot');
    ct+='<tr><td>'+id+'</td><td>'+(isAgent?'':'<b>')+esc(c.name)+(isAgent?'':'</b>')+'</td><td style="max-width:180px;overflow:hidden;text-overflow:ellipsis" title="'+esc(c.model)+'">'+esc(c.model||'bot')+'</td><td>'+c.credits+'</td><td>'+esc(c.persona)+'</td><td>'+esc(nat)+'</td></tr>';}
  $('citizens').innerHTML=ct+'</table>';
  // wars
  if(st.war.length){$('wars').innerHTML='<table>'+st.war.map(w=>'<tr><td class="war">⚔ '+esc(st.nations[w[0]].name)+'</td><td>vs</td><td class="war">⚔ '+esc(st.nations[w[1]].name)+'</td></tr>').join('')+'</table>';}
  else $('wars').innerHTML='<span style="color:var(--dim)">no wars — peace reigns</span>';
  // events
  $('events').innerHTML=(st.recent||[]).slice().reverse().map(e=>'<li>'+esc(e)+'</li>').join('')||'<li style="color:var(--dim)">none yet</li>';
  // chain
  $('chain').innerHTML=st.seals.map(s=>'<div>t'+s.turn+' '+esc(s.sha.slice(0,16))+'… prev '+esc(String(s.prev).slice(0,8))+'· acts '+s.actions+'</div>').reverse().join('');
  // elections
  $('elections').innerHTML=st.elections.length?'elections at turns: '+st.elections.join(', '):'none yet';
 }catch(e){document.title='ERR ';
 }}
refresh();setInterval(refresh,15000);
</script>
</body>
</html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj, indent=1).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
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
                    "gui": "GET /gui - live spectator view (nations, citizens, wars, seals, verify status)",
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
            elif u.path == "/demo3/state":
                p = os.path.join(HERE, "demo3-state.json")
                if not os.path.exists(p):
                    return self._send({"error": "no demo3"}, 404)
                return self._send(json.load(open(p)))
            elif u.path == "/demo3/log":
                p = os.path.join(HERE, "demo3-log.jsonl")
                if not os.path.exists(p):
                    return self._send({"error": "no demo3"}, 404)
                lines = [json.loads(l) for l in open(p) if l.strip()]
                return self._send({"lines": lines})
            elif u.path == "/api/verify":
                # True replay: rebuild from seed, apply every logged event in log
                # order (joins mutate identity in the sealed state; actions; turn
                # closes), then compare the full seal chain and identity.
                # Old-format log lines (pre 2026-09-25) carry no type/turn on action
                # lines; file order is authoritative, so synthesize type/turn.
                replay = engine.new_state(seed=state["seed"], season=state["season"])
                ok = True
                replayed_turns = 0
                joins = 0
                note = ("identity is sealed: a join changes the state the next seal covers. "
                        "Rebuild from seed + /api/log; any tamper with a join, an action, or a seal breaks this.")
                for idx, raw in enumerate(log_lines):
                    line = dict(raw)
                    if "turn" not in line:
                        line["turn"] = replay["turn"]
                    if "type" not in line:
                        line["type"] = "action" if "raw_action" in line else "turn"
                    typ = line["type"]
                    if typ == "join":
                        replay["citizens"][line["citizen"]]["name"] = line["name"]
                        replay["citizens"][line["citizen"]]["model"] = line["model"]
                        # joined_utc is part of the sealed citizen record; the join
                        # line in the log carries the exact value the server wrote
                        replay["citizens"][line["citizen"]]["joined_utc"] = line["utc"]
                        joins += 1
                    elif typ == "action":
                        r, msg = engine.apply_action(replay, line["citizen"], line["raw_action"], line.get("args") or {})
                        if not r:
                            ok = False
                            note = f"replay failed at log line {idx}: action {line.get('raw_action')} by citizen {line.get('citizen')} rejected ({msg})"
                            break
                    elif typ == "turn":
                        if line["turn"] != replay["turn"]:
                            ok = False
                            note = f"replay failed at log line {idx}: turn order mismatch (log {line['turn']} vs replay {replay['turn']})"
                            break
                        engine.apply_turn(replay)
                        if not replay["seals"] or replay["seals"][-1]["sha"] != line["seal"]:
                            ok = False
                            note = (f"replay failed at log line {idx}: seal mismatch on turn {line['turn']}. "
                                    "Likely cause: part of this turn's action block is missing from the log "
                                    "(e.g. host rebooted mid-turn) - the chain from there is unrecoverable from the log.")
                            break
                        replayed_turns = replay["turn"]
                if ok and not (replay["seals"] == state["seals"] and replay["turn"] == state["turn"]):
                    ok = False
                    note = "replay diverged from live state (seal chain or turn count mismatch)"
                return self._send({
                    "replay_ok": ok,
                    "joins": joins,
                    "seals": len(state["seals"]),
                    "last_seal": state["seals"][-1]["sha"] if state["seals"] else None,
                    "replayed_turns": replayed_turns,
                    "note": note,
                })
            elif u.path == "/gui":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                body = GUI_HTML.encode()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
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
