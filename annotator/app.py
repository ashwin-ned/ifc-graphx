"""BIM-Graphs ground-truth annotation server.

Annotation is by *correction*: the pipeline's prediction is drawn over the real
floor plan and the annotator adjudicates it. Labelling 25 buildings from scratch
is prohibitive; judging predictions is not, and it yields exactly the
precision/recall the necessary-condition checks cannot supply.

    python annotator/app.py --port 8000
    # then open http://localhost:8000

Annotations are stored one file per (model, annotator) so the same building can
be labelled independently by two people and inter-annotator agreement measured.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compose import compose

HERE = os.path.dirname(os.path.abspath(__file__))
# Overridable so a plan set augmented by resolve_pins.py can be served without
# copying it over the originals.
# Absolute, both of them: send_from_directory resolves a relative directory
# against the app's root path (annotator/), so a relative --plans or
# BIMSG_PLANS pointing anywhere else served 404s while os.path.exists said the
# file was right there. --plans already absolutised; the env vars did not.
DATA = os.path.abspath(os.environ.get("BIMSG_PLANS") or os.path.join(HERE, "data"))
ANNO = os.path.abspath(os.environ.get("BIMSG_ANNOTATIONS")
                       or os.path.join(HERE, "annotations"))

# `static_url_path=""` serves the app's files from the root, so index.html can
# reference them relatively ("app.js", not "/static/app.js"). That matters
# because the same index.html is published to GitHub Pages, where the site lives
# under /<repo>/ and any absolute path would 404. One set of paths, both builds.
app = Flask(__name__, static_folder=os.path.join(HERE, "static"),
            static_url_path="")

SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _safe(name: str) -> str:
    """Reject anything that could escape the annotations directory."""
    if not name or not SAFE.match(name):
        raise ValueError(f"unsafe name: {name!r}")
    return name


def anno_path(model: str, annotator: str) -> str:
    return os.path.join(ANNO, f"{_safe(model)}__{_safe(annotator)}.json")


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/models")
def models():
    """Available plans, with per-annotator progress."""
    out = []
    for p in sorted(glob.glob(os.path.join(DATA, "*.plan.json"))):
        name = os.path.basename(p).replace(".plan.json", "")
        with open(p) as fh:
            doc = json.load(fh)
        n_rooms = sum(len(s["rooms"]) for s in doc["storeys"])
        n_edges = sum(len(s["edges"]) for s in doc["storeys"])
        done = {}
        for a in glob.glob(os.path.join(ANNO, f"{name}__*.json")):
            who = os.path.basename(a).replace(f"{name}__", "").replace(".json", "")
            try:
                with open(a) as fh:
                    d = json.load(fh)
                done[who] = len(d.get("rooms", {})) + len(d.get("edges", {}))
            except Exception:
                done[who] = 0
        n_vert = len(doc.get("vertical") or [])
        # The 3D view needs to know an IFC is available before it offers the
        # tab. Without this the server build could never show the model, even
        # with the files sitting in annotator/data next to the plans.
        d = ifc_dir(name)
        ifc = os.path.join(d, f"{name}.ifc") if d else None
        has_ifc = ifc is not None
        out.append({"model": name, "storeys": len(doc["storeys"]),
                    "rooms": n_rooms, "edges": n_edges, "vertical": n_vert,
                    "items": n_rooms + n_edges + n_vert, "annotated": done,
                    "hasIfc": has_ifc,
                    "ifcBytes": os.path.getsize(ifc) if has_ifc else 0})
    return jsonify(out)


def ifc_dir(name: str):
    """Where this model's IFC lives, or None.

    Beside the plan is the normal case. The parent is checked too because a
    corpus is naturally kept as a directory of IFC files with the plans
    exported into a `plans/` subfolder -- pointing --plans at that subfolder
    should not silently cost the 3D view.
    """
    for d in (DATA, os.path.dirname(os.path.abspath(DATA))):
        if os.path.exists(os.path.join(d, f"{name}.ifc")):
            return d
    return None


@app.route("/api/plan/<model>")
def plan(model):
    p = os.path.join(DATA, f"{_safe(model)}.plan.json")
    if not os.path.exists(p):
        return jsonify({"error": "not found"}), 404
    return send_from_directory(DATA, f"{model}.plan.json")


@app.route("/api/ifc/<model>")
def ifc(model):
    """The source IFC, for the 3D view. Streamed rather than read into memory:
    these run to tens of megabytes."""
    try:
        name = f"{_safe(model)}.ifc"
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    d = ifc_dir(name[:-len(".ifc")])
    if d is None:
        return jsonify({"error": "no IFC for this model"}), 404
    return send_from_directory(d, name, mimetype="application/octet-stream")


@app.route("/api/annotation/<model>/<annotator>", methods=["GET"])
def get_anno(model, annotator):
    try:
        p = anno_path(model, annotator)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not os.path.exists(p):
        return jsonify({"model": model, "annotator": annotator,
                        "rooms": {}, "edges": {}, "vertical": {},
                        "added_edges": [], "added_vertical": [],
                        "missing_rooms": []})
    with open(p) as fh:
        return jsonify(json.load(fh))


@app.route("/api/annotation/<model>/<annotator>", methods=["POST"])
def put_anno(model, annotator):
    try:
        p = anno_path(model, annotator)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    body = request.get_json(force=True) or {}
    body.update({"model": model, "annotator": annotator,
                 "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    os.makedirs(ANNO, exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(body, fh, indent=1)
    os.replace(tmp, p)          # atomic: a crash mid-save cannot truncate work
    return jsonify({"ok": True, "saved": os.path.basename(p),
                    "updated": body["updated"]})


@app.route("/api/export/<model>/<annotator>")
def export(model, annotator):
    """The annotation as one hierarchical building graph, storeys chained."""
    try:
        p = anno_path(model, annotator)
        plan_p = os.path.join(DATA, f"{_safe(model)}.plan.json")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not os.path.exists(plan_p):
        return jsonify({"error": "no such model"}), 404
    if not os.path.exists(p):
        return jsonify({"error": "nothing annotated yet"}), 404
    with open(plan_p) as fh:
        plan = json.load(fh)
    with open(p) as fh:
        anno = json.load(fh)
    return jsonify(compose(plan, anno))


@app.route("/api/progress")
def progress():
    """Corpus-level annotation status."""
    rows = []
    for a in sorted(glob.glob(os.path.join(ANNO, "*.json"))):
        try:
            with open(a) as fh:
                d = json.load(fh)
        except Exception:
            continue
        rooms = d.get("rooms", {})
        rows.append({
            "model": d.get("model"), "annotator": d.get("annotator"),
            "updated": d.get("updated"),
            "rooms_judged": len(rooms),
            "edges_judged": len(d.get("edges", {})),
            "vertical_judged": len(d.get("vertical", {})),
            "missing_rooms": len(d.get("missing_rooms", [])),
            "added_edges": len(d.get("added_edges", [])),
            "added_vertical": len(d.get("added_vertical", [])),
        })
    return jsonify(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    # 0.0.0.0 so the tool can be handed to annotators on the same network;
    # it holds no credentials and serves only these plans.
    ap.add_argument("--host", default="127.0.0.1",
                    help="use 0.0.0.0 to let other machines reach it")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--plans", help="plan directory to serve "
                                    "(default annotator/data)")
    args = ap.parse_args()

    global DATA
    if args.plans:
        DATA = os.path.abspath(args.plans)

    os.makedirs(ANNO, exist_ok=True)
    n = len(glob.glob(os.path.join(DATA, "*.plan.json")))
    if n == 0:
        print("!! no plans found in annotator/data")
        print("   run: python main/export_plans.py 'dataset/test_set/*.ifc' "
              "--out annotator/data")
    print(f"BIM-Graphs annotator: {n} model(s) available")
    print(f"   http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
