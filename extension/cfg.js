// Shared pairing config for Faceoff Sync (options page, popup, and service worker).
// A blank stored url/token is missing data: url falls back to DEFAULT_URL, token stays
// unpaired. Empty strings must not override those defaults via object spread.
var DEFAULT_URL = ""; // no built-in server — set the Faceoff URL in the extension options

function nonBlank(v) {
  return (typeof v === "string" && v.trim()) ? v.trim() : "";
}

// Typed value wins. A blank field uses defaultUrl (or DEFAULT_URL when omitted).
function resolveFaceoffUrl(typed, defaultUrl) {
  var entered = nonBlank(typed).replace(/\/+$/, "");
  var fallback = nonBlank(defaultUrl === undefined ? DEFAULT_URL : defaultUrl).replace(/\/+$/, "");
  var url = entered || fallback;
  if (!url) return "";
  if (!/^https?:\/\//i.test(url)) url = "http://" + url;
  return url.replace(/\/+$/, "");
}

function faceoffOrigin(url) {
  var raw = nonBlank(url);
  if (!raw) return "";
  var withScheme = /^https?:\/\//i.test(raw) ? raw : ("http://" + raw);
  try { return new URL(withScheme).origin; }
  catch (e) { return ""; }
}

// Host permission to request before save/test. Same rule for the built-in default
// address and a custom one — a default URL is not a reason to skip the prompt.
function hostAccessPattern(url) {
  var origin = faceoffOrigin(url);
  return origin ? (origin + "/*") : "";
}

function mergeCfg(stored, defaultUrl) {
  var s = (stored && typeof stored === "object") ? stored : {};
  var firstSend = parseInt(s.firstSend, 10);
  if (!isFinite(firstSend)) firstSend = 0;
  firstSend = Math.max(0, Math.min(20, firstSend));
  return {
    url: resolveFaceoffUrl(typeof s.url === "string" ? s.url : "", defaultUrl),
    password: typeof s.password === "string" ? s.password : "",
    token: nonBlank(s.token),
    auto: s.auto !== false,
    firstSend: firstSend
  };
}

// Call with the result of mergeCfg. A raw stored url of "" is not paired, even
// when DEFAULT_URL is set, until mergeCfg has filled it in.
function isPaired(cfg) {
  return !!(cfg && nonBlank(cfg.url) && nonBlank(cfg.token));
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    DEFAULT_URL: DEFAULT_URL,
    nonBlank: nonBlank,
    resolveFaceoffUrl: resolveFaceoffUrl,
    faceoffOrigin: faceoffOrigin,
    hostAccessPattern: hostAccessPattern,
    mergeCfg: mergeCfg,
    isPaired: isPaired
  };
}
