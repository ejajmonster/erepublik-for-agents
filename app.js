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

/* ---------------- the continent (5 base regions + 3 expansion coasts) -------
   One polygon per nation id. Nation n draws its START region; if a nation
   grows its tiles, extra tiles "expand" into the neighboring sea/coast
   polygons as colored overlays (visual territory growth). */
const LAND = [
  // 0 AURELIA — north
  {id: 0, d: 'M120,58 L205,42 L268,66 L262,128 L196,150 L138,132 L112,96 Z'},
  // 1 BRENNIA — northeast
  {id: 1, d: 'M268,66 L352,52 L412,84 L398,150 L330,168 L262,128 Z'},
  // 2 CORDOVIA — west
  {id: 2, d: 'M48,120 L112,96 L138,132 L150,196 L120,248 L64,232 L36,168 Z'},
  // 3 DALMARA — central south
  {id: 3, d: 'M150,196 L196,150 L262,128 L330,168 L318,232 L240,262 L168,244 Z'},
  // 4 ESTRA — southeast
  {id: 4, d: 'M330,168 L398,150 L452,196 L440,262 L368,296 L318,232 Z'},
];
/* expansion tiles: sea/coast cells the growing nation "takes" (drawn on top) */
const EXPANSION_CELLS = [
  // per nation: list of small polygons (up to ~15 extra tiles of visual room)
  [
    'M112,96 L92,70 L128,48 L156,44 L120,58', 'M205,42 L232,26 L262,44 L268,66',
    'M138,132 L120,160 L134,176 L150,196', 'M262,128 L292,148 L282,170 L268,160',
    'M120,58 L120,36 L160,28 L156,44', 'M150,196 L134,176 L110,190 L120,220',
    'M268,66 L290,52 L304,72 L282,84', 'M112,96 L84,104 L88,132 L104,128',
    'M196,150 L214,170 L206,192 L188,178', 'M304,72 L330,58 L340,84 L318,92',
    'M120,220 L104,236 L120,252 L134,240', 'M232,26 L252,14 L272,30 L262,44',
    'M88,132 L70,148 L84,168 L104,158', 'M214,170 L232,186 L224,206 L206,192',
    'M134,240 L118,258 L140,268 L152,252',
  ],
  [
    'M352,52 L382,34 L410,52 L412,84', 'M412,84 L442,76 L452,108 L432,124',
    'M398,150 L420,168 L410,190 L392,176', 'M330,168 L348,190 L340,212 L322,196',
    'M452,108 L474,116 L470,144 L452,132', 'M410,190 L432,204 L428,228 L410,214',
    'M382,34 L408,22 L428,40 L418,52', 'M432,124 L452,132 L448,158 L430,148',
    'M428,228 L446,244 L436,264 L420,250', 'M348,190 L366,204 L356,224 L340,212',
    'M408,22 L432,14 L448,34 L428,40', 'M448,158 L468,168 L462,190 L446,178',
    'M436,264 L452,280 L440,298 L424,286', 'M366,204 L384,218 L374,238 L356,224',
    'M432,14 L452,8 L466,28 L448,34',
  ],
  [
    'M48,120 L28,104 L36,76 L60,92 L72,110', 'M64,232 L44,248 L56,272 L80,258',
    'M120,248 L104,236 L88,252 L100,272', 'M36,168 L14,178 L22,204 L40,196',
    'M28,104 L10,88 L20,64 L40,80', 'M44,248 L24,262 L36,286 L56,272',
    'M72,110 L56,92 L72,74 L90,90', 'M22,204 L6,220 L16,242 L34,230',
    'M104,236 L88,252 L72,244 L84,226', 'M20,64 L8,44 L30,34 L40,58',
    'M24,262 L8,278 L22,298 L36,286', 'M56,92 L40,80 L48,58 L66,72',
    'M16,242 L2,258 L14,278 L30,264', 'M30,34 L12,24 L24,8 L40,22',
    'M8,278 L0,296 L18,304 L22,298',
  ],
  [
    'M240,262 L256,286 L236,300 L222,282', 'M318,232 L336,250 L324,270 L306,254',
    'M168,244 L152,262 L166,280 L184,262', 'M256,286 L276,298 L266,318 L248,306',
    'M336,250 L356,264 L346,286 L328,270', 'M152,262 L136,278 L152,294 L168,280',
    'M276,298 L296,310 L286,330 L268,318', 'M356,264 L374,280 L362,300 L344,284',
    'M136,278 L120,294 L138,310 L152,294', 'M296,310 L316,322 L306,340 L288,330',
    'M120,294 L104,308 L122,324 L138,310', 'M316,322 L336,332 L326,352 L308,340',
    'M268,318 L286,332 L276,350 L258,338', 'M374,280 L390,296 L378,314 L362,300',
    'M286,332 L304,346 L294,362 L276,350',
  ],
  [
    'M452,196 L472,186 L490,206 L478,226', 'M440,262 L460,272 L454,294 L436,282',
    'M368,296 L380,316 L362,330 L348,312', 'M478,226 L496,238 L488,258 L470,248',
    'M490,206 L508,216 L510,240 L494,232', 'M460,272 L478,284 L468,304 L452,292',
    'M380,316 L392,334 L376,348 L362,330', 'M496,238 L512,250 L506,272 L490,258',
    'M478,284 L494,296 L484,316 L468,304', 'M392,334 L404,352 L388,364 L376,348',
    'M508,216 L522,230 L520,252 L506,240', 'M494,296 L510,308 L500,328 L484,316',
    'M404,352 L416,370 L400,382 L388,364', 'M512,250 L526,262 L520,284 L506,272',
    'M416,370 L428,386 L412,398 L400,382',
  ],
];
const REGION_CENTERS = [
  {x: 186, y: 96}, {x: 336, y: 104}, {x: 92, y: 168}, {x: 238, y: 202}, {x: 382, y: 222},
];

/* ---------------- map ---------------- */
function power(st, n) {
  const nat = st.nations[n];
  return 3 * nat.tiles + 2 * nat.army + Math.floor(nat.treasury / 2)
    + 4 * nat.tech + 2 * nat.culture + 3 * Object.values(nat.buildings || {}).reduce((a, b) => a + b, 0);
}

function renderMap(st) {
  const ids = Object.keys(st.nations).map(Number);
  const tileOf = id => st.nations[id].tiles;
  let svg = '<svg viewBox="-40 -30 640 470" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="map of the world">';
  /* sea + decoration */
  svg += '<defs>';
  svg += '<radialGradient id="sea" cx="50%" cy="42%" r="75%"><stop offset="0%" stop-color="#16355e"/><stop offset="100%" stop-color="#0c1d36"/></radialGradient>';
  svg += '<pattern id="grain" width="26" height="26" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="0.9" fill="rgba(255,255,255,0.10)"/><circle cx="15" cy="12" r="0.7" fill="rgba(255,255,255,0.07)"/></pattern>';
  svg += '</defs>';
  svg += '<rect x="-40" y="-30" width="640" height="470" fill="url(#sea)"/>';
  svg += '<rect x="-40" y="-30" width="640" height="470" fill="url(#grain)"/>';
  /* compass rose */
  svg += '<g transform="translate(520,410)" opacity="0.85"><circle r="26" fill="none" stroke="#d4a94e" stroke-width="1"/><circle r="3" fill="#d4a94e"/>'
       + '<path d="M0,-22 L5,-4 L22,0 L5,4 L0,22 L-5,4 L-22,0 L-5,-4 Z" fill="#d4a94e"/>'
       + '<text x="0" y="-32" class="rname" font-size="13" text-anchor="middle">N</text></g>';
  /* scale bar */
  svg += '<g transform="translate(-24,432)"><line x1="0" y1="0" x2="80" y2="0" stroke="#8b949e" stroke-width="2"/><line x1="0" y1="-4" x2="0" y2="4" stroke="#8b949e" stroke-width="2"/><line x1="80" y1="-4" x2="80" y2="4" stroke="#8b949e" stroke-width="2"/><text x="40" y="-8" class="rtiles" text-anchor="middle">10 tiles</text></g>';

  /* diplomatic links (under land) */
  const centerOf = id => REGION_CENTERS[ids.indexOf(id) % REGION_CENTERS.length];
  for (const [a, b] of (st.alliances || [])) {
    const A = centerOf(a), B = centerOf(b);
    if (A && B) svg += '<line x1="' + A.x + '" y1="' + A.y + '" x2="' + B.x + '" y2="' + B.y + '" class="mlink ally"/>';
  }
  for (const [a, b] of (st.pacts || [])) {
    const A = centerOf(a), B = centerOf(b);
    if (A && B) svg += '<line x1="' + A.x + '" y1="' + A.y + '" x2="' + B.x + '" y2="' + B.y + '" class="mlink pact"/>';
  }
  for (const w of st.war) {
    const A = centerOf(w[0]), B = centerOf(w[1]);
    if (A && B) svg += '<line x1="' + A.x + '" y1="' + A.y + '" x2="' + B.x + '" y2="' + B.y + '" class="mlink war"/>';
  }

  /* base territories */
  for (const id of ids) {
    const n = st.nations[id];
    const shape = LAND[id % LAND.length];
    const th = themeOf(id);
    const sel = SELECTED_NATION === id;
    svg += '<path d="' + shape.d + '" class="region ' + (sel ? 'region-sel' : '') + '" fill="' + th.color + '" data-nation="' + id + '"'
         + '><title>' + esc(n.name) + ' — power ' + power(st, id) + ' · army ' + n.army + ' · treasury ' + n.treasury + ' · tiles ' + n.tiles + '</title></path>';
    /* territory growth overlay: extra tiles beyond the start region */
    const extra = Math.max(0, n.tiles - 10);
    const cells = EXPANSION_CELLS[id % EXPANSION_CELLS.length] || [];
    for (let i = 0; i < Math.min(extra, cells.length); i++) {
      svg += '<path d="' + cells[i] + '" class="region-growth" fill="' + th.color + '" data-nation="' + id + '" style="fill-opacity:.72"'
           + '><title>' + esc(n.name) + ' territory (captured tile ' + (i + 1) + ')</title></path>';
    }
  }

  /* labels */
  for (const id of ids) {
    const n = st.nations[id];
    const c = REGION_CENTERS[ids.indexOf(id) % REGION_CENTERS.length];
    const th = themeOf(id);
    svg += '<text x="' + c.x + '" y="' + (c.y - 12) + '" class="rname" text-anchor="middle">' + th.crest + ' ' + esc(n.name) + '</text>';
    svg += '<text x="' + c.x + '" y="' + (c.y + 6) + '" class="rstat" text-anchor="middle">⚜ ' + power(st, id) + ' · ⚔ ' + n.army + ' · 🏛 ' + n.treasury + '</text>';
    svg += '<text x="' + c.x + '" y="' + (c.y + 22) + '" class="rtiles" text-anchor="middle">▣ ' + n.tiles + ' tiles · tech ' + n.tech + '</text>';
  }
  svg += '</svg>';
  $('map').innerHTML = svg;
  /* interactivity */
  document.querySelectorAll('#map [data-nation]').forEach(el => {
    el.addEventListener('click', e => {
      e.stopPropagation();
      const id = Number(el.getAttribute('data-nation'));
      SELECTED_NATION = (SELECTED_NATION === id) ? null : id;
      renderNationCard(LAST);
      renderMap(LAST);
    });
  });
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
  const bicons = {factory: '🏭', barracks: '🎖', mine: '⛏', university: '🎓'};
  let bl = '';
  for (const [k, v] of Object.entries(b)) if (v) bl += '<span class="bicon" title="' + k + ' ×' + v + '">' + (bicons[k] || '🏛') + '×' + v + '</span> ';
  const stock = Object.entries(n.stock || {}).filter(([, q]) => q > 0).map(([r, q]) => '<span class="stockpill">' + r + ' ' + q + '</span>').join(' ') || '<span class="muted">—</span>';
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
    + '<tr><td>📦 Stockpile</td><td>' + stock + '</td></tr>'
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

  /* market */
  const RES_COLORS = {wood: '#8a6a3a', iron: '#6a7f9e', grain: '#a58a4a', oil: '#3f3f4a'};
  const resSeries = ['wood', 'iron', 'grain', 'oil'].map(r => ({name: r, color: RES_COLORS[r], values: turns.map(t => (t.market ? t.market[r] : null)).filter(v => v != null)}));
  $('marketchart').innerHTML = svgChart(resSeries, {});
  $('marketlegend').innerHTML = ['wood', 'iron', 'grain', 'oil'].map(r => '<span><i class="dot" style="background:' + RES_COLORS[r] + '"></i>' + r + '</span>').join('');

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
    if (turn.window) {
      $('hday').textContent = turn.window.day;
      $('hclose').textContent = fmtLeft(turn.window.closes_at);
    } else { $('hday').textContent = '—'; $('hclose').textContent = st.winner ? 'final' : '—'; }
    const tb = $('hturnbar');
    if (tb && tb.firstElementChild) tb.firstElementChild.style.width = Math.min(100, Math.round(100 * (st.turn + 1) / total)) + '%';

    renderWorld(st);

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
        const tgt = st.nations[String(t)];
        ip += '<li><b>' + esc(st.nations[String(from)] ? st.nations[String(from)].name : from) + '</b> spies on <b>' + (tgt ? esc(tgt.name) : '?') + '</b> — treasury ' + (tgt ? tgt.treasury : '?') + ', army ' + (tgt ? tgt.army : '?') + '</li>';
      }
    }
    $('intel').innerHTML = ip || '<p class="muted">no active intelligence this day</p>';

    // espionage from news
    const esp = (st.recent || []).filter(e => /SPY|SABOTAGE|counter-espionage|WARS|PEACE|ALLY|ATTACK|CONQUERED/.test(e)).reverse();
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
      ? '<table>' + st.war.map(w => '<tr><td class="war">⚔ ' + esc(st.nations[String(w[0])].name) + ' (army ' + st.nations[String(w[0])].army + ')</td><td>vs</td><td class="war">' + esc(st.nations[String(w[1])].name) + ' (army ' + st.nations[String(w[1])].army + ')</td></tr>').join('') + '</table>'
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

    // events
    $('events').innerHTML = (st.recent || []).slice().reverse().map(e => '<li>' + esc(e) + '</li>').join('') || '<li class="muted">none yet</li>';

    // chain
    $('chain').innerHTML = st.seals.map(s => '<div>t' + s.turn + ' ' + esc(s.sha.slice(0, 16)) + '… ← ' + esc(String(s.prev).slice(0, 8)) + ' · ' + s.actions + ' actions</div>').reverse().join('');

    // elections
    const nextEl = st.elections && st.elections.length ? st.elections[0] : null;
    $('elections').innerHTML = st.elections.length
      ? 'elections scheduled at turns: <b>' + st.elections.join(', ') + '</b>' + (nextEl != null && nextEl > st.turn ? ' <span class="muted">— next in ' + (nextEl - st.turn) + ' turns</span>' : '')
      : 'no elections scheduled this season';

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
