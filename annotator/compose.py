"""Compose a plan and its annotation into a hierarchical building scene graph.

The annotator works one floor at a time, which is the only tractable way to
judge a plan. But a per-floor verdict list is not the deliverable: what the
pipeline has to be scored against is a single building graph with the storeys
chained together, so a route from a bedroom on the second floor to the front
door is one connected object.

This module does that join, and it is the single definition of how verdicts
become ground truth -- the annotator's download button and the batch script
`build_gt.py` both call it, so what an annotator sees is exactly what lands in
the dataset.

Verdict semantics, deliberately conservative:

    correct   -> in the graph
    spurious  -> not in the graph
    unsure    -> not in the graph, but recorded in `held_out` so it can be
                 routed to a second annotator instead of silently becoming a
                 negative
    merge/split, on a room -> the room stays, and the request is recorded;
                 acting on it needs geometry the annotator cannot edit here
    unjudged  -> not in the graph, and counted, so partial work is never
                 mistaken for a finished building

Anything the annotator added is included with `provenance: "annotator"`.
"""

from __future__ import annotations


def _key(a: str, b: str) -> str:
    return f"{a}|{b}" if a < b else f"{b}|{a}"


def compose(plan: dict, anno: dict) -> dict:
    rooms_v = anno.get("rooms", {}) or {}
    edges_v = anno.get("edges", {}) or {}
    vert_v = anno.get("vertical", {}) or {}
    added_e = anno.get("added_edges", []) or []
    added_v = anno.get("added_vertical", []) or []
    missing = anno.get("missing_rooms", []) or []

    nodes, edges, held_out, requests = [], [], [], []
    counts = {"rooms_total": 0, "rooms_judged": 0, "rooms_kept": 0,
              "rooms_labelled_only": 0,
              "links_total": 0, "links_judged": 0, "links_kept": 0,
              "vertical_total": 0, "vertical_judged": 0, "vertical_kept": 0}

    building = plan.get("model", "building")
    nodes.append({"id": building, "layer": "building", "label": building,
                  "provenance": "ifc"})

    keep_rooms = set()
    for st in plan["storeys"]:
        nodes.append({
            "id": st["gid"], "layer": "storey", "label": st["name"],
            "elevation": st["elevation"], "parent": building, "provenance": "ifc",
        })
        edges.append({"a": building, "b": st["gid"], "relation": "contains",
                      "provenance": "ifc"})

        for r in st["rooms"]:
            counts["rooms_total"] += 1
            a = rooms_v.get(r["id"]) or {}
            v = a.get("verdict")
            if v:
                counts["rooms_judged"] += 1
            elif a.get("label") or a.get("note"):
                # Someone looked at this room and corrected it, but never gave a
                # verdict. Worth counting separately: it is the difference
                # between "not started" and "nearly done", and it is the one
                # mistake that loses work silently.
                counts["rooms_labelled_only"] += 1
            if v == "spurious":
                continue
            if v in ("merge", "split"):
                requests.append({"room": r["id"], "request": v,
                                 "storey": st["gid"], "note": a.get("note", "")})
            if not v:
                continue          # unjudged rooms are not ground truth
            keep_rooms.add(r["id"])
            counts["rooms_kept"] += 1
            nodes.append({
                "id": r["id"], "layer": "space",
                "label": a.get("label") or r["label"],
                "ifc_label": r["label"],
                "predicted_label": r.get("predicted_label"),
                "area": r["area"], "centroid": r["centroid"],
                "parent": st["gid"],
                "provenance": "ifc" if r["source"] == "ifc" else "inferred",
                "verdict": v,
                **({"note": a["note"]} if a.get("note") else {}),
            })
            edges.append({"a": st["gid"], "b": r["id"], "relation": "contains",
                          "provenance": "ifc"})

    # ---- intra-floor connectivity -------------------------------------
    for st in plan["storeys"]:
        for e in st["edges"]:
            counts["links_total"] += 1
            a = edges_v.get(_key(e["a"], e["b"])) or {}
            v = a.get("verdict")
            if v:
                counts["links_judged"] += 1
            if v == "unsure":
                held_out.append({"a": e["a"], "b": e["b"], "kind": "link",
                                 "storey": st["gid"]})
                continue
            if v != "correct":
                continue
            # A link is only meaningful if both its rooms survived.
            if e["a"] not in keep_rooms or e["b"] not in keep_rooms:
                held_out.append({"a": e["a"], "b": e["b"], "kind": "link",
                                 "storey": st["gid"],
                                 "why": "endpoint room not confirmed"})
                continue
            counts["links_kept"] += 1
            edges.append({"a": e["a"], "b": e["b"], "relation": e["type"],
                          "storey": st["gid"], "provenance": "ifc+annotator",
                          **({"width": e["width"]} if e.get("width") else {})})

    for e in added_e:
        if e["a"] not in keep_rooms or e["b"] not in keep_rooms:
            held_out.append({"a": e["a"], "b": e["b"], "kind": "link",
                             "why": "endpoint room not confirmed"})
            continue
        counts["links_kept"] += 1
        edges.append({"a": e["a"], "b": e["b"], "relation": "connected_by_door",
                      "storey": e.get("storey"), "provenance": "annotator"})

    # ---- the join between floors --------------------------------------
    for v in plan.get("vertical", []) or []:
        counts["vertical_total"] += 1
        a = vert_v.get(_key(v["a"], v["b"])) or {}
        verdict = a.get("verdict")
        if verdict:
            counts["vertical_judged"] += 1
        if verdict == "unsure":
            held_out.append({"a": v["a"], "b": v["b"], "kind": "vertical"})
            continue
        if verdict != "correct":
            continue
        if v["a"] not in keep_rooms or v["b"] not in keep_rooms:
            held_out.append({"a": v["a"], "b": v["b"], "kind": "vertical",
                             "why": "endpoint room not confirmed"})
            continue
        counts["vertical_kept"] += 1
        edges.append({"a": v["a"], "b": v["b"], "relation": "vertically_connected",
                      "kind": v.get("kind"), "provenance": "ifc+annotator"})

    for v in added_v:
        if v["a"] not in keep_rooms or v["b"] not in keep_rooms:
            held_out.append({"a": v["a"], "b": v["b"], "kind": "vertical",
                             "why": "endpoint room not confirmed"})
            continue
        counts["vertical_kept"] += 1
        edges.append({"a": v["a"], "b": v["b"], "relation": "vertically_connected",
                      "kind": v.get("kind", "manual"), "provenance": "annotator"})

    complete = (counts["rooms_judged"] == counts["rooms_total"]
                and counts["links_judged"] == counts["links_total"]
                and counts["vertical_judged"] == counts["vertical_total"])

    return {
        "model": building,
        "annotator": anno.get("annotator"),
        "updated": anno.get("updated"),
        "source": "annotated",
        "complete": complete,
        "counts": counts,
        "nodes": nodes,
        "edges": edges,
        # Not ground truth, but not thrown away either.
        "held_out": held_out,
        "requests": requests,
        "missing_rooms": missing,
    }


def connectivity_gt(composed: dict) -> dict:
    """Reduce a composed graph to the pair form `eval_connectivity` expects."""
    rooms = [n for n in composed["nodes"] if n["layer"] == "space"]
    pos = [{"a": e["a"], "b": e["b"], "type": e["relation"]}
           for e in composed["edges"]
           if e["relation"] in ("connected_by_door", "open_passage",
                                "vertically_connected")]
    return {
        "building": composed["model"],
        "source": "annotated",
        "annotator": composed.get("annotator"),
        "complete": composed["complete"],
        "rooms": [{"rid": r["id"], "label": r["label"],
                   "storey": r["parent"], "area": r.get("area")} for r in rooms],
        "edges": pos,
        "held_out": composed["held_out"],
    }
