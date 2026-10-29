const $ = (id) => document.getElementById(id);
async function load() {
  const {cfg = {}, logs = []} = await chrome.storage.local.get(["cfg", "logs"]);
  const c = mergeCfg(cfg);
  $("url").value = c.url || ""; $("token").value = c.token || ""; $("password").value = c.password || "";
  $("firstSend").value = c.firstSend || 0; $("logs").textContent = logs.join("\n") || "–";
}
$("save").onclick = async () => {
  const msg = $("msg");
  // Blank field uses the built-in default when one is configured. Either way, host
  // access is requested below — the default address is not a special case.
  const url = resolveFaceoffUrl($("url").value, DEFAULT_URL);
  if (!url) { msg.textContent = "Enter the address of your Faceoff server first (e.g. http://my-server:8088)."; return; }
  const pattern = hostAccessPattern(url);
  if (!pattern) { msg.textContent = "That URL doesn't look right."; return; }
  // chrome.permissions.request must be the first awaited call in this click.
  // Awaiting anything else first drops the user gesture, and Brave/Chrome then
  // never shows the prompt, so the Faceoff origin stays ungranted.
  let granted = false;
  try { granted = await chrome.permissions.request({origins: [pattern]}); }
  catch (_) { granted = false; }
  if (!granted) {
    msg.textContent = "Allow access to " + pattern.replace(/\/\*$/, "") + ", then click Save & test again. Sync now cannot reach Faceoff without it.";
    return;
  }
  const token = $("token").value.trim();
  if (!token) {
    msg.textContent = "Paste a pairing token from Faceoff → Settings → Browser extension → Generate, then click Save & test again.";
    return;
  }
  const {cfg = {}} = await chrome.storage.local.get("cfg");
  const firstSend = Math.max(0, Math.min(20, parseInt($("firstSend").value, 10) || 0));
  await chrome.storage.local.set({cfg: mergeCfg({...cfg, url, token, password: $("password").value, firstSend})});
  msg.textContent = "Saved. Testing…";
  const r = await chrome.runtime.sendMessage({type: "faceoff:ping-server"});
  msg.textContent = r && r.ok ? (r.paused ? `✓ Connected — but Instagram sync is PAUSED in Faceoff (${r.reason || "resume it in Faceoff Settings"}).`
      : `✓ Connected to Faceoff (${r.total} item(s) received so far). Syncing happens only while you're using instagram.com.`)
    : "✗ " + ((r && r.error) || "no reply");
};
$("rebase").onclick = async () => {
  if (!confirm("Forget which saves were already seen? The next sync will take a fresh baseline (sends nothing unless 'latest N' is set).")) return;
  await chrome.runtime.sendMessage({type: "faceoff:reset-baseline"}); load();
};
chrome.storage.onChanged.addListener(load);
load();
