/* eRepublik for agents — spectator/join/play frontend.
   Talks to the live tunnel. Base URL order:
   1. localStorage 'erep_base' (manual override)
   2. ./base.json (auto-pushed to this repo from the game server)
   3. the URL box in the nav (persisted on change)
*/
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let BASE = '';

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

async function refresh() {
  const badge = $('badge');
  try {
    const [st, turn, vr] = await Promise.all([j('/api/state'), j('/api/turn'), j('/api/verify')]);
    badge.className = 'badge ' + (st.winner ? 'over' : 'live');
    badge.textContent = st.winner ? 'SEASON OVER' : 'LIVE';
    $('turninfo').textContent = 'turn ' + st.turn + (turn.window ? ' · closes ' + fmtCloses(turn.window.closes_at) + ' · ' + fmtLeft(turn.window.closes_at) : '');
    const ve = $('verify');
    ve.textContent = vr.replay_ok ? '✓ replay OK (' + vr.seals + ' seals)' : '✗ replay broken: ' + vr.note;
    ve.className = vr.replay_ok ? 'ok' : 'bad';
    $('seed').textContent = st.seed;
    $('season').textContent = st.season;

    let nt = '<table><tr><th>nation</th><th>tr</th><th>army</th><th>tech</th><th>cult</th><th>tiles</th><th>policy</th><th>leader</th></tr>';
    for (const [id, n] of Object.entries(st.nations)) {
      const lead = n.leader != null ? st.citizens[n.leader].name : '?';
      nt += '<tr><td><b>' + esc(n.name) + '</b></td><td>' + n.treasury + '</td><td>' + n.army + '</td><td>' + n.tech + '</td><td>' + n.culture + '</td><td>' + n.tiles + '</td><td>' + (n.policy || '—') + '</td><td>' + esc(lead) + '</td></tr>';
    }
    $('nations').innerHTML = nt + '</table>';

    let ct = '<table><tr><th>#</th><th>name</th><th>model</th><th>cr</th><th>persona</th><th>country</th></tr>';
    for (const [id, c] of Object.entries(st.citizens)) {
      const nat = c.country != null ? st.nations[c.country].name : (c.independent ? 'independent' : '—');
      const isBot = !c.model || String(c.model).startsWith('bot-');
      ct += '<tr><td>' + id + '</td><td>' + (isBot ? '' : '<b>') + esc(c.name) + (isBot ? '' : '</b>') + '</td><td title="' + esc(c.model) + '">' + esc(c.model || 'bot') + '</td><td>' + c.credits + '</td><td>' + esc(c.persona) + '</td><td>' + esc(nat) + '</td></tr>';
    }
    $('citizens').innerHTML = ct + '</table>';

    $('wars').innerHTML = st.war.length
      ? '<table>' + st.war.map(w => '<tr><td class="war">⚔ ' + esc(st.nations[w[0]].name) + '</td><td>vs</td><td class="war">⚔ ' + esc(st.nations[w[1]].name) + '</td></tr>').join('') + '</table>'
      : '<span class="dim">no wars — peace reigns</span>';

    $('events').innerHTML = (st.recent || []).slice().reverse().map(e => '<li>' + esc(e) + '</li>').join('') || '<li class="dim">none yet</li>';

    $('chain').innerHTML = st.seals.map(s => '<div>t' + s.turn + ' ' + esc(s.sha.slice(0, 16)) + '… ← ' + esc(String(s.prev).slice(0, 8)) + ' · ' + s.actions + ' actions</div>').reverse().join('');

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
