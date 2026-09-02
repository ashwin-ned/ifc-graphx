"""Check the dataset-folder backend, including that it writes work back to disk.

`fsdir.js` is the backend that matters most to get right: it is the only one
that writes into the annotator's own filesystem, and the failure that costs a
day's work -- a truncated or unwritten annotation file -- leaves no trace until
someone opens the folder and finds it empty.

Node has no File System Access API, so this shims the small part of it that
`fsdir.js` uses over a real temporary directory. The store is then driven for
real: open a folder, annotate, confirm the file appears on disk with the right
contents, reopen the folder from scratch and confirm the work comes back.

It also forces the case the shim exists to catch -- two saves racing on one
file handle, which can interleave and truncate the JSON.

    python annotator/test_dir_mode.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
NODE = os.environ.get("NODE_BIN") or "node"

TEST = r"""
import fs from "node:fs/promises";
import path from "node:path";
const DIR = process.argv[2];
const STATIC = process.argv[3];

/* --- the slice of the File System Access API that fsdir.js uses ---------- */
function fileHandle(full, name) {
  return {
    kind: "file", name,
    async getFile() {
      const buf = await fs.readFile(full);
      return {
        name,
        async text() { return buf.toString("utf8"); },
        async arrayBuffer() { return buf.buffer.slice(
          buf.byteOffset, buf.byteOffset + buf.byteLength); },
      };
    },
    async createWritable() {
      let acc = "";
      return {
        async write(chunk) {
          // Real writables stream; yielding here lets a racing save interleave,
          // which is exactly what we want to prove cannot corrupt the file.
          await new Promise((r) => setTimeout(r, 1));
          acc += chunk;
        },
        async close() { await fs.writeFile(full, acc); },
      };
    },
  };
}
function dirHandle(full, name) {
  return {
    kind: "directory", name,
    async *entries() {
      for (const e of await fs.readdir(full, { withFileTypes: true }))
        yield [e.name, e.isDirectory()
          ? dirHandle(path.join(full, e.name), e.name)
          : fileHandle(path.join(full, e.name), e.name)];
    },
    async getFileHandle(n, opts) {
      const p = path.join(full, n);
      if (opts && opts.create) { try { await fs.access(p); } catch { await fs.writeFile(p, ""); } }
      return fileHandle(p, n);
    },
    async queryPermission() { return "granted"; },
    async requestPermission() { return "granted"; },
  };
}

globalThis.window = globalThis;
globalThis.BIMSG_CONFIG = { mode: "local" };
for (const f of ["compose.js", "fsdir.js"]) {
  const src = await fs.readFile(path.join(STATIC, f), "utf8");
  (0, eval)(src);
}

let failures = 0;
const ok = (m, c) => { if (!c) failures++; console.log((c ? "  PASS " : "  FAIL ") + m); };

ok("isSupported() is false without the browser API", BIMSGDir.isSupported() === false);

const root = dirHandle(DIR, path.basename(DIR));
const store = await BIMSGDir.DirStore.fromHandle(root);
ok("store opens the folder", store.mode === "dir");

const models = await store.listModels();
ok(`found ${models.length} plans in the folder`, models.length === 2);
ok("IFC files are detected beside the plans", models.every((m) => m.hasIfc));
ok("models are listed in natural order", models[0].model === "model_0");

const name = models[0].model;
const plan = await store.getPlan(name);
ok("plan parses from the folder", Array.isArray(plan.storeys));

const f = await store.getIfcFile(name);
ok("IFC file is reachable for the 3D view", !!f && typeof f.arrayBuffer === "function");

/* --- annotate and confirm it lands on disk ------------------------------ */
const a = await store.getAnnotation(name, "dana");
for (const st of plan.storeys) {
  for (const r of st.rooms) a.rooms[r.id] = { verdict: "correct" };
  for (const e of st.edges)
    a.edges[BIMSGCompose.key(e.a, e.b)] = { verdict: "correct", a: e.a, b: e.b };
}
for (const v of (plan.vertical || []))
  a.vertical[BIMSGCompose.key(v.a, v.b)] = { verdict: "correct", a: v.a, b: v.b };
await store.saveAnnotation(name, "dana", a);

const onDisk = path.join(DIR, BIMSGDir.ANNO_FILE);
const raw = await fs.readFile(onDisk, "utf8");
ok("annotation file written into the dataset folder", raw.length > 0);
let doc = null;
try { doc = JSON.parse(raw); } catch (e) { /* reported below */ }
ok("file on disk is valid JSON", doc !== null);
ok("file is in the same bundle format as the download",
   doc && doc.format === "bimsg-annotation-bundle" && doc.annotations.length === 1);

/* --- concurrent saves must not truncate it ------------------------------ */
const second = await store.getAnnotation(models[1].model, "dana");
const p2 = await store.getPlan(models[1].model);
for (const st of p2.storeys) for (const r of st.rooms) second.rooms[r.id] = { verdict: "unsure" };
await Promise.all([
  store.saveAnnotation(name, "dana", a),
  store.saveAnnotation(models[1].model, "dana", second),
  store.saveAnnotation(name, "dana", a),
]);
let doc2 = null;
try { doc2 = JSON.parse(await fs.readFile(onDisk, "utf8")); } catch (e) { /* below */ }
ok("racing saves leave valid JSON", doc2 !== null);
ok("racing saves keep both models", doc2 && doc2.annotations.length === 2);

/* --- reopening the folder restores the work ----------------------------- */
const reopened = await BIMSGDir.DirStore.fromHandle(dirHandle(DIR, path.basename(DIR)));
ok(`reopening restores ${reopened.restored} annotation(s)`, reopened.restored === 2);
ok("restored annotator name is offered", reopened.restoredAnnotator === "dana");
const back = await reopened.getAnnotation(name, "dana");
ok("restored work is intact",
   Object.keys(back.rooms).length === Object.keys(a.rooms).length);

const g = await reopened.composeGraph(name, "dana");
ok("composes a complete building graph from the folder", g.complete === true);
ok("nothing is ever 'unexported' in folder mode", reopened.unexported().length === 0);

/* --- a corrupt file must be reported, not silently overwritten ---------- */
await fs.writeFile(onDisk, "{ this is not json");
const broken = await BIMSGDir.DirStore.fromHandle(dirHandle(DIR, path.basename(DIR)));
ok("a corrupt annotation file is reported", !!broken.loadError);

process.exit(failures ? 1 : 0);
"""


def main():
    try:
        subprocess.run([NODE, "--version"], capture_output=True, check=True)
    except Exception:
        print(f"node not found (tried {NODE!r}); set NODE_BIN to your node binary")
        return 2

    plans = [os.path.join(HERE, "data", f"{m}.plan.json") for m in ("model_0", "model_9")]
    for p in plans:
        if not os.path.exists(p):
            print(f"missing {p} — run main/export_plans.py first")
            return 1

    with tempfile.TemporaryDirectory() as td:
        folder = os.path.join(td, "dataset")
        os.makedirs(folder)
        for p in plans:
            shutil.copy2(p, folder)
        # Stand-ins for the IFC files: the store only has to find and open them.
        for m in ("model_0", "model_9"):
            with open(os.path.join(folder, f"{m}.ifc"), "w") as fh:
                fh.write("ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n")

        drv = os.path.join(td, "t.mjs")
        with open(drv, "w") as fh:
            fh.write(TEST)
        r = subprocess.run([NODE, drv, folder, os.path.join(HERE, "static")],
                           capture_output=True, text=True)
        print(r.stdout.rstrip())
        if r.returncode != 0:
            print(r.stderr.rstrip())
            return 1

    print("\nPASS the dataset-folder backend saves and restores work correctly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
