// K9X Arena — web UI (vanilla ES module; screens from the K9X Arena Design canvas)
const app = document.getElementById('app');
const S = { me: null, status: null, timers: [], es: null, lobby: null, match: null, stream: null };

// ── helpers ───────────────────────────────────────────────────────────────────
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const starText = (n) => '★'.repeat(n || 0) + '☆'.repeat(5 - (n || 0));
const secs = (ms) => (ms == null ? '—' : `${(ms / 1000).toFixed(1)} s`);
const dur = (s) => { if (!s) return '—'; const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60); return h ? `${h} h ${m} m` : `${m} m`; };
const initials = (tag) => { const b = (tag || '?').split(':')[0].replace(/[^a-z0-9]/gi, ''); const d = (b.match(/\d/) || [''])[0]; return (b[0] || '?').toUpperCase() + (d || (b[1] || '').toUpperCase()); };
const short = (tag) => (tag || '').split(':')[0];
const TYPE_LABEL = { code: 'Code', extraction: 'Extraction', reasoning: 'Reasoning', summarization: 'Summary', chat: 'Chat', adversarial: 'Adversarial' };
const SWORDS = '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#2dd4bf" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14.5 17.5 3 6V3h3l11.5 11.5"/><path d="m13 19 6-6"/><path d="m16 16 4 4"/><path d="m19 21 2-2"/><path d="M9.5 17.5 21 6V3h-3L6.5 14.5"/><path d="m11 19-6-6"/><path d="m8 16-4 4"/><path d="m5 21-2-2"/></svg>';
const SHIELD = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>';

async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: 'same-origin', headers: opts.body && !(opts.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}, ...opts });
  if (res.status === 401 && !path.startsWith('/api/login')) { S.me = null; location.hash = '#/login'; throw new Error('sign in required'); }
  const text = await res.text();
  let data = null; try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) throw new Error((data && data.detail) ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : `HTTP ${res.status}`);
  return data;
}

function clearTimers() {
  S.timers.forEach((t) => clearInterval(t)); S.timers = [];
  if (S.es) { S.es.close(); S.es = null; }
}
const every = (ms, fn) => { S.timers.push(setInterval(fn, ms)); };

// ── header ────────────────────────────────────────────────────────────────────
async function refreshStatus() {
  try { S.status = await api('/api/status'); } catch { /* keep last */ }
  const el = document.getElementById('pills'); if (el) el.innerHTML = pills();
}
function pills() {
  const s = S.status;
  if (!s) return '<span class="pill">Checking hosts…</span>';
  const g = s.guardian;
  return `
    <span class="pill ${s.ollama.reachable ? '' : 'off'}" title="${esc(s.ollama.url)}"><span class="dot"></span>Ollama ${s.ollama.reachable ? '' : 'offline'}</span>
    <span class="pill guard ${g.live ? '' : 'off'}" title="${esc(g.detail || g.model)}">${SHIELD}Guardian ${g.live ? 'Live' : 'Offline'}</span>
    <span class="pill gpu" title="Model loaded on the GPU now"> Model: ${esc((s.gpu && s.gpu.length) ? s.gpu.join(', ') : 'idle')}</span>`;
}
function header(active) {
  const running = S.status && S.status.running_match;
  const liveHref = running ? `#/match/${running}` : (S.lastMatch ? `#/match/${S.lastMatch}` : '#/history');
  const resultsHref = S.lastDone ? `#/results/${S.lastDone}` : '#/history';
  const nav = [['lobby', '#/lobby', 'Lobby'], ['match', liveHref, 'Live match'], ['results', resultsHref, 'Results'], ['history', '#/history', 'History'], ['reviews', '#/reviews', 'Reviews'], ['architecture', '#/architecture', 'Architecture']];
  return `<header class="top">
    <a class="brand" href="#/lobby">${SWORDS}<span>K9X ARENA</span></a>
    <nav class="nav" aria-label="Main">${nav.map(([k, h, l]) => `<a href="${h}" ${k === active ? 'aria-current="page"' : ''}>${l}${k === 'reviews' && S.pendingReviews ? `<span class="count">${S.pendingReviews}</span>` : ''}</a>`).join('')}</nav>
    <div class="grow"></div>
    <div class="pills" id="pills">${pills()}</div>
    <button class="userbtn" data-act="logout" title="Sign out">${esc(S.me?.username)} · ${esc(S.me?.role)} · Sign out</button>
  </header>`;
}

// ── router ────────────────────────────────────────────────────────────────────
async function route() {
  clearTimers();
  const parts = (location.hash || '#/lobby').slice(2).split('/');
  if (parts[0] !== 'login' && !S.me) {
    try { S.me = await api('/api/me'); } catch { return; }
  }
  if (parts[0] !== 'login') {
    if (!S.status) await refreshStatus();
    every(15000, refreshStatus);
    api('/api/reviews').then((r) => { S.pendingReviews = r.length; }).catch(() => {});
  }
  try {
    switch (parts[0]) {
      case 'login': return renderLogin();
      case 'match': return renderMatch(+parts[1], ['octagon', 'orbit'].includes(parts[2]) ? 'octagon' : 'lanes');
      case 'results': return renderResults(+parts[1]);
      case 'task': return renderTask(+parts[1], decodeURIComponent(parts[2] || ''));
      case 'history': return renderHistory();
      case 'reviews': return renderReviews();
      case 'architecture': return renderArchitecture();
      default: return renderLobby();
    }
  } catch (e) {
    app.innerHTML = header('') + `<main class="page"><div class="banner">${esc(e.message)}</div></main>`;
  }
}
window.addEventListener('hashchange', route);

// ── login ─────────────────────────────────────────────────────────────────────
async function renderLogin() {
  let hints = [];
  try { hints = await api('/api/login-hints'); } catch { /* none */ }
  app.innerHTML = `<main class="login"><form id="loginform" aria-labelledby="t">
    <div style="display:flex;justify-content:center">${SWORDS.replace('26', '44').replace('26', '44')}</div>
    <h1 id="t">K9X ARENA</h1>
    <p class="muted" style="margin:-8px 0 4px;text-align:center">LLMs head to head, graded, starred, and the router tested.</p>
    <label class="field">Username<input name="username" autocomplete="username" required></label>
    <label class="field">Password<input name="password" type="password" autocomplete="current-password" required></label>
    <div id="loginerr" class="muted" role="alert"></div>
    <button class="btn primary" type="submit">Sign in</button>
    ${hints.length ? `<div class="hint">${hints.map((h) => `<span><b>${esc(h.label)}:</b> <span class="mono">${esc(h.username)} / ${esc(h.password)}</span></span>`).join('')}</div>` : ''}
  </form></main>`;
  document.getElementById('loginform').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    try {
      S.me = await api('/api/login', { method: 'POST', body: JSON.stringify({ username: f.get('username'), password: f.get('password') }) });
      location.hash = '#/lobby';
    } catch (e) { document.getElementById('loginerr').textContent = e.message; }
  });
}

// ── lobby ─────────────────────────────────────────────────────────────────────
async function renderLobby() {
  app.innerHTML = header('lobby') + '<main class="page"><div class="empty">Loading contenders…</div></main>';
  let models, suites, matches;
  try { [models, suites, matches] = await Promise.all([api(`/api/models${S.showAllModels ? '?all=true' : ''}`), api('/api/suites'), api('/api/matches')]); }
  catch (e) { app.innerHTML = header('lobby') + `<main class="page"><div class="banner">${esc(e.message)}</div></main>`; return; }
  rememberMatches(matches);
  const usable = models.models.filter((m) => !m.is_guardian && !m.is_embedding);
  const sizeNote = { hidden: models.hidden_small, min: models.min_params_b, all: models.show_all };
  const tags = usable.map((m) => m.tag);
  const prev = S.lobby || {};
  const judgeDefault = tags.includes(models.default_judge) ? models.default_judge : (tags[tags.length - 1] || '');
  S.lobby = {
    models: usable, suites, matches, guardian: models.guardian_model,
    selected: prev.selected || new Set(models.default_contestants.filter((t) => tags.includes(t) && t !== judgeDefault)),
    judge: prev.judge || judgeDefault,
    suite: prev.suite || (suites[0] && suites[0].key) || '',
    runs: Math.min(3, prev.runs || models.runs_per_task), routerMode: prev.routerMode ?? models.router_mode,
    upload: prev.upload || null, error: null, busy: false, sizeNote,
    secondJudge: models.second_judge || '', routerUnderTest: models.router_under_test || {},
  };
  drawLobby();
}
function drawLobby() {
  const L = S.lobby;
  const suite = L.suites.find((s) => s.key === L.suite);
  const selected = [...L.selected].filter((t) => t !== L.judge);
  const estH = (selected.length * (suite ? suite.tasks : 0) * L.runs * 25) / 3600;
  const cards = L.models.map((m) => {
    const isJudge = m.tag === L.judge; const on = L.selected.has(m.tag) && !isJudge;
    return `<article class="card ${on ? 'on' : ''}">
      <div class="row"><div class="orb">${esc(initials(m.tag))}</div>
        <div style="flex-grow:1;min-width:0"><div class="name">${esc(m.tag)}</div><div class="muted" style="font-size:13px">${esc([m.family, m.parameters, m.quantization, m.size_gb + ' GB'].filter(Boolean).join(' · '))}</div></div>
        ${isJudge ? '<span class="badge amber">Judge</span>' : ''}</div>
      <div class="stats"><div><b>${m.wins} of ${m.matches}</b>Matches won</div><div><b class="gold">${m.avg_stars ?? '—'}</b>Average stars</div><div><b>${esc(TYPE_LABEL[m.best_at] || '—')}</b>Best at</div></div>
      <label class="check"><input type="checkbox" data-act="toggle" data-tag="${esc(m.tag)}" ${on ? 'checked' : ''} ${isJudge ? 'disabled' : ''}>
        <span>${isJudge ? 'Judging the next match (cannot also compete)' : (on ? 'Entered in next match' : 'Sitting out')}</span></label>
    </article>`;
  }).join('');
  const recent = L.matches.slice(0, 5).map((m) => `<tr class="click" data-href="#/${m.status === 'completed' ? 'results' : 'match'}/${m.id}">
      <td class="mono muted">#${m.id}</td><td>${esc(m.suite_name)}</td><td class="mono" style="font-size:13px">${esc(m.contenders.map(short).join(' · '))}</td>
      <td>${statusBadge(m.status)}</td><td class="mono" style="color:var(--amber);font-size:13px">${esc(m.winner || '—')}</td>
      <td class="muted">${dur(m.finished_at && m.started_at ? m.finished_at - m.started_at : 0)}</td></tr>`).join('');
  const up = L.upload;
  const step = (label, st) => `<span class="s ${st}"><span class="n">${st === 'done' ? '✓' : st === 'fail' ? '✕' : st === 'active' ? '…' : ''}</span>${label}</span>`;
  const stages = !up ? ['', '', ''] : up.stage === 'checking' ? ['active', '', ''] : up.stage === 'precheck' ? ['fail', '', ''] : up.stage === 'guardian' ? ['done', 'fail', ''] : ['done', 'done', 'done'];
  const guardianOff = S.status && !S.status.guardian.live;
  app.innerHTML = header('lobby') + `<main class="page"><div class="lobby">
    <section aria-labelledby="roster" style="display:flex;flex-direction:column;gap:18px;min-width:0">
      <div class="row" style="align-items:baseline;gap:14px"><h1 id="roster">Contenders</h1><span class="muted" style="font-size:14px">Pulled on this Ollama host · pick who enters the next match</span>
        <span class="grow"></span>${L.sizeNote.hidden ? `<button class="btn small" data-act="showall">Show ${L.sizeNote.hidden} smaller model${L.sizeNote.hidden > 1 ? 's' : ''} (&lt; ${L.sizeNote.min}B)</button>` : L.sizeNote.all ? `<button class="btn small" data-act="showtop">Only models ≥ ${L.sizeNote.min}B</button>` : ''}</div>
      ${L.models.length ? `<div class="roster">${cards}</div>` : '<div class="panel empty">No models pulled on the Ollama host yet. Pull some with <span class="mono">ollama pull &lt;model&gt;</span>.</div>'}
      <section class="panel" aria-labelledby="recent"><h2 id="recent">Recent matches</h2>
        ${recent ? `<table class="grid"><thead><tr><th>Match</th><th>Suite</th><th>Contenders</th><th>Status</th><th>Winner</th><th>Duration</th></tr></thead><tbody>${recent}</tbody></table>` : '<div class="empty">No matches yet. Start one on the right.</div>'}
      </section>
    </section>
    <aside class="panel teal newmatch" aria-labelledby="nm">
      <h2 id="nm">New match</h2>
      ${guardianOff ? '<div class="banner">Granite Guardian is offline. Matches and uploads are blocked until it is reachable (it never runs unscreened).</div>' : ''}
      ${L.error ? `<div class="banner" role="alert">${esc(L.error)}</div>` : ''}
      <div class="field">Contenders<div class="chips">${selected.length ? selected.map((t) => `<span class="chip">${esc(t)}</span>`).join('') : '<span class="muted">Pick at least one on the left</span>'}</div></div>
      <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px">
        <label class="field">Judge (not a contender)<select data-act="judge">${L.models.map((m) => `<option ${m.tag === L.judge ? 'selected' : ''}>${esc(m.tag)}</option>`).join('')}</select></label>
        <label class="field">Rounds (runs per task)<select data-act="runs">${[1, 2, 3].map((n) => `<option value="${n}" ${n === L.runs ? 'selected' : ''}>${n}${n === 3 ? ' · full bout' : n === 1 ? ' · quick' : ''}</option>`).join('')}</select></label>
      </div>
      ${judgeNote(L, selected)}
      <label class="field">Task suite<select data-act="suite">${L.suites.map((s) => `<option value="${esc(s.key)}" ${s.key === L.suite ? 'selected' : ''}>${esc(s.name)} · ${s.tasks} tasks · ${s.source}</option>`).join('')}</select></label>
      <div class="upload">
        <div class="row"><div style="flex-grow:1"><div style="font-size:14px;font-weight:600">Or upload your own</div><div class="muted" style="font-size:12px">Task suite (.yaml) or source document (.md, .txt) · scanned by Granite Guardian</div></div>
          <label class="btn small" style="cursor:pointer">Upload<input type="file" accept=".yaml,.yml,.md,.txt" data-act="upload" style="display:none"></label></div>
        <div class="steps" aria-label="Upload screening">${step('Pre-check', stages[0])}<span class="ln"></span>${step('Granite Guardian', stages[1])}<span class="ln"></span>${step('Accepted', stages[2])}</div>
        ${up ? `<div class="mono" style="font-size:12px;color:${up.accepted ? 'var(--teal-2)' : up.stage === 'checking' ? 'var(--muted)' : 'var(--red-2)'}" role="status">${esc(up.file_name)} · ${esc(up.accepted ? `accepted · ${up.tasks} tasks` : up.stage === 'checking' ? 'scanning…' : up.reason)}</div>` : ''}
      </div>
      <label class="check"><input type="checkbox" data-act="router" ${L.routerMode ? 'checked' : ''}><span>Test the Intelligent Model Router (no extra model calls)</span></label>
      ${routerNote(L, selected)}
      <div class="row muted" style="justify-content:space-between;font-size:13px;border-top:1px solid var(--line);padding-top:12px">
        <span>${selected.length} contenders × ${suite ? suite.tasks : 0} tasks × ${L.runs} runs · about ${estH.toFixed(1)} h</span><span>Guardian screens every prompt</span></div>
      <button class="btn primary" style="height:52px;font-size:17px;justify-content:center" data-act="start" ${!selected.length || !L.suite || L.busy || guardianOff ? 'disabled' : ''}>${L.busy ? 'Starting…' : 'Start match'}</button>
    </aside></div></main>`;
}
function routerNote(L, selected) {
  if (!L.routerMode) return '';
  const rut = Object.entries(L.routerUnderTest || {});
  const inMatch = rut.filter(([, m]) => selected.includes(m));
  const rules = !rut.length ? '' : inMatch.length
    ? ` Your config (${rut.map(([a, m]) => `${esc(a)} → <span class="mono">${esc(m)}</span>`).join(' · ')}) is scored alongside it.`
    : ` <span style="color:var(--amber)">Your config's models (${rut.map(([, m]) => esc(m)).join(', ')}) aren't entered, so "your rules" can't be scored; the learned router still is.</span>`;
  return `<div class="muted" style="font-size:12.5px;line-height:1.5;margin-top:-8px">After scoring, each task is hidden in turn; K9ModelRouter learns from the others' scores and picks a model for it.${rules}</div>`;
}

function judgeNote(L, selected) {
  const fam = (tag) => (L.models.find((m) => m.tag === tag) || {}).family;
  const jf = fam(L.judge);
  const clash = jf ? selected.filter((t) => fam(t) === jf) : [];
  const second = L.secondJudge && !selected.includes(L.secondJudge) ? `second opinion from <span class="mono">${esc(L.secondJudge)}</span>` : 'second pass resamples the same judge';
  return `<div class="muted" style="font-size:12.5px;line-height:1.5">Judging is anonymized and calibrated; ${second}.${clash.length ? `<br><span style="color:var(--amber)">Fairness: the judge is the same model family (${esc(jf)}) as ${clash.map(esc).join(', ')}. A judge from another family is fairer.</span>` : ''}</div>`;
}

function statusBadge(s) {
  const map = { completed: 'teal', running: 'amber', queued: 'grey', paused: 'grey', failed: 'red' };
  return `<span class="badge ${map[s] || 'grey'}">${esc(s)}</span>`;
}
function rememberMatches(list) {
  const running = list.find((m) => m.status === 'running');
  S.lastMatch = running ? running.id : (list[0] && list[0].id);
  const done = list.find((m) => m.status === 'completed'); S.lastDone = done && done.id;
}

// ── delegated events ──────────────────────────────────────────────────────────
app.addEventListener('click', async (ev) => {
  const row = ev.target.closest('tr[data-href]');
  if (row && !ev.target.closest('button,a')) { location.hash = row.dataset.href; return; }
  const el = ev.target.closest('[data-act]'); if (!el) return;
  const act = el.dataset.act;
  if (act === 'logout') { await api('/api/logout', { method: 'POST' }).catch(() => {}); S.me = null; location.hash = '#/login'; }
  if (act === 'start') startMatch();
  if (act === 'rescore') {
    el.disabled = true; el.textContent = 'Running…';
    try { await api(`/api/matches/${el.dataset.id}/rescore`, { method: 'POST' }); route(); } catch (e) { alert(e.message); el.disabled = false; el.textContent = el.dataset.label || 'Run the router test'; }
  }
  if (act === 'grid') { S.gridMode = el.dataset.mode; route(); return; }
  if (act === 'celebrate' && S.lastReport) celebrate(S.lastReport.leaderboard, S.lastReport.judge, S.lastReport.verdict);
  if (act === 'close-celebration') closeCelebration();
  if (act === 'showall' || act === 'showtop') { S.showAllModels = act === 'showall'; renderLobby(); }
  if (['pause', 'cancel', 'resume'].includes(act)) {
    try { await api(`/api/matches/${el.dataset.id}/${act}`, { method: 'POST' }); } catch (e) { alert(e.message); }
    if (act === 'resume') route();
  }
  if (act === 'delete' && confirm(`Delete match #${el.dataset.id} and all its results?`)) {
    try { await api(`/api/matches/${el.dataset.id}`, { method: 'DELETE' }); route(); } catch (e) { alert(e.message); }
  }
  if (act === 'copy') {
    const txt = document.getElementById('yaml').textContent;
    try { await navigator.clipboard.writeText(txt); el.textContent = 'Copied'; setTimeout(() => { el.textContent = 'Copy'; }, 1500); } catch { el.textContent = 'Select and copy'; }
  }
  if (act === 'decide') {
    const input = document.getElementById(`rv-${el.dataset.id}`);
    try { await api(`/api/reviews/${el.dataset.id}`, { method: 'POST', body: JSON.stringify({ score: +input.value }) }); route(); } catch (e) { alert(e.message); }
  }
});
app.addEventListener('change', async (ev) => {
  const el = ev.target.closest('[data-act]'); if (!el || !S.lobby) return;
  const L = S.lobby, act = el.dataset.act;
  if (act === 'toggle') { el.checked ? L.selected.add(el.dataset.tag) : L.selected.delete(el.dataset.tag); }
  if (act === 'judge') { L.judge = el.value; L.selected.delete(el.value); }
  if (act === 'runs') L.runs = +el.value;
  if (act === 'suite') L.suite = el.value;
  if (act === 'router') L.routerMode = el.checked;
  if (act === 'upload' && el.files[0]) {
    const file = el.files[0];
    L.upload = { stage: 'checking', file_name: file.name }; drawLobby();
    const fd = new FormData(); fd.append('file', file);
    try {
      const r = await api('/api/uploads', { method: 'POST', body: fd });
      L.upload = r;
      if (r.accepted) { L.suites = await api('/api/suites'); L.suite = r.suite_key; }
    } catch (e) { L.upload = { stage: 'precheck', file_name: file.name, reason: e.message }; }
  }
  drawLobby();
});
async function startMatch() {
  const L = S.lobby; L.busy = true; L.error = null; drawLobby();
  try {
    const r = await api('/api/matches', { method: 'POST', body: JSON.stringify({
      contenders: [...L.selected].filter((t) => t !== L.judge), judge: L.judge, suite: L.suite, runs_per_task: L.runs, router_mode: L.routerMode }) });
    L.busy = false; location.hash = `#/match/${r.id}`;
  } catch (e) { L.busy = false; L.error = e.message; drawLobby(); }
}

// ── live match (lanes + orbit) ────────────────────────────────────────────────
const PHASES = [['screening', 'Screening'], ['answering', 'Answering'], ['grading', 'Grading'], ['judging', 'Judging'], ['scoring', 'Scoring'], ['router', 'Router test']];
async function renderMatch(id, view) {
  if (!id) { location.hash = '#/history'; return; }
  S.stream = { events: [] };
  const load = async () => { S.match = await api(`/api/matches/${id}`); };
  try { await load(); } catch (e) { app.innerHTML = header('match') + `<main class="page"><div class="banner">${esc(e.message)}</div></main>`; return; }
  S.lastMatch = id;
  const draw = () => (view === 'octagon' ? drawOctagon() : drawLanes());
  draw();
  S.es = new EventSource(`/api/matches/${id}/stream`);
  S.es.onmessage = (msg) => {
    const d = JSON.parse(msg.data);
    S.stream.status = d.status; S.stream.phase = d.phase; S.stream.error = d.error; S.stream.progress = d.progress; S.stream.current = d.current;
    if (d.events && d.events.length) S.stream.events = [...S.stream.events, ...d.events].slice(-60);
    const justFinished = d.status === 'completed' && S.match.match.status !== 'completed';
    Object.assign(S.match.match, { status: d.status, phase: d.phase, error: d.error });
    if (justFinished) { S.celebrate = id; setTimeout(() => { location.hash = `#/results/${id}`; }, 1200); }
    S.match.progress = d.progress || S.match.progress; S.match.current = d.current;
    draw();
  };
  every(4000, async () => { if (['running', 'queued'].includes(S.match.match.status)) { try { await load(); draw(); } catch { /* ignore */ } } });
  api(`/api/matches/${id}/events`).then((ev) => { S.stream.events = ev.slice(-60); draw(); }).catch(() => {});
}
function matchControls(m) {
  if (m.status === 'running' || m.status === 'queued') return `<button class="btn" data-act="pause" data-id="${m.id}">Pause after this task</button><button class="btn danger" data-act="cancel" data-id="${m.id}">Cancel</button>`;
  if (m.status === 'paused' || m.status === 'failed') return `<button class="btn primary" data-act="resume" data-id="${m.id}">Resume</button>`;
  if (m.status === 'completed') return `<a class="btn primary" href="#/results/${m.id}">View results</a>`;
  return '';
}
function phaseState(m, key) {
  const order = PHASES.map((p) => p[0]); const cur = m.status === 'completed' ? 'done' : (m.phase || '');
  const iCur = order.indexOf(cur === 'safety' ? 'grading' : cur); const i = order.indexOf(key);
  if (cur === 'done' || (iCur > i)) return 'done';
  return iCur === i ? 'now' : '';
}
function progressOf(key) {
  const p = (S.match.progress || {})[key === 'grading' ? 'safety' : key];
  return p && p.total ? Math.round((100 * p.done) / p.total) : null;
}
function drawLanes() {
  const D = S.match, m = D.match; const live = ['running', 'queued'].includes(m.status);
  const ans = D.lanes.reduce((a, l) => a + l.answers, 0), tot = D.lanes.reduce((a, l) => a + l.total, 0);
  const lanes = D.lanes.map((l) => {
    const active = l.state === 'answering';
    const pill = { answering: 'teal', done: 'grey', waiting: 'grey', queued: 'grey' }[l.state];
    const label = { answering: 'Answering', done: 'All answered', waiting: 'Waiting for GPU', queued: 'Queued' }[l.state];
    const cards = l.recent.length ? l.recent.map((c) => `<a class="tcard" href="#/task/${m.id}/${encodeURIComponent(c.task_id)}">
        <div class="row" style="gap:8px"><span class="mono muted" style="font-size:12px">${esc(c.task_id)}</span><span class="tag">${esc(c.type)}</span><span class="grow"></span><span class="muted" style="font-size:12px">run ${c.run_no} · ${secs(c.latency_ms)}</span></div>
        <div style="font-size:14px">${esc(c.title)}</div>
        <div class="preview">${esc(c.error ? c.error : c.preview || '(empty)')}</div>
        <div class="row" style="gap:8px">${c.error ? '<span class="badge red">Error</span>' : (c.refused && c.type !== 'adversarial') ? '<span class="badge red">Over-refusal</span>' : c.pending ? '<span class="badge amber">Pending review</span>' : c.score != null ? `<span class="badge teal">${c.score.toFixed(0)}</span>` : `<span class="badge grey">${c.type === 'adversarial' ? 'Safety-graded after answers' : 'Judged after answers'}</span>`}</div>
      </a>`).join('') : '<div class="muted" style="font-size:13px;padding:8px">No answers yet.</div>';
    return `<section class="lane ${active ? 'active' : ''}" aria-label="${esc(l.model)}">
      <div class="head"><div class="row"><span class="mono" style="font-size:16px;flex-grow:1;overflow-wrap:anywhere">${esc(l.model)}</span><span class="badge ${pill}">${label}</span></div>
        <div class="row" style="align-items:baseline;gap:14px"><span class="score">${l.score != null ? l.score.toFixed(1) : '—'}</span><span class="muted" style="font-size:12px">average<br>quality</span><span class="grow"></span><span class="muted" style="font-size:13px">${l.answers} / ${l.total} answers</span></div>
        <div class="bar" style="height:5px"><div style="width:${l.total ? (100 * l.answers) / l.total : 0}%"></div></div></div>
      <div class="body">${cards}</div></section>`;
  }).join('');
  const phases = PHASES.map(([k, label]) => { const st = phaseState(m, k); const p = progressOf(k);
    return `<div class="phase ${st}"><span>${st === 'done' ? '✓ ' : ''}${label}</span><div class="bar"><div style="width:${st === 'done' ? 100 : (p ?? 0)}%"></div></div></div>`; }).join('');
  const events = (S.stream.events || []).slice(-14).reverse().map((e) => `<div class="ev"><span class="k k-${esc(e.kind)}">${esc(e.kind)}</span><span>${esc(e.text)}</span></div>`).join('');
  app.innerHTML = header('match') + `<main class="page">
    ${m.status === 'failed' ? `<div class="banner" role="alert">Match stopped: ${esc(m.error)}</div>` : ''}
    ${m.status === 'paused' ? `<div class="banner info">Paused (${esc(m.phase)}). Resume picks up where it stopped; finished answers are kept.</div>` : ''}
    <div class="matchhead">
      <div style="flex-grow:1"><div class="row">${live ? '<span class="live"><span class="dot"></span>LIVE</span>' : statusBadge(m.status)}<h1>Match #${m.id} · ${esc(m.suite_name)}</h1></div>
        <div class="muted" style="font-size:14px;margin-top:4px">${m.runs_per_task} runs per task · judge <span class="mono">${esc(m.judge)}</span> · router mode ${m.router_mode ? 'on' : 'off'} · ${ans} of ${tot} answers</div></div>
      <div class="viewswitch" role="group" aria-label="View"><a href="#/match/${m.id}" aria-current="page">Lanes</a><a href="#/match/${m.id}/octagon">Octagon</a></div>
      ${matchControls(m)}
    </div>
    <div class="phases" aria-label="Match phases">${phases}</div>
    <div class="lanes" style="grid-template-columns:repeat(${Math.min(D.lanes.length, 4)},minmax(0,1fr))">${lanes}</div>
    <section class="ticker" aria-label="Trace events">${events || '<span class="muted">Waiting for events…</span>'}</section>
  </main>`;
}
function drawOctagon() {
  const D = S.match, m = D.match; const cur = D.current || {};
  const W = 1400, H = 860, cx = 700, cy = 440, R = 300;
  const oct = (r) => Array.from({ length: 8 }, (_, k) => { const a = (Math.PI / 8) + k * Math.PI / 4; return `${(cx + r * Math.cos(a)).toFixed(1)},${(cy + r * Math.sin(a)).toFixed(1)}`; }).join(' ');
  let seed = 11; const rand = () => { seed = (seed * 9301 + 49297) % 233280; return seed / 233280; };
  // crowd: stands in elliptical rings around the cage
  const crowd = []; for (let ring = 0; ring < 7; ring++) {
    const rx = R + 90 + ring * 34, ry = R * 0.86 + 70 + ring * 26, n = 70 + ring * 14;
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2 + rand() * 0.03; const x = cx + rx * Math.cos(a), y = cy + ry * Math.sin(a);
      if (x < 8 || x > W - 8 || y < 8 || y > H - 8) continue;
      const flash = rand() < 0.035;
      crowd.push(`<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${(3.2 + rand() * 1.6).toFixed(1)}" fill="${flash ? '#fff7d6' : ['#1c2a3a', '#223244', '#18222f', '#26384a'][i % 4]}" ${flash ? `class="flash" style="animation-delay:-${(rand() * 5).toFixed(2)}s"` : ''}/>`);
    }
  }
  // fighters at fixed corners
  const n = m.contenders.length;
  const fighters = m.contenders.map((tag, i) => {
    const a = -Math.PI / 2 + (i / n) * Math.PI * 2 + (n === 2 ? Math.PI / 2 : 0);
    const fx = cx + R * 0.66 * Math.cos(a), fy = cy + R * 0.66 * Math.sin(a);
    const lane = D.lanes.find((l) => l.model === tag) || {};
    const active = cur.model === tag;
    const state = active ? 'answering' : ({ done: 'all answered', waiting: 'waiting', queued: 'queued' }[lane.state] || '');
    return { tag, fx, fy, active, lane, state };
  });
  const judgeActive = cur.model === 'judge';
  const phase = m.status === 'completed' ? 'completed' : (m.phase || '');
  const guardianActive = phase === 'screening' || cur.model === 'guardian';
  const routing = cur.model === 'router' || phase === 'router';
  const scoringNow = phase === 'scoring';
  const matSub = routing ? 'router test · held-out tasks' : scoringNow ? 'scoring…' : 'Intelligent Model Router';
  const cageLabel = cur.model === 'guardian' ? `GRANITE GUARDIAN · SAFETY PASS · ${cur.task_id} (${short(cur.for_model || '')})`
    : phase === 'screening' ? 'GRANITE GUARDIAN · SCREENING' : 'GRANITE GUARDIAN · CAGE';
  const target = fighters.find((f) => f.active) || (judgeActive ? { fx: cx, fy: cy + R + 70 } : null);
  // beams start at the mat's edge, not its center, so they never cross the K9X text
  let beams = '';
  if (target) {
    const dx = target.fx - cx, dy = target.fy - cy, len = Math.hypot(dx, dy) || 1;
    const ux = dx / len, uy = dy / len, sx = cx + ux * 96, sy = cy + uy * 96;
    const ox = -uy * 6, oy = ux * 6;  // offset the return stream sideways
    const ex = target.fx - ux * 36, ey = target.fy - uy * 36;
    beams = `<line x1="${sx}" y1="${sy}" x2="${ex}" y2="${ey}" class="beam-out"/>`
      + `<line x1="${ex + ox}" y1="${ey + oy}" x2="${sx + ox}" y2="${sy + oy}" class="beam-back"/>`;
  }
  const fighterSvg = fighters.map((f) => `<g class="fighter ${f.active ? 'active' : ''}">
      ${f.active ? `<circle cx="${f.fx}" cy="${f.fy}" r="70" class="spot"/>` : ''}
      <circle cx="${f.fx}" cy="${f.fy}" r="${f.active ? 34 : 28}" class="corner"/>
      <text x="${f.fx}" y="${f.fy + 7}" class="ini" text-anchor="middle">${esc(initials(f.tag))}</text>
      <text x="${f.fx}" y="${f.fy + (f.active ? 58 : 50)}" class="fname" text-anchor="middle">${esc(f.tag)}</text>
      <text x="${f.fx}" y="${f.fy + (f.active ? 76 : 68)}" class="fstate" text-anchor="middle">${esc(f.state)} · ${f.lane.answers || 0}/${f.lane.total || 0}</text></g>`).join('');
  const board = [...D.lanes].sort((a, b) => (b.score ?? -1) - (a.score ?? -1)).map((l) => `<div class="scorerow"><span class="mono grow" style="font-size:13px">${esc(l.model)}</span><span class="muted" style="font-size:12px">${l.answers}/${l.total}</span><span class="display" style="font-size:22px">${l.score != null ? l.score.toFixed(1) : '—'}</span></div>`).join('');
  const live = ['running', 'queued'].includes(m.status);
  const ans = D.lanes.reduce((a, l) => a + l.answers, 0), tot = D.lanes.reduce((a, l) => a + l.total, 0);
  const round = cur.run_no ? `Round ${cur.run_no} of ${m.runs_per_task}` : (m.status === 'completed' ? 'Final' : '');
  app.innerHTML = header('match') + `<main class="octagon-view">
    <svg class="octagon" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="The K9X Octagon: contenders in their corners, the Intelligent Model Router at center, the judges cageside, the crowd around.">
      <defs>
        <radialGradient id="floor" cx="50%" cy="50%" r="60%"><stop offset="0" stop-color="#16303a"/><stop offset=".7" stop-color="#0b1622"/><stop offset="1" stop-color="#070b14"/></radialGradient>
        <radialGradient id="light" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="#ffffff" stop-opacity=".16"/><stop offset="1" stop-color="#ffffff" stop-opacity="0"/></radialGradient>
        <pattern id="mesh" width="14" height="14" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><path d="M0 0H14M0 0V14" stroke="#5eead4" stroke-opacity=".16" stroke-width="1"/></pattern>
      </defs>
      <rect width="${W}" height="${H}" fill="#04060d"/>
      <g aria-hidden="true">${crowd.join('')}</g>
      <polygon points="${oct(R + 26)}" fill="url(#mesh)" stroke="${cur.model === 'guardian' ? '#ffd166' : '#2dd4bf'}" stroke-opacity=".55" stroke-width="3" class="cage ${guardianActive ? 'active' : ''}"/>
      <polygon points="${oct(R)}" fill="url(#floor)" stroke="#1f4d45" stroke-width="2"/>
      <ellipse cx="${cx}" cy="${cy}" rx="${R}" ry="${R * 0.9}" fill="url(#light)"/>
      <text x="${cx}" y="${cy - R - 40}" class="guardlabel ${cur.model === 'guardian' ? 'amber' : ''}" text-anchor="middle">${esc(cageLabel)}</text>
      <circle cx="${cx}" cy="${cy}" r="92" fill="#0b2226" stroke="#2dd4bf" stroke-opacity=".5" stroke-width="2" class="mat ${routing || scoringNow ? 'busy' : ''}"/>
      <text x="${cx}" y="${cy - 4}" class="matlogo" text-anchor="middle">K9X</text>
      <text x="${cx}" y="${cy + 22}" class="matsub" text-anchor="middle">${esc(matSub)}</text>
      ${beams}
      ${fighterSvg}
      <g class="judges ${judgeActive ? 'active' : ''}"><rect x="${cx - 110}" y="${cy + R + 48}" width="220" height="44" rx="10"/>
        <text x="${cx}" y="${cy + R + 76}" text-anchor="middle">JUDGES · ${esc(m.judge)}</text></g>
    </svg>
    <aside class="hud left" aria-label="Match status">
      <div class="row">${live ? '<span class="live"><span class="dot"></span>LIVE</span>' : statusBadge(m.status)}${round ? `<span class="muted" style="font-size:13px">${round}</span>` : ''}</div>
      <h1>THE K9X OCTAGON<br><span style="font-size:22px;color:var(--text-2)">Match #${m.id} · ${esc(m.suite_name)}</span></h1>
      <div class="bar" style="height:6px"><div style="width:${tot ? (100 * ans) / tot : 0}%;box-shadow:0 0 12px var(--teal)"></div></div>
      <span class="muted" style="font-size:13px">${ans} of ${tot} answers</span>
      <div class="phasestrip" aria-label="Match phases">${PHASES.map(([k, label]) => { const st = phaseState(m, k); return `<span class="ps ${st}">${st === 'done' ? '✓ ' : ''}${label}</span>`; }).join('')}</div>
      <div class="box"><span style="font-size:12px;color:var(--teal-2);letter-spacing:1px">NOW</span>
        ${cur.model ? `<span class="mono" style="font-size:15px">${esc(cur.model === 'judge' ? m.judge + ' (judging)' : cur.model === 'router' ? 'K9ModelRouter (router test)' : cur.model === 'guardian' ? 'Granite Guardian (safety pass on ' + (cur.for_model || '') + ')' : cur.model)}</span><span style="font-size:13px;color:var(--text-2)">${esc(cur.task_id)} · ${esc(cur.title)}</span>` : `<span class="muted" style="font-size:13px">${m.status === 'completed' ? 'Fight over' : phase === 'grading' ? 'Grading answers against right answers' : phase === 'scoring' ? 'Scoring the fight' : phase === 'router' ? 'Testing the router on held-out tasks' : 'Between rounds'}</span>`}</div>
      <div class="row"><div class="viewswitch" role="group" aria-label="View"><a href="#/match/${m.id}">Lanes</a><a href="#/match/${m.id}/octagon" aria-current="page">Octagon</a></div>${matchControls(m)}</div>
    </aside>
    <aside class="hud right" aria-label="Scores"><span class="muted" style="font-size:12px;letter-spacing:1px">SCORECARD · AVERAGE QUALITY</span>${board}</aside>
  </main>`;
}

// ── results ───────────────────────────────────────────────────────────────────
async function renderResults(id) {
  if (!id) { location.hash = '#/history'; return; }
  const D = await api(`/api/matches/${id}`); const m = D.match; const R = D.report;
  if (m.status === 'completed') S.lastDone = id;
  S.lastReport = R;
  if (!R) {
    app.innerHTML = header('results') + `<main class="page"><div class="banner info">Match #${id} has no results yet (${esc(m.status)}). <a href="#/match/${id}">Watch it live</a>.</div></main>`; return;
  }
  const types = D.task_types.filter((t) => D.stars.some((s) => s.task_type === t));
  const models = R.leaderboard.map((b) => b.model);
  // "Best" is answer quality first (within the tie margin), then the overall score.
  const margin = (R.verdict && R.verdict.margin) || 2; const F = R.fairness || {};
  const best = {}; D.stars.forEach((s) => { const b = best[s.task_type];
    if (!b || s.quality > b.quality + margin || (Math.abs(s.quality - b.quality) <= margin && s.score > b.score)) best[s.task_type] = s; });
  const isTop = (s) => { const b = best[s.task_type]; return b && Math.abs(s.quality - b.quality) <= margin && s.score === b.score; };
  const byQuality = S.gridMode === 'quality';
  const topQ = {}; D.stars.forEach((s) => { topQ[s.task_type] = Math.max(topQ[s.task_type] ?? -1, Math.round(s.quality)); });
  const qStars = (q) => (q >= 90 ? 5 : q >= 75 ? 4 : q >= 60 ? 3 : q >= 40 ? 2 : 1);
  const cell = (mdl, t) => { const s = D.stars.find((x) => x.model === mdl && x.task_type === t); if (!s) return '<td class="muted">—</td>';
    const isBest = byQuality ? Math.round(s.quality) === topQ[t] : isTop(s); const main = byQuality ? s.quality : s.score; const st = byQuality ? qStars(s.quality) : s.stars;
    const slow = s.latency != null && s.latency < 60;
    const sub = byQuality ? `overall ${s.score.toFixed(0)}` : `answers ${s.quality.toFixed(0)}${slow ? ' · <span style="color:var(--amber)">slow</span>' : ''}`;
    return `<td><div class="cell ${isBest ? 'best' : ''}"><span class="stars" aria-label="${st} of 5 stars">${starText(st)}</span><span class="row" style="gap:6px"><span class="n">${main.toFixed(0)}</span>${isBest ? '<span class="bestbadge">Best</span>' : ''}</span><span class="muted" style="font-size:11.5px">${sub}</span></div></td>`; };
  const gridToggle = `<div class="seg" role="group" aria-label="Grid shows"><button class="btn small ${byQuality ? '' : 'primary'}" data-act="grid" data-mode="overall">Overall</button><button class="btn small ${byQuality ? 'primary' : ''}" data-act="grid" data-mode="quality">Answer quality</button></div>`;
  const grid = `<table class="grid"><thead><tr><th>Model</th>${types.map((t) => `<th>${TYPE_LABEL[t]}</th>`).join('')}</tr></thead><tbody>${models.map((mdl) => `<tr><td class="mono">${esc(mdl)}</td>${types.map((t) => cell(mdl, t)).join('')}</tr>`).join('')}</tbody></table>`;
  const board = R.leaderboard.map((b) => `<tr><td class="rank r${b.rank}">${b.rank}</td><td class="mono">${esc(b.model)}</td><td class="display" style="font-size:20px">${b.score.toFixed(1)}</td><td class="display" style="font-size:18px;color:var(--text-2)">${b.quality != null ? b.quality.toFixed(1) : '—'}</td><td class="stars">${starText(b.stars)}</td><td>${secs(b.p50_ms)} / ${secs(b.p95_ms)}</td><td>${b.over_refusals} of ${b.answers}</td><td>± ${b.spread}</td></tr>`).join('');
  const audit = D.audit.map((a) => { const ok = a.verdict === 'match'; const na = a.verdict === 'not_in_match';
    return `<tr><td>${TYPE_LABEL[a.task_type] || esc(a.task_type)}</td><td class="mono" style="font-size:12px">${esc(short(a.router_model))} (${esc(a.router_alias)}) → best ${esc(short(a.best_model))}</td>
      <td>${na ? '<span class="badge grey" title="The router chose a model that is not competing in this match, so its answer quality is unknown.">Router\'s model not in match</span>' : ok ? '<span class="badge teal">Match</span>' : '<span class="badge amber">Mismatch</span>'}</td>
      <td class="display" style="font-size:18px;text-align:right;color:${ok || na ? 'var(--muted)' : 'var(--amber)'}">${a.regret == null ? '—' : ok ? '0' : '−' + a.regret}</td></tr>`; }).join('');
  const tasks = D.tasks.map((t) => `<tr class="click" data-href="#/task/${id}/${encodeURIComponent(t.id)}"><td class="mono muted">${esc(t.id)}</td><td><span class="tag">${esc(t.type)}</span></td><td>${esc(t.title)}${t.screen && t.screen.excluded ? ' <span class="badge red">Excluded by screening</span>' : ''}</td>${models.map((mdl) => { const v = (D.task_scores[t.id] || {})[mdl]; return `<td class="display" style="font-size:18px">${v == null ? '—' : v.toFixed(0)}</td>`; }).join('')}</tr>`).join('');
  const w = R.winner || {}; const J = R.judge || {}; const RT = R.router_test || null;
  const seenKey = `k9x-arena-celebrated-${id}`;
  let seen = false; try { seen = localStorage.getItem(seenKey) === '1'; } catch { /* storage blocked */ }
  if (m.status === 'completed' && R.leaderboard && R.leaderboard.length && (!seen || S.celebrate === id)) {
    S.celebrate = null; try { localStorage.setItem(seenKey, '1'); } catch { /* storage blocked */ }
    setTimeout(() => celebrate(R.leaderboard, R.judge, R.verdict), 50);
  }
  app.innerHTML = header('results') + `<main class="page">
    <div class="row" style="align-items:flex-end"><div class="grow"><h1 class="display" style="margin:0;font-size:34px">Match #${id} · ${esc(m.suite_name)}</h1>
      <div class="muted" style="font-size:14px">${m.contenders.length} contenders · ${D.tasks.length} tasks · ${m.runs_per_task} runs · judge <span class="mono">${esc(m.judge)}</span> · ${dur(m.finished_at && m.started_at ? m.finished_at - m.started_at : 0)}</div></div>
      ${R.leaderboard && R.leaderboard.length ? '<button class="btn" data-act="celebrate">Celebrate again</button>' : ''}${m.status === 'completed' ? `<button class="btn" data-act="rescore" data-id="${id}" data-label="Rerun report" title="Recompute stars, the router test and the config from the stored grades. No model is called.">Rerun report</button>` : ''}<a class="btn" href="#/match/${id}">Replay view</a><a class="btn primary" href="#/lobby">Rematch</a></div>
    ${J.checked && !J.reliable ? `<div class="banner info" style="margin-top:14px">The judge gave deliberately poor answers an average of ${J.planted_avg}, so its summary and chat grades don't separate the models reliably. Rank on code, extraction, reasoning and adversarial, or use a stricter judge (ARENA_JUDGE_MODEL / ARENA_JUDGE_MODEL_2).</div>` : ''}
    ${F.offloaded && F.offloaded.length ? `<div class="banner info" style="margin-top:14px">${F.offloaded.map((o) => `<span class="mono">${esc(o.model)}</span> ran ${o.cpu_pct}% on the CPU during its turn`).join('; ')}: other models were holding GPU memory. Timings aren't comparable, so latency is left out of this match's scores.</div>` : ''}
    ${J.shared_family && J.shared_family.length ? `<div class="banner info" style="margin-top:14px">Fairness note: the judge shares a model family (${esc(J.shared_family.join(', '))}) with a contender, which can favour that contender's style.</div>` : ''}
    ${J.pending ? `<div class="banner info" style="margin-top:14px">${J.pending} judged answers await review, so these results are provisional. <a href="#/reviews">Review them</a>.</div>` : ''}
    <section class="kpis" aria-label="Summary">
      ${verdictTile(R)}
${routerKpis(RT, R.router)}
      <div class="kpi"><div class="muted" style="font-size:13px">Judge disagreement</div><div class="v"><span class="big">${J.disagreements ?? 0} of ${J.judged ?? 0}</span><span class="muted">sent to review</span></div>
        ${J.checked ? `<div style="font-size:12px;margin-top:2px;color:${J.reliable ? 'var(--teal-2)' : 'var(--amber)'}">Calibration: planted poor answers scored ${J.planted_avg} ${J.reliable ? '· judge discriminates' : '· judge too lenient'}</div>` : ''}</div>
    </section>
    <div class="results">
      <div style="display:flex;flex-direction:column;gap:16px;min-width:0">
        <section class="panel"><div class="row"><h2 class="grow" style="margin:0">Stars by task type</h2>${gridToggle}</div><div style="margin-top:10px">${grid}</div><p class="muted" style="font-size:12px;margin:10px 0 0">${byQuality ? 'Answer quality only: is the answer right and good?' : `Overall = answer quality 60%, consistency 15%, ${F.latency_scored === false ? 'latency (left out this match), ' : `speed 15% (answers under ${((F.latency_floor_ms || 1000) / 1000).toFixed(0)} s get full marks), `}refusals 10%. The small line shows answer quality.`} Code, extraction and reasoning graded against right answers; summary and chat by two anonymized judge passes; adversarial by refusal behaviour, leaks and Granite Guardian's verdict.</p></section>
        <section class="panel"><h2>Leaderboard</h2><table class="grid"><thead><tr><th>#</th><th>Model</th><th>Score</th><th title="Answer quality only">Quality</th><th>Stars</th><th>Latency p50 / p95</th><th>Over-refusals</th><th>Run-to-run spread</th></tr></thead><tbody>${board}</tbody></table></section>
        <section class="panel"><h2>Tasks</h2><table class="grid"><thead><tr><th>Task</th><th>Type</th><th>Title</th>${models.map((mdl) => `<th class="mono" style="text-transform:none">${esc(short(mdl))}</th>`).join('')}</tr></thead><tbody>${tasks}</tbody></table></section>
      </div>
      <div style="display:flex;flex-direction:column;gap:16px;min-width:0">
        ${RT ? routerPanel(RT, m) : `<section class="panel"><div class="row"><h2 class="grow" style="margin:0">Router audit</h2>${m.status === 'completed' ? `<button class="btn small primary" data-act="rescore" data-id="${m.id}" title="Reruns the report from the stored grades. No model is called.">Run the router test</button>` : ''}</div><p class="muted" style="font-size:12.5px;margin:8px 0">This match ran before the router test, so it shows the old audit. The router test needs no GPU: it reuses this match's scores.</p>
          ${audit ? `<table class="grid"><tbody>${audit}</tbody></table>` : `<div class="empty">${esc((m.settings && m.settings.router_note) || 'Router mode was off for this match.')}</div>`}</section>`}
        <section class="panel teal"><div class="row"><h2 class="grow" style="margin:0">Recommended router config</h2><button class="btn small" data-act="copy">Copy</button><a class="btn small primary" href="/api/matches/${id}/config.yaml">Download</a></div>
          <pre class="yaml" id="yaml" style="margin-top:10px">${esc(R.recommended_yaml || '')}</pre>
          <ul class="muted" style="font-size:12.5px;margin:10px 0 0;padding-left:18px">${(R.recommended_notes || []).map((n) => `<li>${esc(n)}</li>`).join('')}</ul></section>
      </div>
    </div></main>`;
}

function routerKpis(RT, legacy) {
  if (!RT && legacy) {  // matches from before the router test
    return `<div class="kpi"><div class="muted" style="font-size:13px">Router picked the best model</div><div class="v"><span class="big">${legacy.types ? `${legacy.matches} of ${legacy.types}` : '—'}</span><span class="muted">task types</span></div></div>
      <div class="kpi"><div class="muted" style="font-size:13px">Router regret</div><div class="v"><span class="big">${legacy.avg_regret ?? '—'}</span><span class="muted">points lost on average</span></div></div>`;
  }
  if (!RT || !RT.available) return `<div class="kpi"><div class="muted" style="font-size:13px">Router test</div><div class="v"><span class="big">—</span><span class="muted">${esc((RT && RT.reason) || 'not run')}</span></div></div><div class="kpi"><div class="muted" style="font-size:13px">Router vs best single model</div><div class="v"><span class="big">—</span></div></div>`;
  const st = RT.strategies; const d = RT.vs_single; const sign = d > 0 ? '+' : d < 0 ? '−' : '±';
  return `<div class="kpi"><div class="muted" style="font-size:13px">Router picked the best model</div><div class="v"><span class="big">${st.learned.best_picks} of ${st.learned.tasks}</span><span class="muted">held-out tasks</span></div></div>
    <div class="kpi"><div class="muted" style="font-size:13px">Router vs best single model</div><div class="v"><span class="big" style="color:${d > 0 ? 'var(--teal-2)' : d < 0 ? 'var(--amber)' : 'inherit'}">${sign}${Math.abs(d).toFixed(1)}</span><span class="muted">points · ${esc(short(RT.single_model))}</span></div></div>`;
}

function routerPanel(RT, m) {
  const note = m.settings && m.settings.router_note;
  if (!RT.available) return `<section class="panel"><h2>Router test</h2><div class="empty">${esc(RT.reason || 'Not run.')}</div></section>`;
  const st = RT.strategies; const top = st.oracle.avg || 1;
  const line = (label, key, sub) => { const v = st[key];
    return `<tr><td>${label}${sub ? `<div class="muted" style="font-size:11.5px">${sub}</div>` : ''}</td>${v ? `<td style="width:38%"><div class="bar"><span style="width:${Math.max(2, 100 * v.avg / top).toFixed(1)}%;background:${key === 'learned' ? 'var(--teal-2)' : key === 'oracle' ? 'var(--amber)' : 'var(--muted)'}"></span></div></td>
      <td class="display" style="font-size:18px;text-align:right">${v.avg.toFixed(1)}</td><td class="muted" style="font-size:12px;text-align:right">${v.best_picks}/${v.tasks}</td>` : '<td colspan="3" class="muted" style="font-size:12px">not scored: config models not competing</td>'}</tr>`; };
  const rows = RT.rows.map((r) => { const hit = r.best - (r.learned_q ?? -1) <= 0.5;
    return `<tr><td class="mono muted" style="font-size:12px">${esc(r.task_id)}</td><td class="mono" style="font-size:12px">${esc(short(r.learned_model))}${r.strategy === 'learned' ? ' <span class="badge teal" title="' + esc(r.rationale) + '">learned</span>' : ' <span class="badge grey" title="' + esc(r.rationale) + '">rules</span>'}</td>
      <td class="display" style="font-size:16px;text-align:right;color:${hit ? 'var(--teal-2)' : 'var(--amber)'}">${r.learned_q == null ? '—' : r.learned_q.toFixed(0)}</td><td class="muted" style="font-size:12px">best ${r.best.toFixed(0)} · ${esc(r.best_models.map(short).join(', '))}</td></tr>`; }).join('');
  const captured = RT.headroom_captured;
  return `<section class="panel"><h2>Router test</h2>
    <p class="muted" style="font-size:12.5px;margin:0 0 8px">Each task was hidden in turn. K9ModelRouter learned from the other ${RT.tasks - 1} tasks' scores, then picked a model for the hidden one; the pick is scored with that model's real result. No model was run again. Scores here are answer quality only; the stars also weigh speed and consistency.</p>
    <table class="grid"><thead><tr><th>Strategy</th><th></th><th style="text-align:right">Avg</th><th style="text-align:right" title="Tasks where it picked a top-scoring model">Best</th></tr></thead><tbody>
      ${line('Best possible', 'oracle', 'top contender on every task')}
      ${line('K9ModelRouter, learned', 'learned', `${RT.learned_decisions} of ${RT.tasks} picks overruled the rules`)}
      ${line('Best single model', 'single', esc(short(RT.single_model)) + ' for everything')}
      ${line('Your rules', 'rules', 'your router config, by task type')}
      ${line('Random pick', 'random', 'the average contender')}</tbody></table>
    ${captured != null ? `<p class="muted" style="font-size:12.5px;margin:8px 0 0">The learned router captured <b style="color:var(--text)">${captured}%</b> of the gap between the best single model and the best possible pick.</p>` : `<p class="muted" style="font-size:12.5px;margin:8px 0 0">One model was best at nearly everything, so there was little for routing to gain.</p>`}
    ${RT.without_evidence === RT.tasks ? `<p class="muted" style="font-size:12.5px;margin:6px 0 0;color:var(--amber)">No hidden task had similar graded tasks to learn from (this suite has about one task per type), so the rules decided every pick. Run a larger suite, such as Claims Ops Starter, to test learned routing.</p>` : RT.without_evidence ? `<p class="muted" style="font-size:12px;margin:6px 0 0">${RT.without_evidence} of ${RT.tasks} hidden tasks had no similar graded tasks, so the rules picked those.</p>` : ''}
    ${note ? `<p class="muted" style="font-size:12px;margin:6px 0 0;color:var(--amber)">${esc(note)}</p>` : ''}
    <details style="margin-top:10px"><summary class="muted" style="cursor:pointer;font-size:13px">Every pick</summary><table class="grid" style="margin-top:6px"><tbody>${rows}</tbody></table></details></section>`;
}

function verdictTile(R) {
  const v = R.verdict || { kind: 'clear' }; const w = R.winner || (R.leaderboard || [])[0] || {};
  if (v.kind === 'draw') {
    const lb = R.leaderboard || []; const pair = lb.slice(0, 2).map((b) => `${esc(short(b.model))} ${b.score.toFixed(1)}`).join(' vs ');
    return `<div class="kpi gold"><div class="muted" style="font-size:13px">Result</div><div class="v"><span class="big" style="color:var(--amber)">Draw</span><span class="muted">within ${v.margin ?? 2} points</span></div><div class="muted" style="font-size:12px;margin-top:2px">${pair}</div></div>`;
  }
  const sub = v.kind === 'speed' ? '<div class="muted" style="font-size:12px;margin-top:2px">Quality tied · decided on speed &amp; consistency</div>' : '';
  return `<div class="kpi gold"><div class="muted" style="font-size:13px">Winner</div><div class="v"><span class="mono" style="font-size:20px;color:var(--amber)">${esc(w.model || '—')}</span><span class="big">${w.score != null ? w.score.toFixed(1) : ''}</span></div>${sub}</div>`;
}

// ── task drill-down ───────────────────────────────────────────────────────────
async function renderTask(id, taskId) {
  const D = await api(`/api/matches/${id}/tasks/${encodeURIComponent(taskId)}`);
  const t = D.task; const isAdmin = S.me && S.me.role === 'admin';
  const byModel = {}; D.runs.filter((r) => r.mode === 'forced').forEach((r) => { (byModel[r.model] = byModel[r.model] || []).push(r); });
  const grades = (r) => Object.values(r.grades).map((g) => `<div style="font-size:13px"><span class="badge ${g.score >= 70 ? 'teal' : g.score >= 40 ? 'amber' : 'red'}">${esc(g.grader)} · ${g.score.toFixed(0)}${g.grade ? ' · ' + esc(g.grade) : ''}</span>
      ${g.detail && g.detail.rationale ? `<span class="muted"> ${esc(g.detail.rationale)}</span>` : g.detail ? `<span class="muted mono" style="font-size:12px"> ${esc(JSON.stringify(g.detail).slice(0, 220))}</span>` : ''}</div>`).join('');
  const review = (r) => r.review ? `<div class="row" style="gap:8px;font-size:13px"><span class="badge ${r.review.status === 'pending' ? 'amber' : 'teal'}">${r.review.status === 'pending' ? 'Pending review' : 'Reviewed: ' + r.review.decided_score}</span><span class="muted">${esc(r.review.reason)}</span>
      ${r.review.status === 'pending' && isAdmin ? `<label class="sr-only" for="rv-${r.review.id}">Score 0 to 100</label><input id="rv-${r.review.id}" type="number" min="0" max="100" value="${Math.round(Object.values(r.grades).filter((g) => g.grader.startsWith('judge')).reduce((a, g, _, arr) => a + g.score / arr.length, 0))}" style="width:80px;height:36px;border-radius:8px;background:var(--bg-2);border:1px solid var(--line);color:var(--text);padding:0 8px"><button class="btn small primary" data-act="decide" data-id="${r.review.id}">Decide</button>` : ''}</div>` : '';
  const outs = Object.entries(byModel).map(([mdl, rs]) => `<section class="panel out"><h2 class="mono">${esc(mdl)}</h2>${rs.map((r) => `<div style="display:flex;flex-direction:column;gap:8px;margin-bottom:14px">
      <div class="row muted" style="font-size:12px">run ${r.run_no} · ${secs(r.latency_ms)}${r.refused ? ' · <span class="badge red">Refused</span>' : ''}</div>
      <pre>${esc(r.error ? 'ERROR: ' + r.error : r.output || '(empty)')}</pre>${grades(r)}${review(r)}</div>`).join('')}</section>`).join('');
  const router = D.runs.filter((r) => r.mode === 'router').map((r) => `<section class="panel out"><h2>Router mode · <span class="mono">${esc(r.alias)} → ${esc(r.model)}</span></h2><pre>${esc(r.error ? 'ERROR: ' + r.error : r.output)}</pre></section>`).join('');
  app.innerHTML = header('results') + `<main class="page" style="display:flex;flex-direction:column;gap:16px">
    <div class="row"><a class="btn small" href="#/results/${id}">← Results</a><h1 class="display grow" style="margin:0;font-size:30px">${esc(t.id)} · ${esc(t.title)}</h1><span class="tag">${esc(t.type)}</span></div>
    ${t.screen ? `<div class="muted" style="font-size:13px">Screening: k9x_Shield ${esc(t.screen.shield)} · Granite Guardian ${esc(t.screen.guardian)}${t.screen.excluded ? ' · <b style="color:var(--red-2)">excluded</b>' : ''}</div>` : ''}
    <section class="panel"><h2>Prompt</h2><pre class="prompt">${esc(t.prompt)}</pre></section>
    <section class="panel"><h2>How it is graded</h2><pre class="prompt">${esc(JSON.stringify(D.grading, null, 2))}</pre></section>
    <div class="outputs">${outs || '<div class="empty">No answers yet.</div>'}</div>${router}
  </main>`;
}

// ── history & reviews ─────────────────────────────────────────────────────────
async function renderHistory() {
  const list = await api('/api/matches'); rememberMatches(list);
  const isAdmin = S.me && S.me.role === 'admin';
  const rows = list.map((m) => `<tr><td class="mono muted">#${m.id}</td><td>${esc(m.suite_name)}</td><td class="mono" style="font-size:13px">${esc(m.contenders.join(', '))}</td><td>${statusBadge(m.status)}</td>
    <td class="mono" style="color:var(--amber)">${esc(m.winner || '—')}${m.winner_score != null ? ` · ${m.winner_score.toFixed(1)}` : ''}</td><td class="muted">${new Date(m.created_at * 1000).toLocaleString()}</td>
    <td><div class="row" style="gap:6px"><a class="btn small" href="#/match/${m.id}">Match</a>${m.status === 'completed' ? `<a class="btn small primary" href="#/results/${m.id}">Results</a>` : ''}${isAdmin ? `<button class="btn small danger" data-act="delete" data-id="${m.id}">Delete</button>` : ''}</div></td></tr>`).join('');
  app.innerHTML = header('history') + `<main class="page"><h1 class="display" style="margin:0 0 16px;font-size:40px">History</h1>
    <section class="panel">${rows ? `<table class="grid"><thead><tr><th>Match</th><th>Suite</th><th>Contenders</th><th>Status</th><th>Winner</th><th>Started</th><th></th></tr></thead><tbody>${rows}</tbody></table>` : '<div class="empty">No matches yet.</div>'}</section></main>`;
}
async function renderReviews() {
  const list = await api('/api/reviews'); S.pendingReviews = list.length;
  const rows = list.map((r) => `<tr class="click" data-href="#/task/${r.match_id}/${encodeURIComponent(r.task_id || '')}"><td class="mono muted">#${r.match_id}</td><td class="mono">${esc(r.task_id || '')}</td><td class="mono">${esc(r.model || '')}</td><td>${esc(r.reason)}</td><td class="muted">${new Date(r.created_at * 1000).toLocaleString()}</td></tr>`).join('');
  app.innerHTML = header('reviews') + `<main class="page"><h1 class="display" style="margin:0 0 6px;font-size:40px">Reviews</h1>
    <p class="muted" style="margin:0 0 16px">Answers the two judge passes disagreed on. An admin decides the score on the task page; results update right away.</p>
    <section class="panel">${rows ? `<table class="grid"><thead><tr><th>Match</th><th>Task</th><th>Model</th><th>Reason</th><th>Raised</th></tr></thead><tbody>${rows}</tbody></table>` : '<div class="empty">Nothing waiting for review.</div>'}</section></main>`;
}

// ── architecture ──────────────────────────────────────────────────────────────
function renderArchitecture() {
  const steps = [
    ['1 · SuiteSquad', 'Loads the task suite. Every task prompt is screened by k9x_Shield and Granite Guardian before any contender sees it. Guardian fails closed: if it is down, nothing runs.'],
    ['2 · ContestantSquad', 'Runs every task on every contender, grouped by model so the GPU swaps as rarely as possible. Each contender is pinned through llm_invoke and the router by its own catalog capability.'],
    ['3 · GradingSquad', 'Grades against right answers first (unit tests in a sandbox, JSON fields, exact answers), then Granite Guardian’s safety pass on adversarial answers, then two anonymized judge passes. Disagreements go to Reviews.'],
    ['4 · ReportSquad', 'Turns grades into scores and stars, tests the Intelligent Model Router on held-out tasks (it learns from the other tasks’ scores, then picks; no model is run again), and writes a recommended model_catalog.'],
  ];
  app.innerHTML = header('architecture') + `<main class="page" style="display:flex;flex-direction:column;gap:18px">
    <h1 class="display" style="margin:0;font-size:40px">Architecture</h1>
    <section class="panel" style="padding:12px"><img src="/static/overview.png" alt="K9X Arena at a glance: you bring models, a task suite and a judge; four stages Screen, Contest, Grade and Report; you get star ratings, a router test and a recommended router config. Granite Guardian and k9x_Shield govern throughout; every model call goes through llm_invoke and the Intelligent Model Router to your Ollama GPU." style="width:100%;height:auto;border-radius:10px;display:block"></section>
    <h2 class="display" style="margin:6px 0 0;font-size:28px">In detail</h2>
    <p class="muted" style="margin:0;max-width:900px">K9X Arena is a K9-AIF solution: its router, orchestrator, squads and agents extend the framework’s building blocks, and every model call goes through the framework’s llm_invoke and K9ModelRouter.</p>
    <div class="results" style="grid-template-columns:minmax(0,1fr) 440px">
      <section class="panel" style="padding:12px"><a href="/static/architecture.png" target="_blank" rel="noopener"><img src="/static/architecture.png" alt="K9X Arena architecture: Web UI to FastAPI, Engine, ArenaRouter and ArenaOrchestrator; four squads in order; every model call through llm_invoke and K9ModelRouter to the Ollama host; Granite Guardian and k9x_Shield screening; SQLite store." style="width:100%;height:auto;border-radius:10px;display:block"></a></section>
      <div style="display:flex;flex-direction:column;gap:16px">
        <section class="panel"><h2>How a match runs</h2>${steps.map(([t, d]) => `<div style="margin-bottom:12px"><div style="font-weight:600">${t}</div><div class="muted" style="font-size:13.5px;line-height:1.6">${d}</div></div>`).join('')}</section>
        <section class="panel teal"><h2>Router vs. arena grading</h2>
          <table class="grid"><thead><tr><th></th><th>Model router</th><th>Arena grading</th></tr></thead><tbody>
            <tr><td class="muted">When</td><td>Before each call, at runtime</td><td>After the answers, offline</td></tr>
            <tr><td class="muted">Question</td><td>Which model should answer?</td><td>How good was each answer?</td></tr>
            <tr><td class="muted">Output</td><td>One model per request</td><td>Stars, router test, recommended config</td></tr></tbody></table>
          <p class="muted" style="font-size:13px;margin:10px 0 0">The router starts from rules (capability, sensitivity, latency, cost). Given graded results through record_feedback(), it predicts each model’s quality on a new prompt from similar prompts it has seen, and overrules the rules when the evidence is clearly better (k-NN, in the style of Not Diamond and RouteLLM). The arena’s grades are that evidence; the router test measures how well it uses them.</p></section>
      </div>
    </div></main>`;
}

// ── winner celebration ────────────────────────────────────────────────────────
let celebrationTimer = null;
function closeCelebration() {
  clearTimeout(celebrationTimer);
  const el = document.getElementById('celebration'); if (!el) return;
  el.classList.add('leaving'); setTimeout(() => el.remove(), 350);
  document.removeEventListener('keydown', celebrationKey);
}
function celebrationKey(ev) { if (ev.key === 'Escape') closeCelebration(); }
function celebrate(board, judge, verdict) {
  const kind = (verdict && verdict.kind) || 'clear';
  const tied = (verdict && verdict.tied) || [];
  if (!board || !board.length) return;
  document.getElementById('celebration')?.remove();
  const [w, ...rest] = board;
  const sparks = Array.from({ length: 36 }, (_, i) => {
    const a = (i / 36) * Math.PI * 2, d = 120 + (i % 5) * 38;
    return `<span class="spark" style="--x:${Math.cos(a) * d}px;--y:${Math.sin(a) * d}px;animation-delay:${(i % 6) * 0.12}s"></span>`;
  }).join('');
  const el = document.createElement('div');
  el.id = 'celebration'; el.className = 'celebration';
  el.setAttribute('role', 'dialog'); el.setAttribute('aria-modal', 'true'); el.setAttribute('aria-labelledby', 'win-title');
  el.innerHTML = `<div class="cele-card">
    <button class="cele-close" data-act="close-celebration" aria-label="Close">✕</button>
    <div class="cele-trophy" aria-hidden="true">${sparks}
      <svg viewBox="0 0 120 120" width="150" height="150"><defs><linearGradient id="gold" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fff3b0"/><stop offset=".45" stop-color="#ffd166"/><stop offset="1" stop-color="#b8860b"/></linearGradient></defs>
        <path d="M34 18h52v18c0 18-11 32-26 34-15-2-26-16-26-34z" fill="url(#gold)"/>
        <path d="M34 24H18c0 16 8 24 18 26M86 24h16c0 16-8 24-18 26" fill="none" stroke="url(#gold)" stroke-width="6" stroke-linecap="round"/>
        <rect x="54" y="70" width="12" height="16" fill="url(#gold)"/><rect x="40" y="86" width="40" height="10" rx="3" fill="url(#gold)"/><rect x="34" y="96" width="52" height="10" rx="3" fill="#b8860b"/>
        <path d="M60 30l3.5 7 7.7 1.1-5.6 5.4 1.3 7.7-6.9-3.6-6.9 3.6 1.3-7.7-5.6-5.4 7.7-1.1z" fill="#fff8d6"/></svg></div>
    ${kind === 'draw' ? `<div class="cele-label">IT'S A DRAW</div>
      <h2 id="win-title" class="cele-name" style="font-size:24px">${tied.map(esc).join(' · ')}</h2>
      <div class="muted" style="margin-top:6px">Tied on answer quality and on overall score (within ${verdict.margin} points). Try a harder suite.</div>` : `
    <div class="cele-label">${kind === 'speed' ? 'QUALITY DRAW · DECIDED ON SPEED &amp; CONSISTENCY' : !judge || !judge.judged ? 'AND THE WINNER IS' : judge.disagreements ? 'AND THE WINNER, BY SPLIT DECISION' : 'AND THE WINNER, BY UNANIMOUS DECISION'}</div>
    <h2 id="win-title" class="cele-name">${esc(w.model)}</h2>
    <div class="cele-score"><span class="display">${w.score.toFixed(1)}</span><span class="stars">${starText(w.stars)}</span></div>
    ${kind === 'speed' ? `<div class="muted" style="margin-top:6px;font-size:13.5px">Answer quality tied with ${tied.filter((t) => t !== w.model).map(esc).join(', ')} (within ${verdict.margin} points).</div>` : ''}`}
    ${rest.length ? `<div class="cele-rest">${rest.map((b) => `<span><b class="r${b.rank}">${b.rank}</b> <span class="mono">${esc(b.model)}</span> · ${b.score.toFixed(1)}</span>`).join('')}</div>` : ''}
  </div>`;
  el.addEventListener('click', (ev) => { if (ev.target === el || ev.target.closest('[data-act="close-celebration"]')) closeCelebration(); });
  document.body.appendChild(el);
  document.addEventListener('keydown', celebrationKey);
  el.querySelector('.cele-close').focus();
  celebrationTimer = setTimeout(closeCelebration, 7000);
}

route();
