// Maxie: Mochi, the look I picked (2026-10-15), without the headphones. Same API as the
// Classic robot (web/mascot.js), so the PWA, the Mac panel (web/bot.html), the notch loops
// (scripts/render_mascot.py) and the icons (scripts/make_icons.py) can use either.
//
//   const m = MaxieCharacter.create(canvas, {framing: "body", maxFps: 30});
//   m.setState("listen");  m.setLook("mochi");  m.setCustom({ears: "round"});  m.stop();  m.start();
//
// Options: framing "stage" | "body" | "portrait"; interactive (eyes follow the pointer, tap = celebrate);
// maxFps; loop (seconds; deterministic, every motion snapped to whole cycles, for seamless loops, driven with
// advance(dt) + render()); eyes:false (the Mac draws live eyes on top); shells (fur quality); custom (Settings ›
// Maxie: {fur, furLength, ears, eyes, accessory}; the default is that pick).
/* global THREE */
(function (root) {
  "use strict";

  // Fur colours (Settings › Maxie). "mochi" is the picked look, value for value.
  const LOOKS = {
    mochi:    { name: "Mochi periwinkle", root: "#6a7bc0", tip: "#dde3ff", body: "#d8dfff", earRoot: "#5a69a8", earTip: "#cfd7ff", muzzle: "#eef1ff", cheek: "#f6b8d0", inner: "#f3c2d6", ink: "#2a2440" },
    sunset:   { name: "Sunset",           root: "#e39a6c", tip: "#ffe3c9", body: "#ffdcc0", earRoot: "#cf845a", earTip: "#ffd4b0", muzzle: "#fff3e6", cheek: "#ff9f8a", inner: "#ffb8a6", ink: "#3b2620" },
    ocean:    { name: "Ocean blue",       root: "#3f6fb0", tip: "#bfe0ff", body: "#b6d9ff", earRoot: "#355f99", earTip: "#a9d1ff", muzzle: "#eaf5ff", cheek: "#f6b8d0", inner: "#f3c2d6", ink: "#1d2a40" },
    cream:    { name: "Cream",            root: "#d9c3a0", tip: "#fffaf0", body: "#fff6e8", earRoot: "#c9ac84", earTip: "#f7ebd6", muzzle: "#fffdf8", cheek: "#ffb3a1", inner: "#ffc9bb", ink: "#3b2a20" },
    charcoal: { name: "Charcoal",         root: "#34343f", tip: "#8a8a9e", body: "#80809a", earRoot: "#2a2a33", earTip: "#9a9ab0", muzzle: "#f6efe6", cheek: "#e9b9b0", inner: "#e9b9b0", ink: "#141418" },
    honey:    { name: "Honey",            root: "#c98a3a", tip: "#ffe4a8", body: "#ffdf9e", earRoot: "#b5772e", earTip: "#f7d28a", muzzle: "#fff6e0", cheek: "#ffab8f", inner: "#ffc2a8", ink: "#3a2614" },
    mint:     { name: "Mint",             root: "#4fa58e", tip: "#d4fbef", body: "#c9f5e7", earRoot: "#418f7a", earTip: "#bdf0df", muzzle: "#f1fffa", cheek: "#f6b8c8", inner: "#f3c2d0", ink: "#1d3a32" },
    lilac:    { name: "Lilac",            root: "#9a78c4", tip: "#efe3ff", body: "#e8dbff", earRoot: "#8566ad", earTip: "#e1cffd", muzzle: "#faf5ff", cheek: "#f6b8d8", inner: "#f3c2dc", ink: "#2e2440" },
    rose:     { name: "Rose",             root: "#d27a95", tip: "#ffe0ea", body: "#ffd8e4", earRoot: "#bd6782", earTip: "#ffcfdd", muzzle: "#fff4f7", cheek: "#ff8fae", inner: "#ffb0c4", ink: "#3d2028" },
    snow:     { name: "Snow",             root: "#b9c2d6", tip: "#ffffff", body: "#fbfcff", earRoot: "#a7b1c7", earTip: "#f2f5fb", muzzle: "#ffffff", cheek: "#f6c2d0", inner: "#f3ccd6", ink: "#2a2a35" },
    cocoa:    { name: "Cocoa",            root: "#6b4a36", tip: "#c9a58a", body: "#c09a7e", earRoot: "#5c3f2e", earTip: "#b8937a", muzzle: "#f3e4d6", cheek: "#e9a99a", inner: "#e9b4a6", ink: "#1f140e" },
    sky:      { name: "Sky",              root: "#6aa3d8", tip: "#e2f2ff", body: "#d9eeff", earRoot: "#5a91c6", earTip: "#d0e8ff", muzzle: "#f5fbff", cheek: "#f6b8d0", inner: "#f3c2d6", ink: "#1e2c3d" },
    midnight: { name: "Midnight",         root: "#262b45", tip: "#6f78a8", body: "#6a73a0", earRoot: "#1f2339", earTip: "#7c86b8", muzzle: "#d9def5", cheek: "#d98fb0", inner: "#d99ab8", ink: "#0e1020" },
    peach:    { name: "Peach",            root: "#e8957a", tip: "#ffe6da", body: "#ffdfd0", earRoot: "#d68268", earTip: "#ffd6c6", muzzle: "#fff6f1", cheek: "#ff9b8f", inner: "#ffb6aa", ink: "#3d2420" },
  };
  const FUR_LENGTHS = { short: 0.55, medium: 0.78, long: 1, extra: 1.25 };
  const EARS = ["round", "floppy", "pointy"];
  const EYES = ["glossy", "sparkle", "sleepy", "button"];
  const ACCESSORIES = ["none", "headphones", "cap", "beanie", "scarf", "glasses", "bow"];
  const DEFAULT_CUSTOM = { fur: "mochi", furLength: "long", ears: "round", eyes: "glossy", accessory: "none" };

  // Hub states (/api/events) plus the character's own. "closed" = eyes closed, not watching.
  const STATES = {
    idle:    { label: "Idle" },
    listen:  { label: "Listening" },
    think:   { label: "Thinking" },
    talk:    { label: "Talking" },
    happy:   { label: "Happy" },
    proud:   { label: "Proud of you" },
    sleep:   { label: "Sleeping" },
    gentle:  { label: "With you" },
    wave:    { label: "Waving" },
    closed:  { label: "Not watching" },
    stretch: { label: "Stretch break" },
  };
  STATES.love = STATES.proud;
  const OPEN_EYES = new Set(["idle", "listen", "think", "talk", "gentle", "wave", "proud"]);
  const NECK = 0.35;  // the head turns about this height

  function mulberry32(seed) {
    return function () {
      seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  /* ---------- building Mochi ---------- */

  function headFur(L, len) {
    return { len: 0.4 * len, droop: 1.25, lean: 0.04, density: 52, thickness: 0.46, wave: 0.9, root: L.root, tip: L.tip, rim: "#ffffff", clearR: 0.43,
      clear: [[-0.36, -0.05, 0.93], [0.36, -0.05, 0.93], [0, -0.38, 0.92]],
      tints: [{ at: [0, -0.42, 0.9], r: 0.4, color: L.muzzle, amount: 0.75 },
        { at: [-0.62, -0.3, 0.7], r: 0.24, color: L.cheek, amount: 0.45 }, { at: [0.62, -0.3, 0.7], r: 0.24, color: L.cheek, amount: 0.45 }] };
  }
  function bodyFur(L, len) { return { len: 0.4 * len, droop: 1.35, density: 52, thickness: 0.46, wave: 0.9, root: L.root, tip: L.body, rim: "#ffffff" }; }
  function earFur(L, len, kind) {
    const tints = [{ at: kind === "pointy" ? [0, 0, 0.25] : [0, 0, 0.3], r: 0.22, color: L.inner, amount: 0.6 }];
    return { len: (kind === "floppy" ? 0.3 : 0.2) * len, droop: kind === "pointy" ? 0.1 : kind === "floppy" ? 1.0 : 0.6, density: 56, thickness: 0.46,
      root: L.earRoot, tip: L.earTip, tints };
  }

  function buildEar(F, L, len, kind, side, shells) {
    const pivot = new THREE.Group();
    let ear;
    if (kind === "floppy") {
      ear = F.furry(F.sphere(0.42), earFur(L, len, kind), shells);
      ear.scale.set(0.62, 1.5, 0.45);
      pivot.position.set(side * 0.98, 1.0 - NECK, 0.05);   // hinge at the top, the ear hangs below
      ear.position.set(side * 0.08, -0.55, 0);
      pivot.rotation.z = side * 0.22;
    } else if (kind === "pointy") {
      ear = F.furry(new THREE.ConeGeometry(0.36, 1.0, 48, 16), earFur(L, len, kind), shells);
      pivot.position.set(side * 0.62, 1.6 - NECK, -0.05);
      ear.position.set(0, 0.42, 0);
      pivot.rotation.z = -side * 0.42;
    } else {  // round: Mochi as picked
      ear = F.furry(F.sphere(0.3), earFur(L, len, kind), shells);
      ear.scale.set(1, 0.92, 0.55);
      pivot.position.set(side * 0.72, 1.86 - NECK - 0.2, 0.05);
      ear.position.set(0, 0.2, 0);
    }
    pivot.add(ear);
    pivot.userData.rest = { rz: pivot.rotation.z, sy: 1 };
    pivot.userData.fur = null;
    return { pivot, ear };
  }

  function buildEye(F, side, style) {
    const r = style === "button" ? 0.2 : 0.18;
    const g = F.eye(r, [side * 0.05, 0]);
    g.position.set(side * 0.36, 0.92 - NECK, 0.96);
    g.rotation.y = side * 0.33;
    const [ball, iris, big, small] = g.children;
    if (style === "sparkle") { big.scale.setScalar(1.35); small.scale.setScalar(1.6); }
    if (style === "button") { big.scale.setScalar(0.8); small.visible = false; }
    g.userData = { r, iris, big, small, ball, base: { ix: iris.position.x, iy: iris.position.y, bx: big.position.x, by: big.position.y, sx: small.position.x, sy: small.position.y },
      lid: style === "sleepy" ? 0.72 : 1 };
    return g;
  }

  function arc(width, color, up) {
    const a = Math.PI * 0.7;
    const t = new THREE.Mesh(new THREE.TorusGeometry(width, width * 0.16, 10, 32, a), new THREE.MeshPhongMaterial({ color, shininess: 30 }));
    t.rotation.z = up ? Math.PI / 2 - a / 2 : -Math.PI / 2 - a / 2;  // up: ^ (happy), down: ‿ (closed)
    return t;
  }

  function accessory(F, kind, L) {
    const g = new THREE.Group();
    const mat = (c, rough = 0.6) => new THREE.MeshStandardMaterial({ color: c, roughness: rough, metalness: 0.05 });
    if (kind === "headphones") {
      g.add(F.headphones(1.42, 0.32, { band: "#3b4a7a", cup: "#ffc46b", glow: "#fff3c4", cushion: "#2f3b63", capColor: "#ffd48f" }));
      g.position.y = 0.95 - NECK;
    } else if (kind === "cap" || kind === "beanie") {
      const dome = new THREE.Mesh(new THREE.SphereGeometry(1.18, 48, 24, 0, Math.PI * 2, 0, Math.PI * 0.42), mat(kind === "cap" ? "#3b4a7a" : "#ffc46b", 0.8));
      g.add(dome);
      if (kind === "cap") {
        const brim = new THREE.Mesh(new THREE.CylinderGeometry(0.62, 0.62, 0.05, 40, 1, false, -Math.PI / 2, Math.PI), mat("#3b4a7a", 0.7));
        brim.position.set(0, 0.62, 0.78);
        brim.rotation.x = 0.18;
        g.add(brim);
      } else {
        const cuff = new THREE.Mesh(new THREE.TorusGeometry(1.02, 0.14, 14, 48), mat("#f2b04f", 0.9));
        cuff.rotation.x = Math.PI / 2;
        cuff.position.y = 0.42;
        const pom = F.furry(F.sphere(0.2), { len: 0.12, droop: 0.2, density: 60, thickness: 0.46, root: "#fff3d6", tip: "#ffffff" }, 24);
        pom.position.y = 1.22;
        g.add(cuff, pom);
      }
      g.position.y = 0.95 - NECK + 0.05;
    } else if (kind === "scarf") {
      const loop = new THREE.Mesh(new THREE.TorusGeometry(0.98, 0.17, 14, 48), mat("#e8836b", 0.9));
      loop.rotation.x = Math.PI / 2 + 0.12;
      loop.position.y = 0.2;
      const tail = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.7, 0.12), mat("#e8836b", 0.9));
      tail.position.set(0.42, -0.2, 0.92);
      tail.rotation.z = 0.15;
      g.add(loop, tail);  // around the neck: stays on the body
      g.userData.onBody = true;
    } else if (kind === "glasses") {
      const m = mat(L.ink, 0.3);
      for (const s of [-1, 1]) {
        const ring = new THREE.Mesh(new THREE.TorusGeometry(0.21, 0.025, 10, 40), m);
        ring.position.set(s * 0.37, 0.92 - NECK, 1.14);
        ring.rotation.y = s * 0.3;
        g.add(ring);
      }
      const bridge = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 0.3, 8), m);
      bridge.rotation.z = Math.PI / 2;
      bridge.position.set(0, 0.96 - NECK, 1.2);
      g.add(bridge);
    } else if (kind === "bow") {
      const m = mat("#f39ab8", 0.5);
      for (const s of [-1, 1]) {
        const wing = new THREE.Mesh(new THREE.ConeGeometry(0.2, 0.36, 24), m);
        wing.rotation.z = s * Math.PI / 2;
        wing.position.set(s * 0.18, 0, 0);
        g.add(wing);
      }
      g.add(new THREE.Mesh(new THREE.SphereGeometry(0.09, 16, 12), m));
      g.position.set(0.55, 1.78 - NECK, 0.35);
      g.rotation.z = -0.4;
    }
    return g;
  }

  /** A small floating sign: "z" (asleep) or a four-point star (happy, proud), drawn as paths (no font needed). */
  function sprite(kind, color) {
    const c = document.createElement("canvas");
    c.width = c.height = 64;
    const x = c.getContext("2d");
    x.fillStyle = x.strokeStyle = color;
    if (kind === "z") {
      x.lineWidth = 9;
      x.lineJoin = "round";
      x.beginPath(); x.moveTo(14, 16); x.lineTo(50, 16); x.lineTo(14, 48); x.lineTo(50, 48); x.stroke();
    } else {
      x.beginPath();
      for (let i = 0; i < 8; i++) {
        const a = (i * Math.PI) / 4 - Math.PI / 2, r = i % 2 ? 7 : 28;
        x.lineTo(32 + Math.cos(a) * r, 32 + Math.sin(a) * r);
      }
      x.closePath(); x.fill();
    }
    return new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), transparent: true, opacity: 0, depthWrite: false }));
  }

  /** The whole character: rig > body + paw + head(eyes, nose, mouth, ears, accessory). */
  function build(custom, shells) {
    const F = root.MaxieFur;
    const L = LOOKS[custom.fur] || LOOKS.mochi;
    const len = FUR_LENGTHS[custom.furLength] ?? 1;
    const rig = new THREE.Group();
    const body = F.furry(F.sphere(0.86), bodyFur(L, len), shells);
    body.scale.set(1.12, 0.78, 0.95);
    body.position.y = -0.25;
    rig.add(body);
    const pawPivot = new THREE.Group();  // only shown while waving
    pawPivot.position.set(0.9, 0.15, 0.5);
    const paw = F.furry(F.sphere(0.22), { len: 0.18 * len, droop: 0.5, density: 52, thickness: 0.46, root: L.root, tip: L.body }, shells);
    paw.position.set(0.58, 0.12, 0);
    paw.scale.set(1, 1.15, 0.9);
    pawPivot.add(paw);
    pawPivot.visible = false;
    rig.add(pawPivot);

    const head = new THREE.Group();
    head.position.y = NECK;
    rig.add(head);
    const skull = F.furry(F.sphere(1.0), headFur(L, len), shells);
    skull.position.y = 0.95 - NECK;
    head.add(skull);
    const ears = [-1, 1].map((s) => buildEar(F, L, len, custom.ears, s, shells));
    ears.forEach((e) => head.add(e.pivot));
    const eyes = [-1, 1].map((s) => buildEye(F, s, custom.eyes));
    eyes.forEach((e) => head.add(e));
    const lids = [-1, 1].map((s) => { const a = arc(0.12, L.ink, false); a.position.set(s * 0.36, 0.9 - NECK, 1.06); a.visible = false; head.add(a); return a; });
    const joys = [-1, 1].map((s) => { const a = arc(0.12, L.ink, true); a.position.set(s * 0.36, 0.86 - NECK, 1.06); a.visible = false; head.add(a); return a; });
    const nose = new THREE.Mesh(F.sphere(0.08, 24, 16), new THREE.MeshPhongMaterial({ color: L.ink, specular: "#ffffff", shininess: 100 }));
    nose.scale.set(1.35, 0.95, 0.9);
    nose.position.set(0, 0.64 - NECK, 1.01);
    head.add(nose);
    const smile = F.smile(0.07, L.ink);
    smile.position.set(0, 0.57 - NECK, 1.0);
    head.add(smile);
    const mouth = new THREE.Group();
    const hole = new THREE.Mesh(new THREE.CircleGeometry(0.075, 32), new THREE.MeshBasicMaterial({ color: "#3a2440" }));
    const tongue = new THREE.Mesh(new THREE.CircleGeometry(0.045, 24), new THREE.MeshBasicMaterial({ color: "#f39ab8" }));
    tongue.position.set(0, -0.03, 0.002);
    mouth.add(hole, tongue);
    mouth.position.set(0, 0.54 - NECK, 1.02);
    mouth.visible = false;
    head.add(mouth);

    const extra = accessory(F, custom.accessory, L);
    (extra.userData.onBody ? rig : head).add(extra);

    const dots = [0, 1, 2].map((i) => {
      const d = new THREE.Mesh(new THREE.SphereGeometry(0.06 + i * 0.025, 16, 12), new THREE.MeshBasicMaterial({ color: "#cfd7ff", transparent: true, opacity: 0 }));
      d.position.set(0.95 + i * 0.22, 2.25 + i * 0.2, 0.3);
      rig.add(d);
      return d;
    });
    const zs = [0, 1, 2].map(() => { const z = sprite("z", "#8FA3D9"); rig.add(z); return z; });
    const sparkles = [0, 1, 2, 3].map(() => { const s = sprite("star", "#ffd48f"); rig.add(s); return s; });
    return { rig, body, paw: pawPivot, head, skull, ears, eyes, lids, joys, nose, smile, mouth, dots, zs, sparkles, look: L };
  }

  /* ---------- the character ---------- */

  function create(canvas, opts = {}) {
    if (!root.THREE || !root.MaxieFur) throw new Error("three.js and maxie-fur.js must be loaded first");
    const o = Object.assign({ framing: "stage", interactive: true, maxFps: 0, loop: 0, eyes: true, seed: 7,
      pixelRatio: Math.min(root.devicePixelRatio || 1, 2), custom: {} }, opts);
    const reduce = o.reduceMotion ?? (root.matchMedia && root.matchMedia("(prefers-reduced-motion: reduce)").matches);
    const LOOP = o.loop;
    const shells = o.shells || (LOOP ? 56 : 32);
    let custom = Object.assign({}, DEFAULT_CUSTOM, o.custom);
    let rand = LOOP ? mulberry32(o.seed) : Math.random;
    const W = (f) => (LOOP ? Math.max(1, Math.round(f * LOOP)) / LOOP : f) * Math.PI * 2;  // whole cycles per loop

    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: !!LOOP || !!o.preserve });
    renderer.setPixelRatio(o.pixelRatio);
    renderer.setClearColor(0x000000, 0);
    const scene = new THREE.Scene();
    scene.add(new THREE.HemisphereLight("#fff7ee", "#8a7f99", 0.75));
    const key = new THREE.DirectionalLight("#fff3e3", 0.85);
    key.position.set(-3, 5, 6);
    scene.add(key);
    const rimLight = new THREE.DirectionalLight("#ffffff", 0.5);
    rimLight.position.set(3, 2, -5);
    scene.add(rimLight);
    const camera = new THREE.PerspectiveCamera(26, 1, 0.1, 100);

    let M = build(custom, shells);
    scene.add(M.rig);

    let state = "idle", stateT = 0, t = 0, returnTo = null, returnAt = 0;
    let blinkAt = 3, look = { x: 0, y: 0 }, pointer = null;
    const listeners = [];
    const damp = (a, b, k, dt) => a + (b - a) * (1 - Math.exp(-k * dt));

    function frameCamera() {
      const w = canvas.clientWidth || canvas.width, h = canvas.clientHeight || canvas.height;
      camera.aspect = w / h;
      if (o.framing === "stage") {
        camera.fov = 28;
        camera.position.set(0, 0.9, 9.2);
        camera.lookAt(0, 0.62, 0);
      } else {
        const [cx, cy, hw, hh] = o.framing === "portrait" ? [0, 1.12, 1.55, 1.45] : [0, 0.48, 1.62, 2.0];
        camera.fov = 24;
        const k = Math.tan((camera.fov * Math.PI) / 360);
        const dist = Math.max(hh / k, hw / (k * camera.aspect)) + 1.2;
        camera.position.set(cx, cy + 0.2, dist);
        camera.lookAt(cx, cy, 0);
      }
      camera.updateProjectionMatrix();
    }
    function resize() {
      const w = canvas.clientWidth || canvas.width, h = canvas.clientHeight || canvas.height;
      renderer.setSize(w, h, false);
      frameCamera();
    }

    function setState(s, temp) {
      if (!STATES[s] || (s === state && !temp)) return;
      if (s === "love") s = "proud";
      state = s; stateT = 0;
      if (!temp) returnTo = null;
      listeners.forEach((f) => f(s));
    }
    function celebrate(ms = 2400) { if (state === "happy") return; const prev = state; setState("happy", true); returnTo = prev; returnAt = t + ms / 1000; }
    function setLook(k) { if (LOOKS[k]) setCustom({ fur: k }); }
    function setCustom(c) {
      const next = Object.assign({}, custom, c);
      if (JSON.stringify(next) === JSON.stringify(custom)) return;
      custom = next;
      scene.remove(M.rig);
      M.rig.traverse((x) => { if (x.material) x.material.dispose(); });
      M = build(custom, shells);
      scene.add(M.rig);
      advance(1 / 60);
    }

    function setGlow(amount) {
      M.ears.forEach((e) => root.MaxieFur.setFurGlow(e.ear, [1.0 * amount, 0.77 * amount, 0.42 * amount]));
    }
    function setCheeks(amount) {
      M.skull.children.forEach((m) => { const v = m.material.uniforms.tintCol.value; v[1].w = amount; v[2].w = amount; });
    }

    function lookAtPointer() {
      if (!pointer) return { x: 0, y: 0 };
      const r = canvas.getBoundingClientRect();
      return { x: Math.max(-1, Math.min(1, ((pointer.x - r.left) / r.width - 0.5) * 2.2)), y: Math.max(-1, Math.min(1, -((pointer.y - r.top) / r.height - 0.35) * 2.2)) };
    }

    function advance(dt) {
      t += dt; stateT += dt;
      if (returnTo && t > returnAt) { const r = returnTo; returnTo = null; setState(r); }
      const m = reduce ? 0.3 : 1;
      const s = state;
      // where the eyes look
      let target = o.interactive ? lookAtPointer() : { x: Math.sin(t * W(0.13)) * 0.35, y: Math.sin(t * W(0.21)) * 0.15 };
      if (s === "think") target = { x: 0.6, y: 0.7 };
      if (s === "gentle") target = { x: target.x * 0.3, y: -0.35 };
      if (s === "talk" || s === "listen") target = { x: target.x * 0.4, y: target.y * 0.4 };
      look.x = damp(look.x, target.x, 6, dt); look.y = damp(look.y, target.y, 6, dt);

      // breathing and posture
      const slow = s === "sleep" || s === "closed";
      const breath = Math.sin(t * W(slow ? 0.25 : 0.45)) * (slow ? 0.035 : 0.022) * m;
      M.body.scale.y = 0.78 * (1 + breath);
      M.body.scale.x = 1.12 * (1 - breath * 0.4);
      let tiltZ = Math.sin(t * W(0.2)) * 0.03 * m, tiltX = 0, bob = breath * 0.6, wag = 0, hop = 0, stretchY = 1;
      if (s === "listen") tiltZ = 0.14;
      if (s === "think") tiltZ = -0.12 + Math.sin(t * W(0.5)) * 0.03 * m;
      if (s === "talk") tiltX = Math.sin(t * W(4)) * 0.04 * m;
      if (s === "gentle") { tiltX = 0.12; tiltZ = 0.1 + Math.sin(t * W(0.3)) * 0.04 * m; }
      if (s === "sleep") { tiltX = 0.22; tiltZ = 0.18; }
      if (s === "closed") { tiltX = 0.06; }
      if (s === "proud") tiltX = Math.max(0, Math.sin(t * W(1.5))) * 0.12 * m;
      if (s === "happy") { hop = Math.abs(Math.sin(stateT * W(2))) * 0.28 * m; wag = Math.sin(t * W(4)) * 0.12 * m; }
      if (s === "wave") tiltZ = -0.08;
      if (s === "stretch") { const p = (Math.sin(t * W(0.5) - Math.PI / 2) + 1) / 2; stretchY = 1 + p * 0.12 * m; tiltX = -p * 0.18; }
      M.head.rotation.x = damp(M.head.rotation.x, tiltX - look.y * 0.12, 6, dt);
      M.head.rotation.z = damp(M.head.rotation.z, tiltZ, 6, dt);
      M.head.rotation.y = damp(M.head.rotation.y, look.x * 0.22, 6, dt);
      M.head.position.y = NECK + bob;
      M.rig.position.y = damp(M.rig.position.y, hop, 14, dt);
      M.rig.rotation.z = damp(M.rig.rotation.z, wag, 10, dt);
      M.rig.scale.y = damp(M.rig.scale.y, stretchY * (s === "happy" ? 1 - Math.cos(stateT * W(2) * 2) * 0.03 * m : 1), 10, dt);
      M.rig.scale.x = damp(M.rig.scale.x, 1 / Math.sqrt(M.rig.scale.y), 10, dt);

      // ears: perk up and glow while listening, droop asleep, twitch now and then
      M.ears.forEach((e, i) => {
        const side = i ? 1 : -1, rest = e.pivot.userData.rest;
        let rz = rest.rz, sy = 1;
        if (s === "listen") { rz = rest.rz - side * 0.12; sy = 1.1; }
        if (s === "sleep") rz = rest.rz + side * 0.25;
        if (s === "idle" && Math.sin(t * W(0.31) + i) > 0.97) rz += side * 0.15;
        e.pivot.rotation.z = damp(e.pivot.rotation.z, rz, 10, dt);
        e.pivot.scale.y = damp(e.pivot.scale.y, sy, 10, dt);
      });
      setGlow(s === "listen" ? (0.22 + 0.16 * Math.sin(t * W(2))) * (reduce ? 0.6 : 1) : 0);
      setCheeks(s === "happy" || s === "proud" ? 0.75 : s === "gentle" ? 0.6 : 0.45);

      // eyes: open, blink, closed (‿) or happy (^)
      const open = OPEN_EYES.has(s);
      let lid = 1;
      if (open) {
        if (LOOP) { const ph = (t % LOOP) / LOOP; if (s === "idle" && ph > 0.62 && ph < 0.66) lid = 0.1; }
        else if (t > blinkAt) { if (t < blinkAt + 0.13) lid = 0.1; else blinkAt = t + 2.5 + rand() * 4; }
      }
      const size = s === "listen" ? 1.08 : s === "gentle" ? 0.9 : s === "think" ? 0.95 : 1;
      M.eyes.forEach((e) => {
        const u = e.userData;
        e.visible = open && o.eyes;
        e.scale.set(size, size * lid * u.lid, size);
        u.iris.position.set(u.base.ix + look.x * u.r * 0.22, u.base.iy + look.y * u.r * 0.18, u.iris.position.z);
        u.big.position.set(u.base.bx + look.x * u.r * 0.08, u.base.by + look.y * u.r * 0.06, u.big.position.z);
        const sparkle = s === "proud" ? 1.25 + 0.2 * Math.sin(t * W(3)) : 1;
        u.big.scale.setScalar(sparkle);
      });
      M.lids.forEach((a) => { a.visible = s === "sleep" || s === "closed"; });
      M.joys.forEach((a) => { a.visible = s === "happy" || s === "stretch"; });

      // mouth
      const talking = s === "talk";
      const amp = talking ? 0.25 + 0.75 * Math.abs(Math.sin(t * W(5)) * 0.6 + Math.sin(t * W(8)) * 0.4) * m : s === "listen" ? 0.35 : s === "happy" ? 0.8 : 0;
      M.mouth.visible = amp > 0.05;
      M.mouth.scale.set(talking ? 0.9 + amp * 0.3 : 1, Math.max(amp, 0.05), 1);
      M.smile.visible = !M.mouth.visible || s === "listen";

      // waving paw
      M.paw.visible = s === "wave";
      if (s === "wave") { M.paw.rotation.z = 0.9 + Math.sin(t * W(2.5)) * 0.45 * m; }

      // thought dots, sleepy z's, sparkles
      M.dots.forEach((d, i) => { const on = s === "think"; d.material.opacity = damp(d.material.opacity, on ? 0.5 + 0.5 * Math.max(0, Math.sin(t * W(1.2) - i * 1.1)) : 0, 8, dt); d.position.y = 2.25 + i * 0.2 + Math.sin(t * W(1.2) - i) * 0.05; });
      M.zs.forEach((z, i) => { if (s !== "sleep") { z.material.opacity = damp(z.material.opacity, 0, 8, dt); return; } const p = ((t * W(0.3)) / (Math.PI * 2) + i / 3) % 1; z.position.set(0.9 + p * 0.7, 1.9 + p * 1.0, 0.4); z.material.opacity = Math.sin(p * Math.PI) * 0.9; z.scale.setScalar(0.3 + p * 0.35); });
      M.sparkles.forEach((sp, i) => { const on = s === "happy" || s === "proud"; if (!on) { sp.material.opacity = damp(sp.material.opacity, 0, 8, dt); return; } const p = ((t * W(0.8)) / (Math.PI * 2) + i / 4) % 1, a = i * 1.7; sp.position.set(Math.cos(a) * (1.3 + p * 0.5), 1.0 + Math.sin(a) * 0.9 + p * 0.6, 0.6); sp.material.opacity = Math.sin(p * Math.PI) * 0.95; sp.scale.setScalar(0.25 + p * 0.15); });

      root.MaxieFur.animateFur(M.rig, t, dt, W, m);  // fur hangs down and flows like hair as he moves
    }

    function render() { renderer.render(scene, camera); }

    /** Where the eyes sit in the image (pixels, top-left origin), for the Mac's live eye layer. */
    function eyeAnchors() {
      scene.updateMatrixWorld(true);
      const w = renderer.domElement.width, h = renderer.domElement.height;
      const px = (v) => { const p = v.clone().project(camera); return { x: (p.x + 1) / 2 * w, y: (1 - p.y) / 2 * h }; };
      const out = {};
      [["left", 0], ["right", 1]].forEach(([k, i]) => {
        const e = M.eyes[i], r = e.userData.r;
        const c = px(e.localToWorld(new THREE.Vector3(0, 0, 0)));
        const edge = px(e.localToWorld(new THREE.Vector3(r, 0, 0)));
        const top = px(e.localToWorld(new THREE.Vector3(0, r * 1.12, 0)));
        out[k] = { x: +c.x.toFixed(2), y: +c.y.toFixed(2), w: +(2 * Math.hypot(edge.x - c.x, edge.y - c.y)).toFixed(2), h: +(2 * Math.hypot(top.x - c.x, top.y - c.y)).toFixed(2) };
      });
      out.color = "#17100d";
      out.style = "glossy";
      return out;
    }

    // live loop: capped fps, pauses when hidden
    let running = false, raf = 0, last = 0;
    const minDt = o.maxFps ? 1 / o.maxFps : 0;
    function frame(now) {
      raf = requestAnimationFrame(frame);
      const dt = Math.min((now - last) / 1000, 0.05);
      if (dt < minDt - 0.002) return;
      last = now; advance(dt); render();
    }
    function start() { if (running || LOOP) return; running = true; last = performance.now(); raf = requestAnimationFrame(frame); }
    function stop() { running = false; cancelAnimationFrame(raf); }
    const onVis = () => (document.hidden ? stop() : start());
    if (!LOOP) document.addEventListener("visibilitychange", onVis);
    if (o.interactive && !LOOP) {
      root.addEventListener("pointermove", (e) => { pointer = { x: e.clientX, y: e.clientY }; });
      canvas.addEventListener("pointerdown", () => celebrate());
    }

    resize();
    advance(1 / 60); render();
    return {
      setState, setLook, setCustom, celebrate, start, stop, advance, render, eyeAnchors, resize,
      get state() { return state; },
      get custom() { return Object.assign({}, custom); },
      onState(f) { listeners.push(f); },
      domElement: renderer.domElement,
      dispose() { stop(); document.removeEventListener("visibilitychange", onVis); renderer.dispose(); },
    };
  }

  root.MaxieCharacter = { create, LOOKS, STATES, FUR_LENGTHS, EARS, EYES, ACCESSORIES, DEFAULT_CUSTOM };
})(typeof window !== "undefined" ? window : globalThis);
