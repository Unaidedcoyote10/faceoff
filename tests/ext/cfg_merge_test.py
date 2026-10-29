"""Pairing config: a blank stored URL must not clobber the default, and Save must request host access."""
import os, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXT = ROOT / "extension"
CFG = EXT / "cfg.js"
OPTIONS = (EXT / "options.js").read_text(encoding="utf-8")
BG = (EXT / "background.js").read_text(encoding="utf-8")
POPUP = (EXT / "popup.js").read_text(encoding="utf-8")
MANIFEST = (EXT / "manifest.json").read_text(encoding="utf-8")

ok = fail = 0

def check(name, cond, extra=""):
    global ok, fail
    print(("PASS " if cond else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)
    ok += bool(cond)
    fail += (not cond)

JS = r"""
const assert = require("assert");
const cfg = require(process.argv[1]);
const DEF = "http://faceoff.example:8088";
const observed = {auto: true, firstSend: 0, password: "", token: "", url: ""};

let m = cfg.mergeCfg(observed, DEF);
assert.strictEqual(m.url, DEF, "blank url overrode the default");
assert.strictEqual(m.token, "");
assert.strictEqual(cfg.isPaired(m), false);

m = cfg.mergeCfg({url: "   ", token: "  fo_tok  ", password: "", auto: true, firstSend: 0}, DEF);
assert.strictEqual(m.url, DEF);
assert.strictEqual(m.token, "fo_tok");
assert.strictEqual(cfg.isPaired(m), true, "paired extension must count as paired so Sync now is clickable");

m = cfg.mergeCfg({url: "http://other.example:9090/", token: "abc"}, DEF);
assert.strictEqual(m.url, "http://other.example:9090");
assert.strictEqual(m.token, "abc");
assert.strictEqual(cfg.isPaired(m), true);

m = cfg.mergeCfg({url: "http://other.example:9090", token: "   "}, DEF);
assert.strictEqual(m.token, "");
assert.strictEqual(cfg.isPaired(m), false);

m = cfg.mergeCfg({url: "", token: "abc"});
assert.strictEqual(m.url, cfg.DEFAULT_URL);

m = cfg.mergeCfg({url: "", token: "t", auto: false, firstSend: 4, password: "secret"}, "http://faceoff.example:8088/");
assert.strictEqual(m.auto, false);
assert.strictEqual(m.firstSend, 4);
assert.strictEqual(m.password, "secret");
assert.strictEqual(m.url, DEF);

m = cfg.mergeCfg({firstSend: "nope"}, DEF);
assert.strictEqual(m.firstSend, 0);
m = cfg.mergeCfg({firstSend: 99}, DEF);
assert.strictEqual(m.firstSend, 20);

assert.strictEqual(cfg.resolveFaceoffUrl("", DEF), DEF);
assert.strictEqual(cfg.resolveFaceoffUrl("   ", DEF), DEF);
assert.strictEqual(cfg.resolveFaceoffUrl("http://custom.example:8088/", DEF), "http://custom.example:8088");
assert.strictEqual(cfg.resolveFaceoffUrl("my-server:8088", ""), "http://my-server:8088");

const defPattern = cfg.hostAccessPattern(cfg.resolveFaceoffUrl("", DEF));
const customPattern = cfg.hostAccessPattern(cfg.resolveFaceoffUrl("https://custom.example/faceoff", DEF));
assert.strictEqual(defPattern, DEF + "/*");
assert.strictEqual(customPattern, "https://custom.example/*");
assert.notStrictEqual(defPattern, "");
assert.strictEqual(cfg.hostAccessPattern(""), "");
assert.strictEqual(cfg.hostAccessPattern(DEF), cfg.hostAccessPattern(DEF));

console.log("cfg merge ok");
"""

proc = subprocess.run(["node", "-e", JS, str(CFG)], capture_output=True, text=True)
check("cfg.js merge and host patterns", proc.returncode == 0, (proc.stdout + proc.stderr).strip())

for path in ("cfg.js", "background.js", "options.js", "popup.js"):
    syntax = subprocess.run(["node", "--check", str(EXT / path)], capture_output=True, text=True)
    check(f"syntax {path}", syntax.returncode == 0, syntax.stderr.strip())

handler = OPTIONS.split('$("save").onclick', 1)[1].split('$("rebase")', 1)[0]
req = handler.find("chrome.permissions.request")
storage = handler.find("chrome.storage.local.get")
check("save requests host access", req >= 0)
check("save does not pre-check permissions.contains", "permissions.contains" not in handler)
check("host permission is requested before reading storage", req >= 0 and (storage < 0 or req < storage))
check("blank options field resolves through resolveFaceoffUrl", "resolveFaceoffUrl(" in handler)
check("save uses hostAccessPattern for the origin", "hostAccessPattern(" in handler)
for bad in ("!== DEFAULT_URL", "!= DEFAULT_URL", "=== DEFAULT_URL", "== DEFAULT_URL"):
    check(f"options does not special-case default ({bad})", bad not in OPTIONS)
check("denied host access does not save", handler.find("if (!granted)") < handler.find("chrome.storage.local.set"))

check("background imports cfg.js", 'importScripts("cfg.js")' in BG)
check("background merges stored cfg", "mergeCfg(" in BG)
check("background does not spread defaults under stored cfg", "...DEF_CFG" not in BG)

check("popup gates Sync now on isPaired", "isPaired(" in POPUP and "!paired || !!status.running" in POPUP)
check("options and popup load cfg.js",
      'src="cfg.js"' in (EXT / "options.html").read_text(encoding="utf-8")
      and 'src="cfg.js"' in (EXT / "popup.html").read_text(encoding="utf-8"))
check("manifest keeps optional Faceoff host patterns",
      '"http://*/*"' in MANIFEST and '"https://*/*"' in MANIFEST)
check("manifest still limits required hosts to Instagram",
      "https://www.instagram.com/*" in MANIFEST and '"http://*/*"' not in MANIFEST.split("optional_host_permissions")[0])

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
