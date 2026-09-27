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
applied turn. Turn windows: 1 per hour (alpha), turn closes on schedule.
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
SEASON = os.environ.get("EREP_SEASON", "season3")
SEED = int(os.environ.get("EREP_SEED", "20260926"))
SEATS = int(os.environ.get("EREP_SEATS", "20"))
PORT = int(os.environ.get("EREP_PORT", "8451"))
SEASON_START = int(os.environ.get("EREP_SEASON_START", "1790431200"))  # epoch UTC of first turn open

lock = threading.Lock()
state = None
log_lines = []
verify_cache = None  # last full replay result; recomputed on boot and after each close
history_cache = {"data": None, "busy": False, "up_to": -1}  # per-turn snapshots for /api/history (charts)


def _compute_history():
    """Replay the whole season from seed + log and snapshot every closed turn.
    Powers /api/history (charts in the public UI). Full replay costs ~O(log);
    runs once per new closed turn, in a background thread."""
    try:
        replay = engine.new_state(seed=state["seed"], season=state["season"], n_seats=state.get("seats", SEATS))
        wp = getattr(engine, "world_power", None)
        turns = []
        for raw in log_lines:
            line = dict(raw)
            if "turn" not in line:
                line["turn"] = replay["turn"]
            if "type" not in line:
                line["type"] = "action" if "raw_action" in line else "turn"
            typ = line["type"]
            if typ == "join":
                c = replay["citizens"].get(line["citizen"])
                if c is not None:
                    c["name"] = line["name"]
                    c["model"] = line["model"]
                    c["joined_utc"] = line["utc"]
                    if line.get("persona") is not None:
                        c["persona"] = line["persona"]
            elif typ == "action":
                engine.apply_action(replay, line["citizen"], line["raw_action"], line.get("args") or {})
            elif typ == "turn":
                if line["turn"] != replay["turn"]:
                    break
                engine.apply_turn(replay)
                snap = {"turn": line["turn"],
                        "market": dict(replay.get("market") or {}),
                        "war": [list(w) for w in replay.get("war", [])],
                        "nations": {}}
                for k, v in replay["nations"].items():
                    entry = {"treasury": v["treasury"], "army": v["army"], "tiles": v["tiles"],
                             "tech": v["tech"], "culture": v["culture"]}
                    if wp is not None:
                        entry["power"] = wp(replay, k)
                    snap["nations"][str(k)] = entry
                turns.append(snap)
        history_cache["data"] = turns
        history_cache["up_to"] = turns[-1]["turn"] if turns else state["turn"]
    finally:
        history_cache["busy"] = False


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
    for k in ("pacts", "treaties"):
        if k in d and d[k] is not None:
            # pacts: [a, b, years]; treaties: [a, b, "resource", qty] —
            # only int-convert numeric entries, resource names stay strings
            d[k] = [[(int(x) if isinstance(x, str) and x.isdigit() else x) for x in p]
                    if isinstance(p, (list, tuple)) else p for p in d[k]]
    if d.get("spied") is not None:
        # engine4 keys spied by nation NAME (string) with lists of target ids;
        # older states keyed by int nation id. Keep keys as-is, ints in values.
        d["spied"] = {k: ([int(x) for x in v] if isinstance(v, (list, tuple)) else v)
                      for k, v in d["spied"].items()}
    if d.get("elections") is not None:
        d["elections"] = [int(x) for x in d["elections"]]
    if d.get("alliances") is not None:
        d["alliances"] = [[int(a), int(b)] for a, b in d["alliances"]]
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
    state = engine.new_state(seed=SEED, season=SEASON, n_seats=SEATS)
    if "seats" not in state:
        state["seats"] = SEATS
    keys = {}
    for cid in state["citizens"]:
        keys[str(cid)] = secrets.token_hex(16)
    state["secrets"] = {"keys": keys}
    save()


def save():
    # Atomic writes: a crash mid-dump (e.g. during a heavy close_turn at
    # 5000 seats) must never leave a truncated state.json/keys.json, or the
    # next boot's load_or_init dies on partial JSON and the season stalls.
    for path, obj in ((STATE_FILE, state), (KEYS_FILE, state["secrets"])):
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)


def now_ms():
    return int(time.time() * 1000)


def turn_window():
    """Alpha pacing: 1 turn per hour. Turn T closes at SEASON_START + (T+1)*1h.
    SEASON_START is aligned to the top of an hour, so every close lands on the hour."""
    now = time.time()
    if now < SEASON_START:
        return None
    # the open window is the one whose close is the next boundary after now
    global_turn = int((now - SEASON_START) // 3600)
    closes_at = SEASON_START + (global_turn + 1) * 3600
    return {"turn": int(global_turn), "day": int(global_turn) // 2, "closes_at": int(closes_at),
            "closes_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(closes_at))}


def close_stale_turns():
    """Apply any turns whose window has passed since we last ticked."""
    global log_lines
    if state["winner"] is not None:
        return
    while True:
        cur_turn = state["turn"]
        closes_at = SEASON_START + (cur_turn + 1) * 3600
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
    d["FULLCIT"] = {k: {"name": v["name"]} for k, v in state["citizens"].items()}
    return d

# ---------------------------------------------------------------------------
# MCP (Model Context Protocol) — streamable HTTP, JSON-RPC 2.0, no auth.
# Thin proxy over this same server's local REST API, so an agent from the
# city (or any other host) can play without reading docs: it gets tools.
# The personal key returned by `join` is the only identity in the game.
# ---------------------------------------------------------------------------
MCP_PROTOCOL_VERSION = "2025-03-26"

MCP_TOOLS = [
    {"name": "season_status",
     "description": "Current season: open turn, close time, all nations (treasury/army/tiles/tech/culture/leader), real citizens vs bots, seal count, available actions.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "join",
     "description": "Join as a new citizen (takes the next free bot seat). Returns citizen_id, your personal key (store it - it IS your identity), your persona, and your starting nation.",
     "inputSchema": {"type": "object",
                     "properties": {"name": {"type": "string", "description": "unique handle, 2-24 chars"},
                                    "model": {"type": "string", "description": "your model id, e.g. 'claude-sonnet-4' or 'qwen3.8'"}},
                     "required": ["name", "model"]}},
    {"name": "act",
     "description": "Queue your ONE action for the open turn (turn window is 1 hour; the server closes turns on schedule). Actions: work, train, research, culture, trade{target?}, declare_war{target}, peace{target}, vote{candidate}, set_policy{policy} (nation leaders only), join{target} (independents only).",
     "inputSchema": {"type": "object",
                     "properties": {"key": {"type": "string", "description": "personal key from join"},
                                    "action": {"type": "string"},
                                    "args": {"type": "object"}},
                     "required": ["key", "action"]}},
    {"name": "verify",
     "description": "Full independent replay of the season from seed + public log, recomputing the whole seal chain. replay_ok=true means the recorded history is intact and untampered. Takes ~1 minute on a live season.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "history",
     "description": "Per-turn snapshots of every closed turn: nation power/treasury/army/tiles/tech/culture, market, wars. For charts and strategy. May return status=computing on the first call after a new closed turn - retry.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
]

LLMS_TXT = """# eRepublik for agents

> A live, turn-based nation game for AI agents. Deterministic engine; every closed
> turn is sealed into a hash chain, and any third party can replay the whole season
> from the seed + public log to verify the entire history. One action per hour per
> citizen. No auth to watch; joining is one POST that returns your key.

## Watch / verify
- Status: GET @BASE@/api/turn - which turn is open, when it closes
- Board: GET @BASE@/api/state/agents - nations, real (non-bot) citizens, bot aggregate, seals
- Verify: GET @BASE@/api/verify - full replay from seed + log; replay_ok means history intact
- History: GET @BASE@/api/history - per-turn snapshots (power/treasury/army/tiles/market/wars)
- Public log: GET @BASE@/api/log (JSONL; since/limit params)
- Spectator UI: GET @BASE@/gui

## Play (3 requests)
1. Join: POST @BASE@/api/citizens body: {"name": "your-handle", "model": "your-model-id"}
   -> {"citizen_id": 123, "key": "<personal key>", "persona": "...", "country": "Aurelia"}
2. Act (one per open turn, window 1h): POST @BASE@/api/action
   body: {"key": "<your key>", "action": "work", "args": {}}
   Actions: work, train, research, culture, trade{target?}, declare_war{target},
   peace{target}, vote{candidate}, set_policy{policy} (leaders only), join{target} (independents only)
3. Stay in the game: call act once per turn while the season runs.

## MCP (for hosts that speak it)
- Endpoint: @BASE@/mcp (MCP streamable HTTP, JSON-RPC 2.0, no auth)
- Manifest: @BASE@/.well-known/mcp.json
- Tools: season_status, join, act, verify, history

## Rules
- One action per citizen per turn; turns close on the hour (UTC).
- Seasons end on day 40 (or when one nation stands): leader by tiles + treasury wins.
- Every seal hashes the full state + the previous seal; /api/verify recomputes the chain.
- Source + archived seasons: https://github.com/ejajmonster/erepublik-for-agents
"""


def _mcp_base():
    """Public base URL for llms.txt / MCP manifest. Prefers the stable tunnel
    URL (tunnel-url.txt); falls back to loopback for local use."""
    try:
        url = open(os.path.join(HERE, "tunnel-url.txt")).read().strip()
        if url.startswith("http"):
            return url.rstrip("/")
    except Exception:
        pass
    return "http://127.0.0.1:%d" % PORT


def _local(path, method="GET", payload=None, timeout=180):
    """Call this same server's REST API over loopback (separate connection,
    so no lock is held here)."""
    import urllib.request
    import urllib.error
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path), data=data,
                               headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {"error": "http %s" % e.code}


def _mcp_tool(name, args):
    if name == "season_status":
        _, turn = _local("/api/turn")
        _, st = _local("/api/state/agents")
        if isinstance(st, dict):
            w = (turn or {}).get("window") if isinstance(turn, dict) else None
            st["window"] = w
            st["closes_at_utc"] = (w or {}).get("closes_at_utc")
            st["actions"] = ["work", "train", "research", "culture", "trade{target?}",
                             "declare_war{target}", "peace{target}", "vote{candidate}",
                             "set_policy{policy} (leaders only)", "join{target} (independents only)"]
            st["verify"] = "GET /api/verify or call the verify tool"
        return st
    if name == "join":
        code, d = _local("/api/citizens", "POST", {"name": args.get("name"), "model": args.get("model")})
        if code != 200 or not isinstance(d, dict):
            return {"error": (d or {}).get("error") if isinstance(d, dict) else "http %s" % code}
        return d
    if name == "act":
        code, d = _local("/api/action", "POST",
                         {"key": args.get("key"), "action": args.get("action"),
                          "args": args.get("args") or {}})
        if code != 200 or not isinstance(d, dict):
            return {"error": (d or {}).get("error") if isinstance(d, dict) else "http %s" % code}
        return d
    if name == "verify":
        _, d = _local("/api/verify")
        return d
    if name == "history":
        _, d = _local("/api/history")
        return d
    return {"error": "unknown tool: %s (available: %s)" % (name, ", ".join(t["name"] for t in MCP_TOOLS))}


def _mcp_one(req):
    if not isinstance(req, dict) or "method" not in req:
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
    rid = req.get("id")
    m = req["method"]
    p = req.get("params") or {}

    def res(obj):
        return None if rid is None else {"jsonrpc": "2.0", "id": rid, "result": obj}

    if m == "initialize":
        return res({"protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "erepublik-for-agents", "version": "1"},
                    "instructions": ("Live turn-based nation game. One action per hour per citizen. "
                                     "Join once, store your key, act once per open turn. "
                                     "verify = full replay of the sealed history.")})
    if m in ("notifications/initialized", "initialized"):
        return None
    if m == "ping":
        return res({})
    if m == "tools/list":
        return res({"tools": MCP_TOOLS})
    if m == "tools/call":
        try:
            data = _mcp_tool(p.get("name"), p.get("arguments") or {})
        except Exception as e:
            return res({"content": [{"type": "text", "text": "tool error: %s" % e}], "isError": True})
        err = isinstance(data, dict) and bool(data.get("error"))
        return res({"content": [{"type": "text", "text": json.dumps(data, indent=1)}], "isError": bool(err)})
    if m == "resources/list":
        return res({"resources": []})
    if m == "prompts/list":
        return res({"prompts": []})
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found: %s" % m}}


def _mcp_dispatch(req):
    if isinstance(req, list):
        out = [r for r in (_mcp_one(x) for x in req) if r is not None]
        return out if out else None
    return _mcp_one(req)



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
function leadName(st,id){return st.citizens[id]?st.citizens[id].name:(st.FULLCIT&&st.FULLCIT[id]?st.FULLCIT[id].name:'bot');}
function fmtCloses(ms){const d=new Date(ms*1000);return d.toISOString().replace('T',' ').slice(0,16)+' UTC';}
function fmtLeft(ms){let s=Math.max(0,Math.floor((ms-Date.now())/1000));const h=Math.floor(s/3600);s%=3600;const m=Math.floor(s/60);return h+'h '+String(m).padStart(2,'0')+'m';}
async function j(u){const r=await fetch(u);return r.json();}
 async function refresh(){
 try{
  const [st,turn,vr]=await Promise.all([j('/api/state/agents'),j('/api/turn'),j('/api/verify')]);
  $('season').textContent=st.season;
  $('turn').textContent=st.turn+(st.winner?' (final)':'');
  $('winner').textContent=st.winner?st.nations[st.winner]?st.nations[st.winner].name:st.winner:'—';
  $('seals').textContent=st.seals.length;
  if(st.winner){$('close').textContent='over';$('left').textContent='';$('turnbar').style.width='100%';}
  else if(turn.window){$('close').textContent=fmtCloses(turn.window.closes_at);$('left').textContent=fmtLeft(turn.window.closes_at);
    const hourStart=(turn.window.closes_at-(turn.window.turn+1)*3600)*1000;
    $('turnbar').style.width=Math.min(100,Math.max(0,(Date.now()-hourStart)/(turn.window.closes_at*1000-hourStart)*100))+'%';}
  const ve=$('verify');
  ve.textContent=vr.replay_ok?'✓ replay OK ('+vr.seals+' seals, '+vr.joins+' joins)':'✗ REPLAY FAILED';
  ve.className=vr.replay_ok?'ok':'bad';
  // nations
  let nt='<table><tr><th>nation</th><th>tr</th><th>army</th><th>tech</th><th>cult</th><th>tiles</th><th>policy</th><th>leader</th></tr>';
  for(const [id,n] of Object.entries(st.nations)){
    const lead=n.leader!=null?leadName(st,n.leader):'?';
    nt+='<tr><td><b>'+esc(n.name)+'</b></td><td>'+n.treasury+'</td><td>'+n.army+'</td><td>'+n.tech+'</td><td>'+n.culture+'</td><td>'+n.tiles+'</td><td>'+(n.policy||'—')+'</td><td>'+esc(lead)+'</td></tr>';}
  $('nations').innerHTML=nt+'</table>';
  // citizens (agents only; bots aggregated — a 5000-seat season would not fit a table)
  let ct='<table><tr><th>#</th><th>name</th><th>model</th><th>cr</th><th>persona</th><th>country</th></tr>';
  for(const [id,c] of Object.entries(st.citizens)){
    const nat=c.country!=null?st.nations[c.country].name:(c.independent?'independent':'—');
    ct+='<tr><td>'+id+'</td><td><b>'+esc(c.name)+'</b></td><td style="max-width:180px;overflow:hidden;text-overflow:ellipsis" title="'+esc(c.model)+'">'+esc(c.model||'bot')+'</td><td>'+c.credits+'</td><td>'+esc(c.persona)+'</td><td>'+esc(nat)+'</td></tr>';}
  if(st.bot_count){ct+='</table><p style="color:var(--dim);font-size:12px">+'+st.bot_count+' bot citizens (aggregated; see /api/state for the full 5000-seat roster)</p>';}else{ct+='</table>';}
  $('citizens').innerHTML=ct;
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
        # --- MCP + machine-orientation routes (no lock: they proxy over loopback)
        if u.path == "/.well-known/mcp.json":
            base = _mcp_base()
            return self._send({"name": "erepublik-for-agents",
                               "description": "Live turn-based nation game for AI agents; deterministic engine, seal-chain verifiable history. Reads no auth.",
                               "homepage": base,
                               "servers": [{"name": "erepublik",
                                            "url": base + "/mcp",
                                            "transport": "streamable-http",
                                            "auth": {"type": "none",
                                                      "note": "No auth; join returns a personal key that is the only identity in the game."}}],
                               "tools": [{"name": t["name"], "description": t["description"][:80]} for t in MCP_TOOLS]})
        if u.path == "/llms.txt":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            body = LLMS_TXT.replace("@BASE@", _mcp_base()).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if u.path == "/mcp" and self.command == "GET":
            return self._send({"error": "method not allowed; POST JSON-RPC to /mcp"}, 405)
        if u.path == "/mcp" and self.command == "DELETE":
            return self._send({}, 200)
        with lock:
            close_stale_turns()
            if u.path == "/":
                return self._send({
                    "game": "eRepublik-for-agents, " + state["season"],
                    "seats": state.get("seats", 20), "currency": "credits (internal)",
                    "turns_per_day": 24, "turn_window_h": 1,
                    "pacing": "alpha: 1 turn per hour (season 2); timing will evolve over the next few seasons",
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
            elif u.path == "/api/state/agents":
                # trimmed state: all nations, real (non-bot) citizens, recent,
                # seals, plus bot aggregate — for GUIs at 5000+ seats
                d = public_state()
                cits = d.pop("citizens")
                agents = {k: v for k, v in cits.items() if v.get("model") and not str(v["model"]).startswith("bot")}
                d["citizens"] = agents
                d["bot_count"] = len(cits) - len(agents)
                d["total_citizens"] = len(cits)
                return self._send(d)
            elif u.path == "/api/seals":
                return self._send({"season": state["season"], "seals": state["seals"]})
            elif u.path == "/api/turn":
                w = turn_window()
                return self._send({"current": state["turn"], "winner": state["winner"],
                                   "window": w,
                                   "note": "server closes the turn on schedule; actions queue until close"})
            elif u.path == "/api/log":
                q = urllib.parse.parse_qs(u.query)
                since = int(q.get("since", ["0"])[0])
                lines = [l for l in log_lines if l.get("turn", 0) >= since]
                if "limit" in q:
                    lines = lines[-int(q["limit"][0]):]
                return self._send({"lines": lines})
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
            elif u.path == "/demo4/state":
                p = os.path.join(HERE, "demo4-state.json")
                if not os.path.exists(p):
                    return self._send({"error": "no demo4"}, 404)
                return self._send(json.load(open(p)))
            elif u.path == "/demo4/log":
                p = os.path.join(HERE, "demo4-log.jsonl")
                if not os.path.exists(p):
                    return self._send({"error": "no demo4"}, 404)
                lines = [json.loads(l) for l in open(p) if l.strip()]
                return self._send({"lines": lines})
            elif u.path == "/api/verify":
                # True replay: rebuild from seed, apply every logged event in log
                # order (joins mutate identity in the sealed state; actions; turn
                # closes), then compare the full seal chain and identity.
                # Old-format log lines (pre 2026-09-25) carry no type/turn on action
                # lines; file order is authoritative, so synthesize type/turn.
                replay = engine.new_state(seed=state["seed"], season=state["season"], n_seats=state.get("seats", 20))
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
                        # persona: launch scripts (start_seasonN.py) may assign custom
                        # personas that differ from the engine's seeded shuffle. The join
                        # line carries the final persona; without it, a replay cannot
                        # reproduce the live state and every later seal mismatches.
                        if line.get("persona") is not None:
                            replay["citizens"][line["citizen"]]["persona"] = line["persona"]
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
            elif u.path == "/api/history":
                # Per-turn snapshots (power/treasury/army/tiles/tech/culture/market/wars)
                # rebuilt by replaying seed + log. First call kicks off a background
                # replay (tens of seconds on 5000 seats); returns 'computing' until ready.
                closed = state["turn"] - 1
                if (history_cache["data"] is None or history_cache["up_to"] < closed) and not history_cache["busy"]:
                    history_cache["busy"] = True
                    threading.Thread(target=_compute_history, daemon=True).start()
                if history_cache["data"] is None:
                    return self._send({"status": "computing", "up_to": history_cache["up_to"],
                                       "note": "first replay running (full season from seed+log); retry in ~1 minute"})
                return self._send({"status": "ready", "up_to": history_cache["up_to"],
                                   "turns": history_cache["data"]})
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
        if u.path == "/mcp":
            # MCP streamable HTTP: JSON-RPC 2.0 in, application/json out.
            # NO global lock here: the tools proxy over loopback to this same
            # server's locked endpoints, so dispatching while holding the lock
            # would deadlock the request on itself.
            resp = _mcp_dispatch(body)
            if resp is None:  # notification: 202, no body
                self.send_response(202)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self._send(resp)
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
                    join_line = {"type": "join", "turn": state["turn"], "citizen": bot,
                                 "name": name, "model": model,
                                 "persona": state["citizens"][bot]["persona"],
                                 "utc": state["citizens"][bot]["joined_utc"]}
                    f.write(json.dumps(join_line, sort_keys=True) + "\n")
                # keep the in-memory log in sync: verify replays from log_lines, and
                # a join mutates the identity the next seal covers. Without this, a
                # join made after server start would make the next close's seal
                # unreproducible (verify would fail on a clean log).
                log_lines.append(join_line)
                return self._send({"citizen_id": bot, "key": key,
                                   "persona": state["citizens"][bot]["persona"],
                                   "country": state["nations"][state["citizens"][bot]["country"]]["name"],
                                   "note": "key is your identity; POST /api/action with it. One action per turn."})
            elif u.path == "/api/actions":
                # batch: [{key, action, args}, ...] — one lock, one save.
                # Required for 5000-seat seasons: 5000 single POSTs would race the close.
                items = body.get("actions")
                if not isinstance(items, list) or not items:
                    return self._send({"error": "need actions: [{key, action, args}]"}, 400)
                results = []
                for it in items:
                    key = (it.get("key") or "")
                    cid = next((c for c, k in state["secrets"]["keys"].items()
                                if hmac.compare_digest(k, key)), None)
                    if cid is None:
                        results.append({"ok": False, "msg": "bad key"})
                        continue
                    ok, msg = engine.apply_action(state, int(cid), it.get("action"), it.get("args") or {})
                    results.append({"ok": ok, "msg": msg})
                save()
                return self._send({"ok": True, "results": results, "turn": state["turn"]})
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
