/* A 3D view of the source IFC, for checking the plan against the real model.
 *
 * The annotator judges a 2D plan that the pipeline extracted. If that
 * extraction is wrong -- a storey split at the wrong elevation, a mezzanine
 * flattened into the floor below -- the plan looks perfectly plausible and the
 * annotator has no way to tell. This panel reads the IFC itself, so it is an
 * independent look at the same building rather than a prettier view of the same
 * derived data.
 *
 * three.js and web-ifc are imported dynamically, on first use only. Only
 * self-contained modules are fetched: three's addons (OrbitControls among them)
 * are published with a bare `import ... from "three"` that a browser cannot
 * resolve without an import map, so `Orbit` below replaces that one.
 *
 * Versions are pinned, and web-ifc's WASM comes from the same pinned directory
 * -- the arrangement ifc-viewx uses. An unpinned URL would let a future release
 * change the ABI under us with no warning.
 */
(function (root) {
  "use strict";

  const THREE_URL = "https://cdn.jsdelivr.net/npm/three@0.184.0/build/three.module.js";
  const WEBIFC_URL = "https://cdn.jsdelivr.net/npm/web-ifc@0.0.77/web-ifc-api.js";
  const WASM_DIR = "https://cdn.jsdelivr.net/npm/web-ifc@0.0.77/";

  let THREE = null, WebIFC = null, ifcApi = null;

  async function loadLibs(onProgress) {
    if (THREE && ifcApi) return;
    onProgress && onProgress("loading 3D libraries…");
    if (!THREE) THREE = await import(THREE_URL);
    if (!ifcApi) {
      WebIFC = await import(WEBIFC_URL);
      onProgress && onProgress("starting IFC engine…");
      ifcApi = new WebIFC.IfcAPI();
      ifcApi.SetWasmPath(WASM_DIR, true);
      await ifcApi.Init();
    }
  }

  /* ------------------------------------------------------------ controls */

  /* Orbit / pan / dolly, in place of three's OrbitControls addon.
   *
   * Damped, so a drag does not feel notched, and the wheel zooms toward the
   * cursor rather than the screen centre -- without that you cannot get inside
   * a building without panning first.
   *
   * Left drag orbits, right/middle/shift drag pans, wheel dollies, double-click
   * recentres on what you clicked. One finger orbits, two pan and pinch.
   */
  class Orbit {
    constructor(camera, dom) {
      this.camera = camera;
      this.dom = dom;
      this.target = new THREE.Vector3();
      this.sph = new THREE.Spherical(20, Math.PI / 3, Math.PI / 4);
      this._goalT = this.target.clone();
      this._goalS = this.sph.clone();
      this.damping = 0.25;
      this.minDistance = 0.01;
      this.maxDistance = Infinity;
      this.onFocus = null;
      this._drag = null;
      this._touch = null;
      this._bind();
      this.apply(true);
    }

    /** Clamp the goal, and when `snap` jump straight to it. */
    apply(snap) {
      const EPS = 1e-4;
      this._goalS.phi = Math.max(EPS, Math.min(Math.PI - EPS, this._goalS.phi));
      this._goalS.radius = Math.max(this.minDistance,
                                    Math.min(this.maxDistance, this._goalS.radius));
      if (snap) {
        this.sph.copy(this._goalS);
        this.target.copy(this._goalT);
      }
      this._place();
    }

    _place() {
      this.camera.position.copy(this.target)
        .add(new THREE.Vector3().setFromSpherical(this.sph));
      this.camera.lookAt(this.target);
    }

    /** Every frame: ease the current state toward the goal. */
    update() {
      const k = this.damping;
      const ds = Math.abs(this.sph.radius - this._goalS.radius) +
                 Math.abs(this.sph.phi - this._goalS.phi) +
                 Math.abs(this.sph.theta - this._goalS.theta);
      const dt = this.target.distanceTo(this._goalT);
      if (ds < 1e-5 && dt < 1e-5) return;
      this.sph.radius += (this._goalS.radius - this.sph.radius) * k;
      this.sph.phi += (this._goalS.phi - this.sph.phi) * k;
      this.sph.theta += (this._goalS.theta - this.sph.theta) * k;
      this.target.lerp(this._goalT, k);
      this._place();
    }

    rotate(dx, dy) {
      const h = this.dom.clientHeight || 600;
      this._goalS.theta -= (2 * Math.PI * dx) / h;
      this._goalS.phi -= (2 * Math.PI * dy) / h;
      this.apply();
    }

    /** Pan in the camera's own plane, scaled so a drag tracks the cursor. */
    pan(dx, dy) {
      const h = this.dom.clientHeight || 600;
      const span = 2 * this.sph.radius *
        Math.tan((this.camera.fov / 2) * Math.PI / 180);
      const right = new THREE.Vector3().setFromMatrixColumn(this.camera.matrix, 0);
      const up = new THREE.Vector3().setFromMatrixColumn(this.camera.matrix, 1);
      this._goalT
        .addScaledVector(right, (-dx * span) / h)
        .addScaledVector(up, (dy * span) / h);
      this.apply();
    }

    dolly(factor) { this._goalS.radius *= factor; this.apply(); }

    /** Dolly toward the point under the cursor, the way a CAD viewer does. */
    dollyToCursor(factor, ev) {
      const r = this.dom.getBoundingClientRect();
      const ndc = new THREE.Vector3(
        ((ev.clientX - r.left) / r.width) * 2 - 1,
        -((ev.clientY - r.top) / r.height) * 2 + 1, 0.5);
      const ray = ndc.unproject(this.camera).sub(this.camera.position).normalize();
      const fwd = new THREE.Vector3();
      this.camera.getWorldDirection(fwd);
      const denom = ray.dot(fwd);
      if (Math.abs(denom) > 1e-6) {
        const dist = new THREE.Vector3()
          .subVectors(this.target, this.camera.position).dot(fwd) / denom;
        if (Number.isFinite(dist)) {
          const hit = this.camera.position.clone().addScaledVector(ray, dist);
          this._goalT.lerp(hit, 1 - factor);
        }
      }
      this.dolly(factor);
    }

    focus(point) { this._goalT.copy(point); this.apply(); }

    _bind() {
      const d = this.dom;
      d.style.touchAction = "none";
      d.addEventListener("contextmenu", (e) => e.preventDefault());

      d.addEventListener("pointerdown", (e) => {
        try { d.setPointerCapture(e.pointerId); } catch (err) { /* fine */ }
        this._drag = { x: e.clientX, y: e.clientY,
                       pan: e.button === 1 || e.button === 2 || e.shiftKey };
      });
      d.addEventListener("pointermove", (e) => {
        if (!this._drag) return;
        const dx = e.clientX - this._drag.x, dy = e.clientY - this._drag.y;
        this._drag.x = e.clientX; this._drag.y = e.clientY;
        this._drag.pan ? this.pan(dx, dy) : this.rotate(dx, dy);
      });
      const end = (e) => {
        this._drag = null;
        try { d.releasePointerCapture(e.pointerId); } catch (err) { /* fine */ }
      };
      d.addEventListener("pointerup", end);
      d.addEventListener("pointercancel", end);
      d.addEventListener("dblclick", (e) => { if (this.onFocus) this.onFocus(e); });

      d.addEventListener("wheel", (e) => {
        e.preventDefault();
        this.dollyToCursor(e.deltaY < 0 ? 1 / 1.15 : 1.15, e);
      }, { passive: false });

      d.addEventListener("touchmove", (e) => {
        if (e.touches.length !== 2) return;
        e.preventDefault();
        const [a, b] = e.touches;
        const dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
        const mx = (a.clientX + b.clientX) / 2, my = (a.clientY + b.clientY) / 2;
        if (this._touch) {
          this.dolly(this._touch.d / dist);
          this.pan(mx - this._touch.x, my - this._touch.y);
        }
        this._touch = { d: dist, x: mx, y: my };
      }, { passive: false });
      d.addEventListener("touchend", () => { this._touch = null; });
    }
  }

  /* -------------------------------------------------------------- viewer */

  class Viewer {
    constructor(container) {
      this.el = container;
      this.modelID = null;
      this.meshes = [];
      this.storeys = [];
      this.bounds = null;          // {min,max} in IFC z (metres)
      this._raf = null;
      this._planes = [];           // shared with every material, mutated in place
    }

    _initScene() {
      if (this.renderer) return;
      const w = this.el.clientWidth || 800, h = this.el.clientHeight || 600;
      this.scene = new THREE.Scene();
      this.scene.background = new THREE.Color(0xeef1f6);
      this.camera = new THREE.PerspectiveCamera(55, w / h, 0.1, 5000);

      this.renderer = new THREE.WebGLRenderer({ antialias: true });
      this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
      this.renderer.setSize(w, h);
      this.renderer.localClippingEnabled = true;
      this.el.appendChild(this.renderer.domElement);

      this.controls = new Orbit(this.camera, this.renderer.domElement);
      this.controls.onFocus = (e) => this._focusAt(e);

      this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8899aa, 2.2));
      const d1 = new THREE.DirectionalLight(0xffffff, 1.3);
      d1.position.set(24, 40, 18);
      this.scene.add(d1);
      const d2 = new THREE.DirectionalLight(0xffffff, 0.5);
      d2.position.set(-30, 20, -25);
      this.scene.add(d2);

      this.root = new THREE.Group();
      // Deliberately no rotation. IFC is Z-up, but web-ifc has already
      // converted: measured on model_0, its output spans Y -1.5..17.7 m against
      // storey elevations 0, 4.9, 9.8 and 11.8, while X and Z carry the
      // 116 x 83 m plan. Rotating again put the vertical axis into Z, which is
      // why the section slider sliced the building sideways.
      this.scene.add(this.root);

      this._ray = new THREE.Raycaster();
      const loop = () => {
        this._raf = requestAnimationFrame(loop);
        this.controls.update();
        this.renderer.render(this.scene, this.camera);
      };
      loop();
    }

    /** Double-click recentres the orbit on whatever was clicked. */
    _focusAt(ev) {
      const r = this.renderer.domElement.getBoundingClientRect();
      const ndc = new THREE.Vector2(
        ((ev.clientX - r.left) / r.width) * 2 - 1,
        -((ev.clientY - r.top) / r.height) * 2 + 1);
      this._ray.setFromCamera(ndc, this.camera);
      const hits = this._ray.intersectObjects(this.meshes, false);
      if (hits.length) this.controls.focus(hits[0].point);
    }

    async load(file, onProgress) {
      await loadLibs(onProgress);
      this._initScene();
      this.clear();

      onProgress && onProgress("reading file…");
      const buf = new Uint8Array(await file.arrayBuffer());
      onProgress && onProgress("parsing IFC…");
      this.modelID = ifcApi.OpenModel(buf, { COORDINATE_TO_ORIGIN: true });

      onProgress && onProgress("building geometry…");
      const byColour = new Map();
      let skipped = 0;
      ifcApi.StreamAllMeshes(this.modelID, (mesh) => {
        const placed = mesh.geometries;
        for (let i = 0; i < placed.size(); i++) {
          const pg = placed.get(i);
          const geo = ifcApi.GetGeometry(this.modelID, pg.geometryExpressID);
          const verts = ifcApi.GetVertexArray(
            geo.GetVertexData(), geo.GetVertexDataSize());
          const idx = ifcApi.GetIndexArray(
            geo.GetIndexData(), geo.GetIndexDataSize());

          // Real files contain geometry web-ifc cannot triangulate -- 26 of
          // 3114 placed geometries in one corpus model, 93 of 7665 in another,
          // which it reports as "No basis found for brep". They come back with
          // no vertices, and an empty BufferGeometry has an *infinite* bounding
          // box: unioned into the model box it sends the camera to infinity and
          // the view looks frozen rather than broken. This is that bug.
          if (verts.length === 0 || idx.length === 0) {
            skipped++;
            geo.delete();
            continue;
          }

          // web-ifc interleaves position and normal, six floats per vertex.
          const n = verts.length / 6;
          const pos = new Float32Array(n * 3);
          const nor = new Float32Array(n * 3);
          for (let v = 0; v < n; v++) {
            pos[v * 3] = verts[v * 6];
            pos[v * 3 + 1] = verts[v * 6 + 1];
            pos[v * 3 + 2] = verts[v * 6 + 2];
            nor[v * 3] = verts[v * 6 + 3];
            nor[v * 3 + 1] = verts[v * 6 + 4];
            nor[v * 3 + 2] = verts[v * 6 + 5];
          }
          const g = new THREE.BufferGeometry();
          g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
          g.setAttribute("normal", new THREE.BufferAttribute(nor, 3));
          g.setIndex(new THREE.BufferAttribute(new Uint32Array(idx), 1));
          g.applyMatrix4(new THREE.Matrix4().fromArray(pg.flatTransformation));

          const c = pg.color;
          const key = `${c.x.toFixed(2)},${c.y.toFixed(2)},${c.z.toFixed(2)},${c.w.toFixed(2)}`;
          if (!byColour.has(key)) byColour.set(key, { colour: c, geos: [] });
          byColour.get(key).geos.push(g);
          geo.delete();
        }
      });

      // One mesh per colour rather than per element: a 10 MB model holds tens
      // of thousands of products, and a draw call each makes it unusable.
      for (const { colour, geos } of byColour.values()) {
        const merged = mergeGeometries(geos);
        geos.forEach((g) => g.dispose());
        if (!merged) continue;
        const mat = new THREE.MeshLambertMaterial({
          color: new THREE.Color(colour.x, colour.y, colour.z),
          transparent: colour.w < 1, opacity: colour.w,
          side: THREE.DoubleSide,
          clippingPlanes: this._planes,
        });
        const m = new THREE.Mesh(merged, mat);
        m.frustumCulled = false;      // one mesh spans the whole building
        this.root.add(m);
        this.meshes.push(m);
      }

      this.storeys = this._readStoreys();
      this.fit();
      this.bounds = this._verticalBounds();
      onProgress && onProgress(null);
      return { meshes: this.meshes.length, storeys: this.storeys.length,
               skipped, bounds: this.bounds };
    }

    /** Storey names and elevations, to drive the section control. */
    _readStoreys() {
      const out = [];
      try {
        const ids = ifcApi.GetLineIDsWithType(this.modelID, WebIFC.IFCBUILDINGSTOREY);
        for (let i = 0; i < ids.size(); i++) {
          const s = ifcApi.GetLine(this.modelID, ids.get(i));
          const e = s.Elevation ? Number(s.Elevation.value) : 0;
          out.push({
            name: (s.Name && s.Name.value) ||
                  (s.LongName && s.LongName.value) || "storey",
            elevation: Number.isFinite(e) ? e : 0,
          });
        }
      } catch (e) { /* the model may simply have none */ }
      return out.sort((a, b) => a.elevation - b.elevation);
    }

    /** The model's own vertical extent in metres; world Y is the elevation. */
    _verticalBounds() {
      const box = this._worldBox();
      return box ? { min: box.min.y, max: box.max.y } : { min: 0, max: 30 };
    }

    /** Union of the mesh boxes, or null if nothing usable. */
    _worldBox() {
      if (!this.meshes.length) return null;
      this.scene.updateMatrixWorld(true);
      const box = new THREE.Box3();
      for (const m of this.meshes) {
        if (!m.geometry.boundingBox) m.geometry.computeBoundingBox();
        const b = m.geometry.boundingBox;
        if (!b || !isFiniteBox(b)) continue;   // never let one poison the union
        box.union(b.clone().applyMatrix4(m.matrixWorld));
      }
      return box.isEmpty() || !isFiniteBox(box) ? null : box;
    }

    /* ---- section ---------------------------------------------------- */

    /** Keep only what lies between two elevations; either may be null. web-ifc
     *  emits Y-up, so an elevation is a world Y and these stay horizontal. */
    setSection(bottom, top) {
      const planes = [];
      if (Number.isFinite(top))
        planes.push(new THREE.Plane(new THREE.Vector3(0, -1, 0), top));
      if (Number.isFinite(bottom))
        planes.push(new THREE.Plane(new THREE.Vector3(0, 1, 0), -bottom));
      // Mutated in place: every material holds a reference to this same array,
      // so replacing it would leave them clipping against the old one.
      this._planes.length = 0;
      this._planes.push(...planes);
    }

    clearSection() { this.setSection(null, null); }

    /** The slab a storey occupies: its elevation up to the next one. */
    storeySlab(i, headroom) {
      const s = this.storeys;
      if (!s.length) return null;
      const j = Math.max(0, Math.min(s.length - 1, i));
      const bottom = s[j].elevation;
      const top = j + 1 < s.length ? s[j + 1].elevation
                                   : bottom + (headroom || 3.2);
      // Slightly inside the slab at both ends, so the floor above is cut away
      // and the floor below does not bleed through.
      return { bottom: bottom + 0.02, top: top - 0.05 };
    }

    /** Index of the storey whose slab contains this elevation. */
    storeyAt(z) {
      let k = -1;
      this.storeys.forEach((s, i) => { if (s.elevation <= z + 1e-6) k = i; });
      return k;
    }

    /* ---- framing ---------------------------------------------------- */

    fit() {
      const box = this._worldBox();
      if (!box) return;
      const size = box.getSize(new THREE.Vector3());
      const c = box.getCenter(new THREE.Vector3());
      const r = Math.max(size.x, size.y, size.z);
      if (!Number.isFinite(r) || r <= 0) return;

      this.camera.near = Math.max(r / 5000, 0.01);
      this.camera.far = r * 60;
      this.camera.updateProjectionMatrix();

      const dist = (r * 0.72) / Math.tan((this.camera.fov / 2) * Math.PI / 180);
      this.controls.minDistance = Math.max(r / 5000, 0.01);
      this.controls.maxDistance = r * 25;
      this.controls._goalT.copy(c);
      this.controls._goalS.set(dist * 1.25, Math.PI / 3.2, Math.PI / 4);
      this.controls.apply(true);
    }

    /** Look straight down, to compare against the floor plan. */
    topView() {
      this.controls._goalS.phi = 0.02;
      this.controls._goalS.theta = 0;
      this.controls.apply();
    }

    resize() {
      if (!this.renderer) return;
      const w = this.el.clientWidth, h = this.el.clientHeight;
      if (!w || !h) return;
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
      this.renderer.setSize(w, h);
    }

    clear() {
      for (const m of this.meshes) {
        this.root.remove(m);
        m.geometry.dispose();
        m.material.dispose();
      }
      this.meshes = [];
      this.storeys = [];
      this.bounds = null;
      if (this.modelID !== null && ifcApi) {
        try { ifcApi.CloseModel(this.modelID); } catch (e) { /* already gone */ }
        this.modelID = null;
      }
    }

    dispose() {
      this.clear();
      if (this._raf) cancelAnimationFrame(this._raf);
      if (this.renderer) {
        this.renderer.dispose();
        const el = this.renderer.domElement;
        if (el.parentNode) el.parentNode.removeChild(el);
        this.renderer = null;
      }
    }
  }

  function isFiniteBox(b) {
    return Number.isFinite(b.min.x) && Number.isFinite(b.min.y) &&
           Number.isFinite(b.min.z) && Number.isFinite(b.max.x) &&
           Number.isFinite(b.max.y) && Number.isFinite(b.max.z);
  }

  /* three's BufferGeometryUtils is another import; merging by hand keeps this
   * file dependency-free and is all we need. Empty geometries are dropped here
   * too, belt and braces with the check at extraction. */
  function mergeGeometries(list) {
    const use = list.filter((g) => g.attributes.position &&
                                   g.attributes.position.count > 0 && g.index);
    if (!use.length) return null;
    let nv = 0, ni = 0;
    for (const g of use) { nv += g.attributes.position.count; ni += g.index.count; }
    const pos = new Float32Array(nv * 3);
    const nor = new Float32Array(nv * 3);
    const idx = new Uint32Array(ni);
    let vo = 0, io = 0;
    for (const g of use) {
      const p = g.attributes.position.array;
      const n = g.attributes.normal ? g.attributes.normal.array : null;
      pos.set(p, vo * 3);
      if (n) nor.set(n, vo * 3);
      const gi = g.index.array;
      for (let i = 0; i < gi.length; i++) idx[io + i] = gi[i] + vo;
      io += gi.length;
      vo += g.attributes.position.count;
    }
    const out = new THREE.BufferGeometry();
    out.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    out.setAttribute("normal", new THREE.BufferAttribute(nor, 3));
    out.setIndex(new THREE.BufferAttribute(idx, 1));
    out.computeBoundingBox();
    out.computeBoundingSphere();
    return out;
  }

  root.BIMSGViewer = { Viewer, Orbit, loadLibs, mergeGeometries };
})(typeof globalThis !== "undefined" ? globalThis : this);
