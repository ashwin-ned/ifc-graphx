"""Give a missing-room pin a footprint, so it can become a graph node.

An annotator who finds a room the pipeline never recovered drops a pin: a
point, a label and a storey. That records a real recall failure, but it cannot
enter the scene graph, because a node needs an extent -- without a polygon
there is nothing to compute adjacency against and no way to decide which rooms
it touches. Inventing edges from a click position would be guessing, and
guessed ground truth is worse than none.

So this derives a footprint from geometry that already exists, and proposes
links the annotator then judges like any other. Two strategies, tried in order:

  project    The same shaft on another storey. A stair modelled on the ground
             floor and pinned on the floor above is the same shaft continuing:
             the polygon comes from the file and the position was confirmed by
             a person. The nearest storey by elevation wins, being the most
             recent known cross-section.

  enclose    No vertical source, so recover it the way the pipeline does --
             flood-fill free space around the pin, bounded by that storey's
             walls. This catches rooms recovery rejected for being small or
             merged into a corridor.

Anything neither strategy resolves stays a pin: still a recorded recall miss,
still not a node. Refusing is the point -- a footprint that is not supported by
geometry would be fabrication dressed as ground truth.

Nothing is asserted. Every room this produces carries `source: "projected"` or
`"recovered"` and the evidence it came from, and every link it proposes is
marked `proposed` for an annotator to confirm or reject on a second pass.

    python annotator/resolve_pins.py --inbox ~/returned --out annotator/data-resolved
"""

from __future__ import annotations

import argparse
import copy
import glob
import json
import os
import sys

import numpy as np
from shapely.geometry import Polygon, Point, box
from shapely.ops import unary_union

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

RES = 0.05             # m per raster cell for the enclosure strategy
MAX_SPAN = 30.0        # m: how far from the pin the flood is allowed to reach
MIN_AREA = 1.0         # m^2: below this it is a cupboard, not a room
MAX_AREA = 400.0       # m^2: above this the fill has escaped into circulation
OVERLAP_TOL = 0.30     # a candidate overlapping an existing room by more than
                       # this fraction of the smaller area is a duplicate
DOOR_REACH = 1.20      # m: a door this close to the footprint serves it
NEIGHBOUR_REACH = 0.60  # m: rooms this close are candidate-adjacent


# Words that mean "this is a vertical shaft": the one class of room whose
# footprint genuinely repeats on every storey it passes through.
SHAFT_WORDS = ("stair", "staircase", "lift", "elevator", "escalator", "shaft",
               "duct", "riser", "mumti")


def poly_of(r) -> Polygon:
    return Polygon(r["polygon"], r.get("holes") or [])


def is_shaft(label) -> bool:
    t = str(label or "").lower()
    return any(w in t for w in SHAFT_WORDS)


def _validate(cand: Polygon, storey: dict, pin: dict) -> tuple:
    """(reason to refuse, notes). Reason is None when the candidate is usable."""
    if cand is None or cand.is_empty or not cand.is_valid:
        return "no usable geometry", []
    if not (MIN_AREA <= cand.area <= MAX_AREA):
        return f"area {cand.area:.1f} m2 outside {MIN_AREA}-{MAX_AREA}", []
    if cand.distance(Point(pin["x"], pin["y"])) > 0.5:
        return "footprint does not contain the pin", []

    notes = []
    for r in storey["rooms"]:
        p = poly_of(r)
        inter = cand.intersection(p).area
        if inter <= OVERLAP_TOL * min(cand.area, p.area):
            continue
        if r["source"] == "ifc":
            # The file already states a room here, so the pin duplicates it.
            return (f"overlaps IFC room {r['label']!r} by {inter:.1f} m2"), []
        # A recovered region overlapping is expected, not a clash: an unmodelled
        # stair void gets swallowed by the circulation the pipeline flood-filled
        # around it, which is exactly why the room went missing. Record it so
        # the annotator can see that region needs shrinking.
        notes.append({"overlaps_recovered": r["id"], "label": r["label"],
                      "area": round(inter, 2)})
    return None, notes


def project(pin: dict, plan: dict) -> tuple:
    """The same footprint from the nearest storey that has one under the pin."""
    here = next((s for s in plan["storeys"] if s["gid"] == pin["storey"]), None)
    if here is None:
        return None, None, "pin names a storey that is not in the plan"
    p = Point(pin["x"], pin["y"])

    # Only a vertical shaft repeats its footprint from floor to floor. Any
    # other room that happens to sit at the same coordinates on another storey
    # is a coincidence, and projecting it invents a room -- on one corpus model
    # that produced a 67 m2 "office" and a 133 m2 "hallway" out of unrelated
    # neighbours. So the pin or the source has to say it is a shaft.
    if not is_shaft(pin.get("label")):
        return None, None, None

    cands = []
    for st in plan["storeys"]:
        if st["gid"] == pin["storey"]:
            continue
        for r in st["rooms"]:
            if not is_shaft(r["label"]):
                continue
            g = poly_of(r)
            if g.contains(p):
                cands.append((abs(st["elevation"] - here["elevation"]), st, r, g))
    if not cands:
        return None, None, None          # not a failure; try the next strategy
    # Nearest storey by elevation: the most recent known section of the shaft.
    cands.sort(key=lambda c: c[0])
    _, st, r, g = cands[0]
    why, notes = _validate(g, here, pin)
    if why:
        return None, None, f"projection from {st['name']} rejected: {why}"
    ev = {"strategy": "project", "from_storey": st["gid"],
          "from_storey_name": st["name"], "from_room": r["id"],
          "from_room_label": r["label"], "notes": notes}
    if r.get("from_pin"):
        # A shaft can be carried up several floors, each step sourced from the
        # step below. That is sound for a shaft, but it is second-hand evidence
        # and the chain should be visible rather than read as if the file said
        # so directly.
        ev["via_projection"] = True
        ev["chain_from"] = r.get("evidence", {}).get("from_room")
    return g, ev, None


def _rle_polygon(mask: np.ndarray, x0: float, y0: float, res: float):
    """Polygon of a boolean raster, as the union of its row runs."""
    boxes = []
    for j in range(mask.shape[0]):
        row = mask[j]
        if not row.any():
            continue
        idx = np.flatnonzero(row)
        starts = [idx[0]]
        ends = []
        for a, b in zip(idx, idx[1:]):
            if b != a + 1:
                ends.append(a)
                starts.append(b)
        ends.append(idx[-1])
        for s, e in zip(starts, ends):
            boxes.append(box(x0 + s * res, y0 + j * res,
                             x0 + (e + 1) * res, y0 + (j + 1) * res))
    if not boxes:
        return None
    g = unary_union(boxes)
    if g.geom_type == "MultiPolygon":
        g = max(g.geoms, key=lambda x: x.area)
    return g.simplify(0.04, preserve_topology=True)


def enclose(pin: dict, plan: dict) -> tuple:
    """Flood-fill free space around the pin, bounded by that storey's walls."""
    st = next((s for s in plan["storeys"] if s["gid"] == pin["storey"]), None)
    if st is None or not st["walls"]:
        return None, None, "no walls on this storey to bound a room"

    px, py = pin["x"], pin["y"]
    x0, y0 = px - MAX_SPAN / 2, py - MAX_SPAN / 2
    n = int(MAX_SPAN / RES)
    xs = x0 + (np.arange(n) + 0.5) * RES
    ys = y0 + (np.arange(n) + 0.5) * RES
    gx, gy = np.meshgrid(xs, ys)

    walls = [Polygon(w) for w in st["walls"] if len(w) >= 4]
    walls = [w for w in walls if w.is_valid and not w.is_empty]
    if not walls:
        return None, None, "no usable wall geometry"
    import shapely
    occ = shapely.contains_xy(unary_union(walls), gx.ravel(), gy.ravel()).reshape(n, n)

    from scipy import ndimage
    lbl, _ = ndimage.label(~occ)
    j0, i0 = int((py - y0) / RES), int((px - x0) / RES)
    if not (0 <= j0 < n and 0 <= i0 < n) or lbl[j0, i0] == 0:
        return None, None, "the pin sits inside a wall"
    region = lbl == lbl[j0, i0]

    # A region touching the window edge has escaped: it is the corridor or the
    # outside, not the room that was missed.
    if region[0, :].any() or region[-1, :].any() or region[:, 0].any() or region[:, -1].any():
        return None, None, "free space around the pin is not enclosed"

    cand = _rle_polygon(region, x0, y0, RES)
    why, notes = _validate(cand, st, pin)
    if why:
        return None, None, f"enclosure rejected: {why}"
    return cand, {"strategy": "enclose", "notes": notes}, None


def propose_links(cand: Polygon, room_id: str, storey: dict, evidence: dict) -> list:
    """Links this footprint implies, for an annotator to judge."""
    out = []
    for r in storey["rooms"]:
        if r["id"] == room_id:
            continue          # the room is already in the list; do not self-link
        g = poly_of(r)
        d = cand.distance(g)
        if d > NEIGHBOUR_REACH:
            continue
        # A door serving the shared boundary makes it a doorway; without one
        # the two merely touch, which is an open passage at best. Both are
        # proposals, so the annotator decides either way.
        served = any(cand.distance(Point(dr["x"], dr["y"])) < DOOR_REACH and
                     g.distance(Point(dr["x"], dr["y"])) < DOOR_REACH
                     for dr in storey["doors"])
        out.append({"a": room_id, "b": r["id"],
                    "type": "connected_by_door" if served else "open_passage",
                    "door": None, "width": None, "kind": None, "proposed": True})
    return out


def resolve(plan: dict, pins: list, verbose=True) -> dict:
    """Add a room per resolvable pin. Returns a report."""
    plan = copy.deepcopy(plan)
    by_gid = {s["gid"]: s for s in plan["storeys"]}
    report = {"model": plan.get("model"), "pins": len(pins),
              "projected": 0, "recovered": 0, "unresolved": [], "added": []}

    for pin in pins:
        st = by_gid.get(pin["storey"])
        if st is None:
            report["unresolved"].append({"pin": pin.get("id"),
                                         "why": "unknown storey"})
            continue

        cand, ev, err = project(pin, plan)
        if cand is None and not err:
            cand, ev, err = enclose(pin, plan)
        if cand is None:
            report["unresolved"].append({"pin": pin.get("id"),
                                         "label": pin.get("label"),
                                         "why": err or "no strategy applied"})
            if verbose:
                print(f"    pin {pin.get('label','?'):<12} UNRESOLVED — {err}")
            continue

        rid = f"PIN-{pin['id']}"
        source = "projected" if ev["strategy"] == "project" else "recovered"
        room = {
            "id": rid,
            "label": pin.get("label") or "room",
            "predicted_label": None, "label_source": None,
            "source": source,
            "area": round(cand.area, 2),
            "polygon": [[round(x, 3), round(y, 3)] for x, y in cand.exterior.coords],
            "centroid": [round(cand.centroid.x, 3), round(cand.centroid.y, 3)],
            "from_pin": pin["id"],
            "evidence": ev,
        }
        holes = [[[round(x, 3), round(y, 3)] for x, y in i.coords]
                 for i in cand.interiors]
        if holes:
            room["holes"] = holes
        st["rooms"].append(room)

        links = propose_links(cand, rid, st, ev)
        st["edges"].extend(links)

        # The point of projecting a stair is that it joins two floors, so the
        # link back to the room it was projected from is proposed too.
        if ev["strategy"] == "project":
            plan.setdefault("vertical", []).append({
                "a": rid, "b": ev["from_room"], "type": "vertically_connected",
                "door": None, "width": None, "kind": "stair",
                "storey_a": st["gid"], "storey_b": ev["from_storey"],
                "proposed": True,
            })

        report["projected" if source == "projected" else "recovered"] += 1
        report["added"].append({"id": rid, "label": room["label"],
                                "storey": st["name"], "area": room["area"],
                                "strategy": ev["strategy"], "links": len(links)})
        if verbose:
            src = (f"from {ev['from_storey_name']} {ev['from_room_label']}"
                   if ev["strategy"] == "project" else "from walls")
            print(f"    pin {room['label']:<12} -> {room['area']:6.2f} m2 "
                  f"({source}, {src}), {len(links)} link(s) proposed")
    return plan, report


def _pins_by_model(inbox: str | None, anno_dir: str | None) -> dict:
    """Every missing-room pin anyone recorded, grouped by model."""
    out = {}
    files = []
    for d in filter(None, [inbox, anno_dir]):
        files += sorted(glob.glob(os.path.join(d, "*.json")))
    for p in files:
        try:
            doc = json.load(open(p))
        except Exception:
            continue
        items = (doc.get("annotations") or []
                 if isinstance(doc, dict) and
                 doc.get("format") == "bimsg-annotation-bundle" else [doc])
        for a in items:
            if not isinstance(a, dict):
                continue
            m = a.get("model")
            pins = a.get("missing_rooms") or []
            if not m or not pins:
                continue
            seen = {q["id"] for q in out.setdefault(m, [])}
            for q in pins:
                if q.get("id") and q["id"] not in seen:
                    out[m].append(q)
                    seen.add(q["id"])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inbox", help="returned annotation/bundle files")
    ap.add_argument("--annotations", default=os.path.join(HERE, "annotations"))
    ap.add_argument("--plans", default=DATA)
    ap.add_argument("--out", default=os.path.join(HERE, "data-resolved"))
    args = ap.parse_args()

    pins = _pins_by_model(args.inbox, args.annotations)
    if not pins:
        print("no missing-room pins found")
        return 0

    os.makedirs(args.out, exist_ok=True)
    total = {"pins": 0, "projected": 0, "recovered": 0, "unresolved": 0}
    reports = []
    for model, ms in sorted(pins.items()):
        pf = os.path.join(args.plans, f"{model}.plan.json")
        if not os.path.exists(pf):
            print(f"  ! no plan for {model}")
            continue
        print(f"  {model}: {len(ms)} pin(s)")
        plan, rep = resolve(json.load(open(pf)), ms)
        json.dump(plan, open(os.path.join(args.out, f"{model}.plan.json"), "w"),
                  indent=1)
        reports.append(rep)
        total["pins"] += rep["pins"]
        total["projected"] += rep["projected"]
        total["recovered"] += rep["recovered"]
        total["unresolved"] += len(rep["unresolved"])

    # Plans nobody pinned are copied through, so the output is a complete set.
    import shutil
    for pf in sorted(glob.glob(os.path.join(args.plans, "*.plan.json"))):
        dst = os.path.join(args.out, os.path.basename(pf))
        if not os.path.exists(dst):
            shutil.copy2(pf, dst)

    json.dump({"totals": total, "models": reports},
              open(os.path.join(args.out, "_pins.json"), "w"), indent=2)
    print(f"\n{total['pins']} pin(s): {total['projected']} projected, "
          f"{total['recovered']} recovered from walls, "
          f"{total['unresolved']} left as pins")
    print(f"plans -> {args.out}")
    print("\nThese rooms and their links are proposals. Hand this folder back to\n"
          "an annotator to judge them; nothing enters ground truth unjudged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
