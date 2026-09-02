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

import json
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
WEBIFC_NODE = "https://cdn.jsdelivr.net/npm/web-ifc@0.0.77/"

AXIS_TEST = r"""
import fs from "node:fs/promises";
const WebIFC = await import(process.argv[2] + "/web-ifc-api-node.js");
const api = new WebIFC.IfcAPI();
api.SetWasmPath(process.argv[2] + "/", true);
await api.Init();
const id = api.OpenModel(new Uint8Array(await fs.readFile(process.argv[3])),
                         { COORDINATE_TO_ORIGIN: true });
const mn = [1e30, 1e30, 1e30], mx = [-1e30, -1e30, -1e30];
api.StreamAllMeshes(id, (mesh) => {
  const pl = mesh.geometries;
  for (let i = 0; i < pl.size(); i++) {
    const pg = pl.get(i);
    const g = api.GetGeometry(id, pg.geometryExpressID);
    const v = api.GetVertexArray(g.GetVertexData(), g.GetVertexDataSize());
    const t = pg.flatTransformation;
    for (let k = 0; k < v.length; k += 6) {
      const x = v[k], y = v[k + 1], z = v[k + 2];
      const p = [t[0]*x + t[4]*y + t[8]*z + t[12],
                 t[1]*x + t[5]*y + t[9]*z + t[13],
                 t[2]*x + t[6]*y + t[10]*z + t[14]];
      for (let a = 0; a < 3; a++) {
        if (p[a] < mn[a]) mn[a] = p[a];
        if (p[a] > mx[a]) mx[a] = p[a];
      }
    }
    g.delete();
  }
});
const ids = api.GetLineIDsWithType(id, WebIFC.IFCBUILDINGSTOREY);
const el = [];
for (let i = 0; i < ids.size(); i++) {
  const s = api.GetLine(id, ids.get(i));
  el.push(s.Elevation ? Number(s.Elevation.value) : 0);
}
api.CloseModel(id);
// web-ifc logs its "No basis found for brep" warnings on stdout, so the result
// goes to a file rather than being fished out of that.
await fs.writeFile(process.argv[4],
                   JSON.stringify({ min: mn, max: mx, elevations: el }));
"""

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
  getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600 }),
  setPointerCapture() {}, releasePointerCapture() {} };

// The controls are damped: an input moves the *goal* and update() eases toward
// it, so every assertion below settles the animation first rather than reading
// a state that is still in flight.
// update() stops easing once the residual is under 1e-5, so a settled value is
// correct to about that, not to machine precision. Tolerances below allow it.
const settle = (c) => { for (let i = 0; i < 200; i++) c.update(); };
const TOL = 1e-4;

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
c.rotate(120, 0); settle(c);
ok("horizontal drag turns the camera", Math.abs(c.sph.theta - th0) > 1e-3);

c.rotate(0, -1e6); settle(c);
ok("phi stays under PI at the pole", c.sph.phi < Math.PI && c.sph.phi > 0);
c.rotate(0, 1e6); settle(c);
ok("phi stays over 0 at the pole", c.sph.phi > 0 && c.sph.phi < Math.PI);

const r0 = c.sph.radius;
c.dolly(0.5); settle(c);
ok("dolly scales the radius", Math.abs(c.sph.radius - r0 * 0.5) < TOL);
c.minDistance = 2; c.dolly(1e-4); settle(c);
ok("radius clamps to minDistance", Math.abs(c.sph.radius - 2) < TOL);

const t0 = c.target.clone();
c.pan(50, 0); settle(c);
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
listeners.pointerup({ pointerId: 1 }); settle(c);
ok("a left-drag orbits", Math.abs(c.sph.theta - before) > 1e-3);

const tgt = c.target.clone();
listeners.pointerdown({ clientX: 100, clientY: 100, button: 2, pointerId: 1 });
listeners.pointermove({ clientX: 160, clientY: 130, pointerId: 1 });
listeners.pointerup({ pointerId: 1 }); settle(c);
ok("a right-drag pans", c.target.distanceTo(tgt) > 1e-6);

const before2 = c.sph.radius;
listeners.wheel({ deltaY: -100, clientX: 400, clientY: 300, preventDefault() {} });
ok("the wheel zooms in", c._goalS.radius < before2);
const tBefore = c._goalT.clone();
listeners.wheel({ deltaY: -100, clientX: 700, clientY: 150, preventDefault() {} });
ok("zooming off-centre also shifts the target toward the cursor",
   c._goalT.distanceTo(tBefore) > 1e-9);

/* --- the empty-geometry trap ------------------------------------------ */
const helpers = src.slice(src.indexOf("  function isFiniteBox(b)"),
                          src.indexOf("  root.BIMSGViewer"));
const H = eval(`(function(){const THREE = globalThis.THREE; ${helpers}
  return { mergeGeometries, isFiniteBox };})()`);

const empty = new THREE.BufferGeometry();
empty.setAttribute("position", new THREE.BufferAttribute(new Float32Array(0), 3));
empty.computeBoundingBox();
ok("an empty BufferGeometry has a non-finite box (the bug being guarded)",
   !H.isFiniteBox(empty.boundingBox));

const cube = new THREE.BoxGeometry(2, 2, 2).toNonIndexed();
const solid = new THREE.BufferGeometry();
solid.setAttribute("position", cube.getAttribute("position"));
solid.setIndex([...Array(cube.getAttribute("position").count).keys()]);
const merged = H.mergeGeometries([solid, empty]);
ok("merging drops the empty geometry", merged !== null);
merged.computeBoundingBox();
ok("the merged box stays finite", H.isFiniteBox(merged.boundingBox));
ok("the merged box is the real one", Math.abs(merged.boundingBox.max.x - 1) < 1e-6);
ok("merging only empties yields null", H.mergeGeometries([empty]) === null);

/* --- section planes ---------------------------------------------------- */
/* The earlier version of this block rotated a group by -PI/2 about X and
 * asserted that an IFC z became a world Y. It passed, and it was wrong: it
 * verified the assumption rather than the library. web-ifc already emits Y-up,
 * so that rotation put the vertical axis into Z and the section sliced the
 * building sideways. The axis convention is now asserted against a real model
 * in Python (see check_axis_convention), and what is checked here is only that
 * the planes isolate the slab they claim to. */
ok("the viewer applies no rotation of its own to web-ifc output",
   !/rotation\.x\s*=/.test(src));
const top = new THREE.Plane(new THREE.Vector3(0, -1, 0), 6);
const bot = new THREE.Plane(new THREE.Vector3(0, 1, 0), -3);
const inside = (v) => top.distanceToPoint(v) > 0 && bot.distanceToPoint(v) > 0;
ok("a point at 4.5 m is inside a 3-6 m slab", inside(new THREE.Vector3(3, 4.5, 7)));
ok("a point at 1 m is below it", !inside(new THREE.Vector3(0, 1, 0)));
ok("a point at 8 m is above it", !inside(new THREE.Vector3(0, 8, 0)));
ok("the slab ignores horizontal position",
   inside(new THREE.Vector3(-500, 4.5, 900)));

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


def check_axis_convention(td: str) -> int:
    """Assert which axis web-ifc puts the building's height on, using a real model.

    This is the check that was missing. The JS suite asserted that a -PI/2
    rotation about X turns an IFC z into a world Y -- true, self-consistent, and
    beside the point, because web-ifc has *already* converted to Y-up. Rotating
    again sent the vertical into Z and the section slider cut the building
    sideways. Testing an assumption proves nothing about the library; this asks
    the library.

    The building's height is known independently, from the storey elevations.
    Whichever axis spans closest to that height is the vertical one, and it must
    be Y.
    """
    model = os.path.join(HERE, "data", "model_0.ifc")
    if not os.path.exists(model):
        print("\n  SKIP no IFC in annotator/data to check the axis convention")
        return 0

    print("\naxis convention, against a real model:")
    lib = os.path.join(td, "webifc")
    os.makedirs(lib, exist_ok=True)
    try:
        for f in ("web-ifc-api-node.js", "web-ifc-node.wasm"):
            with open(os.path.join(lib, f), "wb") as fh:
                fh.write(fetch(WEBIFC_NODE + f))
    except Exception as e:
        print(f"  SKIP could not fetch web-ifc's node build ({e})")
        return 0

    drv = os.path.join(td, "axis.mjs")
    with open(drv, "w") as fh:
        fh.write(AXIS_TEST)
    out = os.path.join(td, "axis.json")
    r = subprocess.run([NODE, drv, lib, model, out], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(out):
        print("  FAIL could not read the model")
        print((r.stderr or r.stdout).strip()[-800:])
        return 1

    with open(out) as fh:
        d = json.load(fh)
    spans = [d["max"][i] - d["min"][i] for i in range(3)]
    el = sorted(d["elevations"])
    if not el:
        print("  SKIP the model states no storeys")
        return 0
    # Storeys give the height from the lowest floor to the top one, plus one
    # more storey of headroom for the roof above the highest slab.
    storey_h = (el[-1] - el[0]) / max(1, len(el) - 1) if len(el) > 1 else 3.2
    expect = (el[-1] - el[0]) + storey_h
    vertical = min(range(3), key=lambda i: abs(spans[i] - expect))

    names = "XYZ"
    print(f"  spans  X {spans[0]:.1f}  Y {spans[1]:.1f}  Z {spans[2]:.1f} m")
    print(f"  storeys {', '.join(f'{e:.1f}' for e in el)} -> expect a height "
          f"near {expect:.1f} m")
    if vertical != 1:
        print(f"  FAIL web-ifc puts the height on {names[vertical]}, not Y — the "
              f"viewer's section planes are on world Y and would slice sideways")
        return 1
    print(f"  PASS the height is on Y ({spans[1]:.1f} m), which is where the "
          f"section planes cut")
    lo, hi = d["min"][1], d["max"][1]
    if not (lo - 1.5 <= el[0] and el[-1] <= hi + 1.5):
        print(f"  FAIL storey elevations {el[0]:.1f}..{el[-1]:.1f} fall outside "
              f"the Y extent {lo:.1f}..{hi:.1f}")
        return 1
    print(f"  PASS every storey elevation lies inside the Y extent "
          f"({lo:.1f}..{hi:.1f} m)")
    return 0


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

        if check_axis_convention(td):
            return 1

    print("\nPASS the viewer's dependencies resolve and its controls behave")
    return 0


if __name__ == "__main__":
    sys.exit(main())
