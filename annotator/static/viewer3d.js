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
  const ORBIT_URL = "https://cdn.jsdelivr.net/npm/three@0.184.0/examples/jsm/controls/OrbitControls.js";
  const WEBIFC_URL = "https://cdn.jsdelivr.net/npm/web-ifc@0.0.77/web-ifc-api.js";
  const WASM_DIR = "https://cdn.jsdelivr.net/npm/web-ifc@0.0.77/";

  let THREE = null, OrbitControls = null, WebIFC = null, ifcApi = null;

  async function loadLibs(onProgress) {
    if (THREE && ifcApi) return;
    onProgress && onProgress("loading 3D libraries…");
    if (!THREE) {
      THREE = await import(THREE_URL);
      ({ OrbitControls } = await import(ORBIT_URL));
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

      this.controls = new OrbitControls(this.camera, this.renderer.domElement);
      this.controls.enableDamping = true;

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
      const box = new THREE.Box3();
      this.meshes.forEach((m) => box.expandByObject(m));
      if (box.isEmpty()) return;
      const size = box.getSize(new THREE.Vector3());
      const c = box.getCenter(new THREE.Vector3());
      const r = Math.max(size.x, size.y, size.z) || 10;
      this.controls.target.copy(c);
      this.camera.position.set(c.x + r, c.y + r * 0.8, c.z + r);
      this.camera.near = r / 500; this.camera.far = r * 20;
      this.camera.updateProjectionMatrix();
      this.controls.update();
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
