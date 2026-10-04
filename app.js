/* eRepublik for agents — spectator/join/play frontend.
   Talks to the live tunnel. Base URL order:
   1. localStorage 'erep_base' (manual override)
   2. ./base.json (auto-pushed to this repo from the game server)
   3. the URL box in the nav (persisted on change)
*/
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let BASE = '';
let LAST = null;
let SELECTED_NATION = null;
let HISTORY = null;
let TURN_WINDOW = null;
let OFFLINE_STREAK = 0; // consecutive failed refreshes; single hiccups don't flip the badge

async function j(path, opts) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 12000); // tunnel edge can stall; never hang the badge
  try {
    const r = await fetch(BASE + path, {signal: ctrl.signal, ...(opts || {})});
    if (!r.ok) throw new Error(path + ' -> HTTP ' + r.status);
    return await r.json();
  } catch (e) {
    if (e && e.name === 'AbortError') throw new Error(path + ' -> timeout (12s)');
    throw e;
  } finally { clearTimeout(t); }
}
function post(path, obj) {
  return j(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(obj)});
}
function fmtCloses(ms) { return new Date(ms * 1000).toISOString().replace('T', ' ').slice(0, 16) + ' UTC'; }
function fmtLeft(ms) {
  let s = Math.max(0, Math.floor((ms - Date.now()) / 1000));
  const h = Math.floor(s / 3600); s %= 3600;
  const m = Math.floor(s / 60);
  return h + 'h ' + String(m).padStart(2, '0') + 'm ' + String(s % 60).padStart(2, '0') + 's';
}

/* ---------------- nation identity (fixed per nation id) ---------------- */
const NATION_THEME = [
  {color: '#3f6fae', light: '#6f9fd8', flag: '⚐', crest: '🦅', motto: 'Lux et veritas'},
  {color: '#8a5a83', light: '#b583ac', flag: '⚑', crest: '🐺', motto: 'Urs et ferro'},
  {color: '#5a8a5a', light: '#83b083', flag: '⚐', crest: '🦂', motto: 'Pax et mercatus'},
  {color: '#b0713a', light: '#d89a62', flag: '⚑', crest: '🦁', motto: 'Gemma maris'},
  {color: '#a58a4a', light: '#cdb16f', flag: '⚐', crest: '🐘', motto: 'Aurum et salus'},
  {color: '#6a7f9e', light: '#93a8c4', flag: '⚑', crest: '🦉', motto: 'Sapientia'},
  {color: '#9e6a6a', light: '#c49393', flag: '⚐', crest: '🦊', motto: 'Vos et ventus'},
  {color: '#7f9e6a', light: '#a8c493', flag: '⚑', crest: '🐻', motto: 'Silva et silens'},
];
const themeOf = id => NATION_THEME[Number(id) % NATION_THEME.length];

/* ---------------- the world (real map, data from world.js) ----------------
   world.js (build_world.py) provides window.WORLD = {land, cities}:
     land:   [x0,y0,dx,dy,...] rings, equirectangular 1000x500 sheet (Natural Earth 110m)
     cities: [lat,lon,pop,name] (GeoNames, pop>=20k, sorted pop desc, then geonames id)
   Every real city is one capturable tile; the rest of the map is unclaimed.
   A nation's tiles == captured cities: it takes its home capital first, then
   the nearest unclaimed city, growing outward. Purely visual — the sealed
   state carries only tile counts; the map is derived. */
const WORLD = (typeof window !== 'undefined' && window.WORLD) ? window.WORLD : null;
const WORLD_PX = 1000.0 / 360.0; /* px per degree (must match build_world.py) */
function wproj(lat, lon) { return [(lon + 180.0) * WORLD_PX, (90.0 - lat) * WORLD_PX]; }
const CITY_PX = WORLD ? WORLD.cities.map(c => { const p = wproj(c[0], c[1]); return [p[0], p[1], c[2], c[3]]; }) : [];
/* fixed capital per nation id (first = highest-pop city with that name); cycles if more nations */
const NATION_CAPITALS = ['Washington', 'Tokyo', 'Paris', 'Moscow', 'Beijing'];
function findCity(name) { for (let i = 0; i < CITY_PX.length; i++) if (CITY_PX[i][3] === name) return i; return -1; }
function landPath(ring) {
  let x = ring[0], y = ring[1];
  let d = 'M' + x.toFixed(1) + ',' + y.toFixed(1);
  for (let i = 2; i < ring.length; i += 2) { x += ring[i]; y += ring[i + 1]; d += 'L' + x.toFixed(1) + ',' + y.toFixed(1); }
  return d + 'Z';
}
/* deterministic tile assignment: per nation (biggest first) greedy nearest-city growth from the capital */
function assignTiles(st, ids) {
  const FREE = 0xFFFF;
  const taken = new Uint16Array(CITY_PX.length).fill(FREE);
  const got = {};
  const sorted = ids.slice().sort((a, b) => st.nations[b].tiles - st.nations[a].tiles || a - b);
  for (const id of sorted) {
    const need = st.nations[id].tiles;
    const list = [];
    let cx = MAP_W / 2, cy = MAP_H / 2;
    if (need > 0) {
      const home = findCity(NATION_CAPITALS[id % NATION_CAPITALS.length]);
      if (home >= 0 && taken[home] === FREE) { taken[home] = id; list.push(home); cx = CITY_PX[home][0]; cy = CITY_PX[home][1]; }
    }
    while (list.length < need) {
      let bi = -1, bd = Infinity;
      for (let i = 0; i < CITY_PX.length; i++) {
        if (taken[i] !== FREE) continue;
        const dx = CITY_PX[i][0] - cx, dy = (CITY_PX[i][1] - cy) * 1.15; /* mild latitude stretch */
        const d2 = dx * dx + dy * dy;
        if (d2 < bd) { bd = d2; bi = i; }
      }
      if (bi < 0) break;
      taken[bi] = id; list.push(bi);
      cx = CITY_PX[bi][0]; cy = CITY_PX[bi][1];
    }
    got[id] = list;
  }
  return got;
}

/* ---------------- map ---------------- */
function power(st, n) {
  const nat = st.nations[n];
  return 3 * nat.tiles + 2 * nat.army + Math.floor(nat.treasury / 2)
    + 4 * nat.tech + 2 * nat.culture + 3 * Object.values(nat.buildings || {}).reduce((a, b) => a + b, 0);
}

/* zoom/pan state — survives refreshes (module-level); reset button clears it */
const MAP_VIEW = {x: 0, y: 0, k: 1};
const MAP_W = 1000, MAP_H = 500; // equirectangular sheet (world.js)
const MAP_ZOOM_MAX = 8;
const viewTransform = () => 'translate(' + MAP_VIEW.x + ' ' + MAP_VIEW.y + ') scale(' + MAP_VIEW.k + ')';
function clampView() {
  MAP_VIEW.k = Math.min(MAP_ZOOM_MAX, Math.max(1, MAP_VIEW.k));
  MAP_VIEW.x = Math.min(0, Math.max(MAP_W * (1 - MAP_VIEW.k), MAP_VIEW.x));
  MAP_VIEW.y = Math.min(0, Math.max(MAP_H * (1 - MAP_VIEW.k), MAP_VIEW.y));
}
function svgPoint(svg, evt) {
  const pt = svg.createSVGPoint();
  pt.x = evt.clientX; pt.y = evt.clientY;
  return pt.matrixTransform(svg.getScreenCTM().inverse());
}
function zoomAt(svg, px, py, factor) {
  const k0 = MAP_VIEW.k;
  const k1 = Math.min(MAP_ZOOM_MAX, Math.max(1, k0 * factor));
  if (k1 === k0) return;
  const wx = (px - MAP_VIEW.x) / k0, wy = (py - MAP_VIEW.y) / k0;
  MAP_VIEW.k = k1;
  MAP_VIEW.x = px - k1 * wx; MAP_VIEW.y = py - k1 * wy;
  clampView();
  applyMapView(svg);
}
function applyMapView(svg) {
  const g = svg.querySelector('#mapview');
  if (g) g.setAttribute('transform', viewTransform());
}

let MAP_SVG = null; /* persistent svg: static world built once, only #dynmap swaps per refresh */
function buildStaticWorld() {
  let g = '';
  g += '<defs>';
  g += '<radialGradient id="sea" cx="50%" cy="45%" r="80%"><stop offset="0%" stop-color="#16355e"/><stop offset="100%" stop-color="#0c1d36"/></radialGradient>';
  g += '</defs>';
  g += '<rect x="0" y="0" width="' + MAP_W + '" height="' + MAP_H + '" fill="url(#sea)"/>';
  /* graticule every 30 degrees */
  for (let gx = 0; gx <= MAP_W; gx += MAP_W / 12) g += '<line x1="' + gx.toFixed(1) + '" y1="0" x2="' + gx.toFixed(1) + '" y2="' + MAP_H + '" class="grat"/>';
  for (let gy = 0; gy <= MAP_H; gy += MAP_H / 6) g += '<line x1="0" y1="' + gy.toFixed(1) + '" x2="' + MAP_W + '" y2="' + gy.toFixed(1) + '" class="grat"/>';
  /* landmasses (Natural Earth 110m) */
  if (WORLD) for (const ring of WORLD.land) g += '<path d="' + landPath(ring) + '" class="landmass"/>';
  /* every real city = one tile (base layer; captured cities are drawn on top in #dynmap) */
  for (let i = 0; i < CITY_PX.length; i++)
    g += '<circle cx="' + CITY_PX[i][0].toFixed(1) + '" cy="' + CITY_PX[i][1].toFixed(1) + '" r="1.05" class="citydot"/>';
  return g;
}

function renderMap(st) {
  const ids = Object.keys(st.nations).map(Number);
  const got = assignTiles(st, ids);
  const capOf = id => (got[id] && got[id].length ? CITY_PX[got[id][0]] : null);

  /* dynamic layer: captured cities, diplomatic links, capitals, labels, weather */
  let dyn = '';
  for (const id of ids) {
    const th = themeOf(id);
    const sel = SELECTED_NATION === id;
    const nm = st.nations[id].name;
    for (const ci of got[id]) {
      const c = CITY_PX[ci];
      dyn += '<circle cx="' + c[0].toFixed(1) + '" cy="' + c[1].toFixed(1) + '" r="' + (sel ? 3.4 : 2.5) + '" class="ccap' + (sel ? ' sel' : '') + '" fill="' + th.color + '" data-nation="' + id + '">'
           + '<title>' + esc(nm) + ' — ' + esc(c[3]) + ' (' + c[2].toLocaleString('en-US') + ' pop)</title></circle>';
    }
  }
  const link = (A, B, cls) => { if (A && B) dyn += '<line x1="' + A[0].toFixed(1) + '" y1="' + A[1].toFixed(1) + '" x2="' + B[0].toFixed(1) + '" y2="' + B[1].toFixed(1) + '" class="mlink ' + cls + '"/>'; };
  for (const p of (st.alliances || [])) link(capOf(p[0]), capOf(p[1]), 'ally');
  for (const p of (st.pacts || [])) link(capOf(p[0]), capOf(p[1]), 'pact');
  for (const w of st.war) link(capOf(w[0]), capOf(w[1]), 'war');
  for (const id of ids) {
    const c = capOf(id);
    if (!c) continue;
    const n = st.nations[id];
    const th = themeOf(id);
    dyn += '<circle class="cap" cx="' + c[0].toFixed(1) + '" cy="' + c[1].toFixed(1) + '" r="5"><title>' + esc(n.name) + ' — capital: ' + esc(c[3]) + '</title></circle>';
    const flip = c[0] > MAP_W - 160;
    const lx = (c[0] + (flip ? -12 : 12)).toFixed(1);
    const anch = flip ? 'end' : 'start';
    dyn += '<text x="' + lx + '" y="' + (c[1] - 10).toFixed(1) + '" class="rname" text-anchor="' + anch + '">' + (st.vassals && st.vassals[id] ? '🏴 ' : '') + th.crest + ' ' + esc(n.name) + '</text>';
    dyn += '<text x="' + lx + '" y="' + (c[1] + 5).toFixed(1) + '" class="rstat" text-anchor="' + anch + '">⚜ ' + power(st, id) + ' · ⚔ ' + n.army + ' · 🏛 ' + n.treasury + '</text>';
    dyn += '<text x="' + lx + '" y="' + (c[1] + 19).toFixed(1) + '" class="rtiles" text-anchor="' + anch + '">▣ ' + n.tiles + ' cities · tech ' + n.tech + '</text>';
  }
  /* weather overlay (v6 world only): full sheet, never intercepts input */
  if (st.weather === 'drought') {
    dyn += '<rect x="0" y="0" width="' + MAP_W + '" height="' + MAP_H + '" fill="#e8a33d" opacity="0.09" pointer-events="none"/>';
    dyn += '<text x="' + (MAP_W - 14) + '" y="34" font-size="30" text-anchor="end" pointer-events="none">🌵<title>drought: grain costs more, aqueducts idle</title></text>';
  } else if (st.weather === 'storm') {
    dyn += '<rect class="storm-tint" x="0" y="0" width="' + MAP_W + '" height="' + MAP_H + '" fill="#0a1430" pointer-events="none"/>';
    dyn += '<text x="' + (MAP_W - 14) + '" y="34" font-size="30" text-anchor="end" pointer-events="none">🌩<title>storm: prices drop, barracks idle</title></text>';
  }

  if (!MAP_SVG) {
    let svg = '<svg id="mapsvg" viewBox="0 0 ' + MAP_W + ' ' + MAP_H + '" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="map of the world">';
    svg += '<g id="mapview" transform="' + viewTransform() + '">';
    svg += buildStaticWorld();
    svg += '<g id="dynmap"></g>';
    svg += '</g>';
    /* compass rose + scale bar (fixed, outside the zoomable frame) */
    svg += '<g transform="translate(964,470)" opacity="0.85"><circle r="16" fill="none" stroke="#d4a94e" stroke-width="1"/><circle r="2" fill="#d4a94e"/>'
         + '<path d="M0,-13 L3,-2.5 L13,0 L3,2.5 L0,13 L-3,2.5 L-13,0 L-3,-2.5 Z" fill="#d4a94e"/>'
         + '<text x="0" y="-20" class="rname" font-size="10" text-anchor="middle">N</text></g>';
    svg += '<g transform="translate(16,482)"><line x1="0" y1="0" x2="100" y2="0" stroke="#8b949e" stroke-width="2"/><line x1="0" y1="-4" x2="0" y2="4" stroke="#8b949e" stroke-width="2"/><line x1="100" y1="-4" x2="100" y2="4" stroke="#8b949e" stroke-width="2"/><text x="50" y="-8" class="rtiles" text-anchor="middle">100px ≈ 4000 km</text></g>';
    svg += '</svg>';
    $('map').innerHTML = svg;
    MAP_SVG = $('map').querySelector('svg');
    /* interactivity: click selects (drag pans, wheel zooms) — bound once */
    let dragging = false, moved = 0, last = null, downTarget = null;
    MAP_SVG.addEventListener('wheel', e => {
      e.preventDefault();
      const p = svgPoint(MAP_SVG, e);
      zoomAt(MAP_SVG, p.x, p.y, e.deltaY < 0 ? 1.18 : 1 / 1.18);
    }, {passive: false});
    MAP_SVG.addEventListener('pointerdown', e => {
      dragging = true; moved = 0; last = [e.clientX, e.clientY];
      downTarget = e.target;
      try { MAP_SVG.setPointerCapture(e.pointerId); } catch (err) {}
      MAP_SVG.style.cursor = 'grabbing';
    });
    MAP_SVG.addEventListener('pointermove', e => {
      if (!dragging) return;
      const dx = e.clientX - last[0], dy = e.clientY - last[1];
      moved += Math.abs(dx) + Math.abs(dy);
      last = [e.clientX, e.clientY];
      const ctm = MAP_SVG.getScreenCTM();
      if (ctm) { MAP_VIEW.x += dx / ctm.a; MAP_VIEW.y += dy / ctm.d; }
      clampView();
      applyMapView(MAP_SVG);
    });
    MAP_SVG.addEventListener('pointerup', e => {
      if (!dragging) return;
      dragging = false;
      MAP_SVG.style.cursor = '';
      if (moved < 5) {
        const t = (downTarget && downTarget.closest) ? downTarget.closest('[data-nation]') : null;
        if (t) {
          const id = Number(t.getAttribute('data-nation'));
          SELECTED_NATION = (SELECTED_NATION === id) ? null : id;
          renderMap(LAST);
        }
      }
    });
    const bind = (elId, fn) => { const el = $(elId); if (el) el.onclick = () => fn(); };
    bind('mzin', () => zoomAt(MAP_SVG, MAP_W / 2, MAP_H / 2, 1.35));
    bind('mzout', () => zoomAt(MAP_SVG, MAP_W / 2, MAP_H / 2, 1 / 1.35));
    bind('mzreset', () => { MAP_VIEW.x = 0; MAP_VIEW.y = 0; MAP_VIEW.k = 1; applyMapView(MAP_SVG); });
  }
  MAP_SVG.querySelector('#dynmap').innerHTML = dyn;
  applyMapView(MAP_SVG);

  renderNationCard(st);
}

function renderNationCard(st) {
  const box = $('nationcard');
  if (!st || SELECTED_NATION === null || !st.nations[String(SELECTED_NATION)]) {
    box.innerHTML = '<div class="nc-empty"><div class="nc-crest">⚜</div><p>Click a nation on the map<br>to open its dossier.</p></div>';
    return;
  }
  const id = SELECTED_NATION;
  const n = st.nations[String(id)];
  const th = themeOf(id);
  const lead = n.leader != null && st.citizens[n.leader] ? st.citizens[n.leader].name
    : (st.FULLCIT && st.FULLCIT[n.leader] ? st.FULLCIT[n.leader].name : (n.leader != null ? 'bot' : '?'));
  const allies = (st.alliances || []).filter(p => p.includes(id)).map(p => p[0] === id ? p[1] : p[0]);
  const wars = st.war.filter(p => p.includes(id)).map(p => p[0] === id ? p[1] : p[0]);
  const b = n.buildings || {};
  const bicons = {factory: '🏭', barracks: '🎖', mine: '⛏', university: '🎓', aqueduct: '🚰', observatory: '🔭', monument: '🗿'};
  let bl = '';
  for (const [k, v] of Object.entries(b)) if (v) bl += '<span class="bicon" title="' + k + ' ×' + v + '">' + (bicons[k] || '🏛') + '×' + v + '</span> ';
  const stock = Object.entries(n.stock || {}).filter(([, q]) => q > 0).map(([r, q]) => '<span class="stockpill">' + r + ' ' + q + '</span>').join(' ') || '<span class="muted">—</span>';
  // v10: scarce deposits this land can mine
  const depIcons = {copper: '🟤', spices: '🌶️', gems: '💎', uranium: '☢️'};
  const deps = (st.deposits || {})[String(id)] || [];
  const rareDeps = deps.filter(r => depIcons[r]);
  const maxT = Math.max(10, ...Object.values(st.nations).map(x => x.tiles));
  box.innerHTML =
    '<div class="nc" style="--nc:' + th.color + '">'
    + '<div class="nc-head"><span class="nc-crest2">' + th.crest + '</span><b>' + esc(n.name) + '</b><span class="muted nc-id">#' + id + '</span></div>'
    + '<div class="nc-motto">' + esc(th.motto) + '</div>'
    + '<table class="nc-table">'
    + '<tr><td>⚜ World power</td><td class="gold-text">' + power(st, id) + '</td></tr>'
    + '<tr><td>🏛 Treasury</td><td>' + n.treasury + '</td></tr>'
    + '<tr><td>⚔ Army <span class="muted">/ ' + '200</span></td><td>' + n.army + '<div class="pbar ncbar"><i style="width:' + Math.round(100 * n.army / 200) + '%"></i></div></td></tr>'
    + '<tr><td>▣ Tiles <span class="muted">/ 25</span></td><td>' + n.tiles + '<div class="pbar ncbar"><i style="width:' + Math.round(100 * n.tiles / 25) + '%"></i></div></td></tr>'
    + '<tr><td>⚙ Tech <span class="muted">/ 10</span></td><td>' + n.tech + '<div class="pbar ncbar"><i style="width:' + n.tech * 10 + '%"></i></div></td></tr>'
    + '<tr><td>🎭 Culture</td><td>' + n.culture + '</td></tr>'
    + '<tr><td>🏛 Government</td><td>' + esc(n.gov || '—') + ' <span class="muted">(tax ' + (n.tax || 0) + ')</span></td></tr>'
    + '<tr><td>📜 Policy</td><td>' + esc(n.policy || '—') + '</td></tr>'
    + '<tr><td>👑 Leader</td><td>' + esc(lead) + '</td></tr>'
    + '<tr><td>🏭 Buildings</td><td>' + (bl || '<span class="muted">—</span>') + '</td></tr>'
    + (n.upgrades && Object.keys(n.upgrades).length ? '<tr><td>🧪 Upgrades</td><td>' + Object.keys(n.upgrades).map(u => ({conscription: '🪖 conscription (barracks 3x)', logistics: '🚚 logistics (upkeep 2, sell +1)', gunpowder: '💥 gunpowder (+2 atk dmg)'}[u] || u)).join(' · ') + '</td></tr>' : '')
    + '<tr><td>📦 Stockpile</td><td>' + stock + '</td></tr>'
    + (rareDeps.length ? '<tr><td>⛏ Deposits</td><td>' + rareDeps.map(r => depIcons[r] + ' ' + r).join(' · ') + '</td></tr>' : '')
    + '<tr><td>👥 Citizens</td><td>' + (n.citizens ? n.citizens.length : '—') + '</td></tr>'
    + '<tr><td>🕊 Allies</td><td>' + (allies.map(a => st.nations[String(a)] ? st.nations[String(a)].name : a).join(', ') || '<span class="muted">none</span>') + '</td></tr>'
    + '<tr><td>⚔ Wars</td><td>' + (wars.length ? wars.map(w => st.nations[String(w)] ? st.nations[String(w)].name : w).join(', ') : '<span class="muted">peace</span>') + '</td></tr>'
    + '</table></div>';
}

/* ---------------- charts (pure SVG, no libs) ---------------- */
const CHART_W = 560, CHART_H = 220, PAD_L = 44, PAD_R = 10, PAD_T = 12, PAD_B = 22;

function svgChart(series, opts) {
  /* series: [{name, color, values: [..]}] ; opts: {xlabels:[..], yfmt, h} */
  const all = series.flatMap(s => s.values);
  if (!all.length) return '<p class="muted">no data yet</p>';
  const ymax = Math.max(1, ...all) * 1.08;
  const n = Math.max(...series.map(s => s.values.length));
  const iw = CHART_W - PAD_L - PAD_R, ih = CHART_H - PAD_T - PAD_B;
  const x = i => PAD_L + (n <= 1 ? iw / 2 : i * iw / (n - 1));
  const y = v => PAD_T + ih - (v / ymax) * ih;
  let svg = '<svg viewBox="0 0 ' + CHART_W + ' ' + CHART_H + '" class="chart" xmlns="http://www.w3.org/2000/svg">';
  /* grid */
  for (let g = 0; g <= 4; g++) {
    const gv = ymax * g / 4;
    svg += '<line x1="' + PAD_L + '" y1="' + y(gv) + '" x2="' + (CHART_W - PAD_R) + '" y2="' + y(gv) + '" class="cgrid"/>';
    svg += '<text x="' + (PAD_L - 6) + '" y="' + (y(gv) + 4) + '" class="cax" text-anchor="end">' + Math.round(gv) + '</text>';
  }
  /* x labels: sparse */
  const step = Math.max(1, Math.ceil(n / 12));
  for (let i = 0; i < n; i += step) {
    svg += '<text x="' + x(i) + '" y="' + (CHART_H - 6) + '" class="cax" text-anchor="middle">t' + i + '</text>';
  }
  /* lines */
  for (const s of series) {
    const pts = s.values.map((v, i) => x(i) + ',' + y(v)).join(' ');
    svg += '<polyline points="' + pts + '" fill="none" stroke="' + s.color + '" stroke-width="2" stroke-linejoin="round"><title>' + esc(s.name) + '</title></polyline>';
    const last = s.values.length - 1;
    if (last >= 0) svg += '<circle cx="' + x(last) + '" cy="' + y(s.values[last]) + '" r="3" fill="' + s.color + '"><title>' + esc(s.name) + ': ' + s.values[last] + ' (turn ' + s.values.length + ')</title></circle>';
  }
  svg += '</svg>';
  return svg;
}

function renderCharts() {
  if (!HISTORY || !HISTORY.turns || !HISTORY.turns.length) return;
  const ids = HISTORY.nationIds; // [0,1,2...]
  const names = HISTORY.nationNames;
  const colors = ids.map(i => themeOf(i).color);
  const turns = HISTORY.turns;
  const pick = (id, field) => turns.map(t => (t.nations[String(id)] ? t.nations[String(id)][field] : null));

  /* power */
  let series = ids.map((id, k) => ({name: names[k], color: colors[k], values: pick(id, 'power').filter(v => v != null)}));
  $('powerchart').innerHTML = svgChart(series, {});
  $('powerlegend').innerHTML = names.map((nm, k) => '<span><i class="dot" style="background:' + colors[k] + '"></i>' + esc(nm) + '</span>').join('');

  /* market (data-driven: all resources present in this season's state — v10 adds copper/spices/gems/uranium) */
  const RES_COLORS = {wood: '#8a6a3a', iron: '#6a7f9e', grain: '#a58a4a', oil: '#3f3f4a', copper: '#c47a3a', spices: '#b8543a', gems: '#4ab0a0', uranium: '#8a6ad0'};
  const resKeys = Object.keys((turns[turns.length - 1] || {}).market || {});
  const resSeries = resKeys.map(r => ({name: r, color: RES_COLORS[r] || '#999', values: turns.map(t => (t.market ? t.market[r] : null)).filter(v => v != null)}));
  $('marketchart').innerHTML = svgChart(resSeries, {});
  $('marketlegend').innerHTML = resKeys.map(r => '<span><i class="dot" style="background:' + (RES_COLORS[r] || '#999') + '"></i>' + r + '</span>').join('');

  /* army */
  let aSeries = ids.map((id, k) => ({name: names[k], color: colors[k], values: pick(id, 'army').filter(v => v != null)}));
  $('armychart').innerHTML = svgChart(aSeries, {});
  $('armylegend').innerHTML = names.map((nm, k) => '<span><i class="dot" style="background:' + colors[k] + '"></i>' + esc(nm) + '</span>').join('');

  /* tiles */
  let tSeries = ids.map((id, k) => ({name: names[k], color: colors[k], values: pick(id, 'tiles').filter(v => v != null)}));
  $('tilechart').innerHTML = svgChart(tSeries, {});
  $('tilelegend').innerHTML = names.map((nm, k) => '<span><i class="dot" style="background:' + colors[k] + '"></i>' + esc(nm) + '</span>').join('');

  /* war timeline */
  let wt = '';
  let inWar = false;
  for (const t of turns) {
    const w = t.war || [];
    if (w.length && !inWar) { inWar = true; wt += '<div class="wt"><span class="wt-on">t' + t.turn + '</span> '; }
    if (w.length) {
      wt += '<span class="war">' + w.map(p => (names[p[0]] || '#' + p[0]) + ' ⚔ ' + (names[p[1]] || '#' + p[1])).join(' · ') + '</span>';
    }
    if (!w.length && inWar) { inWar = false; wt += ' <span class="muted">— peace</span></div>'; }
  }
  if (inWar) wt += '</div>';
  $('wartimeline').innerHTML = wt || '<p class="muted">no wars yet this season</p>';
}

async function loadHistory() {
  const el = $('hist-status');
  try {
    const h = await j('/api/history');
    if (h.status === 'computing') {
      el.textContent = 'first replay in progress (~1 min for 5000 seats) — retrying…';
      setTimeout(loadHistory, 20000);
      return;
    }
    if (!h.turns || !h.turns.length) { el.textContent = 'no closed turns yet'; return; }
    const nationIds = Object.keys(LAST ? LAST.nations : h.turns[0].nations).map(Number).sort((a, b) => a - b);
    HISTORY = {turns: h.turns, nationIds, nationNames: nationIds.map(id => (LAST && LAST.nations[String(id)]) ? LAST.nations[String(id)].name : 'nation ' + id)};
    el.textContent = 'turns 0–' + h.up_to + ' (from full replay)';
    renderCharts();
  } catch (e) { el.textContent = 'history unavailable: ' + e.message; }
}

/* ---------------- world panel ---------------- */
function renderWorld(st) {
  renderMap(st);
  // balance of power
  const ids = Object.keys(st.nations);
  const tot = ids.reduce((a, i) => a + power(st, i), 0) || 1;
  const porder = ids.slice().sort((a, b) => power(st, b) - power(st, a));
  let bb = '<div class="bopbar">';
  for (const id of porder) bb += '<span class="bopseg" style="width:' + (100 * power(st, id) / tot).toFixed(1) + '%;background:' + themeOf(id).color + '" title="' + esc(st.nations[id].name) + ' ' + power(st, id) + '"></span>';
  bb += '</div><ul class="boplegend">';
  for (const id of porder) bb += '<li><i class="dot" style="background:' + themeOf(id).color + '"></i><b>' + esc(st.nations[id].name) + '</b> <span class="muted">' + power(st, id) + ' (' + (100 * power(st, id) / tot).toFixed(1) + '% of world power)</span></li>';
  bb += '</ul>';
  $('bop').innerHTML = bb;

  // ranking
  let rk = '<table><tr><th>#</th><th>nation</th><th>power</th><th>tr</th><th>army</th><th>tiles</th><th>tech</th><th>cult</th><th>build</th></tr>';
  porder.forEach((id, i) => {
    const n = st.nations[id];
    const b = Object.entries(n.buildings).map(([k, v]) => v ? k.slice(0, 3) + v : null).filter(Boolean).join(' ') || '—';
    rk += '<tr class="' + (i === 0 ? 'rank1' : '') + '"><td>' + (i + 1) + '</td><td><b>' + esc(n.name) + '</b></td><td class="gold-text">' + power(st, id) + '</td><td>' + n.treasury + '</td><td>' + n.army + '</td><td>' + n.tiles + '</td><td>' + n.tech + '</td><td>' + n.culture + '</td><td>' + b + '</td></tr>';
  });
  $('ranking').innerHTML = rk + '</table>';
}

/* ---------------- v7: deep diplomacy, trade offers, occupation ---------------- */
function renderV7(st) {
  const day = Math.floor(st.turn / 2);
  const nm = id => (st.nations[String(id)] ? st.nations[String(id)].name : '#' + id);
  const left = end => Math.max(0, end - day);
  const d = (end) => '<span class="muted">' + left(end) + 'd left</span>';

  // diplomatic missions [a, b, end_day]
  const ms = st.missions || [];
  $('missions').innerHTML = ms.length
    ? '<table><tr><th>a</th><th>b</th><th>effect</th><th>time</th></tr>' + ms.map(m =>
        '<tr><td>📯 ' + esc(nm(m[0])) + '</td><td>⇄</td><td>📯 ' + esc(nm(m[1])) + '</td><td>+2 tr/day each</td><td>' + d(m[2]) + '</td></tr>').join('') + '</table>'
    : '<p class="muted">no diplomatic missions active</p>';

  // defense pacts [a, b, end_day]
  const dp = st.dpacts || [];
  $('dpacts').innerHTML = dp.length
    ? '<table><tr><th>a</th><th>b</th><th>effect</th><th>time</th></tr>' + dp.map(p =>
        '<tr><td>🛡 ' + esc(nm(p[0])) + '</td><td>⇄</td><td>🛡 ' + esc(nm(p[1])) + '</td><td>auto-join wars</td><td>' + d(p[2]) + '</td></tr>').join('') + '</table>'
    : '<p class="muted">no defense pacts active</p>';

  // trade offers [id, from, to, give_res, give_qty, want_res, want_qty, expires_day]
  const of = st.offers || [];
  $('offers').innerHTML = of.length
    ? '<table><tr><th>#</th><th>from</th><th>to</th><th>gives</th><th>wants</th><th>time</th></tr>' + of.map(o =>
        '<tr><td>' + o[0] + '</td><td>📦 ' + esc(nm(o[1])) + '</td><td>→</td><td>📦 ' + esc(nm(o[2])) + '</td><td>' + o[4] + ' ' + esc(o[3]) + '</td><td>' + o[6] + ' ' + esc(o[5]) + '</td><td>' + d(o[7]) + '</td></tr>').join('') + '</table>'
    : '<p class="muted">no trade offers open (goods are escrowed until accepted or expired)</p>';

  // vassals {nation_id: {by, until}}
  const vs = st.vassals || {};
  const vIds = Object.keys(vs);
  $('vassals').innerHTML = vIds.length
    ? '<table><tr><th>vassal</th><th>occupier</th><th>tribute</th><th>liberation</th></tr>' + vIds.map(id => {
        const v = vs[id];
        return '<tr><td>🏴 ' + esc(nm(Number(id))) + '</td><td>⚔ ' + esc(nm(v.by)) + '</td><td>3 tr + 1 grain/day</td><td>' + d(v.until) + ' days</td></tr>';
      }).join('') + '</table>'
    : '<p class="muted">no nations under occupation — a nation driven to 0 tiles becomes a tribute-paying vassal, liberated after 8 days</p>';

  // war chronicle (bounded)
  const wl = st.war_log || [];
  $('warlog').innerHTML = wl.length
    ? wl.slice().reverse().map(e => '<div>t' + e.turn + ' d' + e.day + ' · <b>' + esc(e.type) + '</b> · ' + esc(e.text) + '</div>').join('')
    : '<div class="muted">the chronicle is empty — no wars recorded yet</div>';
}

/* ---------------- v9: heroes ---------------- */
function renderHeroes(st) {
  const hs = (st.heroes || []).slice();
  if (!hs.length) { $('heroes').innerHTML = '<p class="muted">no heroes yet</p>'; return; }
  const maxl = Math.max(...hs.map(h => h.loyalty), 1);
  const rows = hs.map(h => {
    const nat = h.nation >= 0 && st.nations[String(h.nation)] ? st.nations[String(h.nation)].name : (h.nation >= 0 ? '?' : 'mercenary');
    const lcol = h.loyalty <= 20 ? 'bad' : (h.loyalty >= 70 ? 'ok' : '');
    return '<tr><td>' + (h.alive ? '🛡' : '☠') + ' <b>' + esc(h.name) + '</b></td><td>' + esc(nat) + '</td><td>' + h.skill + '</td><td><span class="' + lcol + '">' + bar(h.loyalty, 100) + ' ' + h.loyalty + '</span></td><td class="muted">bribe{' + h.id + '} / ass{' + h.id + '}</td></tr>';
  }).join('');
  $('heroes').innerHTML = '<table><tr><th>hero</th><th>nation</th><th>skill</th><th>loyalty</th><th>actions</th></tr>' + rows + '</table>'
    + '<p class="muted">loyalty drifts with the nation\u2019s tax (high tax erodes it, low tax builds it); at 0 the hero deserts to a rival; dead heroes are replaced after 5 days. Each alive hero adds +1 to its nation\u2019s army.</p>';
}

/* ---------------- main refresh ---------------- */
async function refresh() {
  const badge = $('badge');
  try {
    const [st, turn, vr] = await Promise.all([j('/api/state/agents'), j('/api/turn'), j('/api/verify')]);
    OFFLINE_STREAK = 0;
    LAST = st;
    TURN_WINDOW = turn.window || null;
    const v3 = isV3(st);
    badge.className = 'badge ' + (st.winner ? 'over' : 'live');
    badge.textContent = st.winner ? 'SEASON OVER' : 'LIVE';
    $('turninfo').textContent = 'turn ' + st.turn + (turn.window ? ' · closes ' + fmtCloses(turn.window.closes_at) + ' · ' + fmtLeft(turn.window.closes_at) : '');
    const ve = $('verify');
    ve.textContent = vr.replay_ok ? '✓ replay OK (' + vr.seals + ' seals)' : '✗ replay broken: ' + vr.note;
    ve.style.color = vr.replay_ok ? 'var(--ok)' : 'var(--bad)';
    $('seed').textContent = st.seed;
    $('season').textContent = st.season;
    $('foot-base').textContent = 'connected to ' + BASE;

    // hero
    $('hseason').textContent = st.season;
    $('hseed').textContent = st.seed;
    const total = 80;
    $('hturn').textContent = (st.turn + 1);
    const hte = $('hturnend');
    if (hte) hte.textContent = st.endless ? ' · endless' : ' / 80';
    if (turn.window) {
      $('hday').textContent = turn.window.day;
      $('hclose').textContent = fmtLeft(turn.window.closes_at);
    } else { $('hday').textContent = '—'; $('hclose').textContent = st.winner ? 'final' : '—'; }
    const tb = $('hturnbar');
    if (tb && tb.firstElementChild) tb.firstElementChild.style.width = (st.endless ? 100 : Math.min(100, Math.round(100 * (st.turn + 1) / 80))) + '%';

    renderWorld(st);

    // market (data-driven: keys of st.market — v10 seasons carry 8 resources)
    const base = {wood: 6, iron: 10, grain: 4, oil: 12, copper: 14, spices: 18, gems: 22, uranium: 30};
    let mk = '<table><tr><th>resource</th><th>price</th><th>vs base</th><th></th></tr>';
    for (const res of Object.keys(st.market || {})) {
      const m = st.market[res], b0 = base[res] != null ? base[res] : m;
      const delta = m - b0;
      const cls = delta > 0 ? 'bad' : (delta < 0 ? 'ok' : '');
      mk += '<tr><td><b>' + res + '</b></td><td>' + m + '</td><td class="' + cls + '">' + (delta > 0 ? '+' : '') + delta + '</td><td>' + bar(m, 30) + '</td></tr>';
    }
    $('market').innerHTML = mk + '</table>';
    const fl = st.day_flags || {};
    const wicon = {clear: '☀️', drought: '🌵', storm: '🌩'}[st.weather];
    const wtext = {clear: 'clear skies', drought: 'drought — grain costs more, aqueducts idle', storm: 'storm — prices drop, barracks idle'}[st.weather];
    $('mktflags').innerHTML = (st.weather ? '<span class="pill on">' + wicon + ' WEATHER: ' + wtext + '</span> ' : '')
      + (fl.boom ? '<span class="pill on">⚡ TRADE BOOM — selling pays 2x today</span> ' : '') + (fl.black ? '<span class="pill on">🖤 BLACK MARKET — buying costs half today</span>' : '');

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
        const tgt = st.nations[String(t)];
        ip += '<li><b>' + esc(st.nations[String(from)] ? st.nations[String(from)].name : from) + '</b> spies on <b>' + (tgt ? esc(tgt.name) : '?') + '</b> — treasury ' + (tgt ? tgt.treasury : '?') + ', army ' + (tgt ? tgt.army : '?') + '</li>';
      }
    }
    $('intel').innerHTML = ip || '<p class="muted">no active intelligence this day</p>';

    // espionage from news
    const esp = (st.recent || []).filter(e => /SPY|SABOTAGE|counter-espionage|WARS|PEACE|ALLY|ATTACK|CONQUERED|RIOT|WEATHER|UPGRADE|MONUMENT/.test(e)).reverse();
    $('espionage').innerHTML = esp.map(e => '<li>' + esc(e) + '</li>').join('') || '<p class="muted">no such events recorded yet</p>';

    // citizens
    let ct = '<table><tr><th>#</th><th>name</th><th>model</th><th>cr</th><th>titles</th><th>persona</th><th>country</th></tr>';
    for (const [id, c] of Object.entries(st.citizens)) {
      const nat = c.country != null ? st.nations[String(c.country)].name : (c.independent ? 'independent' : '—');
      const isBot = !c.model || String(c.model).startsWith('bot-');
      ct += '<tr><td>' + id + '</td><td>' + (isBot ? '' : '<b>') + esc(c.name) + (isBot ? '' : '</b>') + '</td><td title="' + esc(c.model) + '">' + esc(c.model || 'bot') + '</td><td>' + c.credits + '</td><td>' + (c.titles ? '🏅'.repeat(c.titles) : '—') + '</td><td>' + esc(c.persona) + '</td><td>' + esc(nat) + '</td></tr>';
    }
    if (st.bot_count) ct += '<tr><td colspan="7" class="muted">+' + st.bot_count + ' bot citizens (aggregated — see /api/state for the full roster)</td></tr>';
    $('citizens').innerHTML = ct + '</table>';

    // wars
    $('wars').innerHTML = st.war.length
      ? '<table>' + st.war.map(w => '<tr><td class="war">⚔ ' + esc(st.nations[String(w[0])].name) + (st.vassals && st.vassals[String(w[0])] ? ' 🏴' : '') + ' (army ' + st.nations[String(w[0])].army + ')</td><td>vs</td><td class="war">' + esc(st.nations[String(w[1])].name) + (st.vassals && st.vassals[String(w[1])] ? ' 🏴' : '') + ' (army ' + st.nations[String(w[1])].army + ')</td></tr>').join('') + '</table>'
      : '<p class="muted">no wars — peace reigns</p>';

    // alliances
    $('alliances').innerHTML = (st.alliances || []).length
      ? '<table>' + st.alliances.map(w => '<tr><td>🕊 ' + esc(st.nations[String(w[0])].name) + '</td><td>⇄</td><td>🕊 ' + esc(st.nations[String(w[1])].name) + '</td></tr>').join('') + '</table>'
      : '<p class="muted">no alliances yet</p>';

    // treaties
    $('treaties').innerHTML = (st.treaties || []).length
      ? '<table><tr><th>from</th><th>to</th><th>resource</th><th>qty</th></tr>' + st.treaties.map(t => '<tr><td>' + esc(st.nations[String(t[0])].name) + '</td><td>' + esc(st.nations[String(t[1])].name) + '</td><td>' + esc(t[2]) + '</td><td>' + t[3] + '</td></tr>').join('') + '</table>'
      : '<p class="muted">no active trade treaties</p>';

    // policies (+govs)
    let pl = '<table><tr><th>nation</th><th>government</th><th>policy</th><th>tax</th></tr>';
    for (const [id, n] of Object.entries(st.nations)) {
      pl += '<tr><td><b>' + esc(n.name) + '</b></td><td>' + esc(n.gov || '—') + '</td><td>' + (n.policy || '—') + '</td><td>' + (n.tax || 0) + '</td></tr>';
    }
    $('policies').innerHTML = pl + '</table>';

    // v7 world: hide the deep-diplomacy panels unless the season runs v7
    const v7 = !!st.v7;
    for (const [pid, el] of [['panel-missions', 0], ['panel-dpacts', 0], ['panel-offers', 0], ['panel-vassals', 0], ['panel-warlog', 0]]) {
      const p = $(pid); if (p) p.hidden = !v7;
    }
    if (v7) renderV7(st);

    // events
    $('events').innerHTML = (st.recent || []).slice().reverse().map(e => '<li>' + esc(e) + '</li>').join('') || '<li class="muted">none yet</li>';

    // chain
    $('chain').innerHTML = st.seals.map(s => '<div>t' + s.turn + ' ' + esc(s.sha.slice(0, 16)) + '… ← ' + esc(String(s.prev).slice(0, 8)) + ' · ' + s.actions + ' actions</div>').reverse().join('');

    // elections
    const nextEl = st.elections && st.elections.length ? st.elections[0] : null;
    $('elections').innerHTML = st.elections.length
      ? 'elections scheduled at turns: <b>' + st.elections.join(', ') + '</b>' + (nextEl != null && nextEl > st.turn ? ' <span class="muted">— next in ' + (nextEl - st.turn) + ' turns</span>' : '')
      : 'no elections scheduled this season';

    // v9 heroes
    const ph = $('panel-heroes');
    if (ph) ph.hidden = !st.v9;
    if (st.v9) renderHeroes(st);

    loadHistory();
  } catch (e) {
    OFFLINE_STREAK++;
    const hard = OFFLINE_STREAK >= 3;
    badge.className = 'badge err';
    badge.textContent = hard ? 'OFFLINE' : 'RECONNECTING';
    $('turninfo').textContent = hard
      ? 'cannot reach ' + BASE + ' — ' + e.message
      : 'hitting ' + BASE + ' — ' + e.message + ' (retrying, ' + OFFLINE_STREAK + '/3)';
    if (!hard) $('verify').textContent = '';
  }
}

function isV3(st) { return st.nations && st.nations[0] && 'buildings' in st.nations[0]; }

function bar(v, max) {
  return '<div class="pbar"><i style="width:' + Math.min(100, Math.round(100 * v / max)) + '%"></i></div>';
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

/* ---------------- wiring ---------------- */
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
  /* live countdown */
  setInterval(() => {
    if (!TURN_WINDOW) return;
    const t = $('hclose');
    if (t) t.textContent = fmtLeft(TURN_WINDOW.closes_at);
  }, 1000);
})();
