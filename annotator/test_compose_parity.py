"""Prove `compose.js` and `compose.py` agree, on real plans.

The statically hosted annotator has no server, so it composes the building graph
in the browser; the collection script composes it again in Python when ground
truth is built. Two implementations of the same rules is a liability: if they
drift, an annotator signs off on one graph and the dataset receives a different
one, silently.

This runs both over every exported plan, under randomised verdicts that exercise
each branch (correct / spurious / unsure / merge / split / unjudged, added links,
added floor links, dropped endpoints), and fails on the first difference.

    python annotator/test_compose_parity.py
"""

from __future__ import annotations

import glob
import json
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from compose import compose, connectivity_gt   # noqa: E402

NODE = os.environ.get("NODE_BIN") or "node"
VERDICTS = ["correct", "spurious", "unsure", "merge", "split", None]
LINK_VERDICTS = ["correct", "spurious", "unsure", None]

DRIVER = r"""
const fs = require("fs");
const C = require(process.argv[2]);
const plan = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const anno = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));
const g = C.compose(plan, anno);
process.stdout.write(JSON.stringify({graph: g, gt: C.connectivityGt(g)}));
"""


def _key(a, b):
    return f"{a}|{b}" if a < b else f"{b}|{a}"


def random_anno(plan, rng):
    """A plausible annotation touching every branch of the composer."""
    rooms, edges, vert = {}, {}, {}
    for st in plan["storeys"]:
        for r in st["rooms"]:
            v = rng.choice(VERDICTS)
            if v is None:
                continue
            e = {"verdict": v}
            if rng.random() < 0.3:
                e["label"] = "relabelled"
            if rng.random() < 0.2:
                e["note"] = "a note"
            # Swept verdicts carry a flag both composers must agree about.
            if rng.random() < 0.35:
                e["bulk"] = True
            rooms[r["id"]] = e
        for e in st["edges"]:
            v = rng.choice(LINK_VERDICTS)
            if v is not None:
                edges[_key(e["a"], e["b"])] = {
                    "verdict": v, "a": e["a"], "b": e["b"],
                    **({"bulk": True} if rng.random() < 0.35 else {})}
    for v in plan.get("vertical") or []:
        k = rng.choice(LINK_VERDICTS)
        if k is not None:
            vert[_key(v["a"], v["b"])] = {"verdict": k, "a": v["a"], "b": v["b"]}

    # Added items, including some whose endpoints were never confirmed so the
    # `held_out` path is exercised on both sides.
    ids = [r["id"] for st in plan["storeys"] for r in st["rooms"]]
    added_e, added_v = [], []
    if len(ids) >= 2:
        for _ in range(rng.randint(0, 3)):
            a, b = rng.sample(ids, 2)
            added_e.append({"id": "a" + str(rng.randint(0, 1 << 30)), "a": a, "b": b,
                            "storey": plan["storeys"][0]["gid"], "type": "manual"})
        for _ in range(rng.randint(0, 2)):
            a, b = rng.sample(ids, 2)
            added_v.append({"id": "v" + str(rng.randint(0, 1 << 30)), "a": a, "b": b,
                            "storey_a": plan["storeys"][0]["gid"],
                            "storey_b": plan["storeys"][-1]["gid"], "kind": "manual"})
    return {"rooms": rooms, "edges": edges, "vertical": vert,
            "added_edges": added_e, "added_vertical": added_v,
            "missing_rooms": [], "annotator": "parity", "updated": "2026-01-01T00:00:00"}


def diff(a, b, path="") -> str | None:
    """First structural difference between two JSON values, or None."""
    if type(a) is not type(b) and not (
            isinstance(a, (int, float)) and isinstance(b, (int, float))):
        return f"{path}: type {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                return f"{path}.{k}: missing in python"
            if k not in b:
                return f"{path}.{k}: missing in js"
            d = diff(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: length {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    if isinstance(a, float) or isinstance(b, float):
        return None if abs(a - b) < 1e-9 else f"{path}: {a} vs {b}"
    return None if a == b else f"{path}: {a!r} vs {b!r}"


def main():
    plans = sorted(glob.glob(os.path.join(HERE, "data", "*.plan.json")))
    if not plans:
        print("no plans in annotator/data — run main/export_plans.py first")
        return 1

    try:
        subprocess.run([NODE, "--version"], capture_output=True, check=True)
    except Exception:
        print(f"node not found (tried {NODE!r}); set NODE_BIN to your node binary")
        return 2

    js = os.path.join(HERE, "static", "compose.js")
    rng = random.Random(20260902)
    checked = 0
    with tempfile.TemporaryDirectory() as td:
        drv = os.path.join(td, "driver.js")
        with open(drv, "w") as fh:
            fh.write(DRIVER)
        for p in plans:
            plan = json.load(open(p))
            for trial in range(3):
                anno = random_anno(plan, rng)
                ap = os.path.join(td, "anno.json")
                with open(ap, "w") as fh:
                    json.dump(anno, fh)

                g_py = compose(plan, anno)
                out_py = {"graph": g_py, "gt": connectivity_gt(g_py)}
                r = subprocess.run([NODE, drv, js, p, ap],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    print(f"FAIL {os.path.basename(p)} trial {trial}: node error\n{r.stderr}")
                    return 1
                out_js = json.loads(r.stdout)

                # Round-trip the Python side through JSON so tuples/ints compare
                # on the same footing as the JS side.
                d = diff(json.loads(json.dumps(out_py)), out_js, os.path.basename(p))
                if d:
                    print(f"FAIL parity differs at {d}")
                    return 1
                checked += 1
    print(f"PASS compose.py and compose.js agree — {len(plans)} plans x 3 "
          f"randomised annotations = {checked} comparisons")
    return 0


if __name__ == "__main__":
    sys.exit(main())
