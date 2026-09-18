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

    real / passable      -> in the graph
    not_a_room /
    not_passable         -> not in the graph, and recorded in `negatives`. A
                 rejection is a judgement, not an absence: it is the only thing
                 that tells a scorer a pair was looked at and ruled out, and
                 dropping it left an export that could measure recall but never
                 precision.
    unsure    -> not in the graph, but recorded in `held_out` so it can be
                 routed to a second annotator instead of silently becoming a
                 negative
    merge/split, on a room -> the room stays, and the request is recorded;
                 acting on it needs geometry the annotator cannot edit here
    unjudged  -> not in the graph, and counted, so partial work is never
                 mistaken for a finished building

A verdict applied by "the rest of this floor is correct" is flagged `bulk` on
the node or edge and counted in `rooms_bulk` / `links_bulk`. It is ground truth
like any other, but it was not looked at individually, and anything measuring
against this data should be able to weigh that.

Anything the annotator added is included with `provenance: "annotator"`.
"""

from __future__ import annotations


def _key(a: str, b: str) -> str:
    return f"{a}|{b}" if a < b else f"{b}|{a}"


# The vocabulary, in one place. A room and a link fail in different ways, so
# they do not share words: a file should say what was meant without needing to
# know which dictionary the entry came from.
ROOM_VERDICTS = ("real", "not_a_room", "unsure", "merge", "split")
LINK_VERDICTS = ("passable", "not_passable", "unsure")


def room_verdict(a: dict):
    v = a.get("verdict")
    return v if v in ROOM_VERDICTS else None


def link_verdict(a: dict):
    v = a.get("verdict")
    return v if v in LINK_VERDICTS else None


def compose(plan: dict, anno: dict) -> dict:
    rooms_v = anno.get("rooms", {}) or {}
    edges_v = anno.get("edges", {}) or {}
    vert_v = anno.get("vertical", {}) or {}
    added_e = anno.get("added_edges", []) or []
    added_v = anno.get("added_vertical", []) or []
    missing = anno.get("missing_rooms", []) or []

    nodes, edges, held_out, requests, negatives = [], [], [], [], []
    counts = {"rooms_total": 0, "rooms_judged": 0, "rooms_kept": 0,
              "rooms_labelled_only": 0,
              "rooms_bulk": 0,
              "links_total": 0, "links_judged": 0, "links_kept": 0,
              "links_bulk": 0,
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
            v = room_verdict(a)
            if v:
                counts["rooms_judged"] += 1
                if a.get("bulk"):
                    counts["rooms_bulk"] += 1
            elif a.get("label") or a.get("note"):
                # Someone looked at this room and corrected it, but never gave a
                # verdict. Worth counting separately: it is the difference
                # between "not started" and "nearly done", and it is the one
                # mistake that loses work silently.
                counts["rooms_labelled_only"] += 1
            if v == "not_a_room":
                negatives.append({"room": r["id"], "kind": "room",
                                  "storey": st["gid"],
                                  **({"bulk": True} if a.get("bulk") else {})})
                continue
            if v == "unsure":
                held_out.append({"room": r["id"], "kind": "room",
                                 "storey": st["gid"]})
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
                # "ifc" stated, "inferred" recovered by the pipeline,
                # "projected"/"recovered" resolved from an annotator's pin.
                # Collapsing these loses which evidence the node rests on.
                "provenance": (r["source"] if r["source"] in
                               ("ifc", "inferred", "projected", "recovered")
                               else "inferred"),
                "verdict": v,
                # What justified this room existing at all. A projected or
                # wall-recovered room is only as good as its evidence, and a
                # node that does not carry it cannot be audited later.
                **({"evidence": r["evidence"]} if r.get("evidence") else {}),
                **({"from_pin": r["from_pin"]} if r.get("from_pin") else {}),
                # Whether this was judged on its own or swept in with the rest
                # of a floor. A swept verdict is weaker evidence and anything
                # measuring against this data should be able to tell.
                **({"bulk": True} if a.get("bulk") else {}),
                **({"note": a["note"]} if a.get("note") else {}),
            })
            edges.append({"a": st["gid"], "b": r["id"], "relation": "contains",
                          "provenance": "ifc"})

    # ---- intra-floor connectivity -------------------------------------
    for st in plan["storeys"]:
        for e in st["edges"]:
            counts["links_total"] += 1
            a = edges_v.get(_key(e["a"], e["b"])) or {}
            v = link_verdict(a)
            if v:
                counts["links_judged"] += 1
                if a.get("bulk"):
                    counts["links_bulk"] += 1
            if v == "unsure":
                held_out.append({"a": e["a"], "b": e["b"], "kind": "link",
                                 "storey": st["gid"]})
                continue
            if v == "not_passable":
                # A reviewed rejection. Kept apart from an unjudged link, which
                # is silence and must never be read as a negative.
                if e["a"] not in keep_rooms or e["b"] not in keep_rooms:
                    held_out.append({"a": e["a"], "b": e["b"], "kind": "link",
                                     "storey": st["gid"],
                                     "why": "endpoint room not confirmed"})
                else:
                    negatives.append({"a": e["a"], "b": e["b"], "kind": "link",
                                      "storey": st["gid"],
                                      **({"bulk": True} if a.get("bulk") else {})})
                continue
            if v != "passable":
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
                          **({"bulk": True} if a.get("bulk") else {}),
                          **({"width": e["width"]} if e.get("width") else {})})

    for e in added_e:
        if e["a"] not in keep_rooms or e["b"] not in keep_rooms:
            held_out.append({"a": e["a"], "b": e["b"], "kind": "link",
                             "why": "endpoint room not confirmed"})
            continue
        counts["links_kept"] += 1
        # The annotator says whether it is a door or an open threshold: a door
        # can be shut and an archway cannot, which is the whole difference to
        # anything planning a route.
        rel = e.get("kind")
        if rel not in ("connected_by_door", "open_passage"):
            rel = "connected_by_door"
        edges.append({"a": e["a"], "b": e["b"], "relation": rel,
                      "storey": e.get("storey"), "provenance": "annotator"})

    # ---- the join between floors --------------------------------------
    for v in plan.get("vertical", []) or []:
        counts["vertical_total"] += 1
        a = vert_v.get(_key(v["a"], v["b"])) or {}
        verdict = link_verdict(a)
        if verdict:
            counts["vertical_judged"] += 1
        if verdict == "unsure":
            held_out.append({"a": v["a"], "b": v["b"], "kind": "vertical"})
            continue
        if verdict == "not_passable":
            if v["a"] not in keep_rooms or v["b"] not in keep_rooms:
                held_out.append({"a": v["a"], "b": v["b"], "kind": "vertical",
                                 "why": "endpoint room not confirmed"})
            else:
                negatives.append({"a": v["a"], "b": v["b"], "kind": "vertical"})
            continue
        if verdict != "passable":
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

    # What this review does and does not cover. A scorer that does not know
    # the scope will read silence as a negative: the reviewer saw the links the
    # pipeline proposed, so a pair nobody proposed was never judged at all, and
    # counting it as a true negative would invent agreement.
    review_scope = {
        "rooms_reviewed": counts["rooms_judged"],
        "rooms_total": counts["rooms_total"],
        "links_reviewed": counts["links_judged"],
        "links_total": counts["links_total"],
        "vertical_reviewed": counts["vertical_judged"],
        "vertical_total": counts["vertical_total"],
        "negatives_cover": "links the pipeline proposed and a reviewer rejected",
        "unreviewed_is_not_negative": True,
        "pairs_never_proposed": "not judged",
    }

    return {
        "model": building,
        "annotator": anno.get("annotator"),
        "updated": anno.get("updated"),
        "source": "annotated",
        "complete": complete,
        "counts": counts,
        "review_scope": review_scope,
        "nodes": nodes,
        "edges": edges,
        # Not ground truth, but not thrown away either.
        "held_out": held_out,
        # Reviewed and ruled out. These are what make precision measurable.
        "negatives": negatives,
        "requests": requests,
        "missing_rooms": missing,
    }


def connectivity_gt(composed: dict) -> dict:
    """Reduce a composed graph to the pair form `eval_connectivity` expects.

    Positive, negative and unknown pairs all travel, with the scope that says
    how far the review reached. A positives-only export cannot support a
    precision figure, and the evaluator raised `KeyError` on it rather than
    saying so.
    """
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
        "review_scope": composed["review_scope"],
        "rooms": [{"rid": r["id"], "label": r["label"],
                   "storey": r["parent"], "area": r.get("area")} for r in rooms],
        "edges": pos,
        # The name `eval_connectivity` has always used for a reviewed pair that
        # is adjacent and not joined. Emitting it is what makes this export
        # readable by the evaluator it was written for.
        "adjacent_not_connected": [{"a": n["a"], "b": n["b"]}
                                   for n in composed["negatives"]
                                   if n["kind"] in ("link", "vertical")],
        # Regions a reviewer said are not rooms: confirmed false positives,
        # which room-instance scoring can use directly.
        "rooms_rejected": [n["room"] for n in composed["negatives"]
                           if n["kind"] == "room"],
        "held_out": composed["held_out"],
    }
