/* Compose a plan and its annotation into a hierarchical building scene graph.
 *
 * This is a deliberate port of `annotator/compose.py`, kept line-for-line
 * comparable with it. Two copies of the same logic is a liability, so it is
 * defended by `annotator/test_compose_parity.py`, which runs both over real
 * plans and every verdict combination and fails on the smallest difference.
 *
 * `compose.py` remains authoritative: it is what `build_gt.py` runs when the
 * collected annotations are turned into ground truth. This copy exists so the
 * statically hosted build can show an annotator the building their verdicts
 * produce -- above all whether the storeys actually chain into one connected
 * building -- without a server to ask.
 *
 * If you change one, change the other and run the parity test.
 */
(function (root) {
  "use strict";

  function key(a, b) { return a < b ? a + "|" + b : b + "|" + a; }

  function compose(plan, anno) {
    const roomsV = anno.rooms || {};
    const edgesV = anno.edges || {};
    const vertV = anno.vertical || {};
    const addedE = anno.added_edges || [];
    const addedV = anno.added_vertical || [];
    const missing = anno.missing_rooms || [];

    const nodes = [], edges = [], heldOut = [], requests = [];
    const counts = {
      rooms_total: 0, rooms_judged: 0, rooms_kept: 0,
      rooms_labelled_only: 0,
      links_total: 0, links_judged: 0, links_kept: 0,
      vertical_total: 0, vertical_judged: 0, vertical_kept: 0,
    };

    const building = plan.model || "building";
    nodes.push({ id: building, layer: "building", label: building, provenance: "ifc" });

    const keep = new Set();
    for (const st of plan.storeys) {
      nodes.push({
        id: st.gid, layer: "storey", label: st.name,
        elevation: st.elevation, parent: building, provenance: "ifc",
      });
      edges.push({ a: building, b: st.gid, relation: "contains", provenance: "ifc" });

      for (const r of st.rooms) {
        counts.rooms_total += 1;
        const a = roomsV[r.id] || {};
        const v = a.verdict;
        if (v) counts.rooms_judged += 1;
        // Looked at and relabelled, but never judged -- counted apart so
        // "not started" and "nearly done" are distinguishable.
        else if (a.label || a.note) counts.rooms_labelled_only += 1;
        if (v === "spurious") continue;
        if (v === "merge" || v === "split")
          requests.push({ room: r.id, request: v, storey: st.gid, note: a.note || "" });
        if (!v) continue;                 // unjudged rooms are not ground truth
        keep.add(r.id);
        counts.rooms_kept += 1;
        const n = {
          id: r.id, layer: "space",
          label: a.label || r.label,
          ifc_label: r.label,
          predicted_label: r.predicted_label === undefined ? null : r.predicted_label,
          area: r.area, centroid: r.centroid,
          parent: st.gid,
          provenance: r.source === "ifc" ? "ifc" : "inferred",
          verdict: v,
        };
        if (a.note) n.note = a.note;
        nodes.push(n);
        edges.push({ a: st.gid, b: r.id, relation: "contains", provenance: "ifc" });
      }
    }

    /* ---- intra-floor connectivity ---------------------------------- */
    for (const st of plan.storeys) {
      for (const e of st.edges) {
        counts.links_total += 1;
        const a = edgesV[key(e.a, e.b)] || {};
        const v = a.verdict;
        if (v) counts.links_judged += 1;
        if (v === "unsure") {
          heldOut.push({ a: e.a, b: e.b, kind: "link", storey: st.gid });
          continue;
        }
        if (v !== "correct") continue;
        if (!keep.has(e.a) || !keep.has(e.b)) {
          heldOut.push({ a: e.a, b: e.b, kind: "link", storey: st.gid,
                         why: "endpoint room not confirmed" });
          continue;
        }
        counts.links_kept += 1;
        const out = { a: e.a, b: e.b, relation: e.type, storey: st.gid,
                      provenance: "ifc+annotator" };
        if (e.width) out.width = e.width;
        edges.push(out);
      }
    }

    for (const e of addedE) {
      if (!keep.has(e.a) || !keep.has(e.b)) {
        heldOut.push({ a: e.a, b: e.b, kind: "link", why: "endpoint room not confirmed" });
        continue;
      }
      counts.links_kept += 1;
      edges.push({ a: e.a, b: e.b, relation: "connected_by_door",
                   storey: e.storey === undefined ? null : e.storey,
                   provenance: "annotator" });
    }

    /* ---- the join between floors ----------------------------------- */
    for (const v of (plan.vertical || [])) {
      counts.vertical_total += 1;
      const a = vertV[key(v.a, v.b)] || {};
      const verdict = a.verdict;
      if (verdict) counts.vertical_judged += 1;
      if (verdict === "unsure") {
        heldOut.push({ a: v.a, b: v.b, kind: "vertical" });
        continue;
      }
      if (verdict !== "correct") continue;
      if (!keep.has(v.a) || !keep.has(v.b)) {
        heldOut.push({ a: v.a, b: v.b, kind: "vertical",
                       why: "endpoint room not confirmed" });
        continue;
      }
      counts.vertical_kept += 1;
      edges.push({ a: v.a, b: v.b, relation: "vertically_connected",
                   kind: v.kind === undefined ? null : v.kind,
                   provenance: "ifc+annotator" });
    }

    for (const v of addedV) {
      if (!keep.has(v.a) || !keep.has(v.b)) {
        heldOut.push({ a: v.a, b: v.b, kind: "vertical",
                       why: "endpoint room not confirmed" });
        continue;
      }
      counts.vertical_kept += 1;
      edges.push({ a: v.a, b: v.b, relation: "vertically_connected",
                   kind: v.kind === undefined ? "manual" : v.kind,
                   provenance: "annotator" });
    }

    const complete =
      counts.rooms_judged === counts.rooms_total &&
      counts.links_judged === counts.links_total &&
      counts.vertical_judged === counts.vertical_total;

    return {
      model: building,
      annotator: anno.annotator === undefined ? null : anno.annotator,
      updated: anno.updated === undefined ? null : anno.updated,
      source: "annotated",
      complete: complete,
      counts: counts,
      nodes: nodes,
      edges: edges,
      held_out: heldOut,
      requests: requests,
      missing_rooms: missing,
    };
  }

  function connectivityGt(composed) {
    const rooms = composed.nodes.filter((n) => n.layer === "space");
    const rel = ["connected_by_door", "open_passage", "vertically_connected"];
    return {
      building: composed.model,
      source: "annotated",
      annotator: composed.annotator === undefined ? null : composed.annotator,
      complete: composed.complete,
      rooms: rooms.map((r) => ({
        rid: r.id, label: r.label, storey: r.parent,
        area: r.area === undefined ? null : r.area,
      })),
      edges: composed.edges.filter((e) => rel.includes(e.relation))
        .map((e) => ({ a: e.a, b: e.b, type: e.relation })),
      held_out: composed.held_out,
    };
  }

  /** Is every confirmed room reachable from every other? */
  function connectivityReport(composed) {
    const spaces = composed.nodes.filter((n) => n.layer === "space").map((n) => n.id);
    const parent = {};
    for (const n of composed.nodes) if (n.layer === "space") parent[n.id] = n.parent;
    const adj = {};
    spaces.forEach((s) => (adj[s] = []));
    const trav = ["connected_by_door", "open_passage", "vertically_connected"];
    for (const e of composed.edges) {
      if (!trav.includes(e.relation)) continue;
      if (adj[e.a] && adj[e.b]) { adj[e.a].push(e.b); adj[e.b].push(e.a); }
    }
    const seen = new Set(); const comps = [];
    for (const s of spaces) {
      if (seen.has(s)) continue;
      const stack = [s], comp = [];
      seen.add(s);
      while (stack.length) {
        const n = stack.pop(); comp.push(n);
        for (const m of adj[n]) if (!seen.has(m)) { seen.add(m); stack.push(m); }
      }
      comps.push(comp);
    }
    comps.sort((a, b) => b.length - a.length);
    return {
      rooms: spaces.length,
      components: comps.length,
      largest: comps.length ? comps[0].length : 0,
      storeys_spanned: comps.length
        ? new Set(comps[0].map((n) => parent[n])).size : 0,
      isolated: comps.filter((c) => c.length === 1).length,
    };
  }

  const api = { compose, connectivityGt, connectivityReport, key };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.BIMSGCompose = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
