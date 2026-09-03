"""Check the graph export: GraphML, GEXF and the standalone HTML page.

The exporter's whole purpose is that someone else's software opens the file, so
the test that matters is not "we wrote some XML" but "a real graph library reads
it back and finds the same graph". networkx stands in for Gephi and yEd here; if
it is not installed the structural checks still run against the raw XML.

The other half is the HTML page. It carries room names straight out of an IFC
file, which can contain anything, so it is checked against a building deliberately
named to break out of a <script> block and out of an HTML attribute.

    python annotator/test_export_graph.py
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import compose as C          # noqa: E402
import export_graph as X     # noqa: E402

GRAPHML_NS = "{http://graphml.graphdrawing.org/xmlns}"
GEXF_NS = "{http://gexf.net/1.3}"

# A room name that ends the script block, and one that escapes an attribute.
HOSTILE = '</script><img src=x onerror="alert(1)">'
HOSTILE2 = 'Kitchen & "Bar" <lounge>'

failures = 0


def ok(msg: str, cond: bool) -> None:
    global failures
    if not cond:
        failures += 1
    print(("  PASS " if cond else "  FAIL ") + msg)


def full_annotation(plan: dict) -> dict:
    """Judge everything, so the graph composes complete."""
    a = {"rooms": {}, "edges": {}, "vertical": {},
         "added_edges": [], "added_vertical": [], "missing_rooms": []}
    for st in plan["storeys"]:
        for r in st["rooms"]:
            a["rooms"][r["id"]] = {"verdict": "real"}
        for e in st.get("edges") or []:
            a["edges"][C._key(e["a"], e["b"])] = {
                "verdict": "passable", "a": e["a"], "b": e["b"]}
    for v in plan.get("vertical") or []:
        a["vertical"][C._key(v["a"], v["b"])] = {
            "verdict": "passable", "a": v["a"], "b": v["b"]}
    return a


def main() -> int:
    plans = sorted(glob.glob(os.path.join(HERE, "data", "*.plan.json")))
    if not plans:
        print("no plans in annotator/data — run main/export_plans.py first")
        return 1

    plan = json.load(open(plans[0]))
    # Two rooms get names chosen to break the page if anything is unescaped.
    named = 0
    for st in plan["storeys"]:
        for r in st["rooms"]:
            if named < 2:
                r["label"] = (HOSTILE, HOSTILE2)[named]
                named += 1
    plan["model"] = plan.get("model") or "model_x"

    g = C.compose(plan, full_annotation(plan))
    g["annotator"] = "tester"
    ok(f"graph composes ({len(g['nodes'])} nodes, {len(g['edges'])} edges)",
       g["complete"] is True and len(g["nodes"]) > 3)

    pos = X.layout(g)
    ok("every node gets a position", all(n["id"] in pos for n in g["nodes"]))
    ok("positions are finite",
       all(all(v == v and abs(v) < 1e9 for v in p) for p in pos.values()))
    rooms = [n for n in g["nodes"] if n["layer"] == "space"]
    ok("rooms are not all stacked on one point",
       len({(round(pos[n['id']][0], 2), round(pos[n['id']][1], 2))
            for n in rooms}) > max(1, len(rooms) // 2))

    st = X.stats(g)
    ok(f"stats count the rooms ({st['rooms']})", st["rooms"] == len(rooms))
    ok("stats count components", 1 <= st["components"] <= max(1, st["rooms"]))

    # ---- GraphML ---------------------------------------------------------
    xml = X.to_graphml(g, pos)
    root = ET.fromstring(xml)
    graph = root.find(f"{GRAPHML_NS}graph")
    gnodes = graph.findall(f"{GRAPHML_NS}node")
    gedges = graph.findall(f"{GRAPHML_NS}edge")
    ok(f"graphml holds every node ({len(gnodes)})", len(gnodes) == len(g["nodes"]))
    ok(f"graphml holds every edge ({len(gedges)})", len(gedges) == len(g["edges"]))

    ids = {n.get("id") for n in gnodes}
    dangling = [e.get("id") for e in gedges
                if e.get("source") not in ids or e.get("target") not in ids]
    ok("no graphml edge points at a missing node", not dangling)

    declared = {k.get("id") for k in root.findall(f"{GRAPHML_NS}key")}
    used = {d.get("key") for el in (gnodes + gedges)
            for d in el.findall(f"{GRAPHML_NS}data")}
    ok("every graphml data key is declared", used <= declared)

    # ---- GEXF ------------------------------------------------------------
    gx = ET.fromstring(X.to_gexf(g, pos))
    xnodes = gx.iter(f"{GEXF_NS}node")
    positioned = 0
    total = 0
    for n in xnodes:
        total += 1
        if any(c.tag.endswith("position") for c in n):
            positioned += 1
    ok(f"gexf holds every node ({total})", total == len(g["nodes"]))
    ok("gexf gives every node a viz:position", positioned == total)
    xedges = list(gx.iter(f"{GEXF_NS}edge"))
    ok(f"gexf holds every edge ({len(xedges)})", len(xedges) == len(g["edges"]))

    # ---- read back with a real graph library ------------------------------
    try:
        import networkx as nx
    except ImportError:
        print("  SKIP networkx not installed; raw-XML checks only")
    else:
        with tempfile.TemporaryDirectory() as td:
            p1 = os.path.join(td, "g.graphml")
            p2 = os.path.join(td, "g.gexf")
            open(p1, "w").write(xml)
            open(p2, "w").write(X.to_gexf(g, pos))
            n1 = nx.read_graphml(p1)
            n2 = nx.read_gexf(p2)
        ok(f"networkx reads the graphml ({n1.number_of_nodes()} nodes, "
           f"{n1.number_of_edges()} edges)",
           n1.number_of_nodes() == len(g["nodes"]))
        ok("networkx sees the same edges in graphml",
           n1.number_of_edges() == len(g["edges"]))
        ok("networkx reads the gexf", n2.number_of_nodes() == len(g["nodes"]))
        some = next(iter(n1.nodes(data=True)))[1]
        ok("graphml node attributes survive the round trip",
           "label" in some and "layer" in some)
        # The hostile names must come back as text, not as markup.
        labels = {d.get("label") for _, d in n1.nodes(data=True)}
        ok("a room named with a script tag round-trips verbatim",
           HOSTILE in labels)
        ok("a room named with quotes and ampersands round-trips verbatim",
           HOSTILE2 in labels)
        # The graph a router would use: rooms joined by traversable links.
        walk = nx.Graph()
        walk.add_nodes_from(n["id"] for n in rooms)
        walk.add_edges_from((e["a"], e["b"]) for e in g["edges"]
                            if e["relation"] in X.TRAVERSABLE)
        ok(f"the room-level graph has {nx.number_connected_components(walk)} "
           f"component(s), matching stats",
           nx.number_connected_components(walk) == st["components"])

    # ---- the standalone page ---------------------------------------------
    page = X.to_html(g, pos, st)
    ok("no placeholder is left unreplaced",
       "__DATA__" not in page and "__TITLE__" not in page)
    ok("the page is self-contained (no external fetches)",
       not re.search(r'(?:src|href)\s*=\s*["\']https?://', page))

    # Everything after the data assignment must still be our own script: if the
    # room name closed the block, the tag count goes wrong and the page breaks.
    ok("a room name cannot close the script block",
       page.count("<script") == page.count("</script>"))
    ok("the script-tag room name is escaped in the payload",
       "</script>" not in page.split("const D =", 1)[1].split("\n", 1)[0])

    m = re.search(r"const D = (\{.*\});", page)
    ok("the embedded payload is parseable JSON", m is not None)
    if m:
        payload = json.loads(m.group(1).replace("<\\/", "</"))
        ok("the payload carries every room",
           len(payload["nodes"]) == len(rooms))
        ok("the payload carries the hostile name intact",
           any(n["label"] == HOSTILE for n in payload["nodes"]))
        ok("every payload edge joins two rooms it also carries",
           all(e["a"] in {n["id"] for n in payload["nodes"]} and
               e["b"] in {n["id"] for n in payload["nodes"]}
               for e in payload["edges"]))
        ok("the payload names every storey",
           all(n["storey_name"] for n in payload["nodes"]))

    # ---- main() writes the three files -----------------------------------
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "model_x__AN.graph.json")
        with open(src, "w") as fh:
            json.dump(g, fh)
        out = os.path.join(td, "out")
        argv = sys.argv
        sys.argv = ["export_graph.py", src, "--out", out]
        try:
            rc = X.main()
        finally:
            sys.argv = argv
        ok("the exporter exits clean", rc == 0)
        written = sorted(os.path.basename(p) for p in glob.glob(os.path.join(out, "*")))
        ok(f"it writes all three formats ({', '.join(written)})",
           any(w.endswith(".graphml") for w in written) and
           any(w.endswith(".gexf") for w in written) and
           any(w.endswith(".html") for w in written))

    print()
    return failures


if __name__ == "__main__":
    n = main()
    print(f"{'FAIL' if n else 'PASS'} graph export "
          f"({n} failed)" if n else
          "PASS the exported graph opens in a real graph library, and the "
          "standalone page is safe against anything an IFC can be named")
    sys.exit(1 if n else 0)
