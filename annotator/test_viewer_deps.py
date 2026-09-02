"""Check what the 3D view loads from the CDN, before an annotator does.

The viewer is the one part of this tool that cannot be exercised without a
browser, and it failed in exactly the way that is invisible from here: three's
`OrbitControls` addon is published with a bare `import ... from "three"`, which
a browser refuses to resolve without an import map. Nothing in the Python or
node test suites saw it; the first person to open the tab did.

So this checks the two things that can break that way:

  1. Every module the viewer imports resolves, and none of them uses a bare
     specifier a browser cannot follow. That is a static property of the files
     and is worth asserting on every deploy, because a pinned version can be
     re-published and a new one certainly differs.
  2. The orbit controls written to replace that addon behave -- against the real
     three.js build, driven through their own event listeners.

Network failures are not build failures: if the CDN cannot be reached the test
says so and passes, since a flaky CDN must not block a deploy that does not
depend on it at build time.

    python annotator/test_viewer_deps.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
VIEWER = os.path.join(HERE, "static", "viewer3d.js")
NODE = os.environ.get("NODE_BIN") or "node"
TIMEOUT = 45

# A specifier a browser can follow without an import map.
RESOLVABLE = re.compile(r'^(\.{1,2}/|/|https?://)')
STMT_START = re.compile(r'(?:^|\n)\s*(?:import|export)\b')
FROM_SPEC = re.compile(r'\bfrom\s*["\']([^"\']+)["\']')
SIDE_EFFECT = re.compile(r'^\s*import\s*["\']([^"\']+)["\']')

ORBIT_TEST = r"""
import fs from "node:fs/promises";
const THREE = await import(process.argv[2]);
globalThis.window = globalThis;
globalThis.THREE = THREE;

const src = await fs.readFile(process.argv[3], "utf8");
const start = src.indexOf("  class Orbit {");
const end = src.indexOf("  /* three's BufferGeometryUtils");
if (start < 0 || end < 0) {
  console.log("  FAIL could not find the Orbit class in viewer3d.js");
  process.exit(1);
}
const Orbit = eval(
  `(function(){ const THREE = globalThis.THREE; ${src.slice(start, end)} return Orbit; })()`);

let bad = 0;
const ok = (m, c) => { if (!c) bad++; console.log((c ? "  PASS " : "  FAIL ") + m); };

const listeners = {};
const dom = { clientHeight: 600, clientWidth: 800, style: {},
  addEventListener: (n, f) => { listeners[n] = f; },
  setPointerCapture() {}, releasePointerCapture() {} };

const cam = new THREE.PerspectiveCamera(55, 4 / 3, 0.1, 2000);
const c = new Orbit(cam, dom);

ok("camera sits at target + spherical offset",
   Math.abs(cam.position.distanceTo(c.target) - c.sph.radius) < 1e-6);
ok("camera looks at the target", (() => {
  const fwd = new THREE.Vector3(0, 0, -1).applyQuaternion(cam.quaternion).normalize();
  const want = new THREE.Vector3().subVectors(c.target, cam.position).normalize();
  return fwd.distanceTo(want) < 1e-6;
})());

const th0 = c.sph.theta;
c.rotate(120, 0);
ok("horizontal drag turns the camera", Math.abs(c.sph.theta - th0) > 1e-3);

c.sph.phi = 0.5; c.rotate(0, -1e6);
ok("phi stays under PI at the pole", c.sph.phi < Math.PI && c.sph.phi > 0);
c.rotate(0, 1e6);
ok("phi stays over 0 at the pole", c.sph.phi > 0 && c.sph.phi < Math.PI);

const r0 = c.sph.radius;
c.dolly(0.5);
ok("dolly scales the radius", Math.abs(c.sph.radius - r0 * 0.5) < 1e-9);
c.minDistance = 2; c.dolly(1e-4);
ok("radius clamps to minDistance", c.sph.radius === 2);

const t0 = c.target.clone();
c.pan(50, 0);
ok("pan moves the target", c.target.distanceTo(t0) > 1e-6);
ok("pan holds the camera-target distance",
   Math.abs(cam.position.distanceTo(c.target) - c.sph.radius) < 1e-6);
const right = new THREE.Vector3().setFromMatrixColumn(cam.matrix, 0);
const moved = new THREE.Vector3().subVectors(c.target, t0).normalize();
ok("pan follows the camera basis", Math.abs(Math.abs(moved.dot(right)) - 1) < 1e-3);

ok("the listeners the viewer needs are bound",
   ["pointerdown", "pointermove", "pointerup", "wheel", "contextmenu"]
     .every((n) => listeners[n]));

const before = c.sph.theta;
listeners.pointerdown({ clientX: 100, clientY: 100, button: 0, pointerId: 1 });
listeners.pointermove({ clientX: 160, clientY: 100, pointerId: 1 });
listeners.pointerup({ pointerId: 1 });
ok("a left-drag orbits", Math.abs(c.sph.theta - before) > 1e-3);

const tgt = c.target.clone();
listeners.pointerdown({ clientX: 100, clientY: 100, button: 2, pointerId: 1 });
listeners.pointermove({ clientX: 160, clientY: 130, pointerId: 1 });
listeners.pointerup({ pointerId: 1 });
ok("a right-drag pans", c.target.distanceTo(tgt) > 1e-6);

process.exit(bad ? 1 : 0);
"""


def urls_from_viewer() -> dict:
    """The CDN URLs the viewer is pinned to, read from the source."""
    src = open(VIEWER).read()
    out = {}
    for name, url in re.findall(r'const (\w+_URL|WASM_DIR) = "([^"]+)"', src):
        out[name] = url
    return out


def fetch(url: str) -> bytes:
    return urllib.request.urlopen(url, timeout=TIMEOUT).read()


def module_specifiers(text: str) -> list:
    """Every module specifier a static import/export in `text` refers to.

    Import statements wrap across lines -- three's addons declare thirty
    bindings before their `from "three"` -- so this reads each statement up to
    its semicolon rather than matching a single line. A line-bounded pattern
    silently passes exactly the files that break, which is how the first
    version of this test reported OrbitControls as clean.
    """
    out = []
    for m in STMT_START.finditer(text):
        seg = text[m.start():m.start() + 4000]
        semi = seg.find(";")
        if semi != -1:
            seg = seg[:semi]
        f = FROM_SPEC.search(seg)
        if f:
            out.append(f.group(1))
            continue
        # `import "./side-effect.js"` has no `from`; anything else (an
        # `export const x = "text"`, say) names no module at all.
        se = SIDE_EFFECT.search(seg.lstrip("\n"))
        if se:
            out.append(se.group(1))
    return out


def check_module(url: str) -> list:
    """Bare specifiers in a module a browser is asked to import directly."""
    text = fetch(url).decode("utf8", "replace")
    return sorted({s for s in module_specifiers(text) if not RESOLVABLE.match(s)})


def main():
    urls = urls_from_viewer()
    missing = [k for k in ("THREE_URL", "WEBIFC_URL", "WASM_DIR") if k not in urls]
    if missing:
        print(f"FAIL could not read {missing} from viewer3d.js")
        return 1

    print("pinned dependencies:")
    for k, v in urls.items():
        print(f"  {k:<12} {v}")

    # --- reachability ------------------------------------------------
    print("\nreachable and resolvable:")
    to_check = [urls["THREE_URL"], urls["WEBIFC_URL"],
                urls["WASM_DIR"].rstrip("/") + "/web-ifc.wasm"]
    try:
        for u in to_check:
            fetch(u)
            print(f"  PASS {u.rsplit('/', 1)[-1]} downloads")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"  SKIP the CDN is unreachable ({e}); not failing the build")
        return 0

    # --- the failure that actually happened --------------------------
    bad = 0
    for name in ("THREE_URL", "WEBIFC_URL"):
        bare = check_module(urls[name])
        if bare:
            bad += 1
            print(f"  FAIL {name} imports bare specifier(s) {bare} — a browser "
                  f"cannot resolve these without an import map")
        else:
            print(f"  PASS {name} has no bare imports")

    # three.module.js pulls in a sibling; a browser follows it relatively.
    core = urls["THREE_URL"].rsplit("/", 1)[0] + "/three.core.js"
    try:
        fetch(core)
        print("  PASS three.core.js (three.module.js's relative import) resolves")
    except Exception:
        bad += 1
        print("  FAIL three.module.js imports ./three.core.js but it is not there")

    if bad:
        return 1

    # --- the controls that replaced the addon ------------------------
    try:
        subprocess.run([NODE, "--version"], capture_output=True, check=True)
    except Exception:
        print(f"\nnode not found (tried {NODE!r}); skipping the orbit checks")
        return 0

    print("\norbit controls, against the real three.js build:")
    with tempfile.TemporaryDirectory() as td:
        base = urls["THREE_URL"].rsplit("/", 1)[0]
        for f in ("three.module.js", "three.core.js"):
            with open(os.path.join(td, f), "wb") as fh:
                fh.write(fetch(f"{base}/{f}"))
        drv = os.path.join(td, "orbit.mjs")
        with open(drv, "w") as fh:
            fh.write(ORBIT_TEST)
        r = subprocess.run(
            [NODE, drv, os.path.join(td, "three.module.js"), VIEWER],
            capture_output=True, text=True)
        print(r.stdout.rstrip())
        if r.returncode != 0:
            print(r.stderr.rstrip()[:2000])
            return 1

    print("\nPASS the viewer's dependencies resolve and its controls behave")
    return 0


if __name__ == "__main__":
    sys.exit(main())
