"""Drive the annotator in a real browser and assert it actually works.

Every regression this tool has shipped was invisible to the other suites,
because they test modules and this is a browser application. A name collision
in the state object, a block of code deleted by an over-wide edit, a section
that sliced the wrong axis, a control that stopped being wired -- all of them
would have been caught by loading the page and clicking.

So this loads the built site in Chromium and works through the contract:
the controls exist and are wired, the plan draws, a verdict sticks and undoes,
links can be added and removed, the layouts switch, the IFC renders with a
camera at a finite distance, and the section cuts vertically.

    python annotator/test_e2e.py                 # static build
    python annotator/test_e2e.py --keep-shots    # leave screenshots behind

Needs Playwright with a Chromium download. It skips rather than fails when the
browser is unavailable, so it never blocks a deploy on a missing dependency,
but it is not optional locally: run it before pushing anything that touches the
static/ directory.
"""

from __future__ import annotations

import argparse
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
# Playwright and its Chromium usually live beside the interpreter running this.
PLAYWRIGHT = os.environ.get("PLAYWRIGHT_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(sys.executable)), "lib", "node_modules", "playwright")

DRIVER = r"""
import { chromium } from "PLAYWRIGHT_PATH/index.mjs";

const BASE = process.argv[2];
const SHOTS = process.argv[3];
const IFC_MODEL = process.argv[4] || "";

let failures = 0;
const ok = (m, c, extra) => {
  if (!c) failures++;
  console.log((c ? "  PASS " : "  FAIL ") + m + (extra && !c ? `  [${extra}]` : ""));
};

const browser = await chromium.launch({ args: [
  "--no-sandbox", "--disable-dev-shm-usage",
  "--use-gl=swiftshader", "--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });

const errors = [];
page.on("pageerror", (e) => errors.push("UNCAUGHT " + e.message));
page.on("console", (m) => { if (m.type() === "error") errors.push("console " + m.text()); });

await page.goto(BASE, { waitUntil: "networkidle", timeout: 60000 });
await page.waitForTimeout(600);
// The guide opens on first run; dismiss it so it does not swallow clicks.
if (await page.locator("#mask.on").count()) {
  await page.click("#dlgYes").catch(() => {});
  await page.waitForTimeout(150);
}

console.log("\n-- boot --");
ok("the page loads with no uncaught errors", errors.length === 0, errors.join(" | "));

/* Every control an annotator needs, and whether it is actually on screen.
   "present in the DOM" is not enough: a zero-size or hidden control is missing
   as far as the person using it is concerned. */
const CONTROLS = [
  "btnFolder", "modelSel", "btnPrev", "btnNext", "storeyList", "progress",
  "modeSelect", "modeEdge", "modeVert", "modeRoom",
  "lyWalls", "lyRooms", "lyEdges", "lyDoors", "lyLabels",
  "btnSave", "btnExport", "btnRaw", "btnUndo", "btnRedo", "btnGuide",
  "annotator", "tab2d", "tabSplit", "tab3d", "plan", "inspector", "vertList",
];
const shown = await page.evaluate((ids) => {
  const out = {};
  for (const id of ids) {
    const e = document.getElementById(id);
    if (!e) { out[id] = "MISSING"; continue; }
    const r = e.getBoundingClientRect();
    const cs = getComputedStyle(e);
    out[id] = e.hidden || cs.display === "none" || cs.visibility === "hidden"
      ? "hidden" : (r.width > 0 && r.height > 0 ? "ok" : "zero-size");
  }
  return out;
}, CONTROLS);
const bad = Object.entries(shown).filter(([, v]) => v !== "ok");
ok(`all ${CONTROLS.length} controls are visible`, bad.length === 0,
   bad.map(([k, v]) => `${k}=${v}`).join(", "));

const hooked = await page.evaluate(() => !!window.BIMSGApp);
ok("the debug hook is available", hooked);

console.log("\n-- the model list and the plan --");
const models = await page.evaluate(() => document.getElementById("modelSel").options.length);
ok(`the model list is populated (${models})`, models > 0);

const rooms = await page.evaluate(() => document.querySelectorAll("#gRooms polygon").length);
ok(`the plan draws rooms (${rooms})`, rooms > 0);
const edges = await page.evaluate(() => document.querySelectorAll("#gEdges line").length);
ok(`the plan draws links (${edges})`, edges > 0);
const walls = await page.evaluate(() => document.querySelectorAll("#gWalls polygon").length);
ok(`the plan draws walls (${walls})`, walls > 0);

// The plan must be framed inside the viewport, not off somewhere.
const framed = await page.evaluate(() => {
  const svg = document.getElementById("plan").getBoundingClientRect();
  const r = document.querySelector("#gRooms polygon").getBoundingClientRect();
  return r.width > 2 && r.height > 2 &&
         r.right > svg.left && r.left < svg.right &&
         r.bottom > svg.top && r.top < svg.bottom;
});
ok("the plan is framed inside the canvas", framed);

console.log("\n-- judging --");
await page.fill("#annotator", "e2e");
await page.waitForTimeout(250);

/* Link lines are drawn over the rooms and take the click, which is correct --
   they have to be clickable too -- but it makes a room hard to hit where many
   links converge. The layers panel exists for exactly that, so turn the links
   off the way an annotator would. Doing it here also exercises that control. */
await page.uncheck("#lyEdges");
await page.waitForTimeout(150);
ok("turning off the links layer clears them from the plan",
   (await page.locator("#gEdges line").count()) === 0);

await page.click("#gRooms polygon:last-of-type");
await page.waitForTimeout(120);
ok("clicking a room opens the inspector",
   (await page.locator("#inspector .ins-title").count()) > 0);

await page.keyboard.press("1");
await page.waitForTimeout(120);
let verdicts = await page.evaluate(() =>
  Object.values(window.BIMSGApp.state.anno.rooms).filter((r) => r.verdict).length);
ok("pressing 1 records a verdict", verdicts === 1, `got ${verdicts}`);
ok("the room is outlined as judged",
   (await page.locator("#gRooms polygon.v-correct").count()) > 0);

await page.keyboard.press("Control+z");
await page.waitForTimeout(150);
verdicts = await page.evaluate(() =>
  Object.values(window.BIMSGApp.state.anno.rooms).filter((r) => r.verdict).length);
ok("Ctrl+Z takes it back", verdicts === 0, `got ${verdicts}`);

await page.keyboard.press("Control+Shift+z");
await page.waitForTimeout(150);
verdicts = await page.evaluate(() =>
  Object.values(window.BIMSGApp.state.anno.rooms).filter((r) => r.verdict).length);
ok("Ctrl+Shift+Z puts it back", verdicts === 1, `got ${verdicts}`);

console.log("\n-- adding and removing --");
await page.keyboard.press("Escape");
await page.keyboard.press("a");
/* Rooms are drawn largest first so small ones stay clickable, which means the
   last two in the list are the ones guaranteed not to be covered. */
const polys = page.locator("#gRooms polygon");
const nRooms = await polys.count();
await polys.nth(nRooms - 1).click();
await page.waitForTimeout(120);
await polys.nth(nRooms - 2).click();
await page.waitForTimeout(150);
let added = await page.evaluate(() => window.BIMSGApp.state.anno.added_edges.length);
ok("A links two rooms", added === 1, `got ${added}`);

await page.keyboard.press("Escape");
await page.check("#lyEdges");            // the added link needs to be visible
await page.waitForTimeout(150);
ok("turning the links layer back on redraws them",
   (await page.locator("#gEdges line").count()) > 0);
/* A horizontal <line> has a zero-height bounding box, so Playwright calls it
   invisible however clickable it really is. Click its midpoint by coordinate,
   which is what a person does anyway. */
const clickLine = async (sel) => {
  const pt = await page.evaluate((s) => {
    const e = document.querySelector(s);
    if (!e) return null;
    const r = e.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }, sel);
  if (!pt) return false;
  await page.mouse.click(pt.x, pt.y);
  return true;
};
ok("the added link has a wide click target",
   (await page.locator("#gEdges line.edge-hit").count()) > 0);
await clickLine("#gEdges line.manual");
await page.waitForTimeout(150);
ok("clicking the link selects it",
   (await page.evaluate(() => window.BIMSGApp.state.sel?.kind)) === "added");
await page.keyboard.press("Delete");
await page.waitForTimeout(150);
added = await page.evaluate(() => window.BIMSGApp.state.anno.added_edges.length);
ok("Delete removes the link it added", added === 0, `got ${added}`);

console.log("\n-- accepting the rest of a floor --");
/* The sweep must fill in only what is unjudged: an annotator marks the wrong
   ones first and accepts the remainder, so an existing verdict it overwrote
   would quietly destroy the careful part of their work. */
await page.keyboard.press("Escape");
// The links layer is back on by now and its click targets sit over the rooms,
// so hide it again to reach a room the way the earlier block did.
await page.uncheck("#lyEdges");
await page.waitForTimeout(150);
await polys.nth(nRooms - 1).click();
await page.waitForTimeout(120);
await page.keyboard.press("2");                       // one deliberate spurious
await page.waitForTimeout(150);
const marked = await page.evaluate(() => {
  const e = Object.entries(window.BIMSGApp.state.anno.rooms)
    .find(([, v]) => v.verdict === "spurious");
  return e ? e[0] : null;
});
ok("a room can be marked spurious before sweeping", !!marked);

await page.click("#btnSweep");
await page.waitForTimeout(200);
ok("the sweep asks before doing anything",
   (await page.locator("#mask.on").count()) > 0);
await page.click("#dlgYes");
await page.waitForTimeout(300);

const swept = await page.evaluate((id) => {
  const A = window.BIMSGApp.state;
  const st = A.plan.storeys[A.storey];
  const key = (a, b) => (a < b ? a + "|" + b : b + "|" + a);
  return {
    roomsUnjudged: st.rooms.filter((r) => !(A.anno.rooms[r.id] || {}).verdict).length,
    linksUnjudged: st.edges.filter((e) => !A.anno.edges[key(e.a, e.b)]).length,
    keptSpurious: (A.anno.rooms[id] || {}).verdict,
    spuriousIsBulk: !!(A.anno.rooms[id] || {}).bulk,
    bulkRooms: Object.values(A.anno.rooms).filter((r) => r.bulk).length,
    bulkLinks: Object.values(A.anno.edges).filter((r) => r.bulk).length,
  };
}, marked);
ok("every room on the floor is now judged", swept.roomsUnjudged === 0,
   String(swept.roomsUnjudged));
ok("every link on the floor is now judged", swept.linksUnjudged === 0,
   String(swept.linksUnjudged));
ok("the deliberate 'spurious' survived the sweep",
   swept.keptSpurious === "spurious", String(swept.keptSpurious));
ok("and was not relabelled as bulk", swept.spuriousIsBulk === false);
ok(`swept verdicts are flagged bulk (${swept.bulkRooms} rooms, ${swept.bulkLinks} links)`,
   swept.bulkRooms > 0 && swept.bulkLinks > 0);
ok("the storey now shows as done",
   (await page.locator("#storeyList button .tick").count()) > 0);
await page.check("#lyEdges");
await page.waitForTimeout(120);

await page.keyboard.press("Control+z");
await page.waitForTimeout(250);
const afterUndo = await page.evaluate(() =>
  Object.values(window.BIMSGApp.state.anno.rooms).filter((r) => r.bulk).length);
ok("Ctrl+Z undoes the whole sweep at once", afterUndo === 0, String(afterUndo));
await page.keyboard.press("Control+Shift+z");
await page.waitForTimeout(250);

/* Shortcuts must survive touching a checkbox or the slider. Treating those as
   text entry killed every key until the annotator clicked elsewhere, with
   nothing on screen to explain it. */
await page.check("#lyDoors");
await page.waitForTimeout(120);
const beforeKey = await page.evaluate(() => window.BIMSGApp.state.storey);
await page.keyboard.press("]");
await page.waitForTimeout(250);
const afterKey = await page.evaluate(() => window.BIMSGApp.state.storey);
ok("shortcuts still work with a checkbox focused", afterKey !== beforeKey,
   `${beforeKey} -> ${afterKey}`);
await page.keyboard.press("[");
await page.waitForTimeout(200);

console.log("\n-- storeys --");
const storeys = await page.locator("#storeyList button").count();
ok(`the storey list is populated (${storeys})`, storeys > 0);
if (storeys > 1) {
  const before = await page.evaluate(() => window.BIMSGApp.state.storey);
  await page.keyboard.press("]");
  await page.waitForTimeout(200);
  const after = await page.evaluate(() => window.BIMSGApp.state.storey);
  ok("] moves to the next storey", after !== before, `${before} -> ${after}`);
  await page.keyboard.press("[");
  await page.waitForTimeout(200);
}

await page.screenshot({ path: `${SHOTS}/01-plan.png` });

/* ---------------------------------------------------------------- 3D --- */
const has3d = await page.evaluate(() => window.BIMSGApp.can3d());
if (!has3d) {
  console.log("\n-- 3D skipped: no IFC published in this build --");
} else {
  console.log("\n-- layouts --");
  await page.click("#tabSplit");
  await page.waitForTimeout(400);
  let panes = await page.evaluate(() => {
    const v = (i) => { const e = document.getElementById(i);
      const r = e.getBoundingClientRect();
      return !e.hidden && r.width > 0 && r.height > 0; };
    return { plan: v("pane2d"), model: v("view3d"), splitter: v("splitter"),
             layout: window.BIMSGApp.state.layout };
  });
  ok("Split shows both panes and the divider",
     panes.plan && panes.model && panes.splitter && panes.layout === "split",
     JSON.stringify(panes));

  await page.click("#tab3d");
  await page.waitForTimeout(300);
  panes = await page.evaluate(() => ({
    plan: !document.getElementById("pane2d").hidden,
    model: !document.getElementById("view3d").hidden,
    layout: window.BIMSGApp.state.layout }));
  ok("IFC model hides the plan",
     !panes.plan && panes.model && panes.layout === "model", JSON.stringify(panes));

  await page.click("#tab2d");
  await page.waitForTimeout(300);
  panes = await page.evaluate(() => ({
    plan: !document.getElementById("pane2d").hidden,
    model: !document.getElementById("view3d").hidden }));
  ok("Plan hides the model", panes.plan && !panes.model, JSON.stringify(panes));

  console.log("\n-- the IFC renders --");
  await page.click("#tabSplit");
  // Loading and parsing a real model takes a while under swiftshader.
  await page.waitForFunction(
    () => window.BIMSGApp.viewer && window.BIMSGApp.viewer.meshes.length > 0,
    null, { timeout: 180000 }).catch(() => {});
  await page.waitForTimeout(1500);

  const v = await page.evaluate(() => {
    const vw = window.BIMSGApp.viewer;
    if (!vw || !vw.meshes.length) return null;
    const c = vw.camera.position, t = vw.controls.target;
    return {
      meshes: vw.meshes.length, storeys: vw.storeys.length, bounds: vw.bounds,
      cam: [c.x, c.y, c.z], target: [t.x, t.y, t.z],
      dist: c.distanceTo(t),
      canvas: (() => { const cv = vw.renderer.domElement;
        return [cv.width, cv.height]; })(),
    };
  });
  ok("the IFC produced meshes", v && v.meshes > 0, v ? `${v.meshes}` : "no viewer");

  if (v) {
    ok(`the camera is at a finite distance (${v.dist.toFixed(1)} m)`,
       Number.isFinite(v.dist) && v.dist > 0 && v.dist < 1e6, String(v.dist));
    ok("the camera position is finite", v.cam.every(Number.isFinite), v.cam.join(","));
    ok(`the model has a sane height (${(v.bounds.max - v.bounds.min).toFixed(1)} m)`,
       Number.isFinite(v.bounds.min) && Number.isFinite(v.bounds.max) &&
       v.bounds.max - v.bounds.min > 1 && v.bounds.max - v.bounds.min < 500,
       JSON.stringify(v.bounds));
    ok(`storeys were read (${v.storeys})`, v.storeys > 0);
    ok("the 3D canvas has a real size", v.canvas[0] > 10 && v.canvas[1] > 10,
       v.canvas.join("x"));
  }

  // Something must actually be drawn: sample the canvas and check it is not
  // a flat background. This is the assertion no amount of module testing gives.
  const painted = await page.evaluate(() => {
    const cv = window.BIMSGApp.viewer.renderer.domElement;
    const tmp = document.createElement("canvas");
    tmp.width = cv.width; tmp.height = cv.height;
    tmp.getContext("2d").drawImage(cv, 0, 0);
    const d = tmp.getContext("2d").getImageData(0, 0, tmp.width, tmp.height).data;
    const seen = new Set();
    for (let i = 0; i < d.length; i += 4 * 97)
      seen.add(`${d[i] >> 4},${d[i+1] >> 4},${d[i+2] >> 4}`);
    return seen.size;
  }).catch(() => 0);
  ok(`the 3D canvas is not blank (${painted} distinct colours)`, painted > 3,
     `only ${painted}`);

  console.log("\n-- framing on the rooms --");
  const fr = await page.evaluate(() => {
    const A = window.BIMSGApp, vw = A.viewer;
    const fp = A.roomFootprint();
    A.frameRooms();
    for (let i = 0; i < 300; i++) vw.controls.update();   // settle the damping
    vw.camera.updateMatrixWorld(true);

    // Project the footprint corners. If the plan-to-world mapping or the
    // recentring offset were wrong, the building would sit off to one side and
    // these would land outside the viewport -- which is exactly what a bad
    // offset looked like on screen.
    const el = vw.renderer.domElement;
    const y = (vw.bounds.min + vw.bounds.max) / 2;
    const corners = [[fp.minX, fp.minY], [fp.maxX, fp.minY],
                     [fp.minX, fp.maxY], [fp.maxX, fp.maxY]];
    const screen = corners.map(([x, z]) => {
      const p = vw.planToWorld(x, z, y).project(vw.camera);
      return [p.x, p.y];
    });
    return {
      fp, offset: vw.offset,
      onScreen: screen.filter(([sx, sy]) =>
        sx >= -1 && sx <= 1 && sy >= -1 && sy <= 1).length,
      screen, hits: vw.hitsGeometry(),
      canvas: [el.width, el.height],
    };
  });
  ok("the plan yields a room footprint", !!fr.fp);
  ok(`the model was recentred by a known offset ` +
     `(${fr.offset.x.toFixed(1)}, ${fr.offset.z.toFixed(1)})`,
     Number.isFinite(fr.offset.x) && Number.isFinite(fr.offset.z));
  ok(`all four footprint corners are on screen (${fr.onScreen}/4)`,
     fr.onScreen === 4, JSON.stringify(fr.screen.map((p) => p.map((n) => n.toFixed(2)))));
  ok("a ray to the camera target meets the model", fr.hits);

  await page.screenshot({ path: `${SHOTS}/02-split.png` });

  console.log("\n-- the section --");
  const sec = await page.evaluate(() => {
    const vw = window.BIMSGApp.viewer;
    const b = vw.bounds;
    const mid = (b.min + b.max) / 2;
    vw.setSection(null, mid);
    return { planes: vw._planes.map((p) => ({ n: [p.normal.x, p.normal.y, p.normal.z],
                                              c: p.constant })), mid };
  });
  ok("a section produces a clipping plane", sec.planes.length > 0);
  ok("the section plane is horizontal (cuts on Y, not X or Z)",
     sec.planes.every((p) => Math.abs(p.n[1]) === 1 && p.n[0] === 0 && p.n[2] === 0),
     JSON.stringify(sec.planes.map((p) => p.n)));

  const slab = await page.evaluate(() => {
    const vw = window.BIMSGApp.viewer;
    const sp = vw.storeySlab(0);
    if (!sp) return null;
    vw.setSection(sp.bottom, sp.top);
    return { sp, n: vw._planes.length,
             storey0: vw.storeys[0] ? vw.storeys[0].elevation : null };
  });
  ok("a storey slab produces two planes", slab && slab.n === 2,
     slab ? String(slab.n) : "none");
  if (slab) {
    ok(`the slab sits at storey 0's elevation (${slab.sp.bottom.toFixed(2)} m)`,
       Math.abs(slab.sp.bottom - slab.storey0) < 0.5,
       `${slab.sp.bottom} vs ${slab.storey0}`);
    ok("the slab is roughly one storey tall",
       slab.sp.top - slab.sp.bottom > 1 && slab.sp.top - slab.sp.bottom < 8,
       String(slab.sp.top - slab.sp.bottom));
  }

  // follow-floor: changing storey must move the section with it
  const follow = await page.evaluate(async () => {
    const A = window.BIMSGApp;
    A.state.followFloor = true;
    document.getElementById("cutFollow").checked = true;
    document.getElementById("cutSlab").checked = true;
    A.state.storey = 0; A.syncSectionToFloor();
    const first = A.viewer._planes.map((p) => p.constant).join(",");
    if (A.state.plan.storeys.length < 2) return { skip: true };
    A.state.storey = 1; A.syncSectionToFloor();
    const second = A.viewer._planes.map((p) => p.constant).join(",");
    return { first, second };
  });
  if (follow.skip) console.log("  (only one storey; follow-floor not exercised)");
  else ok("follow floor moves the section when the storey changes",
          follow.first !== follow.second, `${follow.first} vs ${follow.second}`);

  const ticks = await page.locator("#cutTicks i").count();
  ok(`the slider shows a tick per storey (${ticks})`, ticks > 0);

  await page.screenshot({ path: `${SHOTS}/03-section.png` });
}

console.log("\n-- errors seen over the whole run --");
ok("no uncaught errors at any point", errors.length === 0, errors.join(" | "));

await browser.close();
process.exit(failures ? 1 : 0);
"""


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep-shots", action="store_true",
                    help="copy the screenshots out instead of discarding them")
    ap.add_argument("--shots-dir", default="e2e-shots")
    args = ap.parse_args()

    if not os.path.isdir(PLAYWRIGHT):
        print(f"SKIP playwright not found at {PLAYWRIGHT}")
        return 0
    try:
        subprocess.run([NODE, "--version"], capture_output=True, check=True)
    except Exception:
        print(f"SKIP node not found (tried {NODE!r})")
        return 0

    with tempfile.TemporaryDirectory() as td:
        site = os.path.join(td, "site")
        # Publish one real IFC so the 3D half of the run has something to draw,
        # without copying the whole corpus into a temp directory.
        if build(site, quiet=True, with_ifc=False) != 0:
            return 1
        ifc_name = ""
        src_ifc = sorted([f for f in os.listdir(os.path.join(HERE, "data"))
                          if f.endswith(".ifc")])
        if src_ifc:
            ifc_name = src_ifc[0][:-4]
            shutil.copy2(os.path.join(HERE, "data", src_ifc[0]),
                         os.path.join(site, "data", src_ifc[0]))
            mpath = os.path.join(site, "manifest.json")
            with open(mpath) as fh:
                man = json.load(fh)
            for m in man["models"]:
                if m["model"] == ifc_name:
                    m["hasIfc"] = True
                    m["ifcBytes"] = os.path.getsize(
                        os.path.join(site, "data", src_ifc[0]))
            # The 3D half only runs for the model the page opens first.
            man["models"].sort(key=lambda m: (not m.get("hasIfc"), m["model"]))
            with open(mpath, "w") as fh:
                json.dump(man, fh)
            print(f"publishing {ifc_name}.ifc for the 3D checks")
        else:
            print("no IFC in annotator/data — the 3D checks will be skipped")

        shots = os.path.join(td, "shots")
        os.makedirs(shots, exist_ok=True)

        handler = lambda *a, **k: Quiet(*a, directory=site, **k)   # noqa: E731
        with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
            port = httpd.server_address[1]
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{port}/"
            print(f"serving the built site at {base}")

            drv = os.path.join(td, "e2e.mjs")
            with open(drv, "w") as fh:
                fh.write(DRIVER.replace("PLAYWRIGHT_PATH", PLAYWRIGHT))

            env = dict(os.environ)
            # Chromium here needs libasound from the conda package cache.
            extra = []
            pkgs = os.path.join(os.path.dirname(os.path.dirname(
                os.path.dirname(sys.executable))), "pkgs")
            if os.path.isdir(pkgs):
                for d in sorted(os.listdir(pkgs), reverse=True):
                    if d.startswith("alsa-lib-") and os.path.isdir(
                            os.path.join(pkgs, d, "lib")):
                        extra.append(os.path.join(pkgs, d, "lib"))
                        break
            extra.append(os.path.join(os.path.dirname(
                os.path.dirname(sys.executable)), "lib"))
            env["LD_LIBRARY_PATH"] = os.pathsep.join(
                extra + [env.get("LD_LIBRARY_PATH", "")])

            r = subprocess.run([NODE, drv, base, shots, ifc_name],
                               capture_output=True, text=True, env=env)
            print(r.stdout.rstrip())
            # Say plainly how many checks failed. Reading the tail of a long
            # log and counting only the PASS lines is how a red run gets
            # reported as green.
            npass = r.stdout.count("  PASS ")
            nfail = r.stdout.count("  FAIL ")
            print(f"\n{npass} passed, {nfail} failed")
            if nfail:
                print("failing checks:")
                for line in r.stdout.splitlines():
                    if line.startswith("  FAIL "):
                        print("  " + line.strip())
            # Always show the driver's own error: a crash mid-run leaves partial
            # stdout, and hiding stderr behind "stdout was empty" loses it.
            if r.returncode != 0 and r.stderr.strip():
                print("\n-- driver error --")
                print(r.stderr.rstrip()[-2500:])
            httpd.shutdown()

            if args.keep_shots and os.path.isdir(shots):
                os.makedirs(args.shots_dir, exist_ok=True)
                for f in os.listdir(shots):
                    shutil.copy2(os.path.join(shots, f),
                                 os.path.join(args.shots_dir, f))
                print(f"\nscreenshots -> {args.shots_dir}/")
            if r.returncode:
                return 1

    print("PASS the annotator works end to end in a real browser")
    return 0


if __name__ == "__main__":
    sys.exit(main())
