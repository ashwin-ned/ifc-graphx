"""Check the statically hosted annotator actually works, served from a subpath.

The static build has failure modes the Flask build cannot have, and all of them
are silent in a way that only shows up after it is published:

  * GitHub Pages serves a project site from `/<repo>/`, so any absolute asset
    path 404s. This serves the built site from a subdirectory to catch that.
  * There is no server, so annotations live in localStorage and are handed back
    as files. If that round trip is broken, an annotator's day is gone.
  * `manifest.json` replaces the `/api/models` route. If it is missing or stale,
    the model list is empty and the page looks broken with no error.

The browser side runs under node with small `localStorage` and `fetch` shims --
enough to exercise `store.js` and `compose.js`, which is where the logic is.

    python annotator/test_static_build.py
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from build_site import build   # noqa: E402

NODE = os.environ.get("NODE_BIN") or "node"
REPO_DIR = "bim-sg"          # stand-in for the repository name in the URL

BROWSER_TEST = r"""
const BASE = process.argv[2];
const mem = new Map();
globalThis.localStorage = {
  get length() { return mem.size; },
  key: (i) => [...mem.keys()][i],
  getItem: (k) => (mem.has(k) ? mem.get(k) : null),
  setItem: (k, v) => mem.set(k, String(v)),
  removeItem: (k) => mem.delete(k),
};
const realFetch = globalThis.fetch;
globalThis.fetch = (u, o) => realFetch(new URL(u, BASE), o);
globalThis.window = globalThis;

let failures = 0;
const ok = (m, c) => { if (!c) failures++; console.log((c ? "  PASS " : "  FAIL ") + m); };

for (const f of ["config.js", "compose.js", "store.js"]) {
  const t = await (await realFetch(BASE + f)).text();
  (0, eval)(t);
}

ok("config.js selects local mode", globalThis.BIMSG_CONFIG.mode === "local");

const store = await BIMSGStore.makeStore();
ok("store is LocalStore", store.mode === "local");

const models = await store.listModels();
ok(`manifest lists models (${models.length})`, models.length > 0);

const name = models[0].model;
const plan = await store.getPlan(name);
ok(`plan ${name} loads from data/`, Array.isArray(plan.storeys));

let a = await store.getAnnotation(name, "tester");
ok("absent annotation returns an empty shape",
   a && Object.keys(a.rooms).length === 0 && Array.isArray(a.added_vertical));

for (const st of plan.storeys) {
  for (const r of st.rooms) a.rooms[r.id] = { verdict: "correct" };
  for (const e of st.edges)
    a.edges[BIMSGCompose.key(e.a, e.b)] = { verdict: "correct", a: e.a, b: e.b };
}
for (const v of (plan.vertical || []))
  a.vertical[BIMSGCompose.key(v.a, v.b)] = { verdict: "correct", a: v.a, b: v.b };
await store.saveAnnotation(name, "tester", a);

const back = await store.getAnnotation(name, "tester");
ok("annotation round-trips through localStorage",
   Object.keys(back.rooms).length === Object.keys(a.rooms).length);

const listed = await store.listModels();
ok("progress shows in the model list",
   (listed.find((m) => m.model === name) || {}).annotated.tester > 0);

const g = await store.composeGraph(name, "tester");
ok("composed graph is complete", g.complete === true);
ok("composed graph has a building node",
   g.nodes.some((n) => n.layer === "building"));

const rep = BIMSGCompose.connectivityReport(g);
ok(`storeys chain into ${rep.components} component(s)`, rep.components >= 1);

ok("unexported work is flagged", store.unexported("tester").includes(name));
store.markExported([name], "tester");
ok("flag clears once downloaded", !store.unexported("tester").includes(name));

const bundle = await store.bundle("tester");
ok("bundle is well formed",
   bundle.format === "bimsg-annotation-bundle" && bundle.annotations.length === 1);

mem.clear();
const fresh = await BIMSGStore.makeStore();
const r = await fresh.importDoc(bundle);
ok("bundle re-imports into a clean browser", r.loaded.length === 1);
const after = await fresh.getAnnotation(name, "tester");
ok("re-imported work is intact",
   Object.keys(after.rooms).length === Object.keys(a.rooms).length);

process.exit(failures ? 1 : 0);
"""


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def main():
    try:
        subprocess.run([NODE, "--version"], capture_output=True, check=True)
    except Exception:
        print(f"node not found (tried {NODE!r}); set NODE_BIN to your node binary")
        return 2

    with tempfile.TemporaryDirectory() as td:
        site = os.path.join(td, "site")
        if build(site, quiet=True) != 0:
            return 1

        # Serve it one level down, exactly as a project Pages site is served.
        root = os.path.join(td, "root")
        os.makedirs(root, exist_ok=True)
        shutil.copytree(site, os.path.join(root, REPO_DIR))

        handler = lambda *a, **k: Quiet(*a, directory=root, **k)   # noqa: E731
        with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
            port = httpd.server_address[1]
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{port}/{REPO_DIR}/"
            print(f"serving the built site at {base}")

            # Assets must resolve relative to the subpath, not the domain root.
            import urllib.request
            missing = []
            for f in ["", "index.html", "style.css", "app.js", "compose.js",
                      "store.js", "config.js", "manifest.json"]:
                try:
                    urllib.request.urlopen(base + f, timeout=10).read(1)
                except Exception:
                    missing.append(f or "index")
            if missing:
                print(f"  FAIL assets do not resolve under a subpath: {missing}")
                return 1
            print(f"  PASS all assets resolve under /{REPO_DIR}/")

            drv = os.path.join(td, "browser_test.mjs")
            with open(drv, "w") as fh:
                fh.write(BROWSER_TEST)
            r = subprocess.run([NODE, drv, base], capture_output=True, text=True)
            print(r.stdout.rstrip())
            if r.returncode != 0:
                print(r.stderr.rstrip())
                return 1

            httpd.shutdown()

    print("\nPASS the static build works when served from a subpath")
    return 0


if __name__ == "__main__":
    sys.exit(main())
