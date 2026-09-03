"""Turn a composed building graph into something you can actually look at.

`build_gt.py` writes `*.graph.json`, which is the right shape for code and no
shape at all for a person. This converts it into three things:

  .graphml   the interchange format almost every graph tool reads -- yEd,
             Gephi, Cytoscape, networkx. Carries every attribute, plus x/y/r/g/b
             keys, which Gephi picks up as position and colour.
  .gexf      Gephi's own format, with viz:position and viz:color. Positions
             carry the third dimension, so a storey is a real elevation.
  .html      a self-contained page needing no software at all: the storeys as
             stacked floor plates, laid out on the rooms' own coordinates.

The layout is the point. A force-directed hairball of forty rooms tells you
nothing about a building; the same graph drawn on the rooms' real centroids is
a floor plan you can read, and a wrong link is visible as a line crossing the
building instead of hugging a wall.

    python annotator/export_graph.py dataset/annotated_gt/*.graph.json
    python annotator/export_graph.py --out viz dataset/annotated_gt/model_2__AN.graph.json
"""

from __future__ import annotations

import argparse
import glob
import html
import json
import os
import sys
import xml.etree.ElementTree as ET

# Colour by what the node rests on, because that is what a reviewer is
# checking: an inferred or projected room deserves more suspicion than one the
# file stated, and the drawing should say so without being asked.
NODE_COLOUR = {
    "ifc": (37, 99, 235),          # stated by the file
    "inferred": (124, 58, 237),    # recovered by the pipeline
    "projected": (245, 158, 11),   # carried up from another storey
    "recovered": (245, 158, 11),   # flood-filled around a pin
}
EDGE_COLOUR = {
    "connected_by_door": (5, 150, 105),
    "open_passage": (217, 119, 6),
    "vertically_connected": (124, 58, 237),
    "contains": (203, 213, 225),
}
TRAVERSABLE = ("connected_by_door", "open_passage", "vertically_connected")

# Attributes carried onto every node and edge. Anything not listed is dropped
# rather than smuggled through as a stringified blob.
NODE_ATTRS = [("label", "string"), ("layer", "string"), ("storey", "string"),
              ("elevation", "double"), ("area", "double"),
              ("provenance", "string"), ("verdict", "string"),
              ("bulk", "boolean"), ("ifc_label", "string"),
              ("predicted_label", "string"), ("from_pin", "string"),
              ("evidence", "string")]
EDGE_ATTRS = [("relation", "string"), ("provenance", "string"),
              ("storey", "string"), ("bulk", "boolean"), ("kind", "string"),
              ("width", "double")]


def load(path: str) -> dict:
    with open(path) as fh:
        g = json.load(fh)
    if "nodes" not in g or "edges" not in g:
        raise ValueError(f"{os.path.basename(path)} is not a composed graph")
    return g


def layout(g: dict, spread: float = 1.35) -> dict:
    """A position per node, on the building's own coordinates.

    Rooms sit where they sit. Storeys are fanned out horizontally so they do
    not land on top of each other -- a building is drawn one plate at a time,
    not in plan with every floor superimposed.
    """
    storeys = [n for n in g["nodes"] if n["layer"] == "storey"]
    storeys.sort(key=lambda s: s.get("elevation") or 0)
    order = {s["id"]: i for i, s in enumerate(storeys)}

    rooms = [n for n in g["nodes"] if n["layer"] == "space" and n.get("centroid")]
    if not rooms:
        return {n["id"]: (0.0, 0.0, 0.0) for n in g["nodes"]}
    xs = [r["centroid"][0] for r in rooms]
    ys = [r["centroid"][1] for r in rooms]
    width = (max(xs) - min(xs)) or 10.0
    step = width * spread

    pos = {}
    for r in rooms:
        i = order.get(r.get("parent"), 0)
        pos[r["id"]] = (r["centroid"][0] + i * step, r["centroid"][1],
                        float(next((s.get("elevation") or 0) for s in storeys
                                   if s["id"] == r.get("parent")) if storeys else 0))
    # The building and storey nodes sit under their plate, out of the way.
    lo = min(ys) - 6.0
    for s in storeys:
        pos[s["id"]] = (min(xs) + order[s["id"]] * step + width / 2, lo,
                        float(s.get("elevation") or 0))
    b = next((n for n in g["nodes"] if n["layer"] == "building"), None)
    if b:
        pos[b["id"]] = (min(xs) + (len(storeys) - 1) * step / 2, lo - 6.0, 0.0)
    for n in g["nodes"]:
        pos.setdefault(n["id"], (0.0, 0.0, 0.0))
    return pos


def _val(v):
    """Attribute values as text; anything structured becomes compact JSON."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float, str)):
        return str(v)
    return json.dumps(v, separators=(",", ":"))


def to_graphml(g: dict, pos: dict) -> str:
    ns = "http://graphml.graphdrawing.org/xmlns"
    ET.register_namespace("", ns)
    root = ET.Element(f"{{{ns}}}graphml")

    keys = []
    for name, typ in NODE_ATTRS:
        keys.append((f"n_{name}", "node", name, typ))
    for name in ("x", "y", "r", "g", "b", "size"):
        keys.append((f"n_{name}", "node", name, "double" if name in "xy" else "int"))
    for name, typ in EDGE_ATTRS:
        keys.append((f"e_{name}", "edge", name, typ))
    for name in ("r", "g", "b", "weight"):
        keys.append((f"e_{name}", "edge", name,
                     "double" if name == "weight" else "int"))
    for kid, dom, name, typ in keys:
        k = ET.SubElement(root, f"{{{ns}}}key")
        k.set("id", kid); k.set("for", dom)
        k.set("attr.name", name); k.set("attr.type", typ)

    graph = ET.SubElement(root, f"{{{ns}}}graph")
    graph.set("id", str(g.get("model") or "building"))
    graph.set("edgedefault", "undirected")

    def data(parent, kid, value):
        if value is None:
            return
        d = ET.SubElement(parent, f"{{{ns}}}data")
        d.set("key", kid)
        d.text = value

    for n in g["nodes"]:
        el = ET.SubElement(graph, f"{{{ns}}}node")
        el.set("id", n["id"])
        for name, _ in NODE_ATTRS:
            data(el, f"n_{name}", _val(n.get(name)))
        x, y, _z = pos.get(n["id"], (0, 0, 0))
        data(el, "n_x", f"{x:.3f}")
        data(el, "n_y", f"{y:.3f}")
        cr, cg, cb = NODE_COLOUR.get(n.get("provenance"), (148, 163, 184))
        data(el, "n_r", str(cr)); data(el, "n_g", str(cg)); data(el, "n_b", str(cb))
        data(el, "n_size", "18" if n["layer"] == "space" else "10")

    for i, e in enumerate(g["edges"]):
        el = ET.SubElement(graph, f"{{{ns}}}edge")
        el.set("id", f"e{i}"); el.set("source", e["a"]); el.set("target", e["b"])
        for name, _ in EDGE_ATTRS:
            data(el, f"e_{name}", _val(e.get(name)))
        cr, cg, cb = EDGE_COLOUR.get(e.get("relation"), (148, 163, 184))
        data(el, "e_r", str(cr)); data(el, "e_g", str(cg)); data(el, "e_b", str(cb))
        data(el, "e_weight", "1.0" if e["relation"] in TRAVERSABLE else "0.2")

    ET.indent(root, space=" ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + \
        ET.tostring(root, encoding="unicode")


def to_gexf(g: dict, pos: dict) -> str:
    """GEXF with viz:position and viz:color, which Gephi renders directly."""
    NS = "http://gexf.net/1.3"
    VIZ = "http://gexf.net/1.3/viz"
    ET.register_namespace("", NS)
    ET.register_namespace("viz", VIZ)
    root = ET.Element(f"{{{NS}}}gexf", {"version": "1.3"})
    meta = ET.SubElement(root, f"{{{NS}}}meta")
    ET.SubElement(meta, f"{{{NS}}}creator").text = "bim-graphs annotator"
    ET.SubElement(meta, f"{{{NS}}}description").text = (
        f"{g.get('model')} annotated by {g.get('annotator')}")
    graph = ET.SubElement(root, f"{{{NS}}}graph", {"defaultedgetype": "undirected",
                                                   "mode": "static"})

    natt = ET.SubElement(graph, f"{{{NS}}}attributes", {"class": "node"})
    nid = {}
    for i, (name, typ) in enumerate(NODE_ATTRS):
        nid[name] = str(i)
        ET.SubElement(natt, f"{{{NS}}}attribute",
                      {"id": str(i), "title": name,
                       "type": "float" if typ == "double" else
                               ("boolean" if typ == "boolean" else "string")})
    eatt = ET.SubElement(graph, f"{{{NS}}}attributes", {"class": "edge"})
    eid = {}
    for i, (name, typ) in enumerate(EDGE_ATTRS):
        eid[name] = str(i)
        ET.SubElement(eatt, f"{{{NS}}}attribute",
                      {"id": str(i), "title": name,
                       "type": "float" if typ == "double" else
                               ("boolean" if typ == "boolean" else "string")})

    nodes = ET.SubElement(graph, f"{{{NS}}}nodes")
    for n in g["nodes"]:
        el = ET.SubElement(nodes, f"{{{NS}}}node",
                           {"id": n["id"], "label": str(n.get("label") or n["id"])})
        vals = ET.SubElement(el, f"{{{NS}}}attvalues")
        for name, _ in NODE_ATTRS:
            v = _val(n.get(name))
            if v is not None:
                ET.SubElement(vals, f"{{{NS}}}attvalue",
                              {"for": nid[name], "value": v})
        x, y, z = pos.get(n["id"], (0, 0, 0))
        ET.SubElement(el, f"{{{VIZ}}}position",
                      {"x": f"{x:.3f}", "y": f"{y:.3f}", "z": f"{z:.3f}"})
        cr, cg, cb = NODE_COLOUR.get(n.get("provenance"), (148, 163, 184))
        ET.SubElement(el, f"{{{VIZ}}}color",
                      {"r": str(cr), "g": str(cg), "b": str(cb)})
        ET.SubElement(el, f"{{{VIZ}}}size",
                      {"value": "18" if n["layer"] == "space" else "10"})

    edges = ET.SubElement(graph, f"{{{NS}}}edges")
    for i, e in enumerate(g["edges"]):
        el = ET.SubElement(edges, f"{{{NS}}}edge",
                           {"id": f"e{i}", "source": e["a"], "target": e["b"],
                            "label": str(e.get("relation") or "")})
        vals = ET.SubElement(el, f"{{{NS}}}attvalues")
        for name, _ in EDGE_ATTRS:
            v = _val(e.get(name))
            if v is not None:
                ET.SubElement(vals, f"{{{NS}}}attvalue",
                              {"for": eid[name], "value": v})
        cr, cg, cb = EDGE_COLOUR.get(e.get("relation"), (148, 163, 184))
        ET.SubElement(el, f"{{{VIZ}}}color",
                      {"r": str(cr), "g": str(cg), "b": str(cb)})

    ET.indent(root, space=" ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + \
        ET.tostring(root, encoding="unicode")


def stats(g: dict) -> dict:
    """The numbers worth putting on the page."""
    spaces = [n for n in g["nodes"] if n["layer"] == "space"]
    adj = {s["id"]: set() for s in spaces}
    for e in g["edges"]:
        if e["relation"] in TRAVERSABLE and e["a"] in adj and e["b"] in adj:
            adj[e["a"]].add(e["b"]); adj[e["b"]].add(e["a"])
    seen, comps = set(), []
    for s in adj:
        if s in seen:
            continue
        stack, comp = [s], []
        seen.add(s)
        while stack:
            k = stack.pop(); comp.append(k)
            for m in adj[k]:
                if m not in seen:
                    seen.add(m); stack.append(m)
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    from collections import Counter
    return {
        "rooms": len(spaces),
        "components": len(comps),
        "largest": len(comps[0]) if comps else 0,
        "isolated": sum(1 for c in comps if len(c) == 1),
        "relations": dict(Counter(e["relation"] for e in g["edges"])),
        "provenance": dict(Counter(n.get("provenance") for n in spaces)),
        "bulk": sum(1 for n in spaces if n.get("bulk")),
    }


def to_html(g: dict, pos: dict, st: dict) -> str:
    """A self-contained page: the storeys as floor plates you can read."""
    storeys = [n for n in g["nodes"] if n["layer"] == "storey"]
    storeys.sort(key=lambda s: s.get("elevation") or 0)
    sname = {s["id"]: s.get("label") or s["id"] for s in storeys}
    spaces = {n["id"]: n for n in g["nodes"] if n["layer"] == "space"}

    payload = {
        "model": g.get("model"), "annotator": g.get("annotator"),
        "complete": g.get("complete"), "stats": st,
        "storeys": [{"id": s["id"], "name": sname[s["id"]],
                     "elevation": s.get("elevation")} for s in storeys],
        "nodes": [{"id": n["id"], "label": n.get("label"), "storey": n.get("parent"),
                   "storey_name": sname.get(n.get("parent"), ""),
                   "provenance": n.get("provenance"), "verdict": n.get("verdict"),
                   "area": n.get("area"), "bulk": bool(n.get("bulk")),
                   "x": pos[n["id"]][0], "y": pos[n["id"]][1]}
                  for n in spaces.values()],
        "edges": [{"a": e["a"], "b": e["b"], "relation": e["relation"],
                   "provenance": e.get("provenance"), "bulk": bool(e.get("bulk"))}
                  for e in g["edges"] if e["relation"] in TRAVERSABLE
                  and e["a"] in spaces and e["b"] in spaces],
    }
    # Room names come out of an IFC file and can hold anything. Inside a
    # <script> the parser looks for "</script" before it looks at JSON quoting,
    # so a room called "</script><img onerror=...>" would end the block and the
    # rest of the page would be markup. Escaping the sequence keeps it a string.
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    title = html.escape(f"{g.get('model')} — building graph")
    out = _HTML
    for token, value in (("__TITLE__", title), ("__DATA__", data)):
        # A placeholder that silently stops matching would ship a page with the
        # literal token in it, so fail here rather than at the reader.
        assert token in out, f"{token} missing from the page template"
        out = out.replace(token, value)
    return out


_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#eef1f6;--panel:#fff;--line:#d8dee9;--ink:#1a2233;--muted:#67728a}
*{box-sizing:border-box} html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--ink);
  font:13px/1.45 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  display:flex;overflow:hidden}
aside{width:270px;flex:none;background:var(--panel);border-right:1px solid var(--line);
  padding:14px;overflow-y:auto}
h1{font-size:15px;margin:0 0 2px} .sub{color:var(--muted);font-size:11.5px;margin-bottom:14px}
h2{font-size:10.5px;text-transform:uppercase;letter-spacing:.8px;color:var(--muted);
  margin:16px 0 6px;font-weight:700}
.row{display:flex;justify-content:space-between;font-size:12px;padding:3px 0}
.row b{font-variant-numeric:tabular-nums}
.key{display:flex;align-items:center;gap:7px;margin:4px 0;font-size:12px}
.sw{width:11px;height:11px;border-radius:3px;flex:none}
.sw.line{height:0;border-top:3px solid;border-radius:0;width:16px}
label.chk{display:flex;align-items:center;gap:7px;margin:5px 0;cursor:pointer;font-size:12px}
main{flex:1;position:relative}
svg{width:100%;height:100%;display:block;cursor:grab;background:var(--bg)}
svg.pan{cursor:grabbing}
/* Backdrop only: a plate must never swallow a click meant for a room. */
.plate{fill:#fff;stroke:var(--line);pointer-events:none}
/* Sizes are in metres: the viewport is scaled to the building, not to pixels.
   Text also sits in a counter-flipped group, because the +Y-up transform would
   otherwise mirror every character. */
.plabel{fill:var(--muted);font-size:1.1px;font-weight:700;text-anchor:middle}
.node{cursor:pointer}
/* In metres, like everything else in the viewport. */
.node:hover{stroke:#111827;stroke-width:0.14px}
.rlabel{font-size:0.62px;text-anchor:middle;fill:#101828;pointer-events:none;
  paint-order:stroke;stroke:#fff;stroke-width:0.18px}
.tip{position:absolute;pointer-events:none;background:rgba(16,24,40,.93);color:#fff;
  padding:7px 10px;border-radius:7px;font-size:11.5px;line-height:1.5;max-width:270px;
  opacity:0;transition:opacity .1s}
.tip.on{opacity:1}
.hud{position:absolute;left:12px;bottom:12px;color:var(--muted);font-size:11.5px;
  background:rgba(255,255,255,.9);border:1px solid var(--line);padding:5px 9px;
  border-radius:7px;pointer-events:none}
.warn{background:#fef3c7;border:1px solid #fcd34d;border-radius:7px;padding:8px 10px;
  font-size:11.5px;line-height:1.5;margin-top:10px}
</style></head><body>
<aside>
  <h1 id="ttl"></h1><div class="sub" id="sub"></div>
  <h2>Shape</h2><div id="stats"></div>
  <div id="warn"></div>
  <h2>Rooms come from</h2>
  <div class="key"><span class="sw" style="background:#2563eb"></span>the IFC file</div>
  <div class="key"><span class="sw" style="background:#7c3aed"></span>pipeline recovery</div>
  <div class="key"><span class="sw" style="background:#f59e0b"></span>an annotator's pin</div>
  <h2>Links</h2>
  <div class="key"><span class="sw line" style="border-color:#059669"></span>door</div>
  <div class="key"><span class="sw line" style="border-color:#d97706"></span>open passage</div>
  <div class="key"><span class="sw line" style="border-color:#7c3aed"></span>floor to floor</div>
  <h2>Show</h2>
  <label class="chk"><input type="checkbox" id="cLabels" checked> room labels</label>
  <label class="chk"><input type="checkbox" id="cVert" checked> floor-to-floor links</label>
  <label class="chk"><input type="checkbox" id="cBulk"> mark bulk-judged</label>
</aside>
<main>
  <svg id="g"><g id="vp"></g></svg>
  <div class="tip" id="tip"></div>
  <div class="hud">scroll to zoom &middot; drag to pan &middot; double-click to reset</div>
</main>
<script>
const D = __DATA__;
const NS="http://www.w3.org/2000/svg";
const PROV={ifc:"#2563eb",inferred:"#7c3aed",projected:"#f59e0b",recovered:"#f59e0b"};
const REL={connected_by_door:"#059669",open_passage:"#d97706",vertically_connected:"#7c3aed"};
const $=(i)=>document.getElementById(i);
const svg=$("g"), vp=$("vp"), tip=$("tip");
const byId={}; D.nodes.forEach(n=>byId[n.id]=n);

$("ttl").textContent = D.model || "building";
$("sub").textContent = (D.annotator? "annotated by "+D.annotator+" · ":"") +
  (D.complete? "complete" : "partial");
const s=D.stats;
$("stats").innerHTML =
  row("rooms", s.rooms) + row("connected pieces", s.components) +
  row("largest piece", s.largest) + row("isolated rooms", s.isolated) +
  Object.entries(s.relations).map(([k,v])=>row(k.replace(/_/g," "), v)).join("");
function row(k,v){return `<div class="row"><span>${k}</span><b>${v}</b></div>`;}
if (s.components > 1)
  $("warn").innerHTML = `<div class="warn"><b>${s.components} disconnected pieces.</b>
    A building should be one. Usually a floor-to-floor link is missing or an
    endpoint room was never judged.</div>`;

/* Storey plates, sized to the rooms that sit on them. */
const plates={};
for (const st of D.storeys) {
  const rs=D.nodes.filter(n=>n.storey===st.id);
  if(!rs.length) continue;
  const xs=rs.map(r=>r.x), ys=rs.map(r=>r.y);
  plates[st.id]={name:st.name, elevation:st.elevation,
    x0:Math.min(...xs)-4, x1:Math.max(...xs)+4,
    y0:Math.min(...ys)-4, y1:Math.max(...ys)+4};
}
const gPlates=document.createElementNS(NS,"g"); vp.appendChild(gPlates);
const gEdges=document.createElementNS(NS,"g"); vp.appendChild(gEdges);
const gNodes=document.createElementNS(NS,"g"); vp.appendChild(gNodes);
/* Every text group is counter-flipped and its y negated, so labels read the
   right way round under a viewport that puts +Y up. */
const gPlate=document.createElementNS(NS,"g");
gPlate.setAttribute("transform","scale(1,-1)"); vp.appendChild(gPlate);
const gLabels=document.createElementNS(NS,"g");
gLabels.setAttribute("transform","scale(1,-1)"); vp.appendChild(gLabels);

for (const [id,p] of Object.entries(plates)) {
  const r=document.createElementNS(NS,"rect");
  r.setAttribute("x",p.x0); r.setAttribute("y",p.y0);
  r.setAttribute("width",p.x1-p.x0); r.setAttribute("height",p.y1-p.y0);
  r.setAttribute("rx",1.2); r.setAttribute("class","plate");
  gPlates.appendChild(r);
  const t=document.createElementNS(NS,"text");
  t.setAttribute("x",(p.x0+p.x1)/2); t.setAttribute("y",-(p.y1+1.2));
  t.setAttribute("class","plabel");
  t.textContent=`${p.name}${p.elevation!=null?"  ·  "+p.elevation+" m":""}`;
  gPlate.appendChild(t);
}


function draw(){
  gEdges.innerHTML=""; gNodes.innerHTML=""; gLabels.innerHTML="";
  const showVert=$("cVert").checked, showLab=$("cLabels").checked,
        markBulk=$("cBulk").checked;
  for (const e of D.edges) {
    if (e.relation==="vertically_connected" && !showVert) continue;
    const a=byId[e.a], b=byId[e.b]; if(!a||!b) continue;
    const l=document.createElementNS(NS,"line");
    l.setAttribute("x1",a.x); l.setAttribute("y1",a.y);
    l.setAttribute("x2",b.x); l.setAttribute("y2",b.y);
    l.setAttribute("stroke",REL[e.relation]||"#94a3b8");
    l.setAttribute("stroke-width", e.relation==="vertically_connected"?0.35:0.25);
    if (e.relation==="open_passage") l.setAttribute("stroke-dasharray","0.7 0.4");
    if (e.relation==="vertically_connected") l.setAttribute("stroke-dasharray","0.25 0.4");
    if (markBulk && e.bulk) l.setAttribute("opacity","0.35");
    gEdges.appendChild(l);
  }
  for (const n of D.nodes) {
    const c=document.createElementNS(NS,"circle");
    const r=Math.max(0.7, Math.min(2.4, Math.sqrt((n.area||8))/3.2));
    c.setAttribute("cx",n.x); c.setAttribute("cy",n.y); c.setAttribute("r",r);
    c.setAttribute("fill",PROV[n.provenance]||"#94a3b8");
    c.setAttribute("class","node");
    c.setAttribute("stroke-width","0");
    if (n.verdict==="not_a_room") c.setAttribute("fill-opacity","0.25");
    if (markBulk && n.bulk){ c.setAttribute("stroke","#f59e0b");
      c.setAttribute("stroke-width","0.35"); }
    c.addEventListener("mousemove",(ev)=>{
      tip.className="tip on";
      tip.style.left=(ev.clientX+14)+"px"; tip.style.top=(ev.clientY+14)+"px";
      tip.innerHTML=`<b>${esc(n.label||n.id)}</b><br>${esc(n.storey_name)}` +
        (n.area?` · ${n.area} m²`:"") +
        `<br>from ${esc(n.provenance)} · judged ${esc(n.verdict||"—")}` +
        (n.bulk?" (in bulk)":"");
    });
    c.addEventListener("mouseleave",()=>{tip.className="tip";});
    gNodes.appendChild(c);
    if (showLab && (n.area||0) >= 6) {
      const t=document.createElementNS(NS,"text");
      t.setAttribute("x",n.x); t.setAttribute("y",-(n.y+r+0.45));
      t.setAttribute("class","rlabel");
      t.textContent=String(n.label||"").slice(0,18);
      gLabels.appendChild(t);
    }
  }
}
const esc=(s)=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
["cLabels","cVert","cBulk"].forEach(i=>$(i).onchange=draw);
draw();

/* Fit, then pan and zoom. */
let view={x:0,y:0,k:1};
function apply(){ vp.setAttribute("transform",
  `translate(${view.x},${view.y}) scale(${view.k},${-view.k})`); }
function fit(){
  const xs=D.nodes.map(n=>n.x), ys=D.nodes.map(n=>n.y);
  if(!xs.length) return;
  const r=svg.getBoundingClientRect();
  const x0=Math.min(...xs)-6,x1=Math.max(...xs)+6,y0=Math.min(...ys)-8,y1=Math.max(...ys)+10;
  view.k=0.92*Math.min(r.width/(x1-x0||1), r.height/(y1-y0||1));
  view.x=r.width/2-view.k*(x0+x1)/2;
  view.y=r.height/2+view.k*(y0+y1)/2;
  apply();
}
let drag=null;
svg.addEventListener("pointerdown",e=>{drag={x:e.clientX,y:e.clientY,vx:view.x,vy:view.y};
  svg.classList.add("pan"); svg.setPointerCapture(e.pointerId);});
svg.addEventListener("pointermove",e=>{ if(!drag) return;
  view.x=drag.vx+(e.clientX-drag.x); view.y=drag.vy+(e.clientY-drag.y); apply();});
const end=()=>{drag=null; svg.classList.remove("pan");};
svg.addEventListener("pointerup",end); svg.addEventListener("pointercancel",end);
svg.addEventListener("wheel",e=>{e.preventDefault();
  const r=svg.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top;
  const f=e.deltaY<0?1.12:1/1.12;
  view.x=mx-(mx-view.x)*f; view.y=my-(my-view.y)*f; view.k*=f; apply();},{passive:false});
svg.addEventListener("dblclick",fit);
addEventListener("resize",fit);
fit();
</script></body></html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="composed *.graph.json file(s)")
    ap.add_argument("--out", default="graph-viz")
    ap.add_argument("--formats", nargs="*", default=["graphml", "gexf", "html"],
                    choices=["graphml", "gexf", "html"])
    args = ap.parse_args()

    paths = []
    for f in args.inputs:
        paths.extend(sorted(glob.glob(f)) if any(c in f for c in "*?[") else [f])
    if not paths:
        print("no input files")
        return 1

    os.makedirs(args.out, exist_ok=True)
    for p in paths:
        try:
            g = load(p)
        except Exception as e:
            print(f"  ! {os.path.basename(p)}: {e}")
            continue
        base = os.path.basename(p).replace(".graph.json", "")
        pos = layout(g)
        st = stats(g)

        written = []
        if "graphml" in args.formats:
            f = os.path.join(args.out, base + ".graphml")
            open(f, "w").write(to_graphml(g, pos)); written.append("graphml")
        if "gexf" in args.formats:
            f = os.path.join(args.out, base + ".gexf")
            open(f, "w").write(to_gexf(g, pos)); written.append("gexf")
        if "html" in args.formats:
            f = os.path.join(args.out, base + ".html")
            open(f, "w").write(to_html(g, pos, st)); written.append("html")

        flag = "" if st["components"] == 1 else \
            f"  <-- {st['components']} disconnected pieces"
        print(f"  {base:<22} {st['rooms']:3d} rooms  "
              f"{sum(v for k, v in st['relations'].items() if k in TRAVERSABLE):3d} links  "
              f"[{', '.join(written)}]{flag}")

    print(f"\n-> {args.out}")
    print("   .html opens in any browser, no software needed")
    print("   .graphml opens in yEd, Gephi, Cytoscape; .gexf in Gephi")
    return 0


if __name__ == "__main__":
    sys.exit(main())
