const $ = (id) => document.getElementById(id);
const ago = (t) => { if (!t) return "never"; const s = Math.round((Date.now() - t) / 1000);
  return s < 60 ? s + "s ago" : s < 3600 ? Math.round(s / 60) + " min ago" : Math.round(s / 3600) + " h ago"; };
const inT = (t) => { const s = Math.max(0, Math.round((t - Date.now()) / 1000)); return s < 90 ? `in ${s}s` : `in ${Math.round(s / 60)} min`; };
const send = (type, extra = {}) => chrome.runtime.sendMessage({type, ...extra});

async function render() {
  const {cfg = {}, status = {}, bf = {}, nextAuto = 0, control = {}, pages = []} = await chrome.storage.local.get(["cfg", "status", "bf", "nextAuto", "control", "pages"]);
  $("pages24").textContent = pages.filter(t => t > Date.now() - 864e5).length + (control.limits ? ` / ${Math.min(50, control.limits.pages_per_day)} (backfill)` : "");
  const hm = (t) => new Date(t).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
  $("next").textContent = cfg.auto === false ? "auto-check off"
    : !hasTab ? (nextAuto > Date.now() ? `~${hm(nextAuto)} · ` : "") + "open an instagram.com tab"
    : nextAuto > Date.now() ? `~${hm(nextAuto)}` : "when instagram.com is next open";
  $("auto").checked = cfg.auto !== false;
  const stopped = status.stopUntil && status.stopUntil > Date.now();
  $("state").innerHTML = !cfg.token ? '<span class="pill warn">not paired</span>'
    : stopped ? `<span class="pill bad">stopped (IG warning) · ${inT(status.stopUntil)}</span>`
    : control.paused ? '<span class="pill bad">paused by Faceoff</span>'
    : status.running ? `<span class="pill ok">${status.running}…</span>` : '<span class="pill ok">ready</span>';
  $("last").textContent = ago(status.lastSync);
  $("result").textContent = status.lastResult || "–";
  $("sent").textContent = status.sentTotal || 0;
  const showErr = status.lastError && (!status.lastSync || status.lastErrorAt > status.lastSync);
  $("err").hidden = !showErr; $("err").textContent = showErr ? status.lastError : "";
  $("bfPages").textContent = bf.pages || 0; $("bfPosts").textContent = bf.posts || 0; $("bfSent").textContent = bf.sent || 0;
  const pill = $("bfPill");
  if (bf.done) { pill.textContent = "done"; pill.className = "pill ok"; }
  else if (bf.active) { pill.textContent = bf.pausedUntil > Date.now() ? "waiting" : "running"; pill.className = "pill ok"; }
  else if (bf.pages) { pill.textContent = "paused"; pill.className = "pill warn"; }
  else { pill.textContent = "idle"; pill.className = "pill"; }
  $("bfNext").textContent = bf.pausedUntil > Date.now() ? `${bf.reason || "next page"} ${inT(bf.pausedUntil)}` : (bf.reason || "–");
  $("bfGo").textContent = bf.active ? "Pause backfill" : bf.done ? "Run again" : bf.pages ? "Resume backfill" : "Start backfill";
  $("bfRestart").hidden = bf.active || !bf.pages || bf.done;
  $("sync").disabled = !cfg.token || !!status.running;
  $("srv").textContent = (cfg.url || "").replace(/^https?:\/\//, "").replace(/\/$/, "");
}
$("sync").onclick = async () => { $("sync").disabled = true; await send("faceoff:sync-now"); render(); };
$("auto").onchange = async () => { const {cfg = {}} = await chrome.storage.local.get("cfg");
  await chrome.storage.local.set({cfg: {...cfg, auto: $("auto").checked}}); };
$("bfGo").onclick = async () => { const {bf = {}} = await chrome.storage.local.get("bf");
  await send(bf.active ? "faceoff:bf-pause" : "faceoff:bf-start"); render(); };
$("bfRestart").onclick = async () => { if (confirm("Start the backfill over from your newest save? (Faceoff skips anything it already has.)")) { await send("faceoff:bf-start", {restart: true}); render(); } };
$("opts").onclick = () => chrome.runtime.openOptionsPage();
$("open").onclick = async () => { const {cfg = {}} = await chrome.storage.local.get("cfg"); cfg.url ? chrome.tabs.create({url: cfg.url}) : chrome.runtime.openOptionsPage(); };
chrome.storage.onChanged.addListener(render);
let hasTab = true;
const tabCheck = async () => { const r = await send("faceoff:has-tab"); hasTab = !!(r && r.tab); };
tabCheck().then(render); setInterval(render, 1000); setInterval(tabCheck, 5000);
