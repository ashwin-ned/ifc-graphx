/* Where annotations live.
 *
 * The tool runs in two places and they differ in exactly one respect: whether
 * there is a server to save to.
 *
 *   ServerStore  — `python annotator/app.py`. Work is POSTed to the machine
 *                  running the server, so a coordinator collects it centrally
 *                  and two annotators on the same building are visible to each
 *                  other's progress display.
 *
 *   LocalStore   — the GitHub Pages build. There is no server and there is
 *                  nowhere to POST. Work is held in this browser's
 *                  localStorage and the annotator sends it back as a file.
 *
 * Everything above this layer is identical, so the two builds cannot drift.
 *
 * The honest caveat, surfaced in the UI rather than buried here: localStorage
 * belongs to one browser on one machine. Clearing site data, a private window,
 * or a different laptop all mean the work is gone. LocalStore therefore tracks
 * when each model was last exported and the app nags about anything unexported.
 */
(function (root) {
  "use strict";

  const NS = "bimsg";
  const kAnno = (m, w) => `${NS}:anno:${m}:${w}`;
  const kMeta = (m, w) => `${NS}:meta:${m}:${w}`;

  /* ------------------------------------------------------------ server */

  class ServerStore {
    constructor() { this.mode = "server"; this.canCollect = true; }

    async listModels() { return (await fetch("api/models")).json(); }
    async getPlan(name) { return (await fetch(`api/plan/${name}`)).json(); }

    async getAnnotation(model, who) {
      return (await fetch(`api/annotation/${model}/${who}`)).json();
    }

    async saveAnnotation(model, who, anno) {
      const r = await fetch(`api/annotation/${model}/${who}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(anno),
      });
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    }

    async composeGraph(model, who) {
      const r = await fetch(`api/export/${model}/${who}`);
      if (!r.ok) throw new Error(await r.text());
      return r.json();
    }

    // Central collection is the server's job; nothing to hand back by file.
    unexported() { return []; }
    markExported() {}
  }

  /* ------------------------------------------------------------- local */

  class LocalStore {
    constructor(manifest) {
      this.mode = "local";
      this.canCollect = false;
      this.manifest = manifest;
      this._plans = new Map();
    }

    async listModels() {
      // Which annotators exist is whatever this browser happens to hold.
      return this.manifest.models.map((m) => {
        const done = {};
        for (const { model, who, count } of this._scan())
          if (model === m.model) done[who] = count;
        return { ...m, annotated: done };
      });
    }

    _scan() {
      const out = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (!k || !k.startsWith(`${NS}:anno:`)) continue;
        const rest = k.slice(`${NS}:anno:`.length);
        const ix = rest.lastIndexOf(":");
        if (ix < 0) continue;
        const model = rest.slice(0, ix), who = rest.slice(ix + 1);
        let count = 0;
        try {
          const d = JSON.parse(localStorage.getItem(k));
          count = Object.keys(d.rooms || {}).length +
                  Object.keys(d.edges || {}).length +
                  Object.keys(d.vertical || {}).length;
        } catch (e) { /* a corrupt entry must not hide the rest */ }
        out.push({ model, who, count });
      }
      return out;
    }

    async getPlan(name) {
      if (this._plans.has(name)) return this._plans.get(name);
      const r = await fetch(`data/${name}.plan.json`);
      if (!r.ok) throw new Error(`cannot load plan ${name}`);
      const p = await r.json();
      this._plans.set(name, p);
      return p;
    }

    _entry(name) {
      return this.manifest.models.find((m) => m.model === name) || {};
    }

    /** Whether this deployment published the IFC beside the plan. */
    hasIfc(name) { return !!this._entry(name).hasIfc; }

    /** Bytes, so the page can warn before pulling tens of megabytes. */
    ifcBytes(name) { return this._entry(name).ifcBytes || 0; }

    /* Fetched fresh each time rather than cached: these are up to 30 MB and
     * holding several in memory is a worse failure than re-downloading one. */
    async getIfcFile(name) {
      if (!this.hasIfc(name))
        throw new Error(`no IFC published for ${name}`);
      const r = await fetch(`data/${name}.ifc`);
      if (!r.ok) throw new Error(`could not download ${name}.ifc (${r.status})`);
      return r.blob();
    }

    async getAnnotation(model, who) {
      const raw = localStorage.getItem(kAnno(model, who));
      if (!raw) {
        return { model, annotator: who, rooms: {}, edges: {}, vertical: {},
                 added_edges: [], added_vertical: [], missing_rooms: [] };
      }
      try { return JSON.parse(raw); }
      catch (e) {
        throw new Error("stored annotation is corrupt; export a fresh copy " +
                        "before continuing");
      }
    }

    async saveAnnotation(model, who, anno) {
      const body = { ...anno, model, annotator: who,
                     updated: new Date().toISOString().replace(/\.\d+Z$/, "Z") };
      try {
        localStorage.setItem(kAnno(model, who), JSON.stringify(body));
      } catch (e) {
        // Quota is the one failure that loses work silently, so it is raised
        // loudly rather than swallowed.
        throw new Error("this browser's storage is full. Download your work " +
                        "now (Download all my work), then clear old models.");
      }
      const meta = this._meta(model, who);
      meta.updated = body.updated;
      localStorage.setItem(kMeta(model, who), JSON.stringify(meta));
      return { ok: true, updated: body.updated };
    }

    _meta(model, who) {
      try { return JSON.parse(localStorage.getItem(kMeta(model, who))) || {}; }
      catch (e) { return {}; }
    }

    async composeGraph(model, who) {
      const plan = await this.getPlan(model);
      const anno = await this.getAnnotation(model, who);
      return root.BIMSGCompose.compose(plan, anno);
    }

    /** Models edited since they were last exported. */
    unexported(who) {
      const out = [];
      for (const row of this._scan()) {
        if (who && row.who !== who) continue;
        const m = this._meta(row.model, row.who);
        if (!m.exported || (m.updated && m.updated > m.exported))
          out.push(row.model);
      }
      return [...new Set(out)];
    }

    markExported(models, who) {
      const stamp = new Date().toISOString().replace(/\.\d+Z$/, "Z");
      for (const m of models) {
        const meta = this._meta(m, who);
        meta.exported = stamp;
        localStorage.setItem(kMeta(m, who), JSON.stringify(meta));
      }
    }

    /** Everything this person has done, as one file to send back. */
    async bundle(who) {
      const items = [];
      for (const row of this._scan()) {
        if (who && row.who !== who) continue;
        items.push(await this.getAnnotation(row.model, row.who));
      }
      return {
        format: "bimsg-annotation-bundle", version: 1,
        annotator: who || null,
        exported: new Date().toISOString().replace(/\.\d+Z$/, "Z"),
        annotations: items,
      };
    }

    /** Load a bundle or a single annotation file back in. Returns a summary. */
    async importDoc(doc) {
      const list = doc && doc.format === "bimsg-annotation-bundle"
        ? (doc.annotations || []) : [doc];
      const loaded = [], skipped = [];
      for (const a of list) {
        if (!a || !a.model || !a.annotator) { skipped.push(a && a.model); continue; }
        await this.saveAnnotation(a.model, a.annotator, a);
        loaded.push(`${a.model} (${a.annotator})`);
      }
      return { loaded, skipped };
    }
  }

  /* ----------------------------------------------------------- factory */

  async function makeStore() {
    const mode = (root.BIMSG_CONFIG && root.BIMSG_CONFIG.mode) || "server";
    if (mode === "server") return new ServerStore();
    const r = await fetch("manifest.json");
    if (!r.ok) throw new Error("manifest.json missing — the static build is incomplete");
    return new LocalStore(await r.json());
  }

  root.BIMSGStore = { makeStore, ServerStore, LocalStore };
})(typeof globalThis !== "undefined" ? globalThis : this);
