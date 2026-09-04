"""Check the annotators' checklist says true things about a corpus.

The CSV decides what 200-odd models someone does with their week, so the
failure that matters is not a crash -- it is a row that reads plausibly and is
wrong. A model marked done that is half-judged never gets finished; a model
marked annotatable that has no storeys wastes the afternoon it takes to find
that out.

So the corpus here is synthetic and every answer is known in advance: one
ordinary building, one with no storeys at all, one with no way between floors,
and one already annotated but not finished.

    python annotator/test_checklist.py
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import compose  # noqa: E402

failures = 0


def ok(msg: str, cond: bool) -> None:
    global failures
    if not cond:
        failures += 1
    print(("  PASS " if cond else "  FAIL ") + msg)


def ifc_text(storeys: int, spaces: int, doors: int) -> str:
    """Just enough STEP for the text scanner: it counts lines, nothing more."""
    out = ["ISO-10303-21;", "HEADER;", "ENDSEC;", "DATA;"]
    n = 100
    for _ in range(storeys):
        out.append(f"#{n}=IFCBUILDINGSTOREY('g{n}',$,$,$,$,$,$,$,.ELEMENT.,0.);"); n += 1
    for _ in range(spaces):
        out.append(f"#{n}=IFCSPACE('g{n}',$,$,$,$,$,$,$,.ELEMENT.,$,$);"); n += 1
    for _ in range(doors):
        out.append(f"#{n}=IFCDOOR('g{n}',$,$,$,$,$,$,$,$,$);"); n += 1
    out += ["ENDSEC;", "END-ISO-10303-21;"]
    return "\n".join(out) + "\n"


def plan(model, storeys, rooms_per, *, elevations=None, vertical=True,
         doors=2, area=20.0):
    sts = []
    rid = 0
    elevations = elevations or [i * 3.5 for i in range(storeys)]
    for i in range(storeys):
        rooms = []
        for _ in range(rooms_per):
            rid += 1
            rooms.append({"id": f"r{rid}", "label": f"Room {rid}", "source": "ifc",
                          "area": area, "centroid": [rid * 2.0, 0.0],
                          "polygon": [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]})
        edges = [{"a": rooms[k]["id"], "b": rooms[k + 1]["id"],
                  "type": "connected_by_door"} for k in range(len(rooms) - 1)]
        sts.append({"gid": f"s{i}", "name": f"Level{i}", "elevation": elevations[i],
                    "walls": [], "doors": [{"id": f"d{i}_{j}", "x": 0.0, "y": 0.0,
                                            "nx": 1.0, "ny": 0.0}
                                           for j in range(doors)],
                    "rooms": rooms, "edges": edges})
    vert = []
    if vertical:
        for i in range(storeys - 1):
            vert.append({"a": sts[i]["rooms"][0]["id"],
                         "b": sts[i + 1]["rooms"][0]["id"],
                         "type": "vertically_connected",
                         "storey_a": f"s{i}", "storey_b": f"s{i+1}"})
    return {"model": model, "source": "test", "stats": {},
            "storeys": sts, "vertical": vert}


def full_annotation(p, who, *, skip_last_room=False):
    a = {"model": p["model"], "annotator": who, "rooms": {}, "edges": {},
         "vertical": {}, "added_edges": [], "added_vertical": [],
         "missing_rooms": []}
    rooms = [r for s in p["storeys"] for r in s["rooms"]]
    for r in (rooms[:-1] if skip_last_room else rooms):
        a["rooms"][r["id"]] = {"verdict": "real"}
    for s in p["storeys"]:
        for e in s["edges"]:
            a["edges"][compose._key(e["a"], e["b"])] = {
                "verdict": "passable", "a": e["a"], "b": e["b"]}
    for v in p["vertical"]:
        a["vertical"][compose._key(v["a"], v["b"])] = {
            "verdict": "passable", "a": v["a"], "b": v["b"]}
    return a


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        corpus = os.path.join(td, "corpus")
        plans = os.path.join(corpus, "plans")
        os.makedirs(plans)

        # normal, no-storeys, no-lift-or-stair, finished, half-finished
        spec = {
            "model_ok":        (3, 4, dict()),
            "model_nostorey":  (0, 0, dict()),
            "model_nolift":    (3, 4, dict(vertical=False)),
            "model_done":      (2, 3, dict()),
            "model_partial":   (2, 3, dict()),
            "model_mezzanine": (3, 4, dict(elevations=[0.0, 0.6, 4.1])),
            "model_nodoors":   (2, 3, dict(doors=0)),
            "model_huge":      (2, 3, dict(area=900.0)),
        }
        for name, (st, per, kw) in spec.items():
            with open(os.path.join(corpus, f"{name}.ifc"), "w") as fh:
                fh.write(ifc_text(st, st * per, 4 if kw.get("doors") != 0 else 0))
            p = plan(name, st, per, **kw)
            with open(os.path.join(plans, f"{name}.plan.json"), "w") as fh:
                json.dump(p, fh)

        # Two returned annotations: one finished, one a room short.
        bundle = {"format": "bimsg-annotation-bundle", "annotations": []}
        for name, short in (("model_done", False), ("model_partial", True)):
            with open(os.path.join(plans, f"{name}.plan.json")) as fh:
                p = json.load(fh)
            bundle["annotations"].append(
                full_annotation(p, "dana", skip_last_room=short))
        abundle = os.path.join(td, "returned.json")
        with open(abundle, "w") as fh:
            json.dump(bundle, fh)

        out = os.path.join(td, "checklist.csv")
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "make_checklist.py"),
             "--ifc", corpus, "--out", out, "--annotations", abundle],
            capture_output=True, text=True)
        ok("the script exits clean", r.returncode == 0)
        if r.returncode != 0:
            print(r.stdout + r.stderr)
            return 1

        with open(out, newline="") as fh:
            rows = {x["ifc_file"][:-len(".ifc")]: x for x in csv.DictReader(fh)}

        ok(f"one row per IFC ({len(rows)})", len(rows) == len(spec))
        ok("the columns an annotator needs are all there",
           {"done", "ifc_file", "annotator", "status", "notes"} <=
           set(next(iter(rows.values())).keys()))

        n = rows["model_nostorey"]
        ok("a model with no storeys is marked skip", n["status"] == "skip")
        ok("its box is left blank, not offered as work", n["done"] == "")
        ok("and the row says why", "IfcBuildingStorey" in n["notes"])

        g = rows["model_ok"]
        ok("an ordinary building is marked ok", g["status"] == "ok")
        ok("with an empty box to tick", g["done"] == "[ ]")
        ok("and nothing alarming said about it", g["notes"] == "")
        ok("its size is reported for planning",
           g["storeys"] == "3" and g["rooms"] == "12")

        ok("a building with no floor-to-floor links is flagged",
           "floor-to-floor" in rows["model_nolift"]["notes"])
        ok("storeys 0.6 m apart are flagged as not a real floor",
           "apart" in rows["model_mezzanine"]["notes"])
        ok("a building with no doors is flagged",
           "no doors" in rows["model_nodoors"]["notes"])
        ok("900 m² 'rooms' are flagged as whole floors",
           "very large" in rows["model_huge"]["notes"])

        d = rows["model_done"]
        ok("a finished annotation is ticked", d["done"] == "[x]")
        ok("and credited to whoever did it", d["annotator"] == "dana")

        pr = rows["model_partial"]
        ok("an unfinished annotation is NOT ticked", pr["done"] == "[~]")
        ok("it names who has it", "dana" in pr["notes"])
        ok(f"and says exactly what is left ({pr['notes'][:44]}...)",
           "1 room" in pr["notes"] and "still to judge" in pr["notes"])

        untouched = [k for k, v in rows.items() if v["done"] == "[ ]"]
        ok(f"everything else is left unticked ({len(untouched)})",
           len(untouched) == len(spec) - 3)

    print()
    return failures


if __name__ == "__main__":
    n = main()
    print(f"FAIL the checklist has {n} problem(s)" if n else
          "PASS the checklist marks each model with what is actually true of it")
    sys.exit(1 if n else 0)
