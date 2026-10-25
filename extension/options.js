const $ = (id) => document.getElementById(id);
async function load() {
  const {cfg = {}, logs = []} = await chrome.storage.local.get(["cfg", "logs"]);
  $("url").value = cfg.url || ""; $("token").value = cfg.token || ""; $("password").value = cfg.password || "";
  $("firstSend").value = cfg.firstSend || 0; $("logs").textContent = logs.join("\n") || "–";
}
$("save").onclick = async () => {
  const msg = $("msg");
  let url = $("url").value.trim().replace(/\/+$/, "");
  if (!url) { msg.textContent = "Enter the address of your Faceoff server first (e.g. http://my-server:8088)."; return; }
  if (!/^https?:\/\//.test(url)) url = "http://" + url;
  let origin;
  try { origin = new URL(url).origin; } catch (_) { msg.textContent = "That URL doesn't look right."; return; }
  // Ask for access to a custom Faceoff address (must happen inside this click).
  // Host access for your Faceoff address (must happen inside this click). Faceoff also allows the extension via CORS,
  // so declining isn't fatal; it just makes requests depend on the server's CORS headers.
  let granted = await chrome.permissions.contains({origins: [origin + "/*"]});
  if (!granted) { try { granted = await chrome.permissions.request({origins: [origin + "/*"]}); } catch (_) { granted = false; } }
  const {cfg = {}} = await chrome.storage.local.get("cfg");
  await chrome.storage.local.set({cfg: {...cfg, url, token: $("token").value.trim(), password: $("password").value,
    firstSend: Math.max(0, Math.min(20, parseInt($("firstSend").value, 10) || 0))}});
  msg.textContent = "Saved. Testing…";
  const r = await chrome.runtime.sendMessage({type: "faceoff:ping-server"});
  msg.textContent = r && r.ok ? (r.paused ? `✓ Connected — but Instagram sync is PAUSED in Faceoff (${r.reason || 'resume it in Faceoff Settings'}).`
      : `✓ Connected to Faceoff (${r.total} item(s) received so far). Syncing happens only while you're using instagram.com.`)
    : "✗ " + ((r && r.error) || "no reply");
};
$("rebase").onclick = async () => {
  if (!confirm("Forget which saves were already seen? The next sync will take a fresh baseline (sends nothing unless 'latest N' is set).")) return;
  await chrome.runtime.sendMessage({type: "faceoff:reset-baseline"}); load();
};
chrome.storage.onChanged.addListener(load);
load();
