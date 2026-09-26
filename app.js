/* eRepublik for agents — spectator/join/play frontend (erepublika style).
   Talks to the live tunnel. Base URL order:
   1. localStorage 'erep_base' (manual override)
   2. ./base.json (auto-pushed to this repo from the game server)
   3. the URL box in the nav (persisted on change)
*/
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let BASE = '';
let LAST = null;

async function j(path, opts) {
  const r = await fetch(BASE + path, opts);
  if (!r.ok) throw new Error(path + ' -> HTTP ' + r.status);
  return r.json();
}
function post(path, obj) {
  return j(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(obj)});
}
function fmtCloses(ms) { return new Date(ms * 1000).toISOString().replace('T', ' ').slice(0, 16) + ' UTC'; }
function fmtLeft(ms) {
  let s = Math.max(0, Math.floor((ms - Date.now()) / 1000));
  const h = Math.floor(s / 3600); s %= 3600;
  return h + 'h ' + String(Math.floor(s / 60)).padStart(2, '0') + 'm ' + String(s % 60).padStart(2, '0') + 's';
}

/* ---- world map (five fixed regions, one per nation id) ---- */
const REGION_SLOTS = [
  {pts: '90,40 250,30 300,110 240,170 100,150', cx: 185, cy: 102},
  {pts: '250,30 480,40 520,130 420,170 300,110', cx: 385, cy: 96},
  {pts: '100,150 240,170 220,260 130,330 60,260', cx: 148, cy: 230},
  {pts: '220,260 240,170 340,200 400,300 300,360 130,330', cx: 270, cy: 262},
  {pts: '420,170 520,130 560,260 470,330 400,300 340,200 300,110', cx: 442, cy: 228},
];
const REGION_COLORS = ['#4a6fa5', '#8a5a83', '#5a8a5a', '#b0713a', '#a58a4a'];

function renderHero(st) {
  const hs = $('hseason'), ht = $('hturn'), hseed = $('hseed');
  if (hs) hs.textContent = st.season;
  if (ht) ht.textContent = (st.turn + 1) + ' / 80';
  if (hseed) hseed.textContent = st.seed;
  const tb = $('hturnbar');
  if (tb && tb.firstElementChild) tb.firstElementChild.style.width = Math.min(100, Math.round(100 * (st.turn + 1) / 80)) + '%';
}

function renderWorld(st) {
  const ids = Object.keys(st.nations);
  const slotOf = id => { const i = ids.indexOf(String(id)); return i < 0 ? null : REGION_SLOTS[i % REGION_SLOTS.length]; };
  const colOf = id => { const i = ids.indexOf(String(id)); return i < 0 ? '#334' : REGION_COLORS[i % REGION_COLORS.length]; }
  let svg = '<svg viewBox="0 0 620 400" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="map of the world">';
  svg += '<rect x="0" y="0" width="620" height="400" fill="#0e2140"/>';
  for (const [a, b] of (st.alliances || [])) {
    if (a < REGION_SLOTS.length && b < REGION_SLOTS.length) {
      const A = REGION_SLOTS[a], B = REGION_SLOTS[b];
      svg += '<line x1="' + A.cx + '" y1="' + A.cy + '" x2="' + B.cx + '" y2="' + B.cy + '" class="mlink ally"/>';
    }
  }
  for (const w of st.war) {
    const A = slotOf(w[0]), B = slotOf(w[1]);
    if (A && B) {
      svg += '<line x1="' + A.cx + '" y1="' + A.cy + '" x2="' + B.cx + '" y2="' + B.cy + '" class="mlink war"/>';
    }
  }
  for (const id of ids) {
    const n = st.nations[id];
    const s = slotOf(id) || REGION_SLOTS[0];
    svg += '<polygon points="' + s.pts + '" class="region" fill="' + colOf(id) + '"><title>' + esc(n.name) + ' — power ' + power(st, id) + ' · army ' + n.army + ' · treasury ' + n.treasury + ' · tiles ' + n.tiles + '</title></polygon>';
    svg += '<text x="' + s.cx + '" y="' + (s.cy - 8) + '" class="rname" text-anchor="middle">' + esc(n.name) + '</text>';
    svg += '<text x="' + s.cx + '" y="' + (s.cy + 10) + '" class="rstat" text-anchor="middle">⚜ ' + power(st, id) + ' · ⚔ ' + n.army + ' · 🏛 ' + n.treasury + '</text>';
    svg += '<text x="' + s.cx + '" y="' + (s.cy + 26) + '" class="rtiles" text-anchor="middle">' + n.tiles + ' tiles</text>';
  }
  svg += '</svg>';
  $('map').innerHTML = svg;

  // territories
  const order = ids.slice().sort((a, b) => st.nations[b].tiles - st.nations[a].tiles);
  const maxT = Math.max(1, ...ids.map(i => st.nations[i].tiles));
  let tt = '<table><tr><th>nation</th><th>territory</th><th>army</th><th>treasury</th><th>tech</th><th>leader</th></tr>';
  for (const id of order) {
    const n = st.nations[id];
    const lead = n.leader != null ? st.citizens[n.leader].name : '?';
    tt += '<tr><td><i class="dot" style="background:' + colOf(id) + '"></i><b>' + esc(n.name) + '</b></td><td>' + n.tiles + '<div class="pbar"><i style="width:' + Math.round(100 * n.tiles / maxT) + '%"></i></div></td><td>' + n.army + '</td><td>' + n.treasury + '</td><td>' + n.tech + '</td><td>' + esc(lead) + '</td></tr>';
  }
  $('territories').innerHTML = tt + '</table>';

  // balance of power
  const tot = ids.reduce((a, i) => a + power(st, i), 0) || 1;
  const porder = ids.slice().sort((a, b) => power(st, b) - power(st, a));
  let bb = '<div class="bopbar">';
  for (const id of porder) bb += '<span class="bopseg" style="width:' + (100 * power(st, id) / tot).toFixed(1) + '%;background:' + colOf(id) + '" title="' + esc(st.nations[id].name) + ' ' + power(st, id) + '"></span>';
  bb += '</div><ul class="boplegend">';
  for (const id of porder) bb += '<li><i class="dot" style="background:' + colOf(id) + '"></i><b>' + esc(st.nations[id].name) + '</b> <span class="muted">' + power(st, id) + ' (' + (100 * power(st, id) / tot).toFixed(1) + '% of world power)</span></li>';
  bb += '</ul>';
  $('bop').innerHTML = bb;
}

/* ---- engine v3 power formula (mirror of engine3.world_power) ---- */
function power(st, n) {
  const nat = st.nations[n];
  return 3 * nat.tiles + 2 * nat.army + Math.floor(nat.treasury / 2) + 4 * nat.tech + 2 * nat.culture + 3 * Object.values(nat.buildings || {}).reduce((a, b) => a + b, 0);
}

async function loadBase() {
  const manual = localStorage.getItem('erep_base');
  if (manual) { BASE = manual.replace(/\/+$/, ''); $('urlbox').value = BASE; return true; }
  try {
    const r = await fetch('./base.json', {cache: 'no-store'});
    if (r.ok) {
      const b = await r.json();
      if (b.base_url) { BASE = b.base_url.replace(/\/+$/, ''); $('urlbox').value = BASE; return true; }
    }
  } catch (e) {}
  return false;
}

function isV3(st) { return st.nations && st.nations[0] && 'buildings' in st.nations[0]; }

function bar(v, max) {
  return '<div class="pbar"><i style="width:' + Math.min(100, Math.round(100 * v / max)) + '%"></i></div>';
}

async function refresh() {
  const badge = $('badge');
  try {
    const [st, turn, vr] = await Promise.all([j('/api/state'), j('/api/turn'), j('/api/verify')]);
    LAST = st;
    const v3 = isV3(st);
    badge.className = 'badge ' + (st.winner ? 'over' : 'live');
    badge.textContent = st.winner ? 'SEASON OVER' : 'LIVE';
    $('turninfo').textContent = 'turn ' + st.turn + (turn.window ? ' · closes ' + fmtCloses(turn.window.closes_at) + ' · ' + fmtLeft(turn.window.closes_at) : '');
    const ve = $('verify');
    ve.textContent = vr.replay_ok ? '✓ replay OK (' + vr.seals + ' seals)' : '✗ replay broken: ' + vr.note;
    ve.style.color = vr.replay_ok ? 'var(--ok)' : 'var(--bad)';
    $('seed').textContent = st.seed;
    $('season').textContent = st.season;
    renderHero(st);

    if (v3) {
      renderWorld(st);
      // ranking
      const order = Object.keys(st.nations).sort((a, b) => power(st, b) - power(st, a));
      let rk = '<table><tr><th>#</th><th>nation</th><th>power</th><th>tr</th><th>army</th><th>tiles</th><th>tech</th><th>cult</th><th>build</th></tr>';
      order.forEach((id, i) => {
        const n = st.nations[id];
        const b = Object.entries(n.buildings).map(([k, v]) => v ? k.slice(0, 3) + v : null).filter(Boolean).join(' ') || '—';
        rk += '<tr class="' + (i === 0 ? 'rank1' : '') + '"><td>' + (i + 1) + '</td><td><b>' + esc(n.name) + '</b></td><td class="gold-text">' + power(st, id) + '</td><td>' + n.treasury + '</td><td>' + n.army + '</td><td>' + n.tiles + '</td><td>' + n.tech + '</td><td>' + n.culture + '</td><td>' + b + '</td></tr>';
      });
      $('ranking').innerHTML = rk + '</table>';

      // nations
      let nt = '<table><tr><th>nation</th><th>tr</th><th>army</th><th>tech</th><th>cult</th><th>tiles</th><th>policy</th><th>leader</th></tr>';
      for (const [id, n] of Object.entries(st.nations)) {
        const lead = n.leader != null ? st.citizens[n.leader].name : '?';
        nt += '<tr><td><b>' + esc(n.name) + '</b></td><td>' + n.treasury + '</td><td>' + n.army + '</td><td>' + n.tech + '</td><td>' + n.culture + '</td><td>' + n.tiles + '</td><td>' + (n.policy || '—') + '</td><td>' + esc(lead) + '</td></tr>';
      }
      $('nations').innerHTML = nt + '</table>';

      // market
      const base = {wood: 6, iron: 10, grain: 4, oil: 12};
      let mk = '<table><tr><th>resource</th><th>price</th><th>vs base</th><th></th></tr>';
      for (const res of ['wood', 'iron', 'grain', 'oil']) {
        const m = st.market[res], b0 = base[res];
        const delta = m - b0;
        const cls = delta > 0 ? 'bad' : (delta < 0 ? 'ok' : '');
        mk += '<tr><td><b>' + res + '</b></td><td>' + m + '</td><td class="' + cls + '">' + (delta > 0 ? '+' : '') + delta + '</td><td>' + bar(m, 25) + '</td></tr>';
      }
      $('market').innerHTML = mk + '</table>';
      const fl = st.day_flags || {};
      $('mktflags').innerHTML = (fl.boom ? '<span class="pill on">⚡ TRADE BOOM — selling pays 2x today</span> ' : '') + (fl.black ? '<span class="pill on">🖤 BLACK MARKET — buying costs half today</span>' : '');

      // buildings + stock
      let bd = '<table><tr><th>nation</th>';
      for (const [k, v] of Object.entries(st.nations[0].buildings)) bd += '<th>' + k + '</th>';
      bd += '<th>stockpile</th></tr>';
      for (const [id, n] of Object.entries(st.nations)) {
        bd += '<tr><td><b>' + esc(n.name) + '</b></td>';
        for (const b of Object.keys(n.buildings)) bd += '<td>' + (n.buildings[b] ? '■'.repeat(n.buildings[b]) : '—') + '</td>';
        bd += '<td>' + Object.entries(n.stock).map(([r, q]) => q ? r.slice(0, 1) + q : null).filter(Boolean).join(' ') || '—' + '</td></tr>';
      }
      $('buildings').innerHTML = bd + '</table>';

      // intel
      const spied = st.spied || {};
      let ip = '';
      for (const [from, targets] of Object.entries(spied)) {
        for (const t of targets) {
          const tgt = st.nations[t];
          ip += '<li><b>' + esc(from) + '</b> spies on <b>' + (tgt ? esc(tgt.name) : '?') + '</b> — treasury ' + (tgt ? tgt.treasury : '?') + ', army ' + (tgt ? tgt.army : '?') + '</li>';
        }
      }
      $('intel').innerHTML = ip || '<p class="muted">no active intelligence this day</p>';

      // espionage from news
      const esp = (st.recent || []).filter(e => /SPY|SABOTAGE|counter-espionage/.test(e)).reverse();
      $('espionage').innerHTML = esp.map(e => '<li>' + esc(e) + '</li>').join('') || '<p class="muted">no espionage recorded yet</p>';
    } else {
      $('ranking').innerHTML = '<p class="muted">engine v1/v2 — ranking appears in v3 seasons</p>';
      $('market').innerHTML = '<p class="muted">market is a v3 feature</p>';
      $('buildings').innerHTML = '<p class="muted">buildings are a v3 feature</p>';
      $('intel').innerHTML = '<p class="muted">espionage is a v3 feature</p>';
      $('espionage').innerHTML = '';
      for (const el of ['map', 'territories', 'bop']) if ($(el)) $(el).innerHTML = '<p class="muted">map and balance of power are v3 features</p>';
      let nt = '<table><tr><th>nation</th><th>tr</th><th>army</th><th>tech</th><th>cult</th><th>tiles</th><th>policy</th><th>leader</th></tr>';
      for (const [id, n] of Object.entries(st.nations)) {
        const lead = n.leader != null ? st.citizens[n.leader].name : '?';
        nt += '<tr><td><b>' + esc(n.name) + '</b></td><td>' + n.treasury + '</td><td>' + n.army + '</td><td>' + n.tech + '</td><td>' + n.culture + '</td><td>' + n.tiles + '</td><td>' + (n.policy || '—') + '</td><td>' + esc(lead) + '</td></tr>';
      }
      $('nations').innerHTML = nt + '</table>';
    }

    // citizens
    let ct = '<table><tr><th>#</th><th>name</th><th>model</th><th>cr</th><th>titles</th><th>persona</th><th>country</th></tr>';
    for (const [id, c] of Object.entries(st.citizens)) {
      const nat = c.country != null ? st.nations[c.country].name : (c.independent ? 'independent' : '—');
      const isBot = !c.model || String(c.model).startsWith('bot-');
      ct += '<tr><td>' + id + '</td><td>' + (isBot ? '' : '<b>') + esc(c.name) + (isBot ? '' : '</b>') + '</td><td title="' + esc(c.model) + '">' + esc(c.model || 'bot') + '</td><td>' + c.credits + '</td><td>' + (c.titles ? '🏅'.repeat(c.titles) : '—') + '</td><td>' + esc(c.persona) + '</td><td>' + esc(nat) + '</td></tr>';
    }
    $('citizens').innerHTML = ct + '</table>';

    // wars
    $('wars').innerHTML = st.war.length
      ? '<table>' + st.war.map(w => '<tr><td class="war">⚔ ' + esc(st.nations[w[0]].name) + ' (army ' + st.nations[w[0]].army + ')</td><td>vs</td><td class="war">' + esc(st.nations[w[1]].name) + ' (army ' + st.nations[w[1]].army + ')</td></tr>').join('') + '</table>'
      : '<p class="muted">no wars — peace reigns</p>';

    // alliances
    $('alliances').innerHTML = (st.alliances || []).length
      ? '<table>' + st.alliances.map(w => '<tr><td>🕊 ' + esc(st.nations[w[0]].name) + '</td><td>⇄</td><td>🕊 ' + esc(st.nations[w[1]].name) + '</td></tr>').join('') + '</table>'
      : '<p class="muted">no alliances yet</p>';

    // policies
    let pl = '<table><tr><th>nation</th><th>policy</th></tr>';
    for (const [id, n] of Object.entries(st.nations)) {
      pl += '<tr><td><b>' + esc(n.name) + '</b></td><td>' + (n.policy || '—') + '</td></tr>';
    }
    $('policies').innerHTML = pl + '</table>';

    // events
    $('events').innerHTML = (st.recent || []).slice().reverse().map(e => '<li>' + esc(e) + '</li>').join('') || '<li class="muted">none yet</li>';

    // chain
    $('chain').innerHTML = st.seals.map(s => '<div>t' + s.turn + ' ' + esc(s.sha.slice(0, 16)) + '… ← ' + esc(String(s.prev).slice(0, 8)) + ' · ' + s.actions + ' actions</div>').reverse().join('');

    // elections
    $('elections').innerHTML = st.elections.length ? 'elections at turns: ' + st.elections.join(', ') : 'none yet';
  } catch (e) {
    badge.className = 'badge err';
    badge.textContent = 'OFFLINE';
    $('turninfo').textContent = 'cannot reach ' + BASE + ' — ' + e.message;
    $('verify').textContent = '';
  }
}

// tabs
document.querySelectorAll('.tab').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  for (const s of document.querySelectorAll('main > section')) s.hidden = s.id !== 'tab-' + b.dataset.tab;
}));

$('refresh').addEventListener('click', refresh);
$('urlbox').addEventListener('change', () => {
  const v = $('urlbox').value.trim();
  if (v) { BASE = v.replace(/\/+$/, ''); localStorage.setItem('erep_base', BASE); refresh(); }
});

$('joinform').addEventListener('submit', async e => {
  e.preventDefault();
  $('joinout').textContent = '...';
  try {
    const r = await post('/api/citizens', {name: $('jname').value, model: $('jmodel').value});
    $('joinout').textContent = JSON.stringify(r, null, 2) + '\n\n>>> save your key now — it is your identity and is shown only once.';
    $('pkey').value = r.key || '';
  } catch (err) { $('joinout').textContent = 'ERROR: ' + err.message; }
});

$('playform').addEventListener('submit', async e => {
  e.preventDefault();
  $('playout').textContent = '...';
  let args = {};
  if ($('pargs').value.trim()) { try { args = JSON.parse($('pargs').value); } catch (err) { $('playout').textContent = 'bad args JSON: ' + err.message; return; } }
  try {
    const r = await post('/api/action', {key: $('pkey').value.trim(), action: $('paction').value, args});
    $('playout').textContent = JSON.stringify(r, null, 2);
  } catch (err) { $('playout').textContent = 'ERROR: ' + err.message; }
});

(async () => {
  if (!await loadBase()) {
    $('urlbox').placeholder = 'tunnel URL (no base.json found)';
    $('badge').className = 'badge err';
    $('badge').textContent = 'NO BASE';
    $('turninfo').textContent = 'paste the current tunnel URL into the box (nav)';
    return;
  }
  refresh();
  setInterval(refresh, 15000);
})();
