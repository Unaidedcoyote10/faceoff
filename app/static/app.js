(() => {
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const api = async (path, opts = {}) => {
  const r = await fetch(path, { credentials: 'same-origin', ...opts,
    headers: opts.body && !(opts.body instanceof FormData) ? { 'Content-Type': 'application/json', ...(opts.headers || {}) } : opts.headers });
  let data = null; try { data = await r.json(); } catch (_) {}
  if (!r.ok) { const e = new Error((data && (data.detail || data.error)) || `HTTP ${r.status}`); e.status = r.status; e.data = data; throw e; }
  return data;
};
const toast = (msg, ms = 2600) => { const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), ms); };
const ago = ts => { if (!ts) return '—'; const s = (Date.now() / 1000 - ts); const f = Math.abs(s);
  const u = f < 60 ? [Math.round(f), 's'] : f < 3600 ? [Math.round(f / 60), 'm'] : f < 86400 ? [(f / 3600).toFixed(1).replace('.0', ''), 'h'] : [Math.round(f / 86400), 'd'];
  return s >= 0 ? `${u[0]}${u[1]} ago` : `in ${u[0]}${u[1]}`; };
const seg = (el, val, on) => { $$('button', el).forEach(b => b.classList.toggle('on', b.dataset.v === val));
  if (on) el.onclick = e => { const b = e.target.closest('button'); if (!b) return; seg(el, b.dataset.v); on(b.dataset.v); }; };
const rangeFill = r => r.style.setProperty('--p', ((r.value - r.min) / (r.max - r.min) * 100) + '%');

let S = null;           // settings
let items = [];         // gallery
let view = localStorage.getItem('fo.view') || 'after';
let uploadMode = null;
const batchIds = new Set();

/* ---------- tabs ---------- */
function go(tab) {
  $$('.tab').forEach(t => t.classList.toggle('active', t.id === 'tab-' + tab));
  $$('nav.bottom button').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  history.replaceState(null, '', '#' + tab);
  if (tab === 'gallery') loadGallery();
  if (tab === 'settings') { loadSettings(); loadIG(); }
  if (tab === 'docs') loadDocs();
  if (tab === 'saved') savedOpen(); else savedLeave();
  window.scrollTo({ top: 0 });
}
$$('nav.bottom button').forEach(b => b.onclick = () => go(b.dataset.tab));
document.addEventListener('click', e => { const g = e.target.closest('[data-goto]'); if (g) go(g.dataset.goto); });

/* ---------- upload (files + whole folders) ---------- */
const drop = $('#drop'), input = $('#fileInput'), folderInput = $('#folderInput');
const IMG_EXT = /\.(jpe?g|png|webp|heic|heif|bmp|tiff?)$/i;
const IMG_MIME = /^image\/(jpeg|pjpeg|png|webp|heic|heif|heic-sequence|heif-sequence|bmp|x-ms-bmp|tiff)$/i;
const JUNK = /^(thumbs\.db|desktop\.ini|icon\r?)$/i;
const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
const relPath = f => f._rel || f.webkitRelativePath || f.name;
function classify(f) {
  const rel = relPath(f), parts = rel.split('/');
  if (parts.some(p => p.startsWith('.') || p === '__MACOSX') || JUNK.test(f.name)) return 'hidden';
  if (!(IMG_EXT.test(f.name) || IMG_MIME.test(f.type || ''))) return 'other';
  if (!f.size) return 'empty';
  return 'ok';
}
// Walk dropped folders. readEntries() returns batches (~100), so loop until it returns [].
const readAll = dir => new Promise(res => { const r = dir.createReader(), out = [];
  const step = () => r.readEntries(b => { if (!b.length) return res(out); out.push(...b); step(); }, () => res(out)); step(); });
const entryFile = e => new Promise(res => e.file(f => { f._rel = e.fullPath.replace(/^\//, ''); res(f); }, () => res(null)));
async function walk(entry, out, st) {
  if (entry.isFile) { const f = await entryFile(entry); if (f) out.push(f); }
  else if (entry.isDirectory) { st.dirs++; for (const c of await readAll(entry)) await walk(c, out, st); }
}
async function fromDataTransfer(dt) {
  // grab entries synchronously: the DataTransfer is emptied after the first await
  const entries = [...(dt.items || [])].filter(i => i.kind === 'file').map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  if (!entries.length) return { files: [...dt.files], dirs: 0 };
  const out = [], st = { dirs: 0 };
  setStatus('Scanning folders…');
  for (const e of entries) await walk(e, out, st);
  return { files: out, dirs: st.dirs };
}
function intake(files, dirs) {
  const ok = [], skipped = { hidden: 0, other: 0, empty: 0 };
  for (const f of files) { const c = classify(f); if (c === 'ok') ok.push(f); else skipped[c]++; }
  const nSkip = skipped.hidden + skipped.other + skipped.empty;
  dirs = new Set(ok.map(f => relPath(f).split('/').slice(0, -1).join('/')).filter(Boolean)).size;  // folders that hold images
  const msg = `Found ${ok.length} image${ok.length === 1 ? '' : 's'}${dirs ? ` in ${dirs} folder${dirs === 1 ? '' : 's'}` : ''}` +
    (nSkip ? ` (${nSkip} skipped${skipped.other ? ` · ${skipped.other} not images` : ''}${skipped.hidden ? ` · ${skipped.hidden} hidden/system` : ''})` : '');
  setStatus(msg);
  if (!ok.length) { toast('No images found'); return; }
  send(ok, msg);
}
function setStatus(t) { const el = $('#upStatus'); el.textContent = t || ''; el.classList.toggle('hidden', !t); }
['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', async e => { const { files, dirs } = await fromDataTransfer(e.dataTransfer); intake(files, dirs); });
document.addEventListener('dragover', e => e.preventDefault()); document.addEventListener('drop', e => e.preventDefault());
input.onchange = () => { intake([...input.files], 0); input.value = ''; };
folderInput.onchange = () => { intake([...folderInput.files], 0); folderInput.value = ''; };
if (isIOS || !('webkitdirectory' in folderInput)) $('#folderBtn').classList.add('hidden');  // iOS Safari can't pick folders
else $('#folderBtn').onclick = () => folderInput.click();
$('#filesBtn').onclick = () => input.click();

let uploading = false;
function send(files, label) {
  if (!files.length) return;
  if (uploading) { toast('An upload is already running — wait for it to finish'); return; }
  uploading = true;
  const CHUNK = 12, CONC = 3, MAX_CHUNK_BYTES = 60 * 1024 * 1024;
  const chunks = []; let cur = [], curB = 0;
  for (const f of files) { if (cur.length && (cur.length >= CHUNK || curB + f.size > MAX_CHUNK_BYTES)) { chunks.push(cur); cur = []; curB = 0; } cur.push(f); curB += f.size; }
  if (cur.length) chunks.push(cur);
  const box = document.createElement('div'); box.className = 'upbox';
  box.innerHTML = `<div class="progress"><i></i></div><div class="uptxt"></div>`;
  $('#batch').before(box);
  const total = files.reduce((a, f) => a + f.size, 0) || 1, loaded = new Array(chunks.length).fill(0);
  let ok = 0, bad = 0, rejected = [], doneChunks = 0, next = 0, retried = 0;
  let finished = false;
  const paint = () => { if (finished) return; const bar = $('i', box); if (bar) bar.style.width = (loaded.reduce((a, b) => a + b, 0) / total * 100).toFixed(1) + '%';
    $('.uptxt', box).textContent = `Batch ${doneChunks}/${chunks.length} · ${ok}/${files.length} uploaded${bad ? ` · ${bad} failed` : ''}${retried ? ` · ${retried} retried` : ''}`; };
  let raf = 0; const schedulePaint = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; paint(); }); };
  const post = (i) => new Promise(res => {
    const c = chunks[i], fd = new FormData();
    c.forEach(f => fd.append('files', f, relPath(f))); fd.append('mode', uploadMode);
    const x = new XMLHttpRequest(); x.open('POST', '/api/upload'); x.timeout = 10 * 60 * 1000;
    x.upload.onprogress = e => { loaded[i] = e.loaded / (e.total || 1) * c.reduce((a, f) => a + f.size, 0); schedulePaint(); };
    x.onload = () => { let r = null; if (x.status === 200) { try { r = JSON.parse(x.responseText); } catch (_) {} } res(r); };
    x.onerror = x.ontimeout = () => res(null);
    x.send(fd);
  });
  const worker = async () => {
    while (next < chunks.length) {
      const i = next++;
      let r = await post(i);
      if (!r) { retried++; loaded[i] = 0; schedulePaint(); await new Promise(z => setTimeout(z, 1500)); r = await post(i); }
      const sz = chunks[i].reduce((a, f) => a + f.size, 0); loaded[i] = sz;
      if (r) { r.ids.forEach(id => batchIds.add(id)); ok += r.ids.length; bad += r.rejected.length; rejected.push(...r.rejected); }
      else bad += chunks[i].length;
      doneChunks++; schedulePaint();
      if (doneChunks % 3 === 0) poll();
    }
  };
  paint();
  Promise.all(Array.from({ length: Math.min(CONC, chunks.length) }, worker)).then(() => {
    uploading = false; paint(); finished = true; if (raf) cancelAnimationFrame(raf); $('.progress', box).remove();
    $('.uptxt', box).textContent = `${label ? label + ' — ' : ''}${ok} queued for redaction${bad ? `, ${bad} failed` : ''}.` +
      (rejected.length ? ' Skipped: ' + rejected.slice(0, 3).map(r => `${r.name} (${r.error})`).join(', ') + (rejected.length > 3 ? '…' : '') : '');
    toast(`${ok} photo${ok === 1 ? '' : 's'} queued${bad ? ` · ${bad} failed` : ''}`); poll();
  });
}
function renderBatch() {
  const ids = [...batchIds].sort((a, b) => b - a).slice(0, 60);
  const byId = Object.fromEntries(items.map(i => [i.id, i]));
  $('#batch').innerHTML = ids.map(id => { const it = byId[id];
    const done = it && it.status === 'done';
    return `<div class="bi ${done ? '' : 'pending'}">${done ? `<img src="/api/images/${id}/file/thumb-after?v=${it.v}" loading="lazy">` : ''}
      <span class="st">${!it ? '…' : it.status === 'done' ? `${it.faces} 🙂` : it.status === 'error' ? '⚠︎' : '⏳'}</span></div>`; }).join('');
}

/* ---------- gallery ---------- */
function tile(it) {
  const showb = view === 'before';
  const ready = it.status === 'done';
  const vid = it.kind === 'video';
  const chips = [it.ig || it.source === 'saved' ? `<span class="chip ig">IG${it.ig && it.ig.owner ? ' @' + esc(it.ig.owner) : ''}</span>` : '',
    vid ? `<span class="chip">▶ ${it.duration ? fmtDur(it.duration) : 'video'}</span>` : '',
    ready ? `<span class="chip">${it.faces} face${it.faces === 1 ? '' : 's'}</span>` : '',
    ready ? `<span class="chip">${it.mode === 'box' ? '■ box' : '◌ blur'}</span>` : '',
    it.status === 'error' ? `<span class="chip" style="color:var(--bad)">error</span>` : ''].join('');
  return `<div class="tile ${showb ? 'showb' : ''}" data-id="${it.id}">
    ${ready ? `<img class="a" src="/api/images/${it.id}/file/thumb-after?v=${it.v}" loading="lazy" alt="">
    <img class="b" src="/api/images/${it.id}/file/thumb-before?v=${it.v}" loading="lazy" alt="">` : ''}
    ${ready ? `<span class="badge">${showb ? 'BEFORE' : 'AFTER'}</span><button class="open" aria-label="Open"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7"/></svg></button>` : ''}
    ${ready && vid ? '<span class="play">▶</span>' : ''}
    ${ready ? '<button class="dlb" aria-label="Save to device">⬇</button>' : ''}
    ${!ready && it.status !== 'error' ? `<div class="wait"><div><div class="spin"></div>${it.status}${vid && it.status === 'processing' && it.progress ? ` ${Math.round(it.progress * 100)}%` : ''}</div></div>` : ''}
    ${it.status === 'error' ? `<div class="wait"><div>⚠︎<br>${(it.error || 'failed').slice(0, 60)}</div></div>` : ''}
    <div class="chips">${chips}</div></div>`;
}
function renderGrid() {
  $('#grid').innerHTML = items.map(tile).join('');
  $('#empty').classList.toggle('hidden', items.length > 0);
  const done = items.filter(i => i.status === 'done').length;
  $('#gallerySub').textContent = `${items.length} of last 100 · tap to flip before/after · ⤢ to open`;
}
$('#grid').addEventListener('click', e => {
  const t = e.target.closest('.tile'); if (!t) return;
  const it = items.find(i => i.id == t.dataset.id);
  if (e.target.closest('.dlb')) return saveImage(it);
  if (e.target.closest('.open') || (it.kind === 'video' && e.target.closest('.play'))) return openViewer(it);
  if (it.status !== 'done') return;
  t.classList.toggle('showb'); $('.badge', t).textContent = t.classList.contains('showb') ? 'BEFORE' : 'AFTER';
});
// long-press opens viewer too
let lp; $('#grid').addEventListener('touchstart', e => { const t = e.target.closest('.tile'); if (!t) return;
  lp = setTimeout(() => { const it = items.find(i => i.id == t.dataset.id); if (it && it.status === 'done') openViewer(it); }, 550); }, { passive: true });
['touchend', 'touchmove', 'touchcancel'].forEach(ev => $('#grid').addEventListener(ev, () => clearTimeout(lp), { passive: true }));
seg($('#viewMode'), view, v => { view = v; localStorage.setItem('fo.view', v); renderGrid(); });

let sig = '';
let photoPend = 0, docPend = 0;
function setPill() {
  const n = photoPend + docPend;
  $('#queuePill').classList.toggle('hidden', !n);
  $('#queueText').textContent = `${n} processing`;
}
async function loadGallery() {
  try { const d = await api('/api/images'); const ns = JSON.stringify(d.items.map(i => [i.id, i.status, i.v, i.progress]));
    items = d.items; if (ns !== sig) { sig = ns; renderGrid(); renderBatch(); }
    photoPend = (d.counts.queued || 0) + (d.counts.processing || 0);
    setPill();
    return photoPend;
  } catch (e) { return photoPend; }
}
let pollT; async function poll() { clearTimeout(pollT); const pend = await loadGallery(); const dpend = await loadDocs(); pollT = setTimeout(poll, (pend || dpend) ? 1500 : 15000); }

/* ---------- viewer ---------- */
let cur = null;
function setX(p) { $('#cmp').style.setProperty('--x', p + '%'); $('#cmpRange').value = p; rangeFill($('#cmpRange')); }
function openViewer(it) {
  cur = it; const v = $('#viewer'); v.classList.remove('hidden');
  const vid = it.kind === 'video'; v.classList.toggle('isvid', vid);
  if (vid) { const el = $('#vVideo'); el.poster = `/api/images/${it.id}/file/after?v=${it.v}`; vSide = 'after';
    el.src = `/api/images/${it.id}/file/video-after?v=${it.v}`; seg($('#vSide'), 'after', setSide); }
  else { $('#cmpBefore').src = `/api/images/${it.id}/file/before?v=${it.v}`; $('#cmpAfter').src = `/api/images/${it.id}/file/after?v=${it.v}`; }
  $('#vTitle').textContent = it.name || `#${it.id}`;
  $('#vMeta').textContent = [`${it.faces} face${it.faces === 1 ? '' : 's'}`, it.mode === 'box' ? 'black box' : 'blur',
    it.width ? `${it.width}×${it.height}` : '', vid && it.duration ? fmtDur(it.duration) : '', it.exported ? `exported → ${it.exported}` : 'not exported', it.ig ? `IG @${it.ig.owner}` : ''].filter(Boolean).join(' · ') + (vid ? ' · faces = most in one frame' : '');
  $('#vDownload').href = `/api/images/${it.id}/file/full?v=${it.v}`; vDl = () => saveImage(it);
  $('#vRedo').textContent = it.mode === 'blur' ? 'Redo as box' : 'Redo as blur';
  setX(50); document.body.style.overflow = 'hidden';
}
let vSide = 'after';
function setSide(side) {   // swap before/after video, keeping the playhead
  if (!cur || side === vSide) return; vSide = side; const el = $('#vVideo'); const t = el.currentTime, playing = !el.paused;
  el.src = `/api/images/${cur.id}/file/video-${side}?v=${cur.v}`;
  el.addEventListener('loadedmetadata', () => { try { el.currentTime = t; } catch (_) {} if (playing) el.play().catch(() => {}); }, { once: true });
}
function fmtDur(s) { s = Math.round(s); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; }
function closeViewer() { for (const vv of [$('#vVideo'), $('#rawVid')]) { vv.pause(); vv.removeAttribute('src'); vv.load(); }
  $('#viewer').classList.remove('israw', 'rawimg', 'rawvid', 'saved', 'savedvid'); curSaved = null; $('#viewer').classList.add('hidden'); document.body.style.overflow = ''; cur = null; }
$('#vClose').onclick = closeViewer;
let vDl = null; $('#vDownload').textContent = '⬇ Save'; $('#vDownload').onclick = e => { if (vDl) { e.preventDefault(); vDl(); } };
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeViewer(); });
$('#cmpRange').oninput = e => setX(e.target.value);
(() => { const c = $('#cmp'); let drag = false;
  const at = e => { const r = c.getBoundingClientRect(); const x = (e.touches ? e.touches[0].clientX : e.clientX) - r.left; setX(Math.max(0, Math.min(100, x / r.width * 100))); };
  c.addEventListener('pointerdown', e => { drag = true; c.setPointerCapture(e.pointerId); at(e); });
  c.addEventListener('pointermove', e => drag && at(e));
  c.addEventListener('pointerup', () => drag = false); c.addEventListener('pointercancel', () => drag = false);
})();
$('#vRedo').onclick = async () => { if (!cur) return; const mode = cur.mode === 'blur' ? 'box' : 'blur';
  await api(`/api/images/${cur.id}/reprocess`, { method: 'POST', body: JSON.stringify({ mode, use_defaults: true }) });
  toast(`Re-processing as ${mode === 'box' ? 'black box' : 'blur'}…`); closeViewer(); poll(); };
$('#vDelete').onclick = async () => {
  if (cur && !curSaved && cur.source === 'saved') { if (!confirm('Remove this processed copy from the gallery? The post stays in your Saved library (as unprocessed) and exported files are kept.')) return;
    await api(`/api/images/${cur.id}`, { method: 'DELETE' }); closeViewer(); toast('Removed'); sig = ''; loadGallery(); return; }
  if (curSaved) { if (!confirm('Delete from your Faceoff library? (Doesn\'t touch Instagram or files already exported.)')) return;
    await api('/api/saved/delete', { method: 'POST', body: JSON.stringify({ ids: [curSaved.id] }) }); closeViewer(); toast('Deleted'); savedRefresh(true); return; }
  if (!cur || !confirm('Remove from gallery? The exported file stays in your export folder.')) return;
  await api(`/api/images/${cur.id}`, { method: 'DELETE' }); closeViewer(); toast('Removed'); sig = ''; loadGallery(); };

/* ---------- saved library ---------- */
let curSaved = null;
const SV = { kind: localStorage.getItem('fo.skind') || 'all', owner: '', sort: localStorage.getItem('fo.ssort') || 'newest', mode: 'grid', usort: localStorage.getItem('fo.usort') || 'count', q: '', filter: 'all', items: [], total: 0, loading: false, selecting: false, sel: new Set(), last: -1, view: localStorage.getItem('fo.sview') || 'after', timer: 0, active: false };
const sdate = t => { if (!t) return ''; const d = new Date(t * 1000); const n = new Date();
  return d.toLocaleDateString([], d.getFullYear() === n.getFullYear() ? { month: 'short', day: 'numeric' } : { year: '2-digit', month: 'short', day: 'numeric' }); };
const esc = t => String(t || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
function sTile(it, i) {
  const p = it.proc, done = p && p.status === 'done', work = p && (p.status === 'queued' || p.status === 'processing');
  const src = done && SV.view === 'after' ? `/api/images/${p.id}/file/thumb-after?v=${p.v}` : `/api/saved/${it.id}/file/thumb`;
  const tl = [it.kind === 'video' ? `<span>▶ ${it.duration ? fmtDur(it.duration) : ''}</span>` : '',
    done ? `<span class="p">✓ ${p.mode}</span>` : '', work ? `<span class="w">⏳${p.status === 'processing' && p.progress ? ' ' + Math.round(p.progress * 100) + '%' : ''}</span>` : '',
    p && p.status === 'error' ? '<span class="e">⚠︎</span>' : ''].join('');
  return `<div class="st${SV.sel.has(it.id) ? ' sel' : ''}" data-id="${it.id}" data-i="${i}"><img src="${src}" loading="lazy" alt="" onerror="this.style.opacity=0">
    <div class="tl">${tl}</div>${it.index > 1 ? `<span class="tr">${it.index}</span>` : ''}<i class="ck"></i><button class="dlb" aria-label="Save to device">⬇</button>
    <div class="o"><b>@${esc(it.owner || '?')}</b> <span>· ${sdate(it.saved_at)}</span></div></div>`;
}
function sRender() {
  $('#sgrid').innerHTML = SV.items.map(sTile).join('');
  $('#sgrid').classList.toggle('selecting', SV.selecting);
  $('#sEmpty').classList.toggle('hidden', SV.items.length > 0 || SV.loading);
  $('#sMore').textContent = SV.items.length < SV.total ? 'Loading…' : SV.total ? `${SV.total} item${SV.total === 1 ? '' : 's'}` : '';
  sSelBar();
}
function sCounts(c) { $$('#savedFilters button[data-f]').forEach(b => { $('i', b).textContent = c[b.dataset.f] ?? ''; });
  if (!SV.owner) $('#savedSub').textContent = `${c.all} saved · ${c.processed} processed · ${c.unprocessed} unprocessed`;
  else { $('#savedSub').textContent = `${c.all} saved from this account`; }
  if (SV.owner) $('#ownerChip').innerHTML = `${avatar(SV.owner, 30)}<b>@${esc(SV.owner)}</b><span>${c.all} · ${c.processed} done</span><button class="x" aria-label="All accounts">✕</button>`; }
const sQS = (kind = SV.kind) => `filter=${SV.filter}&sort=${SV.sort}&kind=${kind}&owner=${encodeURIComponent(SV.owner)}`;
function avatar(name, px = 42) { let h = 0; for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
  const l = name === '_unknown' ? '?' : (name.replace(/[^a-z0-9]/gi, '')[0] || '?');
  return `<span class="av" style="background:hsl(${h} 55% 42%);width:${px}px;height:${px}px;font-size:${Math.round(px * .4)}px">${esc(l)}</span>`; }
let uT = 0;
async function loadUsers() {
  const d = await api(`/api/saved/owners?sort=${SV.usort}&q=${encodeURIComponent(SV.q)}`);
  $('#userList').innerHTML = d.owners.map(o => `<div class="urow" data-o="${esc(o.name)}">${avatar(o.name)}
    <div class="ui"><b>${o.name === '_unknown' ? 'Unknown account' : '@' + esc(o.name)}</b><span>${o.n} save${o.n === 1 ? '' : 's'}${o.videos ? ` · ${o.videos} video${o.videos === 1 ? '' : 's'}` : ''}${o.processed ? ` · ${o.processed} done` : ''} · latest ${sdate(o.latest)}</span></div>
    ${o.thumb_id ? `<img src="/api/saved/${o.thumb_id}/file/thumb" loading="lazy" alt="">` : ''}</div>`).join('') || '<p class="hint">No accounts match.</p>';
  $('#savedSub').textContent = `${d.owners.length} account${d.owners.length === 1 ? '' : 's'}`;
}
function setMode(m) { SV.mode = m; seg($('#savedMode'), m); $('#tab-saved').classList.toggle('saved-users', m === 'users');
  $('#usersView').classList.toggle('hidden', m !== 'users'); $('#ownerChip').classList.toggle('hidden', m === 'users' || !SV.owner);
  if (m === 'users') { sSetSelecting(false); loadUsers(); } else sLoad(true); }
function setOwner(o) { SV.owner = o; SV.last = -1; window.scrollTo({ top: 0 }); setMode('grid'); }
seg($('#savedMode'), 'grid', setMode);
seg($('#userSort'), SV.usort, v => { SV.usort = v; localStorage.setItem('fo.usort', v); loadUsers(); });
$('#userQ').oninput = e => { SV.q = e.target.value.trim(); clearTimeout(uT); uT = setTimeout(loadUsers, 200); };
$('#userList').onclick = e => { const r = e.target.closest('.urow'); if (r) setOwner(r.dataset.o); };
$('#ownerChip').onclick = e => { if (e.target.closest('.x')) { SV.owner = ''; $('#ownerChip').classList.add('hidden'); sLoad(true); } };
$('#savedSort').value = SV.sort;
function setKind(k) { SV.kind = k; localStorage.setItem('fo.skind', k); seg($('#savedKind'), k); $('#feedBtn').classList.toggle('hidden', k !== 'video'); SV.last = -1; sLoad(true); }
seg($('#savedKind'), SV.kind, setKind); $('#feedBtn').classList.toggle('hidden', SV.kind !== 'video');
$('#feedBtn').onclick = () => openFeed(null);

/* ---------- video feed (Reels-style) ---------- */
const FD = { ids: [], meta: new Map(), muted: true, io: null, active: -1, open: false };
const igUrl = it => `https://www.instagram.com/p/${encodeURIComponent(it.shortcode)}/${it.index > 1 ? '?img_index=' + it.index : ''}`;
async function openFeed(startId) {
  const d = await api(`/api/saved/ids?${sQS('video')}`);
  FD.ids = d.ids; if (!FD.ids.length) { toast('No videos here'); return; }
  const start = Math.max(0, startId ? FD.ids.indexOf(startId) : 0);
  const sc = $('#feedScroll');
  sc.innerHTML = FD.ids.map((id, i) => `<section class="slide" data-i="${i}" data-id="${id}"><span class="paused">▶</span><i class="bar"></i></section>`).join('');
  $('#feed').classList.remove('hidden'); document.body.style.overflow = 'hidden'; FD.open = true; FD.active = -1;
  if (!$('#feed .fcount')) $('#feed').insertAdjacentHTML('beforeend', '<div class="fcount"></div>');
  history.pushState({ feed: 1 }, '');
  sc.scrollTop = start * sc.clientHeight;
  FD.io && FD.io.disconnect();
  FD.io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting && e.intersectionRatio >= 0.6) feedActivate(+e.target.dataset.i); }),
    { root: sc, threshold: [0.6] });
  $$('.slide', sc).forEach(s => FD.io.observe(s));
  feedActivate(start);
}
async function feedMeta(idxs) {
  const need = idxs.filter(i => i >= 0 && i < FD.ids.length && !FD.meta.has(FD.ids[i])).map(i => FD.ids[i]);
  if (need.length) { const d = await api(`/api/saved/batch?ids=${need.join(',')}`); d.items.forEach(it => FD.meta.set(it.id, it)); }
}
function feedSlide(i) { return $(`#feedScroll .slide[data-i="${i}"]`); }
function feedMount(i, active) {
  const s = feedSlide(i); if (!s) return; const it = FD.meta.get(FD.ids[i]); if (!it) return;
  let v = $('video', s);
  if (!v) {
    s.style.backgroundImage = `url(/api/saved/${it.id}/file/thumb)`;
    v = document.createElement('video'); v.loop = true; v.playsInline = true; v.setAttribute('playsinline', ''); v.setAttribute('webkit-playsinline', '');
    v.preload = 'metadata'; v.muted = FD.muted; v.poster = `/api/saved/${it.id}/file/thumb`; v.src = `/api/saved/${it.id}/file/original`;
    v.ontimeupdate = () => { const b = $('.bar', s); if (b && v.duration) b.style.width = (v.currentTime / v.duration * 100) + '%'; };
    s.prepend(v);
    s.insertAdjacentHTML('beforeend', `<div class="meta"><button class="who" data-o="${esc(it.owner || '_unknown')}">@${esc(it.owner || 'unknown')}</button><span class="when">${sdate(it.saved_at)}${it.index > 1 ? ' · item ' + it.index : ''}</span>
      ${it.caption ? `<div class="cap">${esc(it.caption)}</div>` : ''}<a class="iglink" href="${igUrl(it)}" target="_blank" rel="noopener">Open on Instagram ↗</a></div>`);
  }
  if (active) { v.preload = 'auto'; v.muted = FD.muted; const p = v.play(); if (p) p.then(() => s.classList.remove('is-paused')).catch(() => s.classList.add('is-paused')); }
  else { v.pause(); }
}
function feedUnmount(i) { const s = feedSlide(i); const v = s && $('video', s);
  if (v) { v.pause(); v.removeAttribute('src'); v.load(); v.remove(); const m = $('.meta', s); if (m) m.remove(); } }
async function feedActivate(i) {
  if (!FD.open || i === FD.active) return; FD.active = i;
  $('#feed .fcount').textContent = `${i + 1} / ${FD.ids.length}`;
  await feedMeta([i, i + 1, i - 1, i + 2]);
  if (FD.active !== i) return;
  // keep at most 3 <video> elements alive: current + neighbours (memory on phones / Pi bandwidth)
  $$('#feedScroll .slide').forEach(s => { const k = +s.dataset.i; if (Math.abs(k - i) > 1 && $('video', s)) feedUnmount(k); });
  feedMount(i, true); feedMount(i + 1, false); feedMount(i - 1, false);
}
function closeFeed(fromPop) {
  if (!FD.open) return; FD.open = false; FD.io && FD.io.disconnect();
  $$('#feedScroll .slide').forEach(s => feedUnmount(+s.dataset.i)); $('#feedScroll').innerHTML = '';
  $('#feed').classList.add('hidden'); document.body.style.overflow = '';
  if (!fromPop && history.state && history.state.feed) history.back();
}
window.addEventListener('popstate', () => closeFeed(true));
$('#feedClose').onclick = () => closeFeed(false);
$('#feedMute').onclick = () => { FD.muted = !FD.muted; $('#feedMute').textContent = FD.muted ? '🔇' : '🔊';
  $$('#feedScroll video').forEach(v => v.muted = FD.muted); const s = feedSlide(FD.active), v = s && $('video', s); if (v && !FD.muted) v.play().catch(() => {}); };
$('#feedScroll').addEventListener('click', e => {
  const who = e.target.closest('.who');
  if (who) { const o = who.dataset.o; closeFeed(false); setOwner(o); setTimeout(() => openFeed(null), 300); return; }
  if (e.target.closest('a')) return;
  const s = e.target.closest('.slide'); const v = s && $('video', s); if (!v) return;
  if (v.paused) { v.play().catch(() => {}); s.classList.remove('is-paused'); } else { v.pause(); s.classList.add('is-paused'); }
});
document.addEventListener('keydown', e => { if (!FD.open) return; const sc = $('#feedScroll');
  if (e.key === 'Escape') closeFeed(false);
  else if (e.key === 'ArrowDown' || e.key === 'j') { e.preventDefault(); sc.scrollBy({ top: sc.clientHeight, behavior: 'smooth' }); }
  else if (e.key === 'ArrowUp' || e.key === 'k') { e.preventDefault(); sc.scrollBy({ top: -sc.clientHeight, behavior: 'smooth' }); }
  else if (e.key === ' ') { e.preventDefault(); const s = feedSlide(FD.active); s && s.click(); } }, true);
$('#savedSort').onchange = e => { SV.sort = e.target.value; localStorage.setItem('fo.ssort', SV.sort); SV.last = -1; sLoad(true); };
async function sLoad(reset) {
  if (SV.loading) return; SV.loading = true;
  try {
    const off = reset ? 0 : SV.items.length;
    const d = await api(`/api/saved?${sQS()}&offset=${off}&limit=60`);
    SV.items = reset ? d.items : SV.items.concat(d.items.filter(x => !SV.items.some(y => y.id === x.id)));
    SV.total = d.total; sCounts(d.counts);
  } catch (e) { toast(e.message); }
  SV.loading = false; sRender(); sObs();
}
async function savedRefresh(full) {   // refresh what's loaded (status badges) without losing scroll position
  if (full) return sLoad(true);
  const n = Math.min(200, Math.max(60, SV.items.length));
  if (SV.mode === 'users') { loadUsers(); return 0; }
  try { const d = await api(`/api/saved?${sQS()}&offset=0&limit=${n}`);
    const sig = j => JSON.stringify(j.map(x => [x.id, x.proc && x.proc.status, x.proc && x.proc.v, x.proc && x.proc.progress]));
    const changed = sig(d.items) !== sig(SV.items.slice(0, d.items.length)) || d.total !== SV.total;
    SV.items = d.items.concat(SV.items.slice(d.items.length).filter(x => !d.items.some(y => y.id === x.id)));
    SV.total = d.total; sCounts(d.counts); if (changed) sRender();
    return d.counts.working;
  } catch (e) { return 0; }
}
const sIO = new IntersectionObserver(es => { if (es.some(e => e.isIntersecting) && SV.items.length < SV.total) sLoad(false); }, { rootMargin: '600px' });
function sObs() { sIO.disconnect(); if (SV.active) sIO.observe($('#sMore')); }
async function savedOpen() { SV.active = true; await sLoad(true); sTick(); }
function savedLeave() { SV.active = false; clearTimeout(SV.timer); sIO.disconnect(); sSetSelecting(false); }
async function sTick() { clearTimeout(SV.timer); if (!SV.active) return; const w = await savedRefresh(false); SV.timer = setTimeout(sTick, w ? 2500 : 20000); }
$('#savedFilters').onclick = e => { const b = e.target.closest('button[data-f]'); if (!b) return;
  $$('#savedFilters button[data-f]').forEach(x => x.classList.toggle('on', x === b)); SV.filter = b.dataset.f; SV.last = -1; window.scrollTo({ top: 0 }); sLoad(true); };
seg($('#savedView'), SV.view, v => { SV.view = v; localStorage.setItem('fo.sview', v); sRender(); });
function sSetSelecting(on) { SV.selecting = on; if (!on) { SV.sel.clear(); SV.last = -1; } $('#selBtn').textContent = on ? 'Cancel' : 'Select'; sRender(); }
function sSelBar() { const n = SV.sel.size; $('#selbar').classList.toggle('hidden', !SV.selecting);
  $('#selCount').textContent = `${n} selected`; ['#selBlur', '#selBox', '#selDel', '#selSave'].forEach(k => $(k).disabled = !n);
  $('#selAll').textContent = `Select all (${SV.total})`; }
$('#selBtn').onclick = () => sSetSelecting(!SV.selecting);
$('#selDone').onclick = () => sSetSelecting(false);
$('#selNone').onclick = () => { SV.sel.clear(); sRender(); };
$('#selAll').onclick = async () => { const d = await api(`/api/saved/ids?${sQS()}`); d.ids.forEach(i => SV.sel.add(i)); sRender(); };
async function sProcess(ids, mode) {
  const r = await api('/api/saved/process', { method: 'POST', body: JSON.stringify({ ids, mode }) });
  toast(`${r.queued} photo${r.queued === 1 ? '' : 's'} queued for ${mode === 'box' ? 'black boxes' : 'blur'} → exports to your export folder` +
    (r.skipped_videos ? ` · ${r.skipped_videos} video${r.skipped_videos === 1 ? '' : 's'} skipped (videos stay raw)` : ''), 4000); poll(); return r;
}
$('#selBlur').onclick = async () => { await sProcess([...SV.sel], 'blur'); sSetSelecting(false); sTick(); };
$('#selBox').onclick = async () => { await sProcess([...SV.sel], 'box'); sSetSelecting(false); sTick(); };
$('#selDel').onclick = async () => { const n = SV.sel.size;
  if (!confirm(`Delete ${n} item${n === 1 ? '' : 's'} from your Faceoff library?\n\nThis does not delete anything from Instagram or files already exported.`)) return;
  const r = await api('/api/saved/delete', { method: 'POST', body: JSON.stringify({ ids: [...SV.sel] }) });
  toast(`Deleted ${r.deleted}`); sSetSelecting(false); sLoad(true); };
let sLP = 0, sLPFired = false;
$('#sgrid').addEventListener('touchstart', e => { const t = e.target.closest('.st'); if (!t) return; sLPFired = false;
  sLP = setTimeout(() => { sLPFired = true; if (!SV.selecting) sSetSelecting(true); SV.sel.add(+t.dataset.id); SV.last = +t.dataset.i; sRender();
    if (navigator.vibrate) navigator.vibrate(15); }, 500); }, { passive: true });
['touchend', 'touchmove', 'touchcancel'].forEach(ev => $('#sgrid').addEventListener(ev, () => clearTimeout(sLP), { passive: true }));
$('#sgrid').addEventListener('contextmenu', e => { if (e.target.closest('.st')) e.preventDefault(); });
$('#sgrid').addEventListener('click', e => {
  const t = e.target.closest('.st'); if (!t) return;
  if (sLPFired) { sLPFired = false; return; }
  if (e.target.closest('.dlb') && !SV.selecting) { const it = SV.items[+t.dataset.i]; return saveSaved(it); }
  const id = +t.dataset.id, i = +t.dataset.i;
  if (!SV.selecting && (e.shiftKey || e.metaKey || e.ctrlKey)) sSetSelecting(true);
  if (SV.selecting) {
    if (e.shiftKey && SV.last >= 0) { const [a, b] = [Math.min(SV.last, i), Math.max(SV.last, i)]; for (let k = a; k <= b; k++) SV.sel.add(SV.items[k].id); }
    else { SV.sel.has(id) ? SV.sel.delete(id) : SV.sel.add(id); }
    SV.last = i; sRender(); return;
  }
  if (SV.items[i].kind === 'video') return openFeed(SV.items[i].id);
  openSaved(SV.items[i]);
});
function openSaved(it) {
  const p = it.proc, vEl = $('#viewer');
  if (p && p.status === 'done') {
    openViewer({ id: p.id, v: p.v, kind: it.kind, faces: p.faces, mode: p.mode, width: it.width, height: it.height, duration: it.duration,
      name: `@${it.owner} · ${it.shortcode}${it.index > 1 ? ' #' + it.index : ''}`, exported: p.exported, ig: { owner: it.owner } });
  } else {
    cur = null; vEl.classList.remove('hidden', 'isvid'); vEl.classList.add('israw', it.kind === 'video' ? 'rawvid' : 'rawimg');
    const url = `/api/saved/${it.id}/file/original`;
    if (it.kind === 'video') { $('#rawVid').poster = `/api/saved/${it.id}/file/thumb`; $('#rawVid').src = url; } else $('#rawImg').src = url;
    $('#vTitle').textContent = `@${it.owner} · ${it.shortcode}${it.index > 1 ? ' #' + it.index : ''}`;
    $('#vMeta').textContent = [it.kind === 'video' ? 'Video · kept raw (not redacted)' : p && p.status === 'error' ? `⚠︎ ${p.error || 'failed'}` : p ? `${p.status}…` : 'Not processed yet',
      it.width ? `${it.width}×${it.height}` : '', it.duration ? fmtDur(it.duration) : '', `saved ${sdate(it.saved_at)}`, it.caption ? `“${it.caption.slice(0, 80)}”` : ''].filter(Boolean).join(' · ');
    $('#vDownload').href = `/api/saved/${it.id}/file/download`; vDl = () => saveSaved(it); document.body.style.overflow = 'hidden';
  }
  vEl.classList.add('saved'); vEl.classList.toggle('savedvid', it.kind === 'video'); curSaved = it;
}
$('#vBlur').onclick = async () => { if (!curSaved) return; await sProcess([curSaved.id], 'blur'); closeViewer(); sTick(); };
$('#vBox').onclick = async () => { if (!curSaved) return; await sProcess([curSaved.id], 'box'); closeViewer(); sTick(); };

/* ---------- settings ---------- */
let saveT; const save = patch => { Object.assign(S, patch); clearTimeout(saveT);
  saveT = setTimeout(async () => { try { const r = await api('/api/settings', { method: 'POST', body: JSON.stringify(S) }); S = r.settings; toast('Saved', 1200); } catch (e) { toast(e.message); } }, 350); };
async function loadSettings() {
  const d = await api('/api/settings'); S = d.settings;
  if (!uploadMode) uploadMode = S.default_mode;
  seg($('#uploadMode'), uploadMode, v => uploadMode = v);
  $('#exportPath').textContent = d.export.label || d.export.path;
  $('#exportStatus').innerHTML = d.export.writable ? `Exporting to <code>${d.export.label || d.export.path}</code> ✓` : `<span style="color:var(--bad)">Export folder <code>${d.export.path}</code> isn't writable — check the volume mount.</span>`;
  const ige = d.ig_export || {};
  $('#igExportStatus').innerHTML = ige.enabled ? (ige.writable ? `Raw Instagram saves are copied to <code>${esc(ige.label || ige.path)}</code> ✓ (processed copies go to <code>${esc(d.export.label || d.export.path)}</code>)` : `<span style="color:var(--bad)">IG folder <code>${esc(ige.label || ige.path)}</code> isn't writable.</span>`)
    : `Raw Instagram saves stay inside Faceoff (no IG folder configured). Processed copies go to <code>${esc(d.export.label || d.export.path)}</code>.`;
  $('#igExpRow').classList.toggle('hidden', !ige.enabled);
  $('#setIgExport').checked = S.ig_export; $('#setIgExport').onchange = e => save({ ig_export: e.target.checked });
  renderExt(d.extension || {});
  $('#workers').textContent = `${d.workers} worker${d.workers > 1 ? 's' : ''}${d.auth ? ' · password on' : ''}`;
  seg($('#setMode'), S.default_mode, v => { save({ default_mode: v }); uploadMode = v; seg($('#uploadMode'), v); });
  seg($('#setSens'), S.sensitivity, v => save({ sensitivity: v }));
  const pad = $('#setPad'); pad.value = Math.round(S.padding * 100); $('#padVal').textContent = pad.value + '%'; rangeFill(pad);
  pad.oninput = () => { $('#padVal').textContent = pad.value + '%'; rangeFill(pad); save({ padding: pad.value / 100 }); };
  const st = $('#setStr'); st.value = S.blur_strength; const sl = ['', 'Light', 'Medium', 'Strong', 'Heavy', 'Max'];
  $('#strVal').textContent = sl[st.value]; rangeFill(st);
  st.oninput = () => { $('#strVal').textContent = sl[st.value]; rangeFill(st); save({ blur_strength: +st.value }); };
  $('#setExport').checked = S.auto_export; $('#setExport').onchange = e => save({ auto_export: e.target.checked });
  $('#setPrefix').checked = S.export_folder_prefix; $('#setPrefix').onchange = e => save({ export_folder_prefix: e.target.checked });
  $('#setSub').checked = S.export_subfolders; $('#setSub').onchange = e => save({ export_subfolders: e.target.checked });
  $('#igEnabled').checked = S.ig_enabled; $('#igEnabled').onchange = e => { save({ ig_enabled: e.target.checked }); setTimeout(loadIG, 800); };
  const setSel = (el, v, k, num) => { if (![...el.options].some(o => o.value == v)) el.add(new Option(v, v)); el.value = v; el.onchange = () => save({ [k]: num ? +el.value : el.value }); };
  setSel($('#igInterval'), S.ig_interval_hours, 'ig_interval_hours', true);
  setSel($('#igCap'), S.ig_per_run_cap, 'ig_per_run_cap', true);
  setSel($('#igFirst'), S.ig_first_run, 'ig_first_run');
  setSel($('#igMode'), S.ig_mode, 'ig_mode');
}
function renderExt(x) {
  const p = $('#extPause');
  p.className = 'extpause ' + (x.paused ? 'on' : 'off');
  p.innerHTML = x.paused
    ? `<b>⏸ Instagram sync PAUSED</b><span>${esc(x.reason || '')}</span><span>The extension (v2) checks this before every Instagram request and does nothing while paused.</span><button class="btn" id="extResume">Resume sync…</button>`
    : `<b>▶ Instagram sync active</b><span>New-saves check every ${x.limits.new_check_min_h}–${x.limits.new_check_max_h} h while instagram.com is open and visible. Backfill: ${x.backfill ? `on, 1 page per ${x.limits.page_min_s}–${x.limits.page_max_s} s, max ${x.limits.pages_per_day}/day` : 'off'}. Any Instagram warning auto-pauses for ${x.limits.stop_hours} h.</span>
       <label class="row between"><span>Allow slow backfill</span><input type="checkbox" id="extBf" ${x.backfill ? 'checked' : ''}></label><button class="btn ghost danger" id="extStop">Pause now</button>`;
  const post = async b => { await api('/api/ext/pause', { method: 'POST', body: JSON.stringify(b) }); loadSettings(); };
  if ($('#extResume')) $('#extResume').onclick = () => confirm('Resume Instagram sync? The extension will start checking again (slowly) while you use instagram.com. If Instagram has warned you recently, consider waiting a few days.') && post({ paused: false });
  if ($('#extStop')) $('#extStop').onclick = () => post({ paused: true });
  if ($('#extBf')) $('#extBf').onchange = e => post({ backfill: e.target.checked });
  $('#extKv').innerHTML = [['Paired', x.paired ? `yes <code>${x.token_hint}</code>` : 'no — generate a token'],
    ['Last ingest', x.last ? ago(x.last) : 'never'], ['Total received', x.total || 0]].map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');
  $('#extGen').textContent = x.paired ? 'Rotate token' : 'Generate pairing token';
}
$('#extGen').onclick = async () => {
  const d0 = await api('/api/settings');
  if (d0.extension && d0.extension.paired && !confirm('Rotate the token? The extension stops syncing until you paste the new one.')) return;
  const r = await api('/api/ext/token', { method: 'POST' });
  $('#extTok').value = r.token; $('#extTokBox').classList.remove('hidden'); toast('New token — copy it into the extension');
  loadSettings();
};
$('#extCopy').onclick = async () => { const t = $('#extTok'); t.select();
  try { await navigator.clipboard.writeText(t.value); toast('Copied'); } catch (_) { document.execCommand('copy'); toast('Copied'); } };
const IGL = { ok: ['Connected', 'ok'], running: ['Checking…', 'warn'], pending: ['Pending check', 'warn'], paused: ['Paused', ''],
  no_cookie: ['Not connected', ''], cookie_expired: ['Cookie expired', 'bad'], rate_limited: ['Backing off', 'warn'], challenge: ['Security check', 'bad'], error: ['Error', 'bad'] };
let igT;
async function loadIG() {
  clearTimeout(igT);
  let s; try { s = await api('/api/instagram'); } catch (e) { return; }
  const [label, cls] = IGL[s.status] || [s.status, ''];
  const p = $('#igPill'); p.textContent = label; p.className = 'status ' + cls;
  const msgs = { no_cookie: 'Paste your Instagram session cookie below to pull newly saved posts automatically.',
    cookie_expired: 'Your Instagram session expired or was rejected. Log into instagram.com again and paste a fresh sessionid.' };
  $('#igMsg').textContent = s.message || msgs[s.status] || '';
  if (s.status === 'cookie_expired') $('#igMsg').textContent = (s.message || '') + (s.message && s.message.includes('fresh') ? '' : ' ' + msgs.cookie_expired);
  $('#igMsg').style.color = cls === 'bad' ? 'var(--bad)' : '';
  const kv = [['Account', s.username ? '@' + s.username : '—'], ['Cookie', s.cookie ? `sessionid ${s.cookie.sessionid}` : 'none'],
    ['Last check', ago(s.last_run)], ['Next check', s.next_run ? ago(s.next_run) : (s.enabled ? '—' : 'off')], ['Pulled so far', s.total_pulled]];
  if (s.cookie_fields && s.cookie_fields.length) kv.splice(2, 0, ['Cookie fields', s.cookie_fields.length < 3 ? `${s.cookie_fields.join(', ')} <span style="color:var(--warn)">(full header recommended)</span>` : s.cookie_fields.length + ' fields']);
  if (s.rate_limited_until && s.status === 'rate_limited') kv.push(['Back-off until', ago(s.rate_limited_until)]);
  const lr = s.last_response; if (lr && lr.status && lr.status >= 300) kv.push(['Last IG reply', `${lr.endpoint} → HTTP ${lr.status}${lr.message ? ' · ' + String(lr.message).replace(/</g, '&lt;').slice(0, 60) : ''}`]);
  $('#igKv').innerHTML = kv.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');
  $('#igFloor').textContent = s.min_interval_hours;
  $('#cookieSum').textContent = s.cookie ? 'Replace session cookie' : 'Paste session cookie';
  if (!s.cookie || s.status === 'cookie_expired') $('#cookieBox').open = true;
  const btn = $('#igCheck');
  btn.disabled = !s.cookie || s.status === 'running' || !!s.check_now_allowed_at;
  btn.textContent = s.status === 'running' ? 'Checking…' : s.check_now_allowed_at ? `Check now (available ${ago(s.check_now_allowed_at)})` : 'Check now';
  if ($('#tab-settings').classList.contains('active')) igT = setTimeout(loadIG, s.status === 'running' || s.status === 'pending' ? 3000 : 20000);
}
$('#cookieSave').onclick = async () => { const v = $('#cookieIn').value.trim(); if (!v) return toast('Paste a cookie first');
  try { await api('/api/instagram/cookie', { method: 'POST', body: JSON.stringify({ cookie: v }) }); $('#cookieIn').value = '';
    $('#cookieBox').open = false; toast('Cookie saved securely on the server'); loadIG(); } catch (e) { toast(e.message, 4000); } };
$('#cookieClear').onclick = async () => { if (!confirm('Remove the stored Instagram cookie?')) return; await api('/api/instagram/cookie', { method: 'DELETE' }); toast('Cookie removed'); loadIG(); };
$('#igCheck').onclick = async () => { try { await api('/api/instagram/check', { method: 'POST' }); toast('Checking Instagram…'); setTimeout(loadIG, 600); }
  catch (e) { toast(e.message, 4000); loadIG(); } };

/* ---------- docs (keyword redaction; not the face queue) ---------- */
const DOC_EXT = /\.(pdf|jpe?g|png|webp|heic|heif|tiff?|bmp)$/i;
let docSig = '', docItems = [];
const kwIn = $('#kwIn');
if (kwIn) {
  kwIn.value = localStorage.getItem('fo.keywords') || '';
  kwIn.addEventListener('input', () => localStorage.setItem('fo.keywords', kwIn.value.slice(0, 4000)));
}
function renderDocs() {
  const el = $('#docList');
  if (!el) return;
  if (!docItems.length) {
    el.innerHTML = '<div class="empty"><div class="empty-ic">▤</div><div>No documents yet.</div><p class="hint">Add keywords, then a PDF or a photo of text.</p></div>';
    return;
  }
  el.innerHTML = docItems.map(d => {
    const ready = d.status === 'done';
    const state = d.status === 'error' ? 'Failed' : ready ? `${d.hits} match${d.hits === 1 ? '' : 'es'}` : (d.progress ? `${Math.round(d.progress * 100)}%` : d.status);
    const pages = d.pages ? ` · ${d.pages} page${d.pages === 1 ? '' : 's'}` : '';
    const ocr = d.ocr_pages ? ` · OCR` : '';
    const detail = d.error || d.note || (d.keywords || []).join(' · ');
    return `<article class="dcard">
      <div class="dprev">${ready ? `<img src="/api/docs/${d.id}/file/preview?v=${d.v}" alt="">` : `<div class="dph">${d.status === 'error' ? '⚠︎' : '…'}</div>`}</div>
      <div class="dmeta">
        <b title="${esc(d.name)}">${esc(d.name)}</b>
        <div class="hint">${d.kind === 'pdf' ? 'PDF' : 'Image'} · ${esc(state)}${pages}${ocr}${ready && d.preview_page > 1 ? ` · preview p.${d.preview_page}` : ''}</div>
        <div class="hint">${esc(detail)}</div>
        <div class="dacts">
          ${ready ? `<a class="btn small" href="/api/docs/${d.id}/file/download">Download</a>` : ''}
          <button class="btn ghost small" type="button" data-del="${d.id}">Remove</button>
        </div>
      </div></article>`;
  }).join('');
}
async function loadDocs() {
  try {
    const d = await api('/api/docs');
    docItems = d.items || [];
    docPend = (d.counts && ((d.counts.queued || 0) + (d.counts.processing || 0))) || 0;
    setPill();
    const ns = JSON.stringify(docItems.map(i => [i.id, i.status, i.v, i.hits, i.progress, i.note, i.error]));
    if (ns !== docSig) { docSig = ns; renderDocs(); }
    if (d.limits && $('#docHint')) {
      const ocr = d.limits.ocr ? 'English OCR is on for scans and photos.' : 'OCR is off — only PDFs with selectable text can be searched.';
      $('#docHint').textContent = `PDF or image. ${ocr} Up to ${d.limits.max_pages} PDF pages. Black boxes only — faces are not blurred here. A flattened copy goes to the same export folder as photos; the original stays put. OCR can miss handwriting, tiny type, and rotated or non-English text. Review the file.`;
    }
    return docPend;
  } catch (e) { return docPend; }
}
function setDocStatus(t) { const el = $('#docStatus'); if (!el) return; el.textContent = t || ''; el.classList.toggle('hidden', !t); }
async function takeDocs(files) {
  const words = (kwIn && kwIn.value || '').trim();
  if (!words) { toast('Add keywords first — one phrase per line'); return; }
  const ok = (files || []).filter(f => f && f.size && DOC_EXT.test(f.name || ''));
  if (!ok.length) { toast('Use a PDF or an image of text'); return; }
  let done = 0, bad = 0;
  for (const f of ok) {
    setDocStatus(`Uploading ${done + 1}/${ok.length}…`);
    const fd = new FormData();
    fd.append('file', f, f.name);
    fd.append('keywords', words);
    try { await api('/api/docs', { method: 'POST', body: fd }); done++; }
    catch (e) { bad++; toast(`${f.name}: ${e.message}`, 4000); }
  }
  setDocStatus(bad ? `${done} queued, ${bad} failed.` : `${done} queued. The original stays put.`);
  toast(bad ? `${done} queued · ${bad} failed` : `${done} document${done === 1 ? '' : 's'} queued`);
  loadDocs();
  poll();
}
const docDrop = $('#docDrop'), docInput = $('#docInput');
if (docDrop && docInput) {
  ['dragenter', 'dragover'].forEach(ev => docDrop.addEventListener(ev, e => { e.preventDefault(); e.stopPropagation(); docDrop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => docDrop.addEventListener(ev, e => { e.preventDefault(); e.stopPropagation(); docDrop.classList.remove('over'); }));
  docDrop.addEventListener('drop', e => takeDocs([...e.dataTransfer.files]));
  docInput.onchange = () => { takeDocs([...docInput.files]); docInput.value = ''; };
}
$('#docList').addEventListener('click', async e => {
  const b = e.target.closest('[data-del]');
  if (!b) return;
  if (!confirm('Remove this from Docs? The exported file is kept.')) return;
  try { await api('/api/docs/' + b.dataset.del, { method: 'DELETE' }); toast('Removed. Export kept.'); loadDocs(); }
  catch (err) { toast(err.message, 4000); }
});

/* ---------- boot ---------- */
loadSettings().catch(() => {});
poll();
go((location.hash || '#upload').slice(1).replace(/[^a-z]/g, '') || 'upload');
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
/* ---------- save to device (download only; never touches the export folder / redaction) ---------- */
const IS_IOS = /iP(hone|od|ad)/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
const CAN_SHARE_FILES = (() => { try { return window.isSecureContext && !!navigator.canShare &&
  navigator.canShare({ files: [new File(['x'], 'x.mp4', { type: 'video/mp4' })] }); } catch (_) { return false; } })();
const sExt = it => it.ext || (it.kind === 'video' ? '.mp4' : '.jpg');
const dlName = (owner, sc, idx, tail) => `@${owner && owner !== '_unknown' ? owner : 'instagram'}_${sc || 'post'}${idx > 1 ? '_' + idx : ''}${tail}`;
function saveSaved(it) {
  return saveToDevice(`/api/saved/${it.id}/file/download`, dlName(it.owner, it.shortcode, it.index, sExt(it)), it.kind === 'video' ? 'video/mp4' : null);
}
function saveImage(it) {
  if (!it || it.status !== 'done') return toast('Not ready yet');
  const vid = it.kind === 'video';
  const name = it.ig ? dlName(it.ig.owner, it.ig.shortcode, 1, `_${it.mode}${vid ? '.mp4' : '.jpg'}`) : `${(it.name || 'image' + it.id).replace(/\.[^.]+$/, '')}_${it.mode}${vid ? '.mp4' : '.jpg'}`;
  return saveToDevice(`/api/images/${it.id}/file/full?v=${it.v}`, name, vid ? 'video/mp4' : 'image/jpeg');
}
function anchorDownload(url, name) {  // server sends Content-Disposition: attachment → real download (Android/desktop: Downloads; iOS: Files › Downloads)
  const a = document.createElement('a'); a.href = url; a.download = name; a.rel = 'noopener'; a.style.display = 'none';
  document.body.appendChild(a); a.click(); setTimeout(() => a.remove(), 1000);
}
async function fetchFile(url, name, mime, onProg, signal) {
  const r = await fetch(url, { signal, credentials: 'same-origin' }); if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const total = +r.headers.get('content-length') || 0; const type = mime || (r.headers.get('content-type') || '').split(';')[0] || 'application/octet-stream';
  if (!r.body || !r.body.getReader) { const b = await r.blob(); onProg(1); return new File([b], name, { type }); }
  const rd = r.body.getReader(), parts = []; let got = 0;
  for (;;) { const { done, value } = await rd.read(); if (done) break; parts.push(value); got += value.length; onProg(total ? got / total : 0, got); }
  return new File(parts, name, { type });
}
let SS = null;
function closeSheet() { if (SS && SS.ctl) SS.ctl.abort(); SS = null; $('#saveSheet').classList.add('hidden'); }
$('#ssClose').onclick = closeSheet;
$('#saveSheet').addEventListener('click', e => { if (e.target.id === 'saveSheet') closeSheet(); });
$('#ssAlt').onclick = () => setTimeout(closeSheet, 300);
$('#ssShare').onclick = async () => {   // fresh tap = fresh user activation, so iOS lets us open the share sheet
  if (!SS || !SS.files) return;
  try { await navigator.share({ files: SS.files }); closeSheet(); toast('Saved ✓', 1500); }
  catch (e) { if (e.name !== 'AbortError') { $('#ssStatus').textContent = `Couldn't open the share sheet (${e.name}). Use “Download to Files instead”.`; } }
};
async function saveToDevice(url, name, mime) { return saveMany([{ url, name, mime }]); }
async function saveMany(list) {
  if (!list.length) return;
  if (!(IS_IOS && CAN_SHARE_FILES)) {   // Android / desktop / iOS over plain http → attachment download
    list.forEach((x, i) => setTimeout(() => anchorDownload(x.url, x.name), i * 700));
    if (IS_IOS && !window.isSecureContext) { toast('Downloading → Files › Downloads. To save straight to Photos, open Faceoff over HTTPS (see README → HTTPS).', 6000); return; }
    toast(list.length > 1 ? `Downloading ${list.length} files…` : `Downloading ${list[0].name}`, 2500); return;
  }
  // iPhone/iPad over HTTPS: fetch → File → share sheet → “Save Video”/“Save Image” puts it in Photos (or “Save to Files”)
  SS = { ctl: new AbortController(), files: null };
  const me = SS;
  $('#saveSheet').classList.remove('hidden'); $('#ssShare').disabled = true; $('#ssBar').style.width = '0';
  $('#ssName').textContent = list.length > 1 ? `${list.length} items` : list[0].name;
  $('#ssAlt').href = list[0].url; $('#ssAlt').download = list[0].name; $('#ssAlt').classList.toggle('hidden', list.length > 1);
  $('#ssStatus').textContent = 'Preparing…';
  try {
    const files = [];
    for (let i = 0; i < list.length; i++) {
      const x = list[i];
      files.push(await fetchFile(x.url, x.name, x.mime, (f, got) => { if (SS !== me) return;
        $('#ssBar').style.width = ((i + (f || 0)) / list.length * 100) + '%';
        $('#ssStatus').textContent = `Preparing${list.length > 1 ? ` ${i + 1}/${list.length}` : ''}… ${got ? (got / 1048576).toFixed(1) + ' MB' : ''}`; }, me.ctl.signal));
    }
    if (SS !== me) return;
    me.files = files; $('#ssBar').style.width = '100%';
    $('#ssStatus').textContent = 'Ready. Tap below, then choose “Save Video” / “Save Image” (Photos) or “Save to Files”.';
    $('#ssShare').disabled = false;
  } catch (e) { if (SS === me && e.name !== 'AbortError') $('#ssStatus').textContent = `Failed: ${e.message}`; }
}
$('#feedSave').onclick = e => { e.stopPropagation(); const it = FD.meta.get(FD.ids[FD.active]); if (it) saveSaved(it); };
$('#selSave').onclick = () => { const ids = [...SV.sel]; const its = SV.items.filter(x => SV.sel.has(x.id));
  if (its.length < ids.length) toast('Only the loaded items will be saved', 2000);
  if (its.length > 30) return toast('Pick 30 or fewer to save at once');
  saveMany(its.map(it => ({ url: `/api/saved/${it.id}/file/download`, name: dlName(it.owner, it.shortcode, it.index, sExt(it)), mime: it.kind === 'video' ? 'video/mp4' : null }))); };
})();
