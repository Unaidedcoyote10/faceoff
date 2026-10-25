// Faceoff Sync v2 — background service worker (safety-first: server pause flag, slow jittered pacing, auto-stop on any warning).
//
// Rules (enforced here, the Faceoff server can only make them stricter):
//  * Before EVERY Instagram request: ask Faceoff /api/ext/control. Paused, unreachable or any error => do nothing.
//  * Instagram is only touched while your instagram.com tab is visible AND focused (you are actually using it).
//  * New-saves check at most every 3–6 h (first page only). Backfill is off unless allowed in Faceoff Settings;
//    then 1 page every 45–120 s, a 20–40 min cooldown every 10 pages, at most 40 pages per rolling 24 h.
//  * Any warning sign (429, challenge/checkpoint, login_required, redirect, non-JSON, unexpected status) => stop for
//    48 h (72 h for challenges) locally AND tell Faceoff, which pauses everything until you resume it.
//  * Media downloads one at a time, 1.5–4 s apart. Never reads/sends Instagram cookies.
const DEFAULT_URL = "";   // no built-in server: set your Faceoff URL in the extension options
const DEF_CFG = {url: DEFAULT_URL, password: "", token: "", auto: true, firstSend: 0};
const FLOOR = {page_min_s: 45, page_max_s: 120, pages_per_day: 50, new_check_min_h: 3, new_check_max_h: 6, stop_hours: 24};
const NEW_PER_RUN = 20, COOLDOWN_EVERY = 10, COOLDOWN_MIN = 20 * 60e3, COOLDOWN_JITTER = 20 * 60e3;
const SEEN_MAX = 8000;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const rnd = (a, b) => a + Math.random() * (b - a);
const now = () => Date.now();
const get = (k) => chrome.storage.local.get(k);
const set = (o) => chrome.storage.local.set(o);
async function cfg() { return {...DEF_CFG, ...((await get("cfg")).cfg || {})}; }
async function patch(key, delta) { const cur = (await get(key))[key] || {}; const n = {...cur, ...delta}; await set({[key]: n}); return n; }
const setStatus = (d) => patch("status", d);
async function log(line) {
  const {logs = []} = await get("logs");
  logs.unshift(new Date().toLocaleString() + "  " + line);
  await set({logs: logs.slice(0, 60)});
}

// ------------------------------------------------------------------ Faceoff server
async function fo(path, opts = {}) {
  const c = await cfg();
  if (!c.url) throw new Stop("unpaired", "Not set up — enter your Faceoff URL and pairing token in the extension options");
  if (!c.token) throw new Stop("unpaired", "Not paired — paste a pairing token from Faceoff Settings");
  const headers = {"X-Faceoff-Token": c.token, ...(opts.headers || {})};
  if (c.password) headers["Authorization"] = "Basic " + btoa("faceoff:" + c.password);
  let r;
  try { r = await fetch(c.url.replace(/\/+$/, "") + path, {...opts, headers, credentials: "omit"}); }
  catch (e) { throw new Stop("server", "Faceoff unreachable"); }
  if (r.status === 401) throw new Stop("unpaired", "Faceoff rejected the pairing token — generate a new one in Faceoff Settings");
  if (r.status === 423) { const j = await r.json().catch(() => ({})); throw new Stop("paused", j.reason || "Paused in Faceoff"); }
  if (!r.ok) throw new Stop("server", `Faceoff ${path} → HTTP ${r.status}`);
  return r.json();
}

class Stop extends Error { constructor(kind, msg) { super(msg); this.kind = kind; } }

// ------------------------------------------------------------------ the gate (before every Instagram request)
async function igTabs() { return chrome.tabs.query({url: "https://www.instagram.com/*"}); }
async function tabState(tabId) {
  try { return await chrome.tabs.sendMessage(tabId, {type: "faceoff:state"}); } catch (_) { return null; }
}
async function activeIgTab(requireFocus) {
  for (const t of await igTabs()) {
    const s = await tabState(t.id);
    if (s && s.visible && (!requireFocus || s.focused)) return t.id;
  }
  return null;
}
async function gate(purpose, tabId, requireFocus = true) {
  const {status = {}} = await get("status");
  if (status.stopUntil && status.stopUntil > now()) throw new Stop("stopped", "Stopped after an Instagram warning");
  const ctl = await fo("/api/ext/control");           // throws => no Instagram request
  await set({control: {...ctl, at: now()}});
  if (ctl.paused) throw new Stop("paused", ctl.reason || "Paused in Faceoff");
  const s = await tabState(tabId);
  if (!s || !s.visible || (requireFocus && !s.focused)) throw new Stop("inactive", "Waiting until you're using instagram.com");
  const L = limits(ctl);
  if (purpose === "backfill") {
    if (!ctl.backfill) throw new Stop("bf_off", "Backfill is turned off in Faceoff Settings");
    if ((await pagesToday()).length >= L.pages_per_day) throw new Stop("daily", `Daily limit reached (${L.pages_per_day} pages / 24 h)`);
  }
  return L;
}
function limits(ctl) {
  const l = (ctl && ctl.limits) || {};
  return {
    page_min_s: Math.max(FLOOR.page_min_s, l.page_min_s || 0), page_max_s: Math.max(FLOOR.page_max_s, l.page_max_s || 0),
    pages_per_day: Math.min(FLOOR.pages_per_day, l.pages_per_day ?? FLOOR.pages_per_day),
    new_check_min_h: Math.max(FLOOR.new_check_min_h, l.new_check_min_h || 0),
    new_check_max_h: Math.max(FLOOR.new_check_max_h, l.new_check_max_h || 0),
    stop_hours: Math.max(FLOOR.stop_hours, l.stop_hours || 48),
  };
}
async function pagesToday() {
  const {pages = []} = await get("pages");
  const recent = pages.filter((t) => t > now() - 24 * 3600e3);
  if (recent.length !== pages.length) await set({pages: recent});
  return recent;
}

// ------------------------------------------------------------------ Instagram (via content script)
async function igPage(tabId, maxId) {
  await set({pages: [...(await pagesToday()), now()]});
  const r = await chrome.tabs.sendMessage(tabId, {type: "faceoff:ig-page", maxId: maxId || null}).catch(() => null);
  if (!r) throw new Stop("tab", "Instagram tab went away");
  const j = r.json || {};
  const msg = (String(j.message || "") + " " + String(j.error_type || "")).trim();
  let warn = null;
  if (r.status === 429 || /wait a few minutes|rate.?limit|spam|automated|unusual/i.test(msg)) warn = "rate_limit";
  else if (/challenge|checkpoint/i.test(msg) || j.challenge) warn = "challenge";
  else if (/login_required/i.test(msg) || r.status === 401 || r.status === 403 || r.redirected || j.require_login) warn = "login";
  else if (!r.ok || r.notJson || !Array.isArray(j.items) || j.status === "fail") warn = "unexpected";
  if (warn) await warningStop(warn, msg || `HTTP ${r.status}`);
  return {posts: j.items.map(normalize).filter(Boolean), more: !!j.more_available, next: j.next_max_id || null};
}
async function warningStop(kind, msg) {
  const {control} = await get("control");
  const hours = limits(control).stop_hours * (kind === "challenge" ? 1.5 : 1);
  const until = now() + hours * 3600e3;
  await setStatus({stopUntil: until, lastError: `Instagram ${kind}: ${msg}. Stopped for ${Math.round(hours)} h.`, lastErrorAt: now()});
  await patch("bf", {reason: `stopped: Instagram ${kind}`, pausedUntil: 0});
  chrome.alarms.clear("faceoff-bf");
  try { await fo("/api/ext/report", {method: "POST", headers: {"Content-Type": "application/json"},
                                     body: JSON.stringify({kind, message: msg.slice(0, 150)})}); } catch (_) {}
  await log(`⛔ Instagram ${kind} (${msg}) — stopped ${Math.round(hours)} h, Faceoff paused`);
  throw new Stop("warning", `Instagram ${kind} — stopped`);
}
function best(list) {
  if (!Array.isArray(list) || !list.length) return null;
  return [...list].sort((a, b) => (b.width || 0) * (b.height || 0) - (a.width || 0) * (a.height || 0))[0].url;
}
function mediaOf(m, index) {
  if (m.video_versions && m.video_versions.length) return {index, kind: "video", url: best(m.video_versions)};
  const img = best(m.image_versions2 && m.image_versions2.candidates);
  return img ? {index, kind: "image", url: img} : null;
}
function normalize(item) {
  const m = item && (item.media || item);
  if (!m || !m.code) return null;
  const kids = m.carousel_media && m.carousel_media.length ? m.carousel_media : [m];
  return {shortcode: m.code, owner: (m.user && m.user.username) || "", caption: ((m.caption && m.caption.text) || "").slice(0, 300),
          taken_at: m.taken_at || null, media: kids.map((k, i) => mediaOf(k, i + 1)).filter(Boolean)};
}

// ------------------------------------------------------------------ sending to Faceoff (CDN downloads, one at a time)
async function knownKeys(posts) {
  const keys = posts.flatMap((p) => p.media.map((x) => `${p.shortcode}:${x.index}`));
  const known = new Set();
  for (let i = 0; i < keys.length; i += 400)
    (await fo("/api/ingest/known", {method: "POST", headers: {"Content-Type": "application/json"},
                                    body: JSON.stringify({keys: keys.slice(i, i + 400)})})).known.forEach((k) => known.add(k));
  return known;
}
async function markSeen(codes) {
  const {seen = []} = await get("seen");
  const s = new Set(seen); codes.forEach((c) => s.add(c));
  await set({seen: [...s].slice(-SEEN_MAX)});
}
async function sendPosts(posts) {
  let sent = 0, dup = 0;
  if (!posts.length) return {sent, dup};
  const known = await knownKeys(posts);
  for (const p of [...posts].reverse()) {
    for (const x of p.media) {
      if (known.has(`${p.shortcode}:${x.index}`)) { dup++; continue; }
      const r = await fetch(x.url, {credentials: "omit", referrerPolicy: "no-referrer"});
      if (r.status === 429 || r.status === 403) await warningStop("cdn", `media HTTP ${r.status}`);
      if (!r.ok) continue;
      const fd = new FormData();
      fd.append("meta", JSON.stringify({shortcode: p.shortcode, index: x.index, owner: p.owner, caption: p.caption,
                                        taken_at: p.taken_at, kind: x.kind}));
      fd.append("file", await r.blob(), `${p.shortcode}_${x.index}.${x.kind === "video" ? "mp4" : "jpg"}`);
      const res = await fo("/api/ingest", {method: "POST", body: fd});
      res.status === "duplicate" ? dup++ : sent++;
      await sleep(rnd(1500, 4000));
    }
    await markSeen([p.shortcode]);
  }
  const {status = {}} = await get("status");
  await setStatus({sentTotal: (status.sentTotal || 0) + sent});
  return {sent, dup};
}

// ------------------------------------------------------------------ new saves (every 3–6 h, first page only)
let busy = false;
async function scheduleAuto(L) {
  const when = now() + rnd(L.new_check_min_h, L.new_check_max_h) * 3600e3;
  await set({nextAuto: when});
  chrome.alarms.create("faceoff-auto", {when});
}
async function syncNew(tabId, why) {
  if (busy) return;
  busy = true;
  try {
    const L = await gate("new", tabId, why !== "manual");
    await setStatus({running: "checking"});
    const {seen = [], baselined} = await get(["seen", "baselined"]);
    const seenSet = new Set(seen);
    const page = await igPage(tabId, null);
    if (!baselined) {
      const c = await cfg();
      const grab = page.posts.slice(0, Math.max(0, Math.min(10, c.firstSend | 0)));
      await markSeen(page.posts.map((p) => p.shortcode)); await set({baselined: true});
      const r = await sendPosts(grab);
      await setStatus({lastSync: now(), lastResult: `First run: remembered ${page.posts.length} saves, sent ${r.sent}`, lastError: null});
    } else {
      const fresh = [];
      for (const p of page.posts) { if (seenSet.has(p.shortcode)) break; fresh.push(p); }
      const r = await sendPosts(fresh.slice(0, NEW_PER_RUN));
      await setStatus({lastSync: now(), lastResult: fresh.length ? `Sent ${r.sent} file(s) from ${fresh.length} new save(s)` : "No new saves", lastError: null});
      await log(`check (${why}): ${fresh.length} new`);
    }
    await scheduleAuto(L);
  } catch (e) { await noteStop(e); }
  finally { busy = false; await setStatus({running: null}); }
}
async function noteStop(e) {
  if (!(e instanceof Stop)) { await setStatus({lastError: String(e.message || e), lastErrorAt: now()}); return; }
  if (e.kind === "inactive") return;                       // normal: you're not on instagram.com right now
  await setStatus({lastError: e.message, lastErrorAt: now(), lastStopKind: e.kind});
}

// ------------------------------------------------------------------ backfill (opt-in, very slow, only while you use IG)
async function bf() { return (await get("bf")).bf || {}; }
async function bfSchedule(ms, reason) {
  const when = now() + ms;
  await patch("bf", {pausedUntil: when, reason});
  chrome.alarms.create("faceoff-bf", {when});
}
async function runBackfill(tabId) {
  let b = await bf();
  if (!b.active || busy) return;
  if (b.pausedUntil && b.pausedUntil > now() + 1000) { chrome.alarms.create("faceoff-bf", {when: b.pausedUntil}); return; }
  busy = true;
  try {
    const L = await gate("backfill", tabId);
    await setStatus({running: "backfill"});
    const page = await igPage(tabId, b.cursor);
    const r = await sendPosts(page.posts);
    b = await bf();
    const pages = (b.pages || 0) + 1;
    await patch("bf", {pages, posts: (b.posts || 0) + page.posts.length, sent: (b.sent || 0) + r.sent, dup: (b.dup || 0) + r.dup,
                       cursor: page.next, lastAt: now()});
    if (!page.more || !page.next) {
      await patch("bf", {active: false, done: true, finishedAt: now(), reason: "finished", pausedUntil: 0});
      await log(`backfill finished (${pages} pages)`);
    } else if (pages % COOLDOWN_EVERY === 0) await bfSchedule(rnd(COOLDOWN_MIN, COOLDOWN_MIN + COOLDOWN_JITTER), "cooldown");
    else await bfSchedule(rnd(L.page_min_s, L.page_max_s) * 1000, "next page");
  } catch (e) {
    await noteStop(e);
    const wait = {inactive: 2 * 60e3, daily: 60 * 60e3, server: 30 * 60e3, paused: 30 * 60e3, bf_off: 60 * 60e3}[e.kind];
    if (wait && (await bf()).active) await bfSchedule(wait, e.kind === "inactive" ? "waiting for you to use instagram.com" : e.message);
  } finally { busy = false; await setStatus({running: null}); }
}

// ------------------------------------------------------------------ triggers
async function onActive(tabId) {
  const c = await cfg();
  const {nextAuto = 0} = await get("nextAuto");
  if (c.auto && c.token && now() >= nextAuto) await syncNew(tabId, "auto");
  const b = await bf();
  if (b.active && !(b.pausedUntil > now() + 1000)) await runBackfill(tabId);
}
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    if (msg.type === "faceoff:visit" && sender.tab) { await sleep(rnd(4000, 9000)); return onActive(sender.tab.id); }
    if (msg.type === "faceoff:sync-now") {
      const t = await activeIgTab(false);
      if (!t) { await setStatus({lastResult: "Open instagram.com (visible), then press Sync now"}); return {noTab: true}; }
      return syncNew(t, "manual");
    }
    if (msg.type === "faceoff:bf-start") {
      const b = await bf();
      if (b.done || msg.restart) await set({bf: {active: true, cursor: null, pages: 0, posts: 0, sent: 0, dup: 0, startedAt: now()}});
      else await patch("bf", {active: true, pausedUntil: 0});
      const t = await activeIgTab(true);
      if (t) runBackfill(t); else await patch("bf", {reason: "waiting for you to use instagram.com"});
      return {};
    }
    if (msg.type === "faceoff:bf-pause") { chrome.alarms.clear("faceoff-bf"); await patch("bf", {active: false, reason: "paused by you", pausedUntil: 0}); return {}; }
    if (msg.type === "faceoff:ping-server") {
      try { const ctl = await fo("/api/ext/control"); await set({control: {...ctl, at: now()}}); return {ok: true, ...ctl, ...(await fo("/api/ext/ping"))}; }
      catch (e) { return {ok: false, error: e.message}; }
    }
    if (msg.type === "faceoff:clear-stop") { await setStatus({stopUntil: 0, lastError: null}); return {}; }
    if (msg.type === "faceoff:reset-baseline") { await set({seen: [], baselined: false, nextAuto: 0}); return {}; }
    if (msg.type === "faceoff:has-tab") return {tab: (await igTabs()).length > 0};
  })().then(reply, (e) => reply({error: String(e && e.message || e)}));
  return true;
});
chrome.alarms.onAlarm.addListener(async (a) => {
  if (!a.name.startsWith("faceoff-")) return;
  const t = await activeIgTab(true);
  if (!t) {                                   // not using instagram.com right now: no Instagram requests at all
    if (a.name === "faceoff-bf" && (await bf()).active) await bfSchedule(2 * 60e3, "waiting for you to use instagram.com");
    return;
  }
  if (a.name === "faceoff-bf") return runBackfill(t);
  return onActive(t);
});
async function ensureAlarms() {
  if (!(await chrome.alarms.get("faceoff-tick"))) chrome.alarms.create("faceoff-tick", {periodInMinutes: 10});
  const {nextAuto = 0} = await get("nextAuto");
  if (nextAuto > now() && !(await chrome.alarms.get("faceoff-auto"))) chrome.alarms.create("faceoff-auto", {when: nextAuto});
}
chrome.runtime.onInstalled.addListener(async (d) => {
  await ensureAlarms();
  const c = await cfg(); await set({cfg: c});
  if (d.reason === "update") {                 // upgrading from v1: stop any running v1 backfill, start fresh & slow
    chrome.alarms.clear("faceoff-bf");
    await patch("bf", {active: false, reason: "stopped by v2 update — re-enable in Faceoff Settings to continue slowly", pausedUntil: 0});
    await set({nextAuto: now() + rnd(3, 6) * 3600e3});
  }
  if (!c.token) chrome.runtime.openOptionsPage();
});
chrome.runtime.onStartup.addListener(ensureAlarms);
ensureAlarms();
