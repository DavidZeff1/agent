/* Job Agent UI. Plain vanilla JS, no build step. All user/job text is inserted with
   textContent (never innerHTML) so nothing from job boards can inject markup. */
'use strict';

const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];

let STATUS = { ai: false, has_profile: false, model: '', settings: {}, autorun: {} };
let P = null;            // the profile object, mirrors the server schema
let ADD = [];            // P.additional as editable [question, answer] pairs
let RESULTS = [];        // current ranked search results
let RERANKED = false;
let FILTER = 'new';      // results filter: 'new' | 'all'
let SELECTED = new Set();
let APPS = [];
let CURRENT_APP = null;

const STATUS_LABELS = { new: '', skipped: 'skipped', prepared: 'prepared',
                        submitted: 'submitted ✓', interview: 'interview 🎉', rejected: 'rejected' };

/* ---------------- tiny helpers ---------------- */

/* Hosted mode (Vercel): the server is stateless, so profile / API key / job statuses /
   prepared applications all live in this browser's localStorage. */
const HOSTED = () => !!STATUS.hosted;
function lsGet(key, fallback) {
  try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; }
  catch (e) { return fallback; }
}
function lsSet(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); }
  catch (e) { toast('Could not save in this browser (storage full or blocked).', true); }
}
let SEEN = {};      // hosted: job id -> status
let APPS_LS = [];   // hosted: prepared application packets

async function api(path, opts = {}) {
  const headers = {};
  const key = localStorage.getItem('ja_key');
  if (key) headers['X-Groq-Key'] = key.replace(/^"|"$/g, '');
  const init = opts.body !== undefined
    ? { method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' }, body: JSON.stringify(opts.body) }
    : { headers };
  const res = await fetch('/api/' + path, init);
  let data = null;
  try { data = await res.json(); } catch (e) { /* non-JSON error page */ }
  if (!res.ok) throw new Error((data && data.error) || 'Something went wrong. Please try again.');
  return data;
}

function toast(msg, isError = false) {
  const el = document.createElement('div');
  el.className = 'toast' + (isError ? ' error' : '');
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), isError ? 6000 : 3200);
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function copyText(text, btn) {
  navigator.clipboard.writeText(text).then(() => {
    if (btn) {
      const old = btn.textContent;
      btn.textContent = 'Copied ✓';
      setTimeout(() => { btn.textContent = old; }, 1400);
    } else {
      toast('Copied ✓');
    }
  }, () => toast('Could not copy — select and copy manually.', true));
}

function download(filename, text) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

function timeAgo(ts) {
  const s = Math.max(1, Math.floor(Date.now() / 1000 - ts));
  if (s < 90) return 'just now';
  if (s < 3600) return Math.round(s / 60) + ' min ago';
  if (s < 86400 * 2) return Math.round(s / 3600) + ' hours ago';
  return Math.round(s / 86400) + ' days ago';
}

function getPath(obj, path) { return path.split('.').reduce((o, k) => (o == null ? o : o[k]), obj); }
function setPath(obj, path, val) {
  const ks = path.split('.'); const last = ks.pop();
  let o = obj;
  for (const k of ks) { if (o[k] == null || typeof o[k] !== 'object') o[k] = {}; o = o[k]; }
  o[last] = val;
}

/* ---------------- dropdown machinery (see catalogs.js for the option data) ---------------- */

function flatCat(cat) { return cat.flatMap(g => (g && g.items) ? g.items : [g]); }

function fillSelect(sel, options, blankLabel = 'Choose…') {
  sel.appendChild(new Option(blankLabel, ''));
  for (const g of options || []) {
    if (g && g.items) {
      const og = document.createElement('optgroup');
      og.label = g.group;
      g.items.forEach(o => og.appendChild(new Option(o, o)));
      sel.appendChild(og);
    } else {
      sel.appendChild(new Option(g, g));
    }
  }
}

function buildDatalists() {
  const lists = {
    'dl-titles': flatCat(CAT.jobTitles),
    'dl-locations': flatCat(CAT.locations),
    'dl-cities': (CAT.locations.find(g => g.group === 'Cities') || { items: [] }).items,
    'dl-states': CAT.usStates,
    'dl-fields': CAT.fields,
    'dl-languages': CAT.languages,
    'dl-certs': CAT.certs,
  };
  for (const [id, options] of Object.entries(lists)) {
    const dl = document.createElement('datalist');
    dl.id = id;
    options.forEach(o => dl.appendChild(new Option(o, o)));
    document.body.appendChild(dl);
  }
}

function populateStaticSelects() {
  $$('select[data-options]').forEach(sel => fillSelect(sel, CAT[sel.dataset.options]));
}

/* Chip picker: an array field edited via dropdown (plus optional typing), shown as removable chips. */
function chipPicker(host, arr, opts) {
  host.innerHTML = '';
  host.classList.add('chippicker');
  if (arr.length) {
    const chips = el('div', 'chips chips-edit');
    arr.forEach((val, i) => {
      const c = el('span', 'chip2', val);
      const x = el('button', 'chipx', '✕');
      x.type = 'button';
      x.setAttribute('aria-label', 'Remove ' + val);
      x.onclick = () => { arr.splice(i, 1); chipPicker(host, arr, opts); };
      c.appendChild(x);
      chips.appendChild(c);
    });
    host.appendChild(chips);
  }
  const add = v => {
    v = String(v || '').trim();
    if (v && !arr.some(a => String(a).toLowerCase() === v.toLowerCase())) arr.push(v);
    chipPicker(host, arr, opts);
  };
  const row = el('div', 'chiprow');
  const sel = document.createElement('select');
  fillSelect(sel, opts.catalog, opts.placeholder || '+ Add from the list…');
  sel.onchange = () => { if (sel.value) add(sel.value); };
  const inp = document.createElement('input');
  inp.placeholder = 'or type your own…';
  inp.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); add(inp.value); } };
  const btn = el('button', 'btn small', 'Add');
  btn.type = 'button';
  btn.onclick = () => add(inp.value);
  row.append(sel, inp, btn);
  host.appendChild(row);
}

function chipBind(sel, path, catalog, placeholder) {
  let arr = getPath(P, path);
  if (!Array.isArray(arr)) { arr = []; setPath(P, path, arr); }
  chipPicker($(sel), arr, { catalog, placeholder });
}

function salaryBind(sel, path) {
  const s = $(sel);
  s.innerHTML = '';
  s.appendChild(new Option('Choose…', ''));
  CAT.salaries.forEach(v => s.appendChild(new Option(v.toLocaleString('en-US'), v)));
  const cur = getPath(P, path);
  if (cur != null && cur !== '') {
    if (![...s.options].some(o => o.value === String(cur))) {
      s.appendChild(new Option(Number(cur).toLocaleString('en-US'), cur));
    }
    s.value = String(cur);
  }
  s.onchange = () => setPath(P, path, s.value ? parseInt(s.value, 10) : null);
}

function monthYearField(item, key) {
  const wrap = el('div', 'myrow');
  const ms = document.createElement('select');
  ms.appendChild(new Option('Month', ''));
  CAT.months.forEach((m, i) => ms.appendChild(new Option(m, String(i + 1).padStart(2, '0'))));
  const ys = document.createElement('select');
  ys.appendChild(new Option('Year', ''));
  for (let y = new Date().getFullYear() + 1; y >= 1960; y--) ys.appendChild(new Option(y, String(y)));
  const m = String(item[key] || '').match(/^(\d{4})(?:-(\d{1,2}))?/);
  if (m) { ys.value = m[1]; if (m[2]) ms.value = m[2].padStart(2, '0'); }
  const update = () => { item[key] = ys.value ? (ms.value ? ys.value + '-' + ms.value : ys.value) : ''; };
  ms.onchange = update;
  ys.onchange = update;
  wrap.append(ms, ys);
  return wrap;
}

/* ---------------- views & header ---------------- */

function switchView(name) {
  $$('.view').forEach(v => { v.hidden = true; });
  $('#view-' + name).hidden = false;
  $$('.navbtn').forEach(b => b.classList.toggle('active', b.dataset.view === name ||
    (name === 'appdetail' && b.dataset.view === 'apps') ||
    (name === 'welcome' && b.dataset.view === 'profile')));
  $('#selectbar').hidden = !(name === 'find' && SELECTED.size > 0);
  window.scrollTo(0, 0);
}

function renderHeader() {
  const chip = $('#ai-chip');
  chip.textContent = STATUS.ai ? '● AI on' : '○ AI off — click to set up';
  chip.className = 'chip ' + (STATUS.ai ? 'on' : 'off');
}

/* ---------------- profile form ---------------- */

function bindInputs() {
  $$('#view-profile [data-bind]').forEach(inp => {
    const path = inp.dataset.bind;
    const v = getPath(P, path);
    if (inp.type === 'checkbox') {
      inp.checked = !!v;
    } else {
      // keep stored values that aren't in our option lists
      if (inp.tagName === 'SELECT' && v && ![...inp.options].some(o => o.value === String(v))) {
        inp.appendChild(new Option(v, v));
      }
      inp.value = v == null ? '' : v;
    }
    inp.oninput = () => setPath(P, path, inp.type === 'checkbox' ? inp.checked : inp.value);
  });
  chipBind('#nationalities', 'nationalities', CAT.nationalities, '+ Add a nationality…');
  chipBind('#desired_titles', 'preferences.desired_titles', CAT.jobTitles, '+ Add a job title…');
  chipBind('#desired_locations', 'preferences.desired_locations', CAT.locations, '+ Add a location…');
  chipBind('#skills', 'skills', CAT.skills, '+ Add a skill…');
  salaryBind('#salary_min', 'preferences.salary_min');
  salaryBind('#salary_max', 'preferences.salary_max');
}

const EDITORS = [
  {
    key: 'experience', container: '#list-experience', add: '+ Add a job',
    blank: () => ({ company: '', title: '', start_date: '', end_date: '', location: '', employment_type: '', description: '', achievements: [], tech: [] }),
    fields: [
      { k: 'title', label: 'Job title', half: true, list: 'dl-titles', ph: 'Pick from the list or type…' },
      { k: 'company', label: 'Company', half: true },
      { k: 'start_date', label: 'Started', type: 'monthyear', half: true },
      { k: 'end_date', label: 'Ended (leave empty if you still work there)', type: 'monthyear', half: true },
      { k: 'location', label: 'Location', half: true, list: 'dl-locations', ph: 'Pick from the list or type…' },
      { k: 'employment_type', label: 'Type', type: 'select', options: 'employmentTypes', half: true },
      { k: 'description', label: 'What you did, in one line', type: 'textarea' },
      { k: 'achievements', label: 'Things you\'re proud of there (one per line — these become resume bullets)', type: 'lines' },
      { k: 'tech', label: 'Tools & technologies used', type: 'chips', catalog: 'skills' },
    ],
  },
  {
    key: 'education', container: '#list-education', add: '+ Add education',
    blank: () => ({ institution: '', degree: '', field_of_study: '', start_date: '', end_date: '', gpa: '', location: '', honors: '' }),
    fields: [
      { k: 'institution', label: 'School / university', half: true },
      { k: 'degree', label: 'Degree', type: 'select', options: 'degrees', half: true },
      { k: 'field_of_study', label: 'Field of study', half: true, list: 'dl-fields', ph: 'Pick from the list or type…' },
      { k: 'gpa', label: 'GPA (optional)', half: true },
      { k: 'start_date', label: 'Started', type: 'year', half: true },
      { k: 'end_date', label: 'Finished (or expected)', type: 'year', half: true },
      { k: 'location', label: 'Location', half: true, list: 'dl-cities' },
      { k: 'honors', label: 'Honors / awards (optional)', half: true },
    ],
  },
  {
    key: 'projects', container: '#list-projects', add: '+ Add a project',
    blank: () => ({ name: '', description: '', tech: [], link: '', repo: '', role: '', highlights: [] }),
    fields: [
      { k: 'name', label: 'Project name' },
      { k: 'description', label: 'What it is / what it does', type: 'textarea' },
      { k: 'tech', label: 'Tools & technologies', type: 'chips', catalog: 'skills' },
      { k: 'link', label: 'Link (optional)', half: true },
      { k: 'repo', label: 'Code repository (optional)', half: true },
      { k: 'highlights', label: 'Highlights (one per line)', type: 'lines' },
    ],
  },
  {
    key: 'languages', container: '#list-languages', add: '+ Add a language',
    blank: () => ({ language: '', proficiency: '' }),
    fields: [
      { k: 'language', label: 'Language', half: true, list: 'dl-languages', ph: 'Pick from the list or type…' },
      { k: 'proficiency', label: 'Level', type: 'select', options: 'proficiency', half: true },
    ],
  },
  {
    key: 'certifications', container: '#list-certifications', add: '+ Add a certification',
    blank: () => ({ name: '', issuer: '', date: '', credential_id: '' }),
    fields: [
      { k: 'name', label: 'Certification', half: true, list: 'dl-certs', ph: 'Pick from the list or type…' },
      { k: 'issuer', label: 'Issued by', half: true },
      { k: 'date', label: 'Year', type: 'year', half: true },
    ],
  },
  {
    key: 'work_authorizations', container: '#list-work_authorizations', add: '+ Add a country',
    blank: () => ({ country: '', status: '', requires_sponsorship: false, notes: '' }),
    fields: [
      { k: 'country', label: 'Country', type: 'select', options: 'countries', half: true },
      { k: 'status', label: 'Your status there', type: 'select', options: 'authStatuses', half: true },
      { k: 'requires_sponsorship', label: 'I would need visa sponsorship to work there', type: 'check' },
    ],
  },
];

function renderEditor(ed) {
  const host = $(ed.container);
  host.innerHTML = '';
  let arr = getPath(P, ed.key);
  if (!Array.isArray(arr)) { arr = []; setPath(P, ed.key, arr); }
  arr.forEach((item, idx) => host.appendChild(editorItem(ed, item, idx, arr)));
  const add = el('button', 'addbtn', ed.add);
  add.type = 'button';
  add.onclick = () => { arr.push(ed.blank()); renderEditor(ed); };
  host.appendChild(add);
}

function editorItem(ed, item, idx, arr) {
  const div = el('div', 'item');
  const rm = el('button', 'remove', '✕ Remove');
  rm.type = 'button';
  rm.onclick = () => { arr.splice(idx, 1); renderEditor(ed); };
  div.appendChild(rm);

  let grid = null;
  const place = (node, half) => {
    if (half) {
      if (!grid) { grid = el('div', 'grid2'); div.appendChild(grid); }
      grid.appendChild(node);
    } else {
      grid = null;
      div.appendChild(node);
    }
  };

  for (const f of ed.fields) {
    if (f.type === 'check') {
      const lab = el('label', 'check');
      const inp = document.createElement('input');
      inp.type = 'checkbox';
      inp.checked = !!item[f.k];
      inp.oninput = () => { item[f.k] = inp.checked; };
      lab.appendChild(inp);
      lab.appendChild(document.createTextNode(' ' + f.label));
      lab.style.marginTop = '12px';
      place(lab, false);
      continue;
    }
    if (f.type === 'chips') {
      if (!Array.isArray(item[f.k])) item[f.k] = [];
      place(el('label', null, f.label), false);
      const host = el('div');
      chipPicker(host, item[f.k], { catalog: typeof f.catalog === 'string' ? CAT[f.catalog] : f.catalog });
      place(host, false);
      continue;
    }
    if (f.type === 'monthyear') {
      const lab = el('label', null, f.label);
      lab.appendChild(monthYearField(item, f.k));
      place(lab, f.half);
      continue;
    }

    let inp;
    if (f.type === 'textarea') { inp = document.createElement('textarea'); inp.rows = 2; }
    else if (f.type === 'lines') { inp = document.createElement('textarea'); inp.rows = 3; }
    else if (f.type === 'select') {
      inp = document.createElement('select');
      fillSelect(inp, typeof f.options === 'string' ? CAT[f.options] : f.options);
    } else if (f.type === 'year') {
      inp = document.createElement('select');
      inp.appendChild(new Option('Year', ''));
      for (let y = new Date().getFullYear() + 6; y >= 1950; y--) inp.appendChild(new Option(y, String(y)));
    } else {
      inp = document.createElement('input');
      if (f.list) inp.setAttribute('list', f.list);
    }
    if (f.ph) inp.placeholder = f.ph;

    const v = item[f.k];
    if (f.type === 'lines') inp.value = (v || []).join('\n');
    else if (f.type === 'csv') inp.value = (v || []).join(', ');
    else {
      if (inp.tagName === 'SELECT' && v && ![...inp.options].some(o => o.value === String(v))) {
        inp.appendChild(new Option(v, v));  // keep stored values that aren't in our lists
      }
      inp.value = v == null ? '' : v;
    }

    inp.oninput = () => {
      if (f.type === 'lines') item[f.k] = inp.value.split('\n').map(s => s.trim()).filter(Boolean);
      else if (f.type === 'csv') item[f.k] = inp.value.split(',').map(s => s.trim()).filter(Boolean);
      else item[f.k] = inp.value;
    };

    const lab = el('label', null, f.label);
    lab.appendChild(inp);
    place(lab, f.half);
  }
  return div;
}

function renderAdditional() {
  const host = $('#list-additional');
  host.innerHTML = '';
  ADD.forEach((pair, idx) => {
    const div = el('div', 'item');
    const rm = el('button', 'remove', '✕ Remove');
    rm.type = 'button';
    rm.onclick = () => { ADD.splice(idx, 1); renderAdditional(); };
    div.appendChild(rm);
    const lq = el('label', null, 'Question');
    const iq = document.createElement('input');
    iq.value = pair[0];
    iq.placeholder = 'e.g. How did you hear about us?';
    iq.oninput = () => { pair[0] = iq.value; };
    lq.appendChild(iq);
    const la = el('label', null, 'Your answer');
    const ia = document.createElement('textarea');
    ia.rows = 2;
    ia.value = pair[1] == null ? '' : pair[1];
    ia.oninput = () => { pair[1] = ia.value; };
    la.appendChild(ia);
    div.append(lq, la);
    host.appendChild(div);
  });
  const add = el('button', 'addbtn', '+ Add a saved answer');
  add.type = 'button';
  add.onclick = () => { ADD.push(['', '']); renderAdditional(); };
  host.appendChild(add);
}

function isEmptyItem(o) {
  return Object.values(o).every(v => v === '' || v == null || v === false || (Array.isArray(v) && !v.length));
}

async function doGithubImport() {
  const btn = $('#btn-gh-import');
  btn.disabled = true;
  btn.textContent = 'Importing…';
  try {
    const d = await api('github/import', { body: { github: $('#gh-user').value.trim() } });
    if (!Array.isArray(P.projects)) P.projects = [];
    const have = new Set(P.projects.map(p => (p.repo || p.name || '').toLowerCase()));
    let added = 0;
    for (const proj of d.projects) {
      if (have.has((proj.repo || '').toLowerCase()) || have.has(proj.name.toLowerCase())) continue;
      P.projects.push(proj);
      added++;
    }
    if (!$('#gh-user').value.trim() && d.username) $('#gh-user').value = 'github.com/' + d.username;
    if (!P.links.github) P.links.github = 'https://github.com/' + d.username;
    renderEditor(EDITORS.find(e => e.key === 'projects'));
    toast(added ? `Added ${added} project${added > 1 ? 's' : ''} from GitHub — review them, then Save profile.`
                : 'Those projects are already in your profile.');
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Import from GitHub';
  }
}

async function saveProfile() {
  P.additional = Object.fromEntries(ADD.filter(p => p[0].trim()).map(p => [p[0].trim(), p[1]]));
  for (const ed of EDITORS) {  // drop rows the user added but never filled in
    setPath(P, ed.key, (getPath(P, ed.key) || []).filter(o => !isEmptyItem(o)));
  }
  EDITORS.forEach(renderEditor);
  const btn = $('#save-profile');
  btn.disabled = true;
  try {
    if (HOSTED()) {
      if (!(P.full_name || P.preferred_name)) throw new Error('Please enter your name before saving.');
      lsSet('ja_profile', P);  // hosted: the profile lives in this browser only
    } else {
      await api('profile', { body: P });
    }
    STATUS.has_profile = true;
    toast('Profile saved ✓');
    $('#save-hint').textContent = 'Saved. Next: Find jobs →';
    if (!$('#kw').value) $('#kw').value = (P.preferences.desired_titles || []).join(', ');
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
  }
}

/* ---------------- find jobs ---------------- */

function searchStatus(text) {
  $('#search-status').hidden = !text;
  $('#search-status-text').textContent = text || '';
}

async function doSearch() {
  const keywords = $('#kw').value.split(',').map(s => s.trim()).filter(Boolean);
  const btn = $('#btn-search');
  btn.disabled = true;
  searchStatus(`Searching ${STATUS.sources || 7} job boards… this takes 15–40 seconds.`);
  $('#results').innerHTML = '';
  $('#results-head').hidden = true;
  SELECTED.clear();
  updateSelectbar();
  try {
    const body = { keywords, location: $('#loc').value.trim(), remote: $('#remote').checked, limit: 30 };
    if (HOSTED()) body.profile = P;
    const data = await api('search', { body });
    RESULTS = data.results;
    let newCount = data.new;
    if (HOSTED()) {  // job history lives in this browser
      newCount = 0;
      for (const r of RESULTS) {
        if (!SEEN[r.job.id]) { SEEN[r.job.id] = 'new'; newCount++; }
        r.status = SEEN[r.job.id];
      }
      lsSet('ja_seen', SEEN);
    }
    RERANKED = false;
    renderResults();
    if (newCount === 0 && RESULTS.length) toast('No new jobs since last time — everything here you\'ve already seen.');
    else if (newCount > 0) toast(`${newCount} new job${newCount > 1 ? 's' : ''} since last time`);
  } catch (e) {
    toast(e.message, true);
  } finally {
    searchStatus('');
    btn.disabled = false;
  }
}

function renderAutorunInfo() {
  const a = STATUS.autorun || {};
  const elx = $('#autorun-info');
  if (HOSTED() || !a.ran_at) { elx.hidden = true; return; }
  elx.hidden = false;
  let txt = `Last automatic check: ${timeAgo(a.ran_at)} — ${a.found} jobs, ${a.new} new` +
    (a.prepared ? `, ${a.prepared} application${a.prepared > 1 ? 's' : ''} auto-prepared` : '');
  if (a.error) txt = `Last automatic check ${timeAgo(a.ran_at)} hit a problem: ${a.error}`;
  elx.textContent = txt;
}

async function doRerank() {
  const btn = $('#btn-rerank');
  btn.disabled = true;
  searchStatus('Asking the AI to look closely at your top matches… about a minute.');
  try {
    const body = HOSTED() ? { profile: P, jobs: RESULTS.map(r => r.job) } : {};
    const data = await api('rerank', { body });
    RESULTS = data.results;
    if (HOSTED()) RESULTS.forEach(r => { r.status = SEEN[r.job.id] || 'new'; });
    RERANKED = true;
    renderResults();
    toast('Ranking improved with AI ✓');
  } catch (e) {
    toast(e.message, true);
  } finally {
    searchStatus('');
    btn.disabled = false;
  }
}

function renderResults() {
  const host = $('#results');
  host.innerHTML = '';
  $('#results-head').hidden = false;
  $('#btn-rerank').hidden = !(STATUS.ai && RESULTS.length > 1 && !RERANKED);
  const shown = FILTER === 'new' ? RESULTS.filter(r => r.status === 'new') : RESULTS;
  if (!RESULTS.length) {
    $('#results-count').textContent = 'No jobs found';
    const empty = el('div', 'card empty');
    empty.append(el('span', 'big', '🔎'),
      el('div', null, 'Nothing matched. Try simpler or different words — e.g. “designer” instead of “senior visual designer”.'));
    host.appendChild(empty);
    return;
  }
  if (!shown.length) {
    $('#results-count').textContent = 'Nothing new';
    const empty = el('div', 'card empty');
    empty.append(el('span', 'big', '✅'),
      el('div', null, 'You\'ve already handled every job in this search. Switch the filter to “everything” to see them, or search again later.'));
    host.appendChild(empty);
    updateSelectbar();
    return;
  }
  $('#results-count').textContent = shown.length + (FILTER === 'new' ? ' new jobs' : ' jobs') + ', best matches first';
  for (const r of shown) host.appendChild(jobCard(r));
  updateSelectbar();
}

function jobCard(r) {
  const j = r.job;
  const card = el('div', 'card jobcard');
  const handled = r.status && r.status !== 'new';

  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = SELECTED.has(j.id);
  cb.setAttribute('aria-label', 'Select ' + j.title);
  cb.onchange = () => { cb.checked ? SELECTED.add(j.id) : SELECTED.delete(j.id); updateSelectbar(); };
  if (handled && r.status !== 'skipped') cb.style.visibility = 'hidden';
  card.appendChild(cb);

  const main = el('div', 'jobmain');
  const title = el('div', 'jobtitle');
  const a = el('a', null, j.title);
  a.href = j.url; a.target = '_blank'; a.rel = 'noopener';
  title.appendChild(a);
  if (handled) title.appendChild(el('span', 'badge st-' + r.status, ' ' + (STATUS_LABELS[r.status] || r.status)));
  main.appendChild(title);
  main.appendChild(el('div', 'jobmeta',
    [j.company, j.location, j.salary].filter(Boolean).join('  ·  ')));
  const why = r.rationale || r.reasons;
  if (why) main.appendChild(el('div', 'jobwhy', why));

  const det = document.createElement('details');
  det.appendChild(el('summary', null, 'More about this job'));
  if (r.matched_skills.length) {
    const chips = el('div', 'chips');
    chips.appendChild(el('b', 'tiny', 'You have: '));
    r.matched_skills.slice(0, 8).forEach(s => chips.appendChild(el('span', 'skillchip', s)));
    det.appendChild(chips);
  }
  if (r.missing_keywords.length) {
    const chips = el('div', 'chips');
    chips.appendChild(el('b', 'tiny', 'They also mention: '));
    r.missing_keywords.slice(0, 8).forEach(s => chips.appendChild(el('span', 'skillchip gap', s)));
    det.appendChild(chips);
  }
  if (j.description_preview) det.appendChild(el('div', 'jobdesc', j.description_preview));
  const open = el('a', null, 'View full posting ↗');
  open.href = j.url; open.target = '_blank'; open.rel = 'noopener';
  det.appendChild(open);
  main.appendChild(det);
  card.appendChild(main);

  const match = el('div', 'match');
  const cls = r.score >= 72 ? 'great' : r.score >= 55 ? 'good' : 'fair';
  const score = el('div', 'matchscore ' + cls, String(r.score));
  score.appendChild(el('small', null, 'MATCH'));
  match.appendChild(score);
  if (r.verdict) match.appendChild(el('div', 'verdict', r.verdict));
  if (!handled) {
    const skip = el('button', 'btn small skipbtn', 'Skip');
    skip.title = 'Hide this job and never show it again';
    skip.onclick = async () => {
      r.status = 'skipped';
      SELECTED.delete(j.id);
      renderResults();
      if (HOSTED()) { SEEN[j.id] = 'skipped'; lsSet('ja_seen', SEEN); return; }
      try {
        await api('track', { body: { job_id: j.id, status: 'skipped', title: j.title, company: j.company, url: j.url } });
      } catch (e) { toast(e.message, true); }
    };
    match.appendChild(skip);
  }
  card.appendChild(match);

  return card;
}

function updateSelectbar() {
  const bar = $('#selectbar');
  const n = SELECTED.size;
  bar.hidden = n === 0 || $('#view-find').hidden;
  $('#btn-prepare').textContent = n === 1 ? 'Prepare 1 application' : `Prepare ${n} applications`;
  const tailorLabel = $('#tailor').parentElement;
  tailorLabel.style.display = STATUS.ai ? '' : 'none';
}

async function doPrepare() {
  const ids = [...SELECTED];
  if (!ids.length) return;
  const tailor = STATUS.ai && $('#tailor').checked;
  const dlg = $('#progress');
  dlg.showModal();
  // Poll the server's one-line status so the user sees what the AI is doing right now.
  // (Hosted: each request runs on its own serverless instance, so there's nothing to poll.)
  const poll = HOSTED() ? 0 : setInterval(async () => {
    try {
      const p = await api('progress');
      $('#progress-detail').textContent = p.text || '';
    } catch (e) { /* server busy; try again next tick */ }
  }, 800);
  let ok = 0, fail = 0, done = 0, next = 0;
  // Two at a time: enough to overlap the slow AI calls, small enough to stay inside the
  // free tier's tokens-per-minute budget (more workers just trade speed for rate-limit waits).
  const workers = Math.min(2, ids.length);
  $('#progress-title').textContent = `Preparing ${ids.length} application${ids.length > 1 ? 's' : ''}`;
  $('#progress-text').textContent = workers > 1 ? `Working on ${workers} at a time…` : '';
  const worker = async () => {
    while (next < ids.length) {
      const id = ids[next++];
      const r = RESULTS.find(x => x.job.id === id);
      try {
        const body = { job_id: id, tailor };
        if (HOSTED()) { body.job = r.job; body.profile = P; body.inline = true; }
        const resp = await api('apply', { body });
        if (HOSTED() && resp.packet) {
          APPS_LS = APPS_LS.filter(p => (p.job || {}).id !== id);
          APPS_LS.unshift({ ...resp.packet, created: Math.floor(Date.now() / 1000) });
          APPS_LS = APPS_LS.slice(0, 25);  // stay well inside browser storage limits
          lsSet('ja_apps', APPS_LS);
          SEEN[id] = 'prepared';
          lsSet('ja_seen', SEEN);
        }
        ok++;
      } catch (e) {
        fail++;
        toast((r ? r.job.company + ': ' : '') + e.message, true);
      }
      done++;
      $('#progress-title').textContent = `Prepared ${done} of ${ids.length}`;
      $('#progress-fill').style.width = Math.round((done / ids.length) * 100) + '%';
    }
  };
  await Promise.all(Array.from({ length: workers }, worker));
  clearInterval(poll);
  $('#progress-detail').textContent = '';
  $('#progress-fill').style.width = '100%';
  setTimeout(() => dlg.close(), 300);
  for (const id of ids) {
    const r = RESULTS.find(x => x.job.id === id);
    if (r) r.status = 'prepared';
  }
  SELECTED.clear();
  renderResults();
  await loadApps();
  switchView('apps');
  if (ok) toast(`${ok} application${ok > 1 ? 's' : ''} ready ✓`);
}

/* ---------------- applications ---------------- */

async function loadApps() {
  if (HOSTED()) {
    APPS = APPS_LS.map(p => ({
      folder: (p.job || {}).id || '',
      title: (p.job || {}).title || 'Application',
      company: (p.job || {}).company || '',
      url: (p.job || {}).url || '',
      tailored: !!p.tailored,
      status: SEEN[(p.job || {}).id] || 'prepared',
      created: p.created || 0,
    }));
    renderApps();
    return;
  }
  try {
    const d = await api('applications');
    APPS = d.applications;
  } catch (e) { APPS = []; }
  renderApps();
}

function renderApps() {
  const host = $('#apps-list');
  host.innerHTML = '';
  if (!APPS.length) {
    const empty = el('div', 'card empty');
    empty.append(el('span', 'big', '📄'),
      el('div', null, 'No applications yet. Go to Find jobs, tick the ones you like, and click “Prepare applications”.'));
    host.appendChild(empty);
    return;
  }
  for (const a of APPS) {
    const item = el('div', 'card appitem');
    const left = el('div');
    const t = el('div', 'jobtitle', a.title + '  ');
    if (a.tailored) t.appendChild(el('span', 'badge tailored', 'AI-tailored'));
    if (a.status && a.status !== 'prepared') t.appendChild(el('span', 'badge st-' + a.status, STATUS_LABELS[a.status] || a.status));
    left.appendChild(t);
    left.appendChild(el('div', 'jobmeta', a.company));
    item.appendChild(left);
    item.appendChild(el('div', 'when', timeAgo(a.created)));
    item.onclick = () => openApp(a.folder);
    host.appendChild(item);
  }
}

async function openApp(folder) {
  if (HOSTED()) {
    const p = APPS_LS.find(x => (x.job || {}).id === folder);
    if (!p) { toast('That application is no longer stored in this browser.', true); return; }
    CURRENT_APP = {
      folder,
      job: p.job || {},
      tailored: !!p.tailored,
      review: p.review || {},
      status: SEEN[folder] || 'prepared',
      fields: p.fields || {},
      common_answers: p.common_answers || {},
      resume_txt: p.resume_txt || '',
      resume_md: p.resume_md || '',
      cover_letter: p.cover_letter || '',
      resume_html: p.resume_html || '',
      letter_html: p.letter_html || '',
      files: {},
    };
    renderAppDetail();
    switchView('appdetail');
    return;
  }
  try {
    CURRENT_APP = await api('application/' + encodeURIComponent(folder));
  } catch (e) {
    toast(e.message, true);
    return;
  }
  renderAppDetail();
  switchView('appdetail');
}

function printDoc(html, title) {
  const w = window.open('', '_blank');
  if (!w) { toast('Your browser blocked the print window — allow pop-ups for this site.', true); return; }
  w.document.write(html);
  w.document.title = title;
  w.document.close();
  setTimeout(() => w.print(), 400);
}

function renderAppDetail() {
  const d = CURRENT_APP;
  $('#app-title').textContent = d.job.title || d.folder;
  let sub = d.job.company + (d.tailored ? '  ·  tailored by AI' : '');
  const rv = d.review || {};
  if (rv.checked) {
    sub += rv.issues && rv.issues.length
      ? (rv.fixed ? `  ·  fact-checked: ${rv.issues.length} issue${rv.issues.length > 1 ? 's' : ''} found & fixed ✓`
                  : `  ·  ⚠ reviewer flagged: ${rv.issues.map(i => i.detail).join('; ').slice(0, 140)}`)
      : '  ·  fact-checked ✓';
  }
  $('#app-sub').textContent = sub;
  const url = d.job.apply_url || d.job.url || '';
  $('#app-open').href = url;
  $('#app-open').style.display = url ? '' : 'none';

  const fh = $('#app-fields');
  fh.innerHTML = '';
  for (const [k, v] of Object.entries(d.fields || {})) {
    const row = el('div', 'fieldrow');
    row.appendChild(el('div', 'k', k));
    row.appendChild(el('div', 'v', v));
    const btn = el('button', 'btn small', 'Copy');
    btn.onclick = () => copyText(v, btn);
    row.appendChild(btn);
    fh.appendChild(row);
  }

  const ah = $('#app-answers');
  ah.innerHTML = '';
  const ca = d.common_answers || {};
  const answers = [
    ['Why do you want to work at ' + (d.job.company || 'this company') + '?', ca.why_company],
    ['Why are you a good fit for this role?', ca.why_fit],
  ].filter(x => x[1]);
  $('#app-answers-card').hidden = answers.length === 0;
  for (const [q, a] of answers) {
    const box = el('div', 'qa');
    box.appendChild(el('div', 'q', q));
    box.appendChild(el('pre', 'doc', a));
    const btn = el('button', 'btn small', 'Copy answer');
    btn.onclick = () => copyText(a, btn);
    box.appendChild(btn);
    ah.appendChild(box);
  }

  $('#app-cover').textContent = d.cover_letter || '(no cover letter file found)';
  $('#app-resume').textContent = d.resume_txt || '(no resume file found)';
  const files = d.files || {};
  const fileUrl = fn => 'api/application/' + encodeURIComponent(d.folder) + '/file/' + fn;
  $('#dl-resume-pdf').hidden = !files['resume.pdf'];
  $('#dl-resume-pdf').href = fileUrl('resume.pdf');
  $('#dl-cover-pdf').hidden = !files['cover_letter.pdf'];
  $('#dl-cover-pdf').href = fileUrl('cover_letter.pdf');

  const wrap = $('#app-status-wrap');
  wrap.hidden = !d.job.id;
  if (d.job.id) {
    const sel = $('#app-status');
    sel.value = ['prepared', 'submitted', 'interview', 'rejected', 'skipped'].includes(d.status) ? d.status : 'prepared';
    sel.onchange = async () => {
      try {
        if (HOSTED()) {
          SEEN[d.job.id] = sel.value;
          lsSet('ja_seen', SEEN);
        } else {
          await api('track', { body: { job_id: d.job.id, status: sel.value, title: d.job.title, company: d.job.company, url: d.job.url } });
        }
        const item = APPS.find(a => a.folder === d.folder);
        if (item) item.status = sel.value;
        toast(sel.value === 'submitted' ? 'Marked as submitted ✓ — it won\'t show up as new again.' : 'Status saved ✓');
        renderApps();
      } catch (e) { toast(e.message, true); }
    };
  }

  // Hosted mode: no server-made PDFs or desktop autofill — print-to-PDF instead.
  const canPrint = !!(d.resume_html || d.letter_html);
  $('#print-resume').hidden = !canPrint;
  $('#print-cover').hidden = !canPrint;
  if (canPrint) {
    $('#print-resume').onclick = () => printDoc(d.resume_html, 'Resume - ' + (P.full_name || 'me'));
    $('#print-cover').onclick = () => printDoc(d.letter_html, 'Cover letter - ' + (d.job.company || 'job'));
  }
  const afBtn = $('#btn-autofill');
  afBtn.hidden = HOSTED();
  if (HOSTED()) {
    $('#autofill-result').hidden = false;
    $('#autofill-result').textContent =
      'Auto-fill runs in the desktop version — it opens and fills the employer\'s form in your own '
      + 'browser, which a website can\'t do. Use the copy buttons below instead.';
  }
  $('#qa-question').value = '';
  $('#qa-result').hidden = true;
}

async function doAutofill() {
  const btn = $('#btn-autofill');
  const out = $('#autofill-result');
  btn.disabled = true;
  btn.textContent = 'Working… watch for a browser window';
  out.hidden = true;
  try {
    const d = await api('autofill', { body: { folder: CURRENT_APP.folder } });
    out.hidden = false;
    out.textContent = `Filled ${d.filled.length} field(s) on the ${d.ats} form.` +
      (d.skipped.length
        ? ` Couldn't answer: ${d.skipped.slice(0, 8).join('; ')} — fill those in yourself, then submit.`
        : ' Review everything, then click Submit.');
    toast('Form filled — review it in the browser window ✓');
  } catch (e) {
    out.hidden = false;
    out.textContent = e.message;
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = '🪄 Auto-fill in my browser';
  }
}

async function doAnswer() {
  const q = $('#qa-question').value.trim();
  if (!q) return;
  const btn = $('#btn-answer');
  btn.disabled = true;
  btn.textContent = 'Thinking…';
  try {
    const body = { question: q };
    if (HOSTED()) body.profile = P;
    const d = await api('answer', { body });
    let text = d.answer;
    if (text === 'NOT IN PROFILE') {
      text = "That isn't in your profile yet. Add it under Profile → Saved answers, then ask again.";
    }
    $('#qa-answer').textContent = text;
    $('#qa-result').hidden = false;
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Answer';
  }
}

/* ---------------- settings ---------------- */

function openSettings() {
  $('#settings-status').textContent = STATUS.ai
    ? 'AI is on ✓  (free Groq account, model: ' + STATUS.model + ')'
    : 'AI is currently off — matching still works, documents are just not tailored.';
  $('#automation-block').hidden = HOSTED();  // needs a computer that stays on; not available hosted
  $('#key-input').value = '';
  const s = STATUS.settings || {};
  $('#set-autosearch').checked = !!s.autosearch;
  $('#set-hours').value = String(s.autosearch_hours || 6);
  $('#set-minscore').value = String(s.auto_prepare_min_score || 0);
  $('#set-maxprep').value = String(s.auto_prepare_max || 3);
  $('#settings').showModal();
}

async function saveAutomation() {
  try {
    STATUS.settings = await api('settings', { body: {
      autosearch: $('#set-autosearch').checked,
      autosearch_hours: parseInt($('#set-hours').value, 10),
      auto_prepare_min_score: parseInt($('#set-minscore').value, 10),
      auto_prepare_max: parseInt($('#set-maxprep').value, 10),
    } });
    toast('Automation settings saved ✓');
  } catch (e) { toast(e.message, true); }
}

async function runCheckNow() {
  const btn = $('#btn-run-now');
  btn.disabled = true;
  btn.textContent = 'Checking… (up to a minute)';
  try {
    const s = await api('autosearch/run', { body: {} });
    STATUS.autorun = s;
    renderAutorunInfo();
    if (s.error) { toast(s.error, true); }
    else {
      toast(`Checked ${s.found} jobs — ${s.new} new` + (s.prepared ? `, ${s.prepared} auto-prepared` : ''));
      const d = await api('results');
      RESULTS = d.results;
      RERANKED = false;
      renderResults();
      loadApps();
      $('#settings').close();
      switchView('find');
    }
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Check for new jobs now';
  }
}

async function saveKey(key) {
  const btn = $('#btn-save-key');
  btn.disabled = true;
  btn.textContent = 'Testing…';
  try {
    const d = await api('settings/key', { body: { key } });  // server validates with a live call
    if (HOSTED()) {  // hosted: the key stays in this browser and rides along on each request
      if (key) localStorage.setItem('ja_key', key);
      else localStorage.removeItem('ja_key');
    }
    STATUS.ai = d.ai;
    renderHeader();
    toast(d.message || 'Saved ✓');
    $('#settings').close();
  } catch (e) {
    STATUS.ai = false;
    if (HOSTED()) localStorage.removeItem('ja_key');
    renderHeader();
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Save & test';
  }
}

/* ---------------- init ---------------- */

async function init() {
  try {
    STATUS = await api('status');
  } catch (e) {
    toast('Cannot reach Job Agent. Close this tab and start the app again.', true);
    return;
  }
  renderHeader();
  $$('.nboards').forEach(n => { n.textContent = STATUS.sources || 7; });
  buildDatalists();
  populateStaticSelects();
  if (HOSTED()) {
    SEEN = lsGet('ja_seen', {});
    APPS_LS = lsGet('ja_apps', []);
    P = lsGet('ja_profile', null) || await api('profile');  // server returns an empty template
    STATUS.has_profile = !!(P.full_name || (P.skills || []).length);
  } else {
    P = await api('profile');
  }
  ADD = Object.entries(P.additional || {});
  bindInputs();
  EDITORS.forEach(renderEditor);
  renderAdditional();
  loadApps();

  // navigation
  $$('.navbtn').forEach(b => { b.onclick = () => switchView(b.dataset.view); });
  $('#welcome-start').onclick = () => switchView('profile');
  $('#btn-back').onclick = () => switchView('apps');
  $('#ai-chip').onclick = openSettings;

  // profile
  $('#save-profile').onclick = saveProfile;
  $('#btn-gh-import').onclick = doGithubImport;
  if (P.links.github) $('#gh-user').value = P.links.github.replace(/^https?:\/\//, '');

  // find jobs
  $('#btn-search').onclick = doSearch;
  $('#kw').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
  $('#btn-rerank').onclick = doRerank;
  $('#btn-prepare').onclick = doPrepare;
  $('#filter').onchange = () => { FILTER = $('#filter').value; renderResults(); };

  // application detail
  $('#btn-autofill').onclick = doAutofill;
  $('#btn-answer').onclick = doAnswer;
  $('#qa-question').addEventListener('keydown', e => { if (e.key === 'Enter') doAnswer(); });
  $('#dl-cover').onclick = () => download('Cover letter - ' + (CURRENT_APP.job.company || 'job') + '.txt', CURRENT_APP.cover_letter);
  $('#dl-resume').onclick = () => download('Resume - ' + (P.full_name || 'me') + '.txt', CURRENT_APP.resume_txt);

  // settings
  $('#btn-save-key').onclick = () => saveKey($('#key-input').value.trim());
  $('#key-input').addEventListener('keydown', e => { if (e.key === 'Enter') saveKey($('#key-input').value.trim()); });
  $('#btn-remove-key').onclick = () => saveKey('');
  $('#btn-save-settings').onclick = saveAutomation;
  $('#btn-run-now').onclick = runCheckNow;
  $('#btn-close-settings').onclick = () => $('#settings').close();

  // generic copy buttons (copy the text of the element in data-copy)
  document.addEventListener('click', e => {
    const btn = e.target.closest('[data-copy]');
    if (btn) copyText($(btn.dataset.copy).textContent, btn);
  });

  // first screen
  renderAutorunInfo();
  if (!STATUS.has_profile) {
    switchView('welcome');
  } else {
    $('#kw').value = (P.preferences.desired_titles || []).join(', ');
    switchView('find');
    // show the last saved ranking right away (local mode: auto-search keeps this fresh;
    // hosted mode has no server-side memory, so the user just searches)
    if (!HOSTED()) {
      try {
        const d = await api('results');
        if (d.results.length) {
          RESULTS = d.results;
          renderResults();
        }
      } catch (e) { /* no saved results yet */ }
    }
  }
}

init();
