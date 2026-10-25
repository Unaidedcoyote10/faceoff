// Faceoff Sync content script: runs only inside YOUR logged-in instagram.com tab. It never reads or sends cookies
// to anyone except Instagram itself (the browser attaches them to same-origin requests as usual). The only cookie it
// looks at is the public csrftoken, which instagram.com's own page JavaScript sends as a header.
(() => {
  if (window.top !== window) return;
  const FALLBACK_APP_ID = "936619743392459";
  function appId() {
    try {
      const html = document.documentElement.innerHTML;
      const m = html.match(/"X-IG-App-ID":"(\d{6,20})"/) || html.match(/"appId":"(\d{6,20})"/) || html.match(/"APP_ID":"(\d{6,20})"/);
      if (m) return m[1];
    } catch (_) {}
    return FALLBACK_APP_ID;
  }
  function csrf() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }
  async function savedPage(maxId) {
    const u = new URL("/api/v1/feed/saved/posts/", location.origin);
    if (maxId) u.searchParams.set("max_id", maxId);
    let r;
    try {
      r = await fetch(u.toString(), {
        credentials: "include", redirect: "manual",
        headers: {"X-IG-App-ID": appId(), "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest",
                  "X-ASBD-ID": "129477", "Accept": "*/*"}
      });
    } catch (e) { return {ok: false, status: 0, error: "network: " + e.message}; }
    const out = {ok: r.ok, status: r.status, redirected: r.type === "opaqueredirect" || r.redirected};
    let txt = "";
    try { txt = await r.text(); } catch (_) {}
    try { out.json = JSON.parse(txt); } catch (_) { out.notJson = true; out.snippet = txt.slice(0, 120); }
    return out;
  }
  chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
    if (msg && msg.type === "faceoff:ig-page") { savedPage(msg.maxId).then(reply); return true; }
    if (msg && msg.type === "faceoff:alive") { reply({ok: true, href: location.href}); return false; }
    if (msg && msg.type === "faceoff:state") {
      reply({visible: document.visibilityState === "visible", focused: document.hasFocus(), href: location.href}); return false;
    }
  });
  let lastPing = 0;
  const ping = () => {
    if (document.visibilityState !== "visible" || Date.now() - lastPing < 5 * 60e3) return;
    lastPing = Date.now();
    chrome.runtime.sendMessage({type: "faceoff:visit", href: location.href}).catch(() => {});
  };
  document.addEventListener("visibilitychange", ping);
  window.addEventListener("focus", ping);
  ping();
})();
