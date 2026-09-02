/* A 3D view of the source IFC, for checking the plan against the real model.
 *
 * The annotator judges a 2D plan that the pipeline extracted. If that
 * extraction is wrong -- a storey split at the wrong elevation, a mezzanine
 * flattened into the floor below -- the plan looks perfectly plausible and the
 * annotator has no way to tell. This panel reads the IFC itself, so it is an
 * independent look at the same building rather than a prettier view of the same
 * derived data.
 *
 * three.js and web-ifc are imported dynamically, on first use only. They are
 * several megabytes; an annotator who never opens this panel never pays for it,
 * and the 2D tool keeps working if the CDN is unreachable.
 *
 * Versions are pinned. web-ifc's WASM is fetched at runtime from the same
 * pinned directory, which is the arrangement ifc-viewx uses (it copies the
 * .wasm beside its bundle); an unpinned URL would let a future release change
 * the ABI under us with no warning.
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
    if (!THREE) {
      // Only the two self-contained modules are fetched. three's own addons --
      // OrbitControls among them -- are published with a bare `from "three"`
      // import, which a browser cannot resolve without an import map, and
      // loading one was what broke this view. `Orbit` below replaces it.
      THREE = await import(THREE_URL);
    }
    if (!ifcApi) {
      WebIFC = await import(WEBIFC_URL);
      onProgress && onProgress("starting IFC engine…");
      ifcApi = new WebIFC.IfcAPI();
      ifcApi.SetWasmPath(WASM_DIR, true);
      await ifcApi.Init();
    }
  }

  class Viewer {
    constructor(container) {
      this.el = container;
      this.modelID = null;
      this.meshes = [];
      this.storeys = [];
      this._raf = null;
    }

    _initScene() {
      if (this.renderer) return;
      const w = this.el.clientWidth || 800, h = this.el.clientHeight || 600;
      this.scene = new THREE.Scene();
      this.scene.background = new THREE.Color(0xeef1f6);

      this.camera = new THREE.PerspectiveCamera(55, w / h, 0.1, 2000);
      this.camera.position.set(20, 18, 20);

      this.renderer = new THREE.WebGLRenderer({ antialias: true });
      this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
      this.renderer.setSize(w, h);
      this.el.appendChild(this.renderer.domElement);

      this.controls = new Orbit(this.camera, this.renderer.domElement);

      this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8899aa, 2.2));
      const d = new THREE.DirectionalLight(0xffffff, 1.4);
      d.position.set(24, 40, 18);
      this.scene.add(d);

      this.root = new THREE.Group();
      // IFC is Z-up, three.js is Y-up.
      this.root.rotation.x = -Math.PI / 2;
      this.scene.add(this.root);

      const loop = () => {
        this._raf = requestAnimationFrame(loop);
        this.controls.update();
        this.renderer.render(this.scene, this.camera);
      };
      loop();
    }

    /** Load an IFC from a File/Blob and build the meshes. */
    async load(file, onProgress) {
      await loadLibs(onProgress);
      this._initScene();
      this.clear();

      onProgress && onProgress("reading file…");
      const buf = new Uint8Array(await file.arrayBuffer());
      onProgress && onProgress("parsing IFC…");
      this.modelID = ifcApi.OpenModel(buf, {
        COORDINATE_TO_ORIGIN: true,
      });

      onProgress && onProgress("building geometry…");
      const byColour = new Map();
      ifcApi.StreamAllMeshes(this.modelID, (mesh) => {
        const placed = mesh.geometries;
        for (let i = 0; i < placed.size(); i++) {
          const pg = placed.get(i);
          const geo = ifcApi.GetGeometry(this.modelID, pg.geometryExpressID);
          const verts = ifcApi.GetVertexArray(
            geo.GetVertexData(), geo.GetVertexDataSize());
          const idx = ifcApi.GetIndexArray(
            geo.GetIndexData(), geo.GetIndexDataSize());

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

      // One mesh per colour rather than per element: a 10 MB model can hold tens
      // of thousands of products, and a draw call each makes it unusable.
      for (const { colour, geos } of byColour.values()) {
        const merged = mergeGeometries(geos);
        if (!merged) continue;
        const mat = new THREE.MeshLambertMaterial({
          color: new THREE.Color(colour.x, colour.y, colour.z),
          transparent: colour.w < 1, opacity: colour.w,
          side: THREE.DoubleSide,
        });
        const m = new THREE.Mesh(merged, mat);
        this.root.add(m);
        this.meshes.push(m);
        geos.forEach((g) => g.dispose());
      }

      this.storeys = this._readStoreys();
      this.fit();
      onProgress && onProgress(null);
      return { meshes: this.meshes.length, storeys: this.storeys.length };
    }

    /** Storey names and elevations, to drive the clipping control. */
    _readStoreys() {
      const out = [];
      try {
        const ids = ifcApi.GetLineIDsWithType(this.modelID, WebIFC.IFCBUILDINGSTOREY);
        for (let i = 0; i < ids.size(); i++) {
          const s = ifcApi.GetLine(this.modelID, ids.get(i));
          out.push({
            name: (s.Name && s.Name.value) || (s.LongName && s.LongName.value) || "storey",
            elevation: s.Elevation ? Number(s.Elevation.value) : 0,
          });
        }
      } catch (e) { /* the model may simply have none */ }
      return out.sort((a, b) => a.elevation - b.elevation);
    }

    /** Show only what lies below `z` (metres), so a floor plate can be read. */
    setCut(z) {
      if (!this.renderer) return;
      if (z === null || z === undefined) {
        this.renderer.clippingPlanes = [];
        return;
      }
      this.renderer.localClippingEnabled = true;
      // The group is rotated into Y-up, so the world plane is horizontal in Y.
      this.renderer.clippingPlanes = [new THREE.Plane(new THREE.Vector3(0, -1, 0), z)];
    }

    fit() {
      if (!this.meshes.length) return;
      // The meshes hang off a group rotated from IFC's Z-up into three's Y-up.
      // Box3 reads world matrices, and nothing has rendered yet when fit() runs
      // straight after load(), so those matrices are still identity and the box
      // comes out in the wrong axes -- the model then frames off-screen.
      this.scene.updateMatrixWorld(true);
      const box = new THREE.Box3();
      this.meshes.forEach((m) => box.expandByObject(m));
      if (box.isEmpty()) return;
      const size = box.getSize(new THREE.Vector3());
      const c = box.getCenter(new THREE.Vector3());
      const r = Math.max(size.x, size.y, size.z) || 10;
      this.controls.target.copy(c);
      this.camera.near = Math.max(r / 5000, 0.01);
      this.camera.far = r * 40;
      this.camera.updateProjectionMatrix();
      // Frame the box from a corner, far enough out that it fits the vertical
      // field of view with a little margin.
      this.controls.sph.set(
        r * 1.6 / Math.tan((this.camera.fov / 2) * Math.PI / 180) * 0.5,
        Math.PI / 3.2, Math.PI / 4);
      this.controls.minDistance = Math.max(r / 1000, 0.01);
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
        if (this.renderer.domElement.parentNode)
          this.renderer.domElement.parentNode.removeChild(this.renderer.domElement);
        this.renderer = null;
      }
    }
  }

  /* Orbit / pan / dolly, in place of three's OrbitControls addon.
   *
   * The addon is published with a bare `import ... from "three"`, which a
   * browser refuses to resolve without an import map -- that is what broke this
   * view. An import map would fix it, but writing the ~60 lines here removes
   * the dependency and its failure mode altogether, and mirrors the pan/zoom
   * already used by the 2D plan.
   *
   * Left drag orbits, right drag or shift-drag pans, wheel dollies. One finger
   * orbits and two pinch, so a tablet works.
   */
  class Orbit {
    constructor(camera, dom) {
      this.camera = camera;
      this.dom = dom;
      this.target = new THREE.Vector3();
      this.sph = new THREE.Spherical(20, Math.PI / 3, Math.PI / 4);
      this.minDistance = 0.01;
      this.maxDistance = Infinity;
      this._drag = null;
      this._pinch = 0;
      this._bind();
      this.apply();
    }

    /** Re-derive the orbit from wherever the camera currently is. */
    syncFromCamera() {
      this.sph.setFromVector3(
        new THREE.Vector3().subVectors(this.camera.position, this.target));
    }

    apply() {
      const EPS = 1e-4;
      this.sph.phi = Math.max(EPS, Math.min(Math.PI - EPS, this.sph.phi));
      this.sph.radius = Math.max(this.minDistance,
                                 Math.min(this.maxDistance, this.sph.radius));
      this.camera.position.copy(this.target)
        .add(new THREE.Vector3().setFromSpherical(this.sph));
      this.camera.lookAt(this.target);
    }

    rotate(dx, dy) {
      const h = this.dom.clientHeight || 600;
      this.sph.theta -= (2 * Math.PI * dx) / h;
      this.sph.phi -= (2 * Math.PI * dy) / h;
      this.apply();
    }

    /** Pan in the camera's own plane, scaled so a drag tracks the cursor. */
    pan(dx, dy) {
      const h = this.dom.clientHeight || 600;
      const span = 2 * this.sph.radius *
        Math.tan((this.camera.fov / 2) * Math.PI / 180);
      const right = new THREE.Vector3().setFromMatrixColumn(this.camera.matrix, 0);
      const up = new THREE.Vector3().setFromMatrixColumn(this.camera.matrix, 1);
      this.target
        .addScaledVector(right, (-dx * span) / h)
        .addScaledVector(up, (dy * span) / h);
      this.apply();
    }

    dolly(factor) { this.sph.radius *= factor; this.apply(); }

    _bind() {
      const d = this.dom;
      d.style.touchAction = "none";
      d.addEventListener("contextmenu", (e) => e.preventDefault());

      d.addEventListener("pointerdown", (e) => {
        d.setPointerCapture(e.pointerId);
        this._drag = {
          x: e.clientX, y: e.clientY,
          pan: e.button === 2 || e.button === 1 || e.shiftKey,
        };
      });
      d.addEventListener("pointermove", (e) => {
        if (!this._drag) return;
        const dx = e.clientX - this._drag.x, dy = e.clientY - this._drag.y;
        this._drag.x = e.clientX; this._drag.y = e.clientY;
        this._drag.pan ? this.pan(dx, dy) : this.rotate(dx, dy);
      });
      const end = (e) => {
        this._drag = null;
        try { d.releasePointerCapture(e.pointerId); } catch (err) { /* gone */ }
      };
      d.addEventListener("pointerup", end);
      d.addEventListener("pointercancel", end);

      d.addEventListener("wheel", (e) => {
        e.preventDefault();
        this.dolly(e.deltaY < 0 ? 1 / 1.12 : 1.12);
      }, { passive: false });

      d.addEventListener("touchmove", (e) => {
        if (e.touches.length !== 2) return;
        e.preventDefault();
        const [a, b] = e.touches;
        const dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
        if (this._pinch) this.dolly(this._pinch / dist);
        this._pinch = dist;
      }, { passive: false });
      d.addEventListener("touchend", () => { this._pinch = 0; });
    }

    // The render loop calls this; there is no inertia to integrate.
    update() {}
  }

  /* three's BufferGeometryUtils is another import; merging positions and
   * indices by hand keeps this file dependency-free and is all we need. */
  function mergeGeometries(list) {
    if (!list.length) return null;
    let nv = 0, ni = 0;
    for (const g of list) {
      nv += g.attributes.position.count;
      ni += g.index ? g.index.count : 0;
    }
    const pos = new Float32Array(nv * 3);
    const nor = new Float32Array(nv * 3);
    const idx = new Uint32Array(ni);
    let vo = 0, io = 0;
    for (const g of list) {
      const p = g.attributes.position.array;
      const n = g.attributes.normal ? g.attributes.normal.array : null;
      pos.set(p, vo * 3);
      if (n) nor.set(n, vo * 3);
      if (g.index) {
        const gi = g.index.array;
        for (let i = 0; i < gi.length; i++) idx[io + i] = gi[i] + vo;
        io += gi.length;
      }
      vo += g.attributes.position.count;
    }
    const out = new THREE.BufferGeometry();
    out.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    out.setAttribute("normal", new THREE.BufferAttribute(nor, 3));
    out.setIndex(new THREE.BufferAttribute(idx, 1));
    return out;
  }

  root.BIMSGViewer = { Viewer, loadLibs };
})(typeof globalThis !== "undefined" ? globalThis : this);
