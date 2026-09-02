/* BIM-Graphs ground-truth annotator.
 *
 * Annotation is by correction: the pipeline's prediction is drawn over the real
 * floor plan and the annotator adjudicates it. Everything is keyed by the
 * pipeline's own node ids so the result joins straight back onto the graph.
 *
 * Two rules shape the code below.
 *
 * 1. Every mutation goes through `mutate()`. It snapshots the annotation first,
 *    so undo is total rather than a special case a few actions remember to
 *    support. Anything that edits `S.anno` outside `mutate()` is a bug.
 *
 * 2. Anything the annotator can add, the annotator can remove. Added links,
 *    added floor-to-floor links and missing-room pins all carry a stable `id`
 *    precisely so they can be deleted again. The previous version pushed them
 *    into arrays with no inverse, which made a misclick permanent.
 */

const SVGNS = "http://www.w3.org/2000/svg";
const VERDICTS = ["correct", "spurious", "unsure", "merge", "split"];
const EDGE_VERDICTS = ["correct", "spurious", "unsure"];
const AUTOSAVE_MS = 20000;
const MAX_HISTORY = 120;

const S = {
  model: null, plan: null, storey: 0,
  anno: emptyAnno(),
  sel: null,                 // {kind:'room'|'edge'|'vedge', id, ...}
  mode: "select",            // select | edge | vert | room
  edgeFrom: null,            // {id,label} pending intra-floor link
  vertFrom: null,            // {id,label,storey,storeyName} pending cross-floor link
  dirty: false, store: null,
  models: [], viewer: null,
  layout: "plan", splitPct: 55, followFloor: true,
  history: [], future: [],
  view: { x: 0, y: 0, k: 30 },
};

const $ = (id) => document.getElementById(id);
const svg = $("plan");

function emptyAnno() {
  return {
    rooms: {}, edges: {}, vertical: {},
    added_edges: [], added_vertical: [], missing_rooms: [],
  };
}

/* ---------------------------------------------------------------- utils */

function annotatorName() {
  return ($("annotator").value || "").trim().replace(/[^A-Za-z0-9_.-]/g, "_");
}
function edgeKey(a, b) { return a < b ? a + "|" + b : b + "|" + a; }
function uid() { return "x" + Math.random().toString(36).slice(2, 10); }
function clone(o) { return JSON.parse(JSON.stringify(o)); }
function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

let bannerTimer = null;
function banner(text, kind) {
  const b = $("banner");
  b.textContent = text;
  b.className = "banner on" + (kind ? " " + kind : "");
  clearTimeout(bannerTimer);
  if (text) bannerTimer = setTimeout(() => (b.className = "banner"), 2600);
}

function setDirty(d) {
  S.dirty = d;
  const el = $("saveState");
  el.textContent = d ? "unsaved changes" : "saved";
  el.className = "saving" + (d ? " dirty" : "");
}

/* ------------------------------------------------------- undo / redo core */

/** Run `fn`, having first recorded the state it is about to change. */
function mutate(label, fn) {
  S.history.push({ anno: clone(S.anno), label });
  if (S.history.length > MAX_HISTORY) S.history.shift();
  S.future.length = 0;
  fn();
  setDirty(true);
  render();
}

function undo() {
  if (!S.history.length) { banner("nothing to undo", "warn"); return; }
  const prev = S.history.pop();
  S.future.push({ anno: clone(S.anno), label: prev.label });
  S.anno = prev.anno;
  S.sel = null; S.edgeFrom = null; S.vertFrom = null;
  setDirty(true);
  banner("undo: " + prev.label);
  render();
}

function redo() {
  if (!S.future.length) { banner("nothing to redo", "warn"); return; }
  const nxt = S.future.pop();
  S.history.push({ anno: clone(S.anno), label: nxt.label });
  S.anno = nxt.anno;
  S.sel = null; S.edgeFrom = null; S.vertFrom = null;
  setDirty(true);
  banner("redo: " + nxt.label);
  render();
}

function renderHistoryButtons() {
  $("btnUndo").disabled = !S.history.length;
  $("btnRedo").disabled = !S.future.length;
  $("btnUndo").title = S.history.length
    ? `Undo ${S.history[S.history.length - 1].label} (Ctrl+Z)` : "Nothing to undo";
  $("btnRedo").title = S.future.length
    ? `Redo ${S.future[S.future.length - 1].label} (Ctrl+Shift+Z)` : "Nothing to redo";
}

/* ------------------------------------------------------------- data i/o */

async function loadModels() {
  let list;
  try {
    list = await S.store.listModels();
  } catch (e) {
    banner(S.store.mode === "local" ? "cannot load plan index" : "cannot reach server", "err");
    console.error(e); return;
  }
  const sel = $("modelSel");
  sel.innerHTML = "";
  if (!list.length) {
    sel.innerHTML = "<option>— no plans exported —</option>";
    $("modelInfo").textContent =
      "Run: python main/export_plans.py 'dataset/test_set/*.ifc' --out annotator/data";
    return;
  }
  S.models = list;
  list.forEach((m) => {
    const o = document.createElement("option");
    o.value = m.model;
    const who = Object.keys(m.annotated);
    o.textContent = m.model + (m.hasIfc ? "  ⬤" : "") +
      (who.length ? "  ✓ " + who.join(", ") : "");
    o.title = m.hasIfc ? "an IFC file is available for this model" : "";
    sel.appendChild(o);
  });
  // Guard the select itself: if the annotator cancels the unsaved-work prompt
  // the dropdown has already moved, so it has to be put back or the header
  // shows one model while the canvas shows another.
  sel.onchange = async () => {
    const ok = await loadModel(sel.value);
    if (!ok) sel.value = S.model;
  };
  await loadModel(list[0].model);
}

function confirmDiscard() {
  return !S.dirty ||
    confirm("You have unsaved changes. Discard them?\n\n" +
            "Cancel, then press Save (Ctrl+S) to keep your work.");
}

async function loadModel(name) {
  if (!confirmDiscard()) return false;
  S.model = name;
  S.plan = await S.store.getPlan(name);
  S.storey = 0;
  S.sel = null; S.edgeFrom = null; S.vertFrom = null;
  S.history.length = 0; S.future.length = 0;
  await loadAnnotation();
  const nr = S.plan.storeys.reduce((a, s) => a + s.rooms.length, 0);
  $("modelInfo").textContent =
    `${S.plan.storeys.length} storeys · ${nr} rooms · ` +
    `${(S.plan.vertical || []).length} floor links`;
  renderStoreys();
  render();
  fit();
  renderModelNav();
  on3dModelChanged();
  return true;
}

/** Position in the sequence, and whether stepping is possible. */
function renderModelNav() {
  const i = S.models.findIndex((m) => m.model === S.model);
  $("modelPos").textContent = i >= 0 ? `${i + 1} of ${S.models.length}` : "";
  $("btnPrev").disabled = i <= 0;
  $("btnNext").disabled = i < 0 || i >= S.models.length - 1;
}

async function stepModel(d) {
  const i = S.models.findIndex((m) => m.model === S.model);
  const j = i + d;
  if (i < 0 || j < 0 || j >= S.models.length) return;
  const name = S.models[j].model;
  if (await loadModel(name)) $("modelSel").value = name;
}

async function loadAnnotation() {
  const who = annotatorName();
  if (!who) { S.anno = emptyAnno(); setDirty(false); return; }
  try {
    const d = await S.store.getAnnotation(S.model, who);
    const e = emptyAnno();
    S.anno = {
      rooms: d.rooms || e.rooms,
      edges: d.edges || e.edges,
      vertical: d.vertical || e.vertical,
      added_edges: d.added_edges || e.added_edges,
      added_vertical: d.added_vertical || e.added_vertical,
      missing_rooms: d.missing_rooms || e.missing_rooms,
    };
    // Older files predate stable ids; without one, deletion cannot target a row.
    for (const k of ["added_edges", "added_vertical", "missing_rooms"])
      S.anno[k].forEach((r) => { if (!r.id) r.id = uid(); });
  } catch (e) { S.anno = emptyAnno(); }
  setDirty(false);
}

async function save(quiet) {
  const who = annotatorName();
  if (!who) {
    banner("enter your name first — annotations are stored per person", "warn");
    $("annotator").focus();
    return false;
  }
  const el = $("saveState");
  el.textContent = "saving…"; el.className = "saving";
  try {
    await S.store.saveAnnotation(S.model, who, S.anno);
    setDirty(false);
    renderStorageWarning();
    if (!quiet) banner("saved");
    return true;
  } catch (e) {
    el.textContent = "save failed"; el.className = "saving err";
    banner(e.message || "save failed — your work is still in the page", "err");
    console.error(e);
    return false;
  }
}

setInterval(() => { if (S.dirty && annotatorName() && S.model) save(true); }, AUTOSAVE_MS);

/* ------------------------------------------------------------- rendering */

function storey() { return S.plan.storeys[S.storey]; }
function storeyById(gid) { return S.plan.storeys.find((s) => s.gid === gid); }
function roomAnywhere(id) {
  for (const st of S.plan.storeys) {
    const r = st.rooms.find((x) => x.id === id);
    if (r) return { room: r, storey: st };
  }
  return null;
}

function storeyDone(st) {
  const r = st.rooms.every((x) => S.anno.rooms[x.id]);
  const e = st.edges.every((x) => S.anno.edges[edgeKey(x.a, x.b)]);
  return r && e && (st.rooms.length || st.edges.length);
}

function renderStoreys() {
  const box = $("storeyList");
  box.innerHTML = "";
  S.plan.storeys.forEach((st, i) => {
    const b = document.createElement("button");
    const done = storeyDone(st);
    b.innerHTML =
      `<span>${esc(st.name)}</span>` +
      `<span class="el">${st.elevation} m · ${st.rooms.length}r</span>` +
      (done ? `<span class="tick">✓</span>` : "");
    if (i === S.storey) b.className = "active";
    b.onclick = () => gotoStorey(i);
    box.appendChild(b);
  });
}

function gotoStorey(i) {
  if (i < 0 || i >= S.plan.storeys.length) return;
  S.storey = i;
  S.sel = null;
  S.edgeFrom = null;          // an intra-floor link cannot span storeys
  renderStoreys();
  render();
  fit();
  syncSectionToFloor();
  if (S.vertFrom)
    banner(`floor link: pick the room on ${storey().name} that connects to ` +
           `${S.vertFrom.label}`);
}

function poly(points) { return points.map((p) => p.join(",")).join(" "); }

/** Every vertical link, predicted and added, as one list. */
function allVertical() {
  const out = (S.plan.vertical || []).map((v) => ({
    ...v, key: edgeKey(v.a, v.b), added: false,
  }));
  for (const v of S.anno.added_vertical)
    out.push({ ...v, key: edgeKey(v.a, v.b), added: true, type: "vertically_connected" });
  return out;
}

function render() {
  if (!S.plan) return;
  const st = storey();
  const G = {
    walls: $("gWalls"), rooms: $("gRooms"), edges: $("gEdges"),
    doors: $("gDoors"), missing: $("gMissing"), vert: $("gVert"), labels: $("gLabels"),
  };
  Object.values(G).forEach((g) => (g.innerHTML = ""));

  if ($("lyWalls").checked)
    for (const w of st.walls) {
      const e = document.createElementNS(SVGNS, "polygon");
      e.setAttribute("points", poly(w));
      e.setAttribute("class", "wall");
      G.walls.appendChild(e);
    }

  if ($("lyRooms").checked)
    // Largest first, so a big circulation region cannot bury the small rooms
    // that sit on top of it and make them unclickable.
    for (const r of [...st.rooms].sort((a, b) => b.area - a.area)) {
      const e = document.createElementNS(SVGNS, "polygon");
      e.setAttribute("points", poly(r.polygon));
      const v = S.anno.rooms[r.id]?.verdict;
      const pending = S.edgeFrom?.id === r.id || S.vertFrom?.id === r.id;
      e.setAttribute("class", [
        "room",
        r.source === "inferred" ? "inferred" : "ifc",
        v ? "v-" + v : "",
        S.sel?.kind === "room" && S.sel.id === r.id ? "sel" : "",
        pending ? "pending" : "",
      ].filter(Boolean).join(" "));
      e.onclick = (ev) => { ev.stopPropagation(); onRoomClick(r); };
      G.rooms.appendChild(e);
    }

  if ($("lyEdges").checked) {
    const byId = Object.fromEntries(st.rooms.map((r) => [r.id, r]));
    const draw = (a, b, cls, onclick) => {
      if (!byId[a] || !byId[b]) return;
      const e = document.createElementNS(SVGNS, "line");
      e.setAttribute("x1", byId[a].centroid[0]); e.setAttribute("y1", byId[a].centroid[1]);
      e.setAttribute("x2", byId[b].centroid[0]); e.setAttribute("y2", byId[b].centroid[1]);
      e.setAttribute("class", cls);
      e.onclick = (ev) => { ev.stopPropagation(); onclick(); };
      G.edges.appendChild(e);
    };
    for (const ed of st.edges) {
      const k = edgeKey(ed.a, ed.b);
      const v = S.anno.edges[k]?.verdict;
      draw(ed.a, ed.b, ["edge", ed.type, v ? "v-" + v : "",
                        S.sel?.kind === "edge" && S.sel.id === k ? "sel" : ""]
                       .filter(Boolean).join(" "),
           () => { S.sel = { kind: "edge", id: k, a: ed.a, b: ed.b }; render(); });
    }
    for (const ed of S.anno.added_edges) {
      if (ed.storey !== st.gid) continue;
      draw(ed.a, ed.b, "edge manual" +
           (S.sel?.kind === "added" && S.sel.id === ed.id ? " sel" : ""),
           () => { S.sel = { kind: "added", id: ed.id }; render(); });
    }
  }

  if ($("lyDoors").checked)
    for (const d of st.doors) {
      const e = document.createElementNS(SVGNS, "circle");
      e.setAttribute("cx", d.x); e.setAttribute("cy", d.y);
      e.setAttribute("r", 0.16); e.setAttribute("class", "door");
      G.doors.appendChild(e);
    }

  for (const m of S.anno.missing_rooms) {
    if (m.storey !== st.gid) continue;
    const e = document.createElementNS(SVGNS, "circle");
    e.setAttribute("cx", m.x); e.setAttribute("cy", m.y);
    e.setAttribute("r", 0.34);
    e.setAttribute("class", "missing" +
      (S.sel?.kind === "missing" && S.sel.id === m.id ? " sel" : ""));
    e.style.cursor = "pointer";
    e.onclick = (ev) => { ev.stopPropagation(); S.sel = { kind: "missing", id: m.id }; render(); };
    G.missing.appendChild(e);
  }

  // A room that carries a floor-to-floor link gets a marker, so the storey
  // where the stair lands is visible without opening the side list.
  const byId = Object.fromEntries(st.rooms.map((r) => [r.id, r]));
  for (const v of allVertical()) {
    for (const end of [v.a, v.b]) {
      const r = byId[end];
      if (!r) continue;
      const e = document.createElementNS(SVGNS, "circle");
      e.setAttribute("cx", r.centroid[0]);
      e.setAttribute("cy", r.centroid[1] + 0.45);
      e.setAttribute("r", 0.2);
      e.setAttribute("class", "vbadge");
      e.style.cursor = "pointer";
      e.onclick = (ev) => {
        ev.stopPropagation();
        S.sel = { kind: "vedge", id: v.key, added: v.added, vid: v.id };
        render();
      };
      G.vert.appendChild(e);
    }
  }

  if ($("lyLabels").checked) {
    // The viewport is scaled (k,-k) to put world +Y up, which would also
    // mirror any text. Counter-flip this group and negate y so labels read
    // the right way round.
    G.labels.setAttribute("transform", "scale(1,-1)");
    for (const r of st.rooms) {
      const t = document.createElementNS(SVGNS, "text");
      t.setAttribute("x", r.centroid[0]);
      t.setAttribute("y", -r.centroid[1]);
      t.setAttribute("class", "plabel");
      // Show the model's guess on the plan itself for still-anonymous rooms
      // ("room_1") -- otherwise the annotator has nothing to react to when
      // scanning the floor plate, only when they happen to click in.
      const shown = S.anno.rooms[r.id]?.label
        || (r.predicted_label ? `${r.predicted_label}?` : r.label);
      t.textContent = String(shown).slice(0, 22);
      G.labels.appendChild(t);
    }
    for (const m of S.anno.missing_rooms) {
      if (m.storey !== st.gid) continue;
      const t = document.createElementNS(SVGNS, "text");
      t.setAttribute("x", m.x); t.setAttribute("y", -(m.y + 0.55));
      t.setAttribute("class", "blabel");
      t.setAttribute("fill", "var(--r-manual)");
      t.textContent = m.label;
      G.labels.appendChild(t);
    }
  }

  renderProgress();
  renderInspector();
  renderVertList();
  renderStoreys();
  renderHistoryButtons();
}

function renderProgress() {
  const st = storey();
  const totR = st.rooms.length;
  const doneR = st.rooms.filter((r) => S.anno.rooms[r.id]).length;
  const totE = st.edges.length;
  const doneE = st.edges.filter((e) => S.anno.edges[edgeKey(e.a, e.b)]).length;
  const vAll = allVertical();
  const vDone = vAll.filter((v) => v.added || S.anno.vertical[v.key]).length;
  const pct = (a, b) => (b ? Math.round((100 * a) / b) : 100);
  $("progress").innerHTML = `
    <div class="row"><span>rooms, this floor</span><b>${doneR}/${totR}</b></div>
    <div class="bar"><i style="width:${pct(doneR, totR)}%"></i></div>
    <div class="row"><span>links, this floor</span><b>${doneE}/${totE}</b></div>
    <div class="bar"><i style="width:${pct(doneE, totE)}%"></i></div>
    <div class="row"><span>floor links, building</span><b>${vDone}/${vAll.length}</b></div>
    <div class="bar"><i style="width:${pct(vDone, vAll.length)}%"></i></div>
    <div class="row" style="margin-top:6px"><span>you added</span>
      <b>${S.anno.added_edges.length} links · ${S.anno.added_vertical.length} floor
      · ${S.anno.missing_rooms.length} rooms</b></div>`;
}

function renderVertList() {
  const box = $("vertList");
  const items = allVertical();
  if (!items.length) {
    box.innerHTML = '<div class="muted small">None yet. Press <b>V</b>, click a ' +
      'room, switch floor, click the room it opens onto.</div>';
    return;
  }
  box.innerHTML = "";
  for (const v of items) {
    const ra = roomAnywhere(v.a), rb = roomAnywhere(v.b);
    const verdict = v.added ? "manual" : (S.anno.vertical[v.key]?.verdict || "");
    const row = document.createElement("div");
    row.className = "vrow" + (S.sel?.kind === "vedge" && S.sel.id === v.key ? " sel" : "");
    row.innerHTML =
      `<div><div>${esc(ra?.room.label || "?")} ↕ ${esc(rb?.room.label || "?")}</div>` +
      `<div class="st">${esc(ra?.storey.name || "?")} → ${esc(rb?.storey.name || "?")}` +
      `${v.kind ? " · " + esc(v.kind) : ""}</div></div>` +
      `<span class="badge ${verdict}"></span>`;
    row.onclick = () => {
      S.sel = { kind: "vedge", id: v.key, added: v.added, vid: v.id };
      // Jump to the floor the link starts on so the annotator can see it.
      if (ra) {
        const i = S.plan.storeys.indexOf(ra.storey);
        if (i >= 0 && i !== S.storey) { S.storey = i; fit(); }
      }
      render();
    };
    box.appendChild(row);
  }
}

/* ----------------------------------------------------------- interaction */

function onRoomClick(r) {
  if (S.mode === "edge") {
    if (!S.edgeFrom) {
      S.edgeFrom = { id: r.id, label: r.label };
      banner(`from ${r.label} — now click the room it connects to`);
      render();
    } else if (S.edgeFrom.id !== r.id) {
      const a = S.edgeFrom.id, b = r.id;
      const dupPred = storey().edges.some((e) => edgeKey(e.a, e.b) === edgeKey(a, b));
      const dupAdd = S.anno.added_edges.some((e) => edgeKey(e.a, e.b) === edgeKey(a, b));
      if (dupPred || dupAdd) {
        banner(dupPred ? "that link is already predicted — judge it instead"
                       : "you already added that link", "warn");
        S.edgeFrom = null; render(); return;
      }
      mutate("add link", () => {
        S.anno.added_edges.push({
          id: uid(), a, b, storey: storey().gid, type: "manual",
        });
      });
      S.edgeFrom = null;
      banner("link added — Ctrl+Z undoes it");
    }
    return;
  }

  if (S.mode === "vert") {
    if (!S.vertFrom) {
      S.vertFrom = {
        id: r.id, label: r.label,
        storey: storey().gid, storeyName: storey().name,
      };
      banner(`from ${r.label} on ${storey().name} — switch floor ([ or ]) and ` +
             `click the room it connects to`);
      render();
    } else if (S.vertFrom.id !== r.id) {
      if (S.vertFrom.storey === storey().gid) {
        banner("a floor link must join two different floors — switch floor first", "warn");
        return;
      }
      const a = S.vertFrom.id, b = r.id;
      const dup = allVertical().some((v) => v.key === edgeKey(a, b));
      if (dup) {
        banner("those two rooms are already linked", "warn");
        S.vertFrom = null; render(); return;
      }
      mutate("add floor link", () => {
        S.anno.added_vertical.push({
          id: uid(), a, b,
          storey_a: S.vertFrom.storey, storey_b: storey().gid, kind: "manual",
        });
      });
      S.vertFrom = null;
      banner("floor link added");
    }
    return;
  }

  S.sel = { kind: "room", id: r.id };
  render();
}

function setVerdict(v) {
  if (!S.sel) return;
  const k = S.sel.kind;
  if (k === "room") {
    mutate(v ? `room → ${v}` : "clear room verdict", () => {
      if (!v) {
        const cur = S.anno.rooms[S.sel.id] || {};
        // Keep a corrected label even when the verdict is cleared -- they are
        // separate judgements, and losing typed text to a stray keystroke is
        // the kind of thing that makes people stop trusting the tool.
        if (cur.label || cur.note) {
          delete cur.verdict;
          S.anno.rooms[S.sel.id] = cur;
        } else {
          delete S.anno.rooms[S.sel.id];
        }
      } else {
        S.anno.rooms[S.sel.id] = { ...(S.anno.rooms[S.sel.id] || {}), verdict: v };
      }
    });
  } else if (k === "edge") {
    mutate(v ? `link → ${v}` : "clear link verdict", () => {
      if (!v) delete S.anno.edges[S.sel.id];
      else S.anno.edges[S.sel.id] = {
        ...(S.anno.edges[S.sel.id] || {}), verdict: v, a: S.sel.a, b: S.sel.b };
    });
  } else if (k === "vedge" && !S.sel.added) {
    mutate(v ? `floor link → ${v}` : "clear floor-link verdict", () => {
      if (!v) delete S.anno.vertical[S.sel.id];
      else {
        const [a, b] = S.sel.id.split("|");
        S.anno.vertical[S.sel.id] = {
          ...(S.anno.vertical[S.sel.id] || {}), verdict: v, a, b };
      }
    });
  }
}

/** Remove whatever is selected, when it is something the annotator added. */
function deleteSelected() {
  if (!S.sel) return;
  const k = S.sel.kind;
  if (k === "added") {
    mutate("delete link", () => {
      S.anno.added_edges = S.anno.added_edges.filter((e) => e.id !== S.sel.id);
    });
    S.sel = null; render(); banner("link deleted");
  } else if (k === "missing") {
    mutate("delete missing-room pin", () => {
      S.anno.missing_rooms = S.anno.missing_rooms.filter((m) => m.id !== S.sel.id);
    });
    S.sel = null; render(); banner("pin deleted");
  } else if (k === "vedge" && S.sel.added) {
    mutate("delete floor link", () => {
      S.anno.added_vertical = S.anno.added_vertical.filter((v) => v.id !== S.sel.vid);
    });
    S.sel = null; render(); banner("floor link deleted");
  } else if (k === "edge" || k === "vedge") {
    // A predicted link is not the annotator's to delete: the point of the task
    // is to record that the pipeline proposed it and was wrong.
    banner("this link is the model's prediction — mark it spurious (2) instead", "warn");
  } else if (k === "room") {
    banner("rooms come from the model — mark spurious (2) if it is not real", "warn");
  }
}

function verdictButtons(list, current, note) {
  return `<div class="vbtns">
    ${list.map((v, i) => `<button class="vbtn ${v} ${current === v ? "on" : ""}"
       data-v="${v}" title="key ${i + 1}"><span class="dot"
       style="background:var(--v-${v})"></span>${v}</button>`).join("")}
  </div>` + (note ? `<div class="note">${note}</div>` : "");
}

function renderInspector() {
  const box = $("inspector");
  if (!S.sel) {
    box.innerHTML = '<div class="empty muted">Select a room or link on the plan.' +
      '<br><br>Press <b>?</b> for the annotation guide.</div>';
    return;
  }

  if (S.sel.kind === "room") {
    const r = storey().rooms.find((x) => x.id === S.sel.id);
    if (!r) { box.innerHTML = ""; return; }
    const a = S.anno.rooms[r.id] || {};
    const hasPred = !!r.predicted_label;
    const defaultVal = a.label ?? (hasPred ? r.predicted_label : r.label);
    const tierCol = r.source === "inferred" ? "var(--h-space-inf)" : "var(--h-space)";
    box.innerHTML = `
      <div class="ins-tier" style="background:${tierCol}">space</div>
      <div class="ins-title">${esc(a.label || r.label)}</div>
      <div class="ins-sub">${r.source === "inferred"
        ? "recovered geometrically — the IFC never stated this room"
        : "stated by the IFC"}</div>
      ${hasPred ? `<div class="ins-row"><span>model guess</span>
        <span title="${esc(r.label_source || "")}">${esc(r.predicted_label)}</span></div>` : ""}
      <div class="ins-row"><span>area</span><span>${r.area} m²</span></div>
      <div class="ins-row"><span>storey</span><span>${esc(storey().name)}</span></div>
      <div class="ins-row"><span>id</span><span title="${esc(r.id)}">${esc(r.id.slice(0, 14))}…</span></div>
      ${verdictButtons(VERDICTS, a.verdict, "")}
      <div class="field">
        <label>${hasPred
          ? "correct functional label — edit if the guess is wrong, leave to confirm"
          : "corrected label (ground truth)"}</label>
        <input id="lblIn" value="${esc(defaultVal)}">
      </div>
      <div class="field">
        <label>note (optional)</label>
        <textarea id="noteIn" placeholder="anything the verdict cannot capture">${esc(a.note || "")}</textarea>
      </div>
      <div class="note">${hasPred
        ? "This room had no label until the model guessed one. Confirming or " +
          "correcting it here is the ground truth for scoring that guess."
        : "Correct the label if the IFC name is wrong or missing — this " +
          "doubles as ground truth for semantic labelling."}</div>`;
    wireVerdicts(box, a.verdict);
    $("lblIn").onchange = (ev) => mutate("edit label", () => {
      S.anno.rooms[r.id] = { ...(S.anno.rooms[r.id] || {}), label: ev.target.value.trim() };
    });
    $("noteIn").onchange = (ev) => mutate("edit note", () => {
      S.anno.rooms[r.id] = { ...(S.anno.rooms[r.id] || {}), note: ev.target.value.trim() };
    });
    return;
  }

  if (S.sel.kind === "added") {
    const e = S.anno.added_edges.find((x) => x.id === S.sel.id);
    if (!e) { S.sel = null; box.innerHTML = ""; return; }
    const nm = (id) => storey().rooms.find((r) => r.id === id)?.label || "?";
    box.innerHTML = `
      <div class="ins-tier" style="background:var(--r-manual)">link you added</div>
      <div class="ins-title">${esc(nm(e.a))} ↔ ${esc(nm(e.b))}</div>
      <div class="ins-sub">not predicted by the model — you added it</div>
      <button class="danger" id="delBtn">Delete this link</button>
      <div class="note">Added links are yours to remove. Delete, or press
      <b>Del</b>. <b>Ctrl+Z</b> undoes either way.</div>`;
    $("delBtn").onclick = deleteSelected;
    return;
  }

  if (S.sel.kind === "missing") {
    const m = S.anno.missing_rooms.find((x) => x.id === S.sel.id);
    if (!m) { S.sel = null; box.innerHTML = ""; return; }
    box.innerHTML = `
      <div class="ins-tier" style="background:var(--r-manual)">missing room</div>
      <div class="ins-title">${esc(m.label)}</div>
      <div class="ins-sub">a room the model never recovered, at
        (${m.x}, ${m.y}) on ${esc(storey().name)}</div>
      <div class="field"><label>label</label>
        <input id="mLbl" value="${esc(m.label)}"></div>
      <button class="danger" id="delBtn">Delete this pin</button>`;
    $("mLbl").onchange = (ev) => mutate("rename pin", () => {
      const t = S.anno.missing_rooms.find((x) => x.id === m.id);
      if (t) t.label = ev.target.value.trim() || "room";
    });
    $("delBtn").onclick = deleteSelected;
    return;
  }

  if (S.sel.kind === "vedge") {
    const v = allVertical().find((x) => x.key === S.sel.id);
    if (!v) { S.sel = null; box.innerHTML = ""; return; }
    const ra = roomAnywhere(v.a), rb = roomAnywhere(v.b);
    const a = S.anno.vertical[v.key] || {};
    box.innerHTML = `
      <div class="ins-tier" style="background:var(--r-vertical)">floor-to-floor</div>
      <div class="ins-title">${esc(ra?.room.label || "?")} ↕ ${esc(rb?.room.label || "?")}</div>
      <div class="ins-sub">${esc(ra?.storey.name || "?")} → ${esc(rb?.storey.name || "?")}
        ${v.kind ? "· " + esc(v.kind) : ""}${v.added ? " · added by you" : ""}</div>
      ${v.added
        ? `<button class="danger" id="delBtn">Delete this floor link</button>
           <div class="note">This is the link you drew. Deleting it is safe —
           <b>Ctrl+Z</b> brings it back.</div>`
        : verdictButtons(EDGE_VERDICTS, a.verdict,
            "Can you actually get between these two floors here? This is what " +
            "chains the storeys into one building graph, so a wrong answer " +
            "disconnects the whole model.")}`;
    if (v.added) $("delBtn").onclick = deleteSelected;
    else wireVerdicts(box, a.verdict);
    return;
  }

  // predicted intra-floor link
  const a = S.anno.edges[S.sel.id] || {};
  const ed = storey().edges.find((e) => edgeKey(e.a, e.b) === S.sel.id);
  const nm = (id) => storey().rooms.find((r) => r.id === id)?.label || "?";
  box.innerHTML = `
    <div class="ins-tier" style="background:${ed?.type === "open_passage"
      ? "var(--r-passage)" : "var(--r-door)"}">${esc((ed?.type || "link").replace(/_/g, " "))}</div>
    <div class="ins-title">${esc(nm(S.sel.a))} ↔ ${esc(nm(S.sel.b))}</div>
    <div class="ins-sub">predicted by the model</div>
    ${ed?.width ? `<div class="ins-row"><span>door width</span><span>${ed.width} m</span></div>` : ""}
    ${verdictButtons(EDGE_VERDICTS, a.verdict,
      "Is this passage real? Connectivity is what IFC omits entirely, so these " +
      "judgements are the most valuable ones here.")}`;
  wireVerdicts(box, a.verdict);
}

function wireVerdicts(box, current) {
  box.querySelectorAll(".vbtn").forEach((b) => {
    b.onclick = () => setVerdict(current === b.dataset.v ? null : b.dataset.v);
  });
}

/* ----------------------------------------------------------------- modes */

function setMode(m) {
  S.mode = m;
  if (m !== "edge") S.edgeFrom = null;
  if (m !== "vert") S.vertFrom = null;
  for (const [id, key] of [["modeSelect", "select"], ["modeEdge", "edge"],
                           ["modeVert", "vert"], ["modeRoom", "room"]])
    $(id).classList.toggle("active", m === key);
  svg.classList.toggle("picking", m !== "select");
  $("modeHint").textContent = {
    select: "Click a room or link, then press 1–5.",
    edge: "Click two rooms on this floor to link them.",
    vert: "Click a room, switch floor with [ or ], click the room it opens onto.",
    room: "Click where the model missed a room.",
  }[m];
  render();
}

/* --------------------------------------------------------- pan and zoom */

function applyView() {
  $("viewport").setAttribute("transform",
    `translate(${S.view.x},${S.view.y}) scale(${S.view.k},${-S.view.k})`);
}

function fit() {
  const st = storey();
  const pts = [];
  st.rooms.forEach((r) => pts.push(...r.polygon));
  st.walls.forEach((w) => pts.push(...w));
  const r = svg.getBoundingClientRect();
  if (!pts.length) {
    // An empty storey (they exist -- "Mumti" in model_0 has no rooms) used to
    // leave the previous floor's viewport, which looks like a failed load.
    S.view = { x: r.width / 2, y: r.height / 2, k: 30 };
    applyView();
    return;
  }
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const minx = Math.min(...xs), maxx = Math.max(...xs);
  const miny = Math.min(...ys), maxy = Math.max(...ys);
  const k = 0.9 * Math.min(r.width / (maxx - minx || 1), r.height / (maxy - miny || 1));
  S.view.k = k;
  S.view.x = r.width / 2 - k * (minx + maxx) / 2;
  S.view.y = r.height / 2 + k * (miny + maxy) / 2;
  applyView();
}

function svgPoint(ev) {
  const r = svg.getBoundingClientRect();
  return {
    x: (ev.clientX - r.left - S.view.x) / S.view.k,
    y: -(ev.clientY - r.top - S.view.y) / S.view.k,
  };
}

let drag = null;
svg.addEventListener("mousedown", (e) => {
  drag = { sx: e.clientX, sy: e.clientY, vx: S.view.x, vy: S.view.y, moved: false };
  svg.classList.add("panning");
});
window.addEventListener("mousemove", (e) => {
  if (!drag) return;
  const dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
  if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
  S.view.x = drag.vx + dx; S.view.y = drag.vy + dy;
  applyView();
});
window.addEventListener("mouseup", (e) => {
  if (drag && !drag.moved && S.mode === "room") {
    const p = svgPoint(e);
    askText("Label for the missing room", "room", (label) => {
      mutate("mark missing room", () => {
        S.anno.missing_rooms.push({
          id: uid(), storey: storey().gid,
          x: +p.x.toFixed(2), y: +p.y.toFixed(2), label,
        });
      });
    });
  } else if (drag && !drag.moved && S.mode === "select") {
    S.sel = null; render();
  }
  drag = null; svg.classList.remove("panning");
});
svg.addEventListener("wheel", (e) => {
  e.preventDefault();
  const r = svg.getBoundingClientRect();
  const mx = e.clientX - r.left, my = e.clientY - r.top;
  const f = e.deltaY < 0 ? 1.12 : 1 / 1.12;
  S.view.x = mx - (mx - S.view.x) * f;
  S.view.y = my - (my - S.view.y) * f;
  S.view.k *= f;
  applyView();
}, { passive: false });

/* --------------------------------------------------------------- dialogs */

function closeDlg() { $("mask").className = "mask"; }
$("mask").onclick = (e) => { if (e.target === $("mask")) closeDlg(); };

function askText(title, def, cb) {
  $("dlg").innerHTML = `
    <h3>${esc(title)}</h3>
    <div class="field"><input id="dlgIn" value="${esc(def)}"></div>
    <div class="row2">
      <button class="ghost" id="dlgNo">Cancel</button>
      <button class="primary" id="dlgYes" style="margin:0">OK</button>
    </div>`;
  $("mask").className = "mask on";
  const inp = $("dlgIn");
  inp.focus(); inp.select();
  const ok = () => { const v = inp.value.trim(); closeDlg(); if (v) cb(v); };
  $("dlgYes").onclick = ok;
  $("dlgNo").onclick = closeDlg;
  inp.onkeydown = (e) => {
    if (e.key === "Enter") ok();
    if (e.key === "Escape") closeDlg();
  };
}

function showGuide() {
  $("dlg").innerHTML = `
    <h3>How to annotate</h3>
    <p>The model has already guessed the answer. Your job is to judge that
    guess, not to draw the building from scratch.</p>

    <h4>1 · Work one floor at a time</h4>
    <p>Pick a storey on the left, or press <code>[</code> and <code>]</code>.
    A floor is done when every room and every link on it has a verdict — the
    storey then shows a ✓.</p>

    <h4>2 · Judge rooms and links</h4>
    <p>Click one, then press <code>1</code> correct, <code>2</code> spurious,
    <code>3</code> unsure, <code>4</code> should-merge, <code>5</code>
    should-split. <code>0</code> clears it. Use <b>unsure</b> freely — a
    guessed verdict is worse than an honest "unsure", because we can route
    those to a second annotator.</p>

    <h4>3 · Add what the model missed</h4>
    <p><code>A</code> then click two rooms to add a link it failed to find.
    <code>M</code> then click to pin a room it never recovered. Anything you
    add, you can select and delete with <code>Del</code>.</p>

    <h4>4 · Chain the floors together</h4>
    <p>This is what turns separate floor plans into one building graph. Press
    <code>V</code>, click the room containing the stair or lift, switch floor
    with <code>[</code> / <code>]</code>, then click the room it arrives in.
    Predicted floor links appear in the right-hand list — judge those too.</p>

    <h4>Checking against the real model</h4>
    <p>If you opened a dataset folder that contains the <code>.ifc</code> files,
    the <b>IFC model</b> tab shows the real building in 3D. Drag the cut slider
    to slice down to one floor plate — it is labelled with the storey name.
    Press <code>T</code> to flip between plan and model. Judgements are always
    made on the plan; the 3D view is for checking it.</p>

    <h4>Colour</h4>
    <p>Hue tells you <b>what</b> a thing is: blue rooms are stated by the IFC,
    violet rooms were recovered by the pipeline, orange dots are doors, and
    link colour gives the relation type. Your verdict is drawn as the
    <b>outline</b>, so it never hides what you are looking at.</p>

    <h4>Keys</h4>
    <p><code>Ctrl+Z</code> undo · <code>Ctrl+Shift+Z</code> redo ·
    <code>Ctrl+S</code> save · <code>F</code> fit · <code>Esc</code> judge mode ·
    <code>Del</code> delete what you added · <code>T</code> plan/3D ·
    <code>Ctrl+→</code> next building · <code>?</code> this guide</p>

    ${S.store && S.store.mode === "local" ? `
    <h4>Important: where your work lives</h4>
    <p>This version runs entirely in your browser. <b>There is no server</b>, so
    "saved" means saved in this browser on this computer and nowhere else — it is
    gone if you clear browsing data or switch machine. When you finish a session,
    press <b>Download all my work</b> in the bottom left and send that file back.
    The sidebar tells you what is not yet downloaded.</p>` : ""}

    <p class="muted">Your work saves automatically every 20 seconds, and the
    full guide is in <code>ANNOTATION_GUIDE.md</code>.</p>
    <div class="row2"><button class="primary" id="dlgYes" style="margin:0">Got it</button></div>`;
  $("mask").className = "mask on";
  $("dlgYes").onclick = closeDlg;
}

/* ------------------------------------------------------------ shortcuts */

window.addEventListener("keydown", (e) => {
  const typing = ["INPUT", "TEXTAREA"].includes(e.target.tagName);
  const k = e.key.toLowerCase();

  if ((e.ctrlKey || e.metaKey) && k === "s") { e.preventDefault(); save(); return; }
  if ((e.ctrlKey || e.metaKey) && k === "z") {
    e.preventDefault(); e.shiftKey ? redo() : undo(); return;
  }
  if ((e.ctrlKey || e.metaKey) && k === "y") { e.preventDefault(); redo(); return; }
  if ((e.ctrlKey || e.metaKey) && e.key === "ArrowRight") { e.preventDefault(); stepModel(1); return; }
  if ((e.ctrlKey || e.metaKey) && e.key === "ArrowLeft") { e.preventDefault(); stepModel(-1); return; }
  if (typing) return;
  if (k === "t") { cycleView(); return; }

  if (k === "escape") setMode("select");
  else if (k === "a") setMode("edge");
  else if (k === "v") setMode("vert");
  else if (k === "m") setMode("room");
  else if (k === "f") fit();
  else if (k === "?" || (e.shiftKey && k === "/")) showGuide();
  else if (k === "delete" || k === "backspace") { e.preventDefault(); deleteSelected(); }
  else if (k === "0") setVerdict(null);
  else if (["1", "2", "3", "4", "5"].includes(k)) {
    const list = S.sel?.kind === "room" ? VERDICTS : EDGE_VERDICTS;
    const v = list[+k - 1];
    if (v) setVerdict(v);
  }
  else if (k === "[") gotoStorey(S.storey - 1);
  else if (k === "]") gotoStorey(S.storey + 1);
});

/* --------------------------------------------------- local-build storage */

/** In the static build there is no server, so unexported work is at risk. */
function renderStorageWarning() {
  const box = $("storageWarn");
  if (!box || S.store.mode !== "local") return;
  const who = annotatorName();
  const pending = who ? S.store.unexported(who) : [];
  if (!pending.length) {
    box.innerHTML = "All your work has been downloaded. It is still in this " +
      "browser too, but the downloaded file is the copy that counts.";
    box.style.borderLeftColor = "var(--v-correct)";
  } else {
    box.innerHTML = `<b>${pending.length} model(s) not yet downloaded.</b> ` +
      `Your work lives only in this browser — clearing site data or switching ` +
      `machine loses it. Press <b>Download all my work</b> and send the file back.`;
    box.style.borderLeftColor = "var(--v-unsure)";
  }
}

function wireLocalTools() {
  if (S.store.mode !== "local") return;
  $("localTools").hidden = false;

  $("btnBundle").onclick = async () => {
    const who = annotatorName();
    if (!who) { banner("enter your name first", "warn"); return; }
    if (S.dirty && !(await save())) return;
    const b = await S.store.bundle(who);
    if (!b.annotations.length) { banner("nothing annotated yet", "warn"); return; }
    download(`bimsg-annotations__${who}.json`, JSON.stringify(b, null, 1));
    S.store.markExported(b.annotations.map((a) => a.model), who);
    renderStorageWarning();
    banner(`downloaded ${b.annotations.length} model(s)`);
  };

  $("btnImport").onclick = () => $("fileIn").click();
  $("fileIn").onchange = async (ev) => {
    const f = ev.target.files && ev.target.files[0];
    ev.target.value = "";
    if (!f) return;
    if (!confirmDiscard()) return;
    try {
      const doc = JSON.parse(await f.text());
      const r = await S.store.importDoc(doc);
      if (!r.loaded.length) { banner("nothing loadable in that file", "err"); return; }
      banner(`loaded ${r.loaded.length} annotation(s)`);
      // Adopt the name the file was saved under, else the work looks missing.
      const first = (doc.annotations && doc.annotations[0]) || doc;
      if (first && first.annotator) {
        $("annotator").value = first.annotator;
        localStorage.setItem("bimsg_annotator", first.annotator);
      }
      S.history.length = 0; S.future.length = 0;
      await loadModels();
      renderStorageWarning();
    } catch (e) {
      banner("that file could not be read as an annotation", "err");
      console.error(e);
    }
  };
}

/* ------------------------------------------------- dataset folder (mode C) */

/** Adopt a freshly opened DirStore and reload everything from it. */
async function adoptStore(store) {
  S.store = store;
  S.history.length = 0; S.future.length = 0;
  if (store.loadError) banner(store.loadError, "err");
  if (store.restoredAnnotator && !annotatorName()) {
    $("annotator").value = store.restoredAnnotator;
    localStorage.setItem("bimsg_annotator", store.restoredAnnotator);
  }
  await loadModels();
  renderFolderInfo();
  renderStorageWarning();
  if (store.restored)
    banner(`folder opened — ${store.restored} existing annotation(s) restored`);
}

function renderFolderInfo() {
  const box = $("folderInfo");
  if (!box) return;
  if (!S.store || S.store.mode !== "dir") {
    box.innerHTML = BIMSGDir.isSupported()
      ? "Point at the folder holding your IFC files and their plans. Your work " +
        "is written back into it after every save."
      : "<b>This browser cannot open folders.</b> Directory access needs Chrome, " +
        "Edge or another Chromium browser. You can still annotate here and use " +
        "<b>Download all my work</b>.";
    return;
  }
  const withIfc = S.models.filter((m) => m.hasIfc).length;
  box.innerHTML =
    `<b>${esc(S.store.dirName)}</b><br>${S.models.length} plans · ` +
    `${withIfc} with an IFC file<br>` +
    `<span style="color:var(--v-correct)">Saving to ${BIMSGDir.ANNO_FILE} ` +
    `in this folder.</span>`;
}

function wireFolder() {
  const sec = $("folderSec");
  if (!sec) return;
  // Offered in every build: even with a server available, working straight out
  // of a dataset folder is usually what an annotator actually wants.
  sec.hidden = false;
  renderFolderInfo();

  $("btnFolder").onclick = async () => {
    if (!BIMSGDir.isSupported()) {
      banner("this browser cannot open folders — use Chrome or Edge", "err");
      return;
    }
    if (!confirmDiscard()) return;
    try {
      await adoptStore(await BIMSGDir.DirStore.open());
    } catch (e) {
      if (e && e.name === "AbortError") return;      // the picker was dismissed
      banner(e.message || "could not open that folder", "err");
      console.error(e);
    }
  };
}

/** Offer to reconnect to the folder used last time. */
async function offerReconnect() {
  if (!BIMSGDir.isSupported()) return;
  let handle = null;
  try { handle = await BIMSGDir.recallDir(); } catch (e) { return; }
  if (!handle) return;
  const box = $("folderInfo");
  box.innerHTML = `Last used <b>${esc(handle.name)}</b>. ` +
    `<button id="btnReconnect" class="hbtn" style="margin-top:6px">Reopen it</button>`;
  // Browsers require a gesture before re-granting access, so this cannot be
  // done silently on load however convenient that would be.
  $("btnReconnect").onclick = async () => {
    try {
      const store = await BIMSGDir.DirStore.reopenRemembered(true);
      if (!store) { banner("permission was not granted", "warn"); return; }
      await adoptStore(store);
    } catch (e) { banner("could not reopen that folder", "err"); }
  };
}

/* --------------------------------------------------------- the 3D panel */

function can3d() {
  return S.store && typeof S.store.hasIfc === "function" && S.store.hasIfc(S.model);
}

/** "29 MB" for a published IFC, so the cost is visible before clicking. */
function ifcSizeLabel() {
  if (!S.store || typeof S.store.ifcBytes !== "function") return "";
  const b = S.store.ifcBytes(S.model);
  return b ? `${(b / 1e6).toFixed(0)} MB` : "";
}

/* Layout modes. Split is the point of the exercise -- judging a plan against
 * the model it came from means seeing both at once -- but either alone gets the
 * full window when you want to look closely.
 *
 * Note the name: `S.view` was already the 2D pan/zoom state, and reusing it
 * here silently clobbered the plan's viewport. Hence `S.layout`.
 */
const MODES = ["plan", "split", "model"];

function setView(mode) {
  if (mode !== "plan" && !can3d()) mode = "plan";
  S.layout = mode;
  const show3 = mode !== "plan";
  const show2 = mode !== "model";

  $("pane2d").hidden = !show2;
  $("view3d").hidden = !show3;
  $("splitter").hidden = mode !== "split";
  $("panes").style.setProperty("--split", mode === "split" ? S.splitPct + "%" : "100%");

  $("tab2d").classList.toggle("active", mode === "plan");
  $("tabSplit").classList.toggle("active", mode === "split");
  $("tab3d").classList.toggle("active", mode === "model");

  if (show3) load3d();
  // Both canvases must be re-measured once the panes have their new widths.
  requestAnimationFrame(() => {
    if (show2 && S.plan) fit();
    if (S.viewer) S.viewer.resize();
  });
}

function cycleView() {
  const i = MODES.indexOf(S.layout);
  let next = MODES[(i + 1) % MODES.length];
  if (next !== "plan" && !can3d()) next = "plan";
  setView(next);
}

function on3dModelChanged() {
  const has = can3d();
  $("tab3d").disabled = !has;
  $("tabSplit").disabled = !has;
  $("tabNote").textContent = has ? ifcSizeLabel() :
    (S.store && S.store.mode === "dir"
      ? "no IFC file for this model in the folder"
      : "no IFC published for this model");
  loaded3dFor = null;                    // a different building needs reloading
  setView(has ? S.layout : "plan");
}

let loaded3dFor = null;
let loading3d = null;

async function load3d() {
  if (loaded3dFor === S.model) return;
  if (loading3d) return loading3d;
  const note = $("load3d");
  const say = (t) => {
    if (t === null || t === undefined) { note.className = "loading3d"; return; }
    note.className = "loading3d on";
    note.textContent = t;
  };
  loading3d = (async () => {
    try {
      const sz = ifcSizeLabel();
      say(sz ? `downloading the IFC (${sz})…` : "loading 3D libraries…");
      if (!S.viewer) S.viewer = new BIMSGViewer.Viewer($("canvas3d"));
      const file = await S.store.getIfcFile(S.model);
      const info = await S.viewer.load(file, say);
      loaded3dFor = S.model;
      S.viewer.resize();
      setupSection();
      syncSectionToFloor();
      say(null);
      banner(`IFC loaded — ${info.storeys} storeys` +
             (info.skipped ? `, ${info.skipped} unreadable solids skipped` : ""));
    } catch (e) {
      console.error(e);
      say("Could not load this IFC.\n\n" + (e && e.message ? e.message : "") +
          "\n\nThe 3D view needs three.js and web-ifc from a CDN; if this " +
          "machine is offline it will not work. Annotation is unaffected.");
      loaded3dFor = null;
    } finally { loading3d = null; }
  })();
  return loading3d;
}

/* ---------------------------------------------------------- the section */

/* The slider runs over the model's own vertical extent rather than 0..100, so
 * its label is a real elevation and lines up with the storey names. */
function sectionRange() {
  const b = (S.viewer && S.viewer.bounds) || { min: 0, max: 30 };
  const lo = Number.isFinite(b.min) ? b.min : 0;
  const hi = Number.isFinite(b.max) ? b.max : lo + 30;
  return { lo, hi: hi > lo ? hi : lo + 30 };
}

function setupSection() {
  renderStoreyTicks();
  $("cutSlider").value = $("cutSlider").max;   // whole building to begin with
  applySection();
}

/** Storey names along the slider, so it reads as a building not a percentage. */
function renderStoreyTicks() {
  const box = $("cutTicks");
  if (!box) return;
  box.innerHTML = "";
  const st = (S.viewer && S.viewer.storeys) || [];
  const { lo, hi } = sectionRange();
  for (const s of st) {
    const f = (s.elevation - lo) / (hi - lo);
    if (!Number.isFinite(f) || f < 0 || f > 1) continue;
    const t = document.createElement("i");
    t.style.left = (f * 100).toFixed(2) + "%";
    t.title = `${s.name} · ${s.elevation.toFixed(2)} m`;
    box.appendChild(t);
  }
}

/** Read the controls and push a section to the viewer. */
function applySection() {
  if (!S.viewer || !S.viewer.meshes.length) return;
  const sl = $("cutSlider");
  const { lo, hi } = sectionRange();
  const f = +sl.value / +sl.max;
  const z = lo + (hi - lo) * f;
  const lab = $("cutVal");

  if (f >= 1 && !$("cutSlab").checked) {
    S.viewer.clearSection();
    lab.textContent = "whole building";
    return;
  }

  if ($("cutSlab").checked) {
    const i = S.viewer.storeyAt(z);
    const sp = i >= 0 ? S.viewer.storeySlab(i) : null;
    if (sp) {
      S.viewer.setSection(sp.bottom, sp.top);
      lab.textContent = `${S.viewer.storeys[i].name} · ${sp.bottom.toFixed(1)} m`;
      return;
    }
  }
  S.viewer.setSection(null, z);
  const i = S.viewer.storeyAt(z);
  lab.textContent = `up to ${z.toFixed(1)} m` +
    (i >= 0 ? ` · ${S.viewer.storeys[i].name}` : "");
}

/* Put the section on the storey being annotated. The plan's storeys and the
 * IFC's are different lists -- the pipeline repairs and sometimes re-bands
 * them -- so they are matched by elevation rather than by index, which would
 * silently show the wrong floor wherever the two disagree. */
function syncSectionToFloor() {
  if (!S.followFloor || !S.viewer || !S.viewer.meshes.length) return;
  if (!S.plan || !S.plan.storeys[S.storey]) return;
  const want = S.plan.storeys[S.storey].elevation;
  const st = S.viewer.storeys;
  if (!st.length) return;
  let best = 0, bestD = Infinity;
  st.forEach((s, i) => {
    const d = Math.abs(s.elevation - want);
    if (d < bestD) { bestD = d; best = i; }
  });
  const sp = S.viewer.storeySlab(best);
  if (!sp) return;
  if ($("cutSlab").checked) S.viewer.setSection(sp.bottom, sp.top);
  else S.viewer.setSection(null, sp.top);

  const { lo, hi } = sectionRange();
  const sl = $("cutSlider");
  sl.value = Math.round(((sp.bottom - lo) / (hi - lo)) * +sl.max);
  $("cutVal").textContent = `${st[best].name} · ${sp.bottom.toFixed(1)} m` +
    (bestD > 0.6 ? ` (nearest to ${want} m)` : "");
}

function wireViewer() {
  $("tab2d").onclick = () => setView("plan");
  $("tabSplit").onclick = () => setView("split");
  $("tab3d").onclick = () => setView("model");
  $("btnFit3d").onclick = () => S.viewer && S.viewer.fit();
  $("btnTop3d").onclick = () => S.viewer && S.viewer.topView();

  $("cutSlider").oninput = () => {
    // Dragging the slider is an explicit instruction; stop following.
    if (S.followFloor) { S.followFloor = false; $("cutFollow").checked = false; }
    applySection();
  };
  $("cutSlab").onchange = () => (S.followFloor ? syncSectionToFloor() : applySection());
  $("cutFollow").onchange = (e) => {
    S.followFloor = e.target.checked;
    S.followFloor ? syncSectionToFloor() : applySection();
  };

  // Drag the divider to rebalance the two panes.
  const sp = $("splitter");
  let dragging = false;
  sp.addEventListener("pointerdown", (e) => {
    dragging = true; sp.classList.add("dragging");
    try { sp.setPointerCapture(e.pointerId); } catch (err) { /* fine */ }
  });
  sp.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const r = $("panes").getBoundingClientRect();
    S.splitPct = Math.max(15, Math.min(85, ((e.clientX - r.left) / r.width) * 100));
    $("panes").style.setProperty("--split", S.splitPct + "%");
    if (S.plan) fit();
    if (S.viewer) S.viewer.resize();
  });
  const stop = (e) => {
    dragging = false; sp.classList.remove("dragging");
    try { sp.releasePointerCapture(e.pointerId); } catch (err) { /* fine */ }
    localStorage.setItem("bimsg_split", String(S.splitPct));
  };
  sp.addEventListener("pointerup", stop);
  sp.addEventListener("pointercancel", stop);

  const saved = +localStorage.getItem("bimsg_split");
  if (saved >= 15 && saved <= 85) S.splitPct = saved;
}

/* ----------------------------------------------------------------- init */

["lyWalls", "lyRooms", "lyEdges", "lyDoors", "lyLabels"]
  .forEach((id) => ($(id).onchange = render));
$("modeSelect").onclick = () => setMode("select");
$("modeEdge").onclick = () => setMode("edge");
$("modeVert").onclick = () => setMode("vert");
$("modeRoom").onclick = () => setMode("room");
$("btnSave").onclick = () => save();
$("btnUndo").onclick = undo;
$("btnRedo").onclick = redo;
$("btnGuide").onclick = showGuide;
$("btnPrev").onclick = () => stepModel(-1);
$("btnNext").onclick = () => stepModel(1);
wireViewer();

function download(name, text) {
  const blob = new Blob([text], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

$("btnRaw").onclick = () =>
  download(`${S.model}__${annotatorName() || "anon"}.json`,
           JSON.stringify(S.anno, null, 1));

$("btnExport").onclick = async () => {
  const who = annotatorName();
  if (!who) { banner("enter your name first", "warn"); return; }
  if (S.dirty && !(await save())) return;
  try {
    const g = await S.store.composeGraph(S.model, who);
    download(`${S.model}__${who}.graph.json`, JSON.stringify(g, null, 1));
    S.store.markExported([S.model], who);
    renderStorageWarning();
    // Say whether the storeys actually chained. A building that exports as
    // several disconnected pieces is the single most likely thing to be wrong,
    // and the annotator is the only person able to fix it.
    const rep = BIMSGCompose.connectivityReport(g);
    if (rep && rep.components > 1)
      banner(`exported — but ${rep.components} disconnected pieces; ` +
             `check the floor-to-floor links`, "warn");
    else banner("building graph downloaded");
  } catch (err) { banner(err.message || "export failed", "err"); console.error(err); }
};

const savedName = localStorage.getItem("bimsg_annotator");
if (savedName) $("annotator").value = savedName;
$("annotator").onchange = async () => {
  // Switching identity mid-edit used to bin the work silently.
  if (!confirmDiscard()) { $("annotator").value = localStorage.getItem("bimsg_annotator") || ""; return; }
  localStorage.setItem("bimsg_annotator", annotatorName());
  S.history.length = 0; S.future.length = 0;
  await loadAnnotation();
  render();
  renderStorageWarning();
};

window.addEventListener("beforeunload", (e) => {
  if (S.dirty) { e.preventDefault(); e.returnValue = ""; }
});
window.addEventListener("resize", () => {
  if (S.plan) fit();
  if (S.viewer) S.viewer.resize();
});

(async function init() {
  try {
    S.store = await BIMSGStore.makeStore();
  } catch (e) {
    document.body.innerHTML =
      '<div style="padding:40px;font:15px system-ui">' +
      '<h2>Could not start</h2><p>' + esc(e.message) + '</p></div>';
    return;
  }
  setMode("select");
  wireLocalTools();
  wireFolder();
  await loadModels();
  await offerReconnect();
  renderStorageWarning();
  if (!localStorage.getItem("bimsg_guide_seen")) {
    showGuide();
    localStorage.setItem("bimsg_guide_seen", "1");
  }
})();
