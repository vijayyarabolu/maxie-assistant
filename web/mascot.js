// The Maxie bot: the approved mascot, ported to a reusable module.
// Same model, materials, face drawing and per-state animation. Used by the PWA header, the Mac panel's
// live bot (web/bot.html in a WKWebView) and scripts/render_mascot.py (deterministic loop frames).
//
//   const bot = ClaudeMascot.create(canvas, {look: "sunset", framing: "body", maxFps: 30});
//   bot.setState("think");  bot.setLook("midnight");  bot.stop();  bot.start();
//
// Options:
//   framing      "stage" (the reference camera), "body" (whole bot head to feet, ~3:5) or "portrait" (icons)
//   floor        soft contact shadow under the feet (off for transparent renders)
//   interactive  eyes follow the pointer, tap = little celebration
//   maxFps       cap (30 on phones); 0 = every animation frame
//   loop         seconds; deterministic mode for rendering seamless loops (seeded randomness, all
//                motion frequencies snapped to whole cycles per loop, scheduled blinks). Drive it with
//                bot.advance(dt) + bot.render() instead of start().
//   eyes         false = draw the screen without eyes (the Mac draws live eyes on top)
(function (root) {
  "use strict";

  const LOOKS = {
    sunset:   { name: "Sunset",       body: 0xFFD9B8, accent: 0x3B4A7A, soft: 0x8FA3D9, eyes: "#FFC48A" },
    clay:     { name: "Cream + clay", body: 0xF3F1EC, accent: 0xD97757, soft: 0xF0B49C, eyes: "#7FE7F2" },
    allclay:  { name: "All clay",     body: 0xD97757, accent: 0xFFF1E4, soft: 0xFFFFFF, eyes: "#FFE6CF" },
    midnight: { name: "Midnight",     body: 0x2E3240, accent: 0xD97757, soft: 0xF0B49C, eyes: "#7FE7F2" },
    sky:      { name: "Sky",          body: 0xBFD6F2, accent: 0xD97757, soft: 0xF0B49C, eyes: "#7FE7F2" },
  };

  // Hub states (/api/events) -> mascot states. "proud" is the reference's "love" (heart eyes).
  const STATES = {
    idle:   { label: "Idle",         eyes: "round",  mouth: "smile", size: 1,    gap: 1,    blush: 0.5, tilt: 0,     lean: 0,     bounce: 1,   tip: 1.2 },
    listen: { label: "Listening",    eyes: "round",  mouth: "o",     size: 1.18, gap: 1,    blush: 0.3, tilt: 0.14,  lean: 0.08,  bounce: 0.6, tip: 2.4 },
    think:  { label: "Thinking",     eyes: "round",  mouth: "dots",  size: 0.9,  gap: 0.95, blush: 0,   tilt: -0.14, lean: -0.06, bounce: 0.5, tip: 1.6 },
    talk:   { label: "Talking",      eyes: "round",  mouth: "talk",  size: 1,    gap: 1,    blush: 0.4, tilt: 0,     lean: 0.03,  bounce: 1,   tip: 1.6 },
    happy:  { label: "Happy",        eyes: "happy",  mouth: "big",   size: 1,    gap: 1,    blush: 1,   tilt: 0,     lean: 0,     bounce: 0,   tip: 3 },
    proud:  { label: "Proud of you", eyes: "heart",  mouth: "smile", size: 1,    gap: 1,    blush: 1,   tilt: 0.08,  lean: 0.04,  bounce: 1,   tip: 2.2 },
    sleep:  { label: "Sleeping",     eyes: "closed", mouth: "sleep", size: 1,    gap: 1,    blush: 0.2, tilt: 0.18,  lean: 0.3,   bounce: 0.4, tip: 0.25 },
    // A hard day: softer, smaller eyes looking a little down, leaning in, slow sway, antenna low. Kind, not sad.
    gentle: { label: "With you",     eyes: "round",  mouth: "smile", size: 0.86, gap: 1.04, blush: 0.7, tilt: 0.12,  lean: 0.14,  bounce: 0.35, tip: 0.6 },
  };
  STATES.love = STATES.proud;

  function mulberry32(seed) {
    return function () {
      seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function create(canvas, opts = {}) {
    const THREE = root.THREE;
    if (!THREE) throw new Error("three.js is not loaded");
    const o = Object.assign({ look: "sunset", framing: "stage", floor: true, interactive: true, maxFps: 0,
                              loop: 0, eyes: true, seed: 7, pixelRatio: Math.min(root.devicePixelRatio || 1, 2),
                              shadows: true }, opts);
    const reduce = o.reduceMotion ?? (root.matchMedia && root.matchMedia("(prefers-reduced-motion: reduce)").matches);
    const LOOP = o.loop;
    let rand = LOOP ? mulberry32(o.seed) : Math.random;
    // In loop mode every oscillation completes a whole number of cycles per loop, so frame N == frame 0.
    const W = LOOP ? (w) => (Math.max(1, Math.round((w * LOOP) / (2 * Math.PI))) * 2 * Math.PI) / LOOP : (w) => w;
    const C = LOOP ? (c) => Math.max(1, Math.round(c * LOOP)) / LOOP : (c) => c;

    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: !!LOOP });
    renderer.setPixelRatio(o.pixelRatio);
    renderer.setClearColor(0x000000, 0);
    renderer.outputEncoding = THREE.sRGBEncoding; renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.0;
    renderer.shadowMap.enabled = o.shadows; renderer.shadowMap.type = THREE.PCFSoftShadowMap;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(28, 1, 0.1, 100);

    const pmrem = new THREE.PMREMGenerator(renderer); const env = new THREE.Scene();
    env.add(new THREE.Mesh(new THREE.BoxGeometry(20, 20, 20), new THREE.MeshBasicMaterial({ color: 0x8a8c92, side: THREE.BackSide })));
    const panel = (w, h, c, x, y, z, rx, ry) => { const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color: c, side: THREE.DoubleSide })); m.position.set(x, y, z); m.rotation.set(rx || 0, ry || 0, 0); env.add(m); };
    panel(10, 4, 0xffffff, 0, 7, 2, Math.PI / 2, 0); panel(5, 7, 0xffffff, -8, 1, 3, 0, Math.PI / 2); panel(4, 6, 0xdfe8ff, 8, 2, -2, 0, -Math.PI / 2);
    scene.environment = pmrem.fromScene(env, 0.04).texture;

    scene.add(new THREE.HemisphereLight(0xffffff, 0xcfd3dc, 0.55));
    const key = new THREE.DirectionalLight(0xffffff, 1.6); key.position.set(2.5, 5, 4); key.castShadow = o.shadows; key.shadow.mapSize.set(1024, 1024); Object.assign(key.shadow.camera, { left: -3, right: 3, top: 3, bottom: -3 }); key.shadow.radius = 8; key.shadow.bias = -0.0005; scene.add(key);
    const rim = new THREE.DirectionalLight(0xdfe8ff, 0.8); rim.position.set(-3, 2, -3); scene.add(rim);

    const FLOOR = -1.62;
    const blobTex = (() => { const c = document.createElement("canvas"); c.width = c.height = 128; const g = c.getContext("2d"); const r = g.createRadialGradient(64, 64, 4, 64, 64, 62); r.addColorStop(0, "rgba(70,80,110,0.42)"); r.addColorStop(1, "rgba(70,80,110,0)"); g.fillStyle = r; g.fillRect(0, 0, 128, 128); return new THREE.CanvasTexture(c); })();
    const blob = new THREE.Mesh(new THREE.PlaneGeometry(2.0, 1.4), new THREE.MeshBasicMaterial({ map: blobTex, transparent: true, depthWrite: false })); blob.rotation.x = -Math.PI / 2; blob.position.y = FLOOR + 0.005;
    if (o.floor) {
      const floor = new THREE.Mesh(new THREE.PlaneGeometry(20, 20), new THREE.ShadowMaterial({ opacity: 0.12 })); floor.rotation.x = -Math.PI / 2; floor.position.y = FLOOR; floor.receiveShadow = true; scene.add(floor);
      scene.add(blob);
    }

    // ---------- materials ----------
    const shell = new THREE.MeshPhysicalMaterial({ color: 0xF3F1EC, roughness: 0.5, clearcoat: 0.35, clearcoatRoughness: 0.4, envMapIntensity: 0.6 });
    const clay = new THREE.MeshPhysicalMaterial({ color: 0xD97757, roughness: 0.42, clearcoat: 0.5, clearcoatRoughness: 0.3, envMapIntensity: 0.7 });
    const clayLight = new THREE.MeshStandardMaterial({ color: 0xF0B49C, roughness: 0.5 });
    const darkM = new THREE.MeshStandardMaterial({ color: 0x2A2C33, roughness: 0.4, metalness: 0.2 });
    const tipM = new THREE.MeshStandardMaterial({ color: 0xFF9A72, emissive: 0xFF8A60, emissiveIntensity: 1.2 });
    const heartM = new THREE.MeshStandardMaterial({ color: 0x7FE7F2, emissive: 0x7FE7F2, emissiveIntensity: 1.1 });
    [shell, clay, clayLight, darkM, tipM, heartM].forEach((m) => { m.color.convertSRGBToLinear(); if (m.emissive) m.emissive.convertSRGBToLinear(); });
    const sph = (r, mat, ws, hs) => { const m = new THREE.Mesh(new THREE.SphereGeometry(r, ws || 48, hs || 32), mat); m.castShadow = o.shadows; return m; };

    // ---------- the bot ----------
    const rig = new THREE.Group(); scene.add(rig);
    const bot = new THREE.Group(); rig.add(bot);
    const HS = new THREE.Vector3(1.12, 0.94, 1.0);
    const head = new THREE.Group(); head.position.y = 0.62; bot.add(head);
    const skull = sph(1, shell, 64, 48); skull.scale.copy(HS); head.add(skull);

    // screen face: a canvas drawn every frame
    const FC = document.createElement("canvas"); FC.width = 640; FC.height = 380; const fx = FC.getContext("2d");
    const faceTex = new THREE.CanvasTexture(FC); faceTex.encoding = THREE.sRGBEncoding; faceTex.anisotropy = 4;
    const SCREEN = [1.012, 64, 40, Math.PI / 2 - 0.86, 1.72, 0.66, 1.13];
    const screen = new THREE.Mesh(new THREE.SphereGeometry(...SCREEN), new THREE.MeshBasicMaterial({ map: faceTex, transparent: true })); screen.scale.copy(HS); head.add(screen);
    const glass = new THREE.Mesh(new THREE.SphereGeometry(1.02, 64, 40, Math.PI / 2 - 0.86, 1.72, 0.66, 1.13), new THREE.MeshPhysicalMaterial({ color: 0xffffff, roughness: 0.05, metalness: 0, transparent: true, opacity: 0.12, clearcoat: 1, envMapIntensity: 1.6 })); glass.scale.copy(HS); head.add(glass);

    [-1, 1].forEach((s) => { const g = new THREE.Group(); g.position.set(1.1 * s, 0.02, -0.02); g.rotation.z = (Math.PI / 2) * s;
      const cup = new THREE.Mesh(new THREE.CylinderGeometry(0.28, 0.32, 0.22, 48), clay); cup.castShadow = o.shadows; g.add(cup);
      g.add(new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.2, 0.24, 40), clayLight));
      const dot = sph(0.06, tipM, 16, 12); dot.position.y = 0.13; g.add(dot);
      head.add(g); });

    const antenna = new THREE.Group(); antenna.position.set(0.18, 0.9, 0); antenna.rotation.z = -0.15; head.add(antenna);
    const stalk = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.04, 0.42, 16), darkM); stalk.position.y = 0.21; antenna.add(stalk);
    const tip = sph(0.1, tipM, 24, 16); tip.position.y = 0.45; antenna.add(tip);
    const tipLight = new THREE.PointLight(0xFF9A72, 0.5, 1.8); tip.add(tipLight);

    const collar = new THREE.Mesh(new THREE.TorusGeometry(0.38, 0.07, 16, 48), clay); collar.rotation.x = Math.PI / 2; collar.position.y = -0.3; collar.castShadow = o.shadows; bot.add(collar);
    const body = sph(1, shell, 48, 36); body.scale.set(0.66, 0.6, 0.58); body.position.y = -0.78; bot.add(body);
    const heart = sph(0.07, heartM, 20, 14); heart.position.set(0, -0.62, 0.56); bot.add(heart);
    const heartRing = new THREE.Mesh(new THREE.TorusGeometry(0.12, 0.025, 12, 32), clay); heartRing.position.copy(heart.position); heartRing.rotation.x = -0.3; bot.add(heartRing);

    const arms = [-1, 1].map((s) => { const p = new THREE.Group(); p.position.set(0.6 * s, -0.5, 0); bot.add(p);
      const a = sph(1, shell, 32, 24); a.scale.set(0.17, 0.32, 0.17); a.position.y = -0.24; p.add(a);
      return { p, s }; });
    const legs = [-1, 1].map((s) => { const p = new THREE.Group(); p.position.set(0.27 * s, -1.15, 0); rig.add(p);
      const l = sph(1, shell, 32, 24); l.scale.set(0.17, 0.2, 0.17); l.position.y = -0.12; p.add(l);
      const f = sph(1, clay, 32, 20); f.scale.set(0.2, 0.1, 0.25); f.position.set(0, -0.37, 0.05); p.add(f);
      return p; });

    // ---------- effects ----------
    const ringM = () => new THREE.MeshBasicMaterial({ color: 0xD97757, transparent: true, opacity: 0 });
    const rings = [0, 1, 2].map(() => { const m = new THREE.Mesh(new THREE.TorusGeometry(0.14, 0.012, 8, 48), ringM()); scene.add(m); return m; });
    const zTex = (() => { const c = document.createElement("canvas"); c.width = c.height = 64; const g = c.getContext("2d"); g.fillStyle = "#9A4A2E"; g.font = "700 52px system-ui, sans-serif"; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText("z", 32, 36); const t = new THREE.CanvasTexture(c); t.encoding = THREE.sRGBEncoding; return t; })();
    const zs = [0, 1, 2].map(() => { const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: zTex, transparent: true, opacity: 0, depthWrite: false })); scene.add(s); return s; });
    const confetti = Array.from({ length: 40 }, (_, i) => { const cols = [0xD97757, 0x7FE7F2, 0xF2C14E, 0x8FA8FF, 0xF0B49C]; const m = new THREE.Mesh(new THREE.BoxGeometry(0.07, 0.07, 0.015), new THREE.MeshStandardMaterial({ color: cols[i % 5], transparent: true, opacity: 0, side: THREE.DoubleSide })); m.userData = { v: new THREE.Vector3(), w: new THREE.Vector3(), life: 0 }; scene.add(m); return m; });
    const tmp = new THREE.Vector3();
    function burst() { head.getWorldPosition(tmp); confetti.forEach((m) => { m.position.copy(tmp).add(new THREE.Vector3(0, 1, 0)); m.rotation.set(0, 0, 0); m.userData.v.set((rand() - 0.5) * 4.5, 2 + rand() * 2.6, (rand() - 0.2) * 2.4); m.userData.w.set(rand() * 8, rand() * 8, rand() * 8); m.userData.life = 1.8 + rand() * 0.7; }); }

    // ---------- face drawing ----------
    let EYE = "#FFC48A";
    function setLook(k) { const L = LOOKS[k] || LOOKS.sunset; shell.color.setHex(L.body).convertSRGBToLinear(); clay.color.setHex(L.accent).convertSRGBToLinear(); clayLight.color.setHex(L.soft).convertSRGBToLinear(); EYE = L.eyes; heartM.color.set(L.eyes).convertSRGBToLinear(); heartM.emissive.set(L.eyes).convertSRGBToLinear(); rings.forEach((r) => r.material.color.setHex(L.accent)); }
    function rr(x, y, w, h, r) { fx.beginPath(); fx.moveTo(x + r, y); fx.arcTo(x + w, y, x + w, y + h, r); fx.arcTo(x + w, y + h, x, y + h, r); fx.arcTo(x, y + h, x, y, r); fx.arcTo(x, y, x + w, y, r); fx.closePath(); }
    function heartPath(cx, cy, s) { fx.beginPath(); fx.moveTo(cx, cy + s * 0.9); fx.bezierCurveTo(cx - s * 1.6, cy - s * 0.1, cx - s * 0.8, cy - s * 1.3, cx, cy - s * 0.45); fx.bezierCurveTo(cx + s * 0.8, cy - s * 1.3, cx + s * 1.6, cy - s * 0.1, cx, cy + s * 0.9); fx.closePath(); }
    const eyeSpot = { cx: 0, cy: 0, gap: 0, ew: 0, eh: 0 };
    function drawFace(F, t) {
      const Wd = FC.width, H = FC.height; fx.clearRect(0, 0, Wd, H);
      const g = fx.createLinearGradient(0, 0, 0, H); g.addColorStop(0, "#1A2438"); g.addColorStop(1, "#070B14");
      rr(14, 10, Wd - 28, H - 20, 150); fx.fillStyle = g; fx.fill();
      const hl = fx.createLinearGradient(0, 0, 0, H * 0.5); hl.addColorStop(0, "rgba(255,255,255,0.13)"); hl.addColorStop(1, "rgba(255,255,255,0)");
      rr(90, 26, Wd - 180, H * 0.32, 90); fx.fillStyle = hl; fx.fill();
      fx.save(); fx.shadowColor = EYE; fx.shadowBlur = 28; fx.fillStyle = EYE; fx.strokeStyle = EYE; fx.lineCap = "round"; fx.lineJoin = "round";
      const cx = Wd / 2 + F.lx * 46, cy = H * 0.44 - F.ly * 34, gap = 118 * F.gap;
      const ew = 62 * F.size, eh = 84 * F.size;
      Object.assign(eyeSpot, { cx, cy, gap, ew, eh });
      if (o.eyes) {
        if (F.eyes === "round") { [-1, 1].forEach((s) => { const h = Math.max(8, eh * F.open * (s < 0 ? F.leftOpen : 1)); rr(cx + s * gap - ew / 2, cy - h / 2, ew, h, Math.min(ew, h) / 2); fx.fill(); });
          fx.shadowBlur = 0; fx.fillStyle = "rgba(255,255,255,0.85)"; if (F.open > 0.6) [-1, 1].forEach((s) => { fx.beginPath(); fx.arc(cx + s * gap + ew * 0.18, cy - eh * 0.2 * F.open, 7 * F.size, 0, Math.PI * 2); fx.fill(); }); fx.fillStyle = EYE; fx.shadowBlur = 28; }
        if (F.eyes === "happy") { fx.lineWidth = 20; [-1, 1].forEach((s) => { fx.beginPath(); fx.arc(cx + s * gap, cy + 18, 36, Math.PI * 1.12, Math.PI * 1.88); fx.stroke(); }); }
        if (F.eyes === "closed") { fx.lineWidth = 14; [-1, 1].forEach((s) => { fx.beginPath(); fx.arc(cx + s * gap, cy - 10, 34, Math.PI * 0.2, Math.PI * 0.8); fx.stroke(); }); }
        if (F.eyes === "heart") { [-1, 1].forEach((s) => { heartPath(cx + s * gap, cy, 34 * (1 + 0.08 * Math.sin(t * W(8)))); fx.fill(); }); }
      }
      if (F.blush > 0.01) { fx.save(); fx.shadowBlur = 24; fx.shadowColor = "#FF9A72"; fx.fillStyle = `rgba(255,140,110,${0.55 * F.blush})`; [-1, 1].forEach((s) => { fx.beginPath(); fx.ellipse(cx + s * (gap + 56), cy + 64, 30, 15, 0, 0, Math.PI * 2); fx.fill(); }); fx.restore(); }
      const my = H * 0.74 - F.ly * 16, mx = Wd / 2 + F.lx * 30;
      fx.lineWidth = 14;
      if (F.mouth === "smile") { fx.beginPath(); fx.arc(mx, my - 34, 44, Math.PI * 0.25, Math.PI * 0.75); fx.stroke(); }
      if (F.mouth === "o") { fx.lineWidth = 12; fx.beginPath(); fx.arc(mx, my, 16 + 3 * Math.sin(t * W(6)), 0, Math.PI * 2); fx.stroke(); }
      if (F.mouth === "talk") { const h = 10 + F.talk * 46; rr(mx - 40, my - h / 2, 80, h, Math.min(40, h / 2)); fx.fill(); }
      if (F.mouth === "flat") { fx.beginPath(); fx.moveTo(mx - 30, my); fx.lineTo(mx + 30, my); fx.stroke(); }
      if (F.mouth === "big") { fx.beginPath(); fx.moveTo(mx - 50, my - 16); fx.quadraticCurveTo(mx, my + 56, mx + 50, my - 16); fx.closePath(); fx.fill(); }
      if (F.mouth === "dots") { fx.shadowBlur = 18; [-1, 0, 1].forEach((k, i) => { fx.globalAlpha = 0.35 + 0.65 * Math.max(0, Math.sin(t * W(6) - i * 0.9)); fx.beginPath(); fx.arc(mx + k * 34, my, 10, 0, Math.PI * 2); fx.fill(); }); fx.globalAlpha = 1; }
      if (F.mouth === "sleep") { fx.lineWidth = 10; fx.beginPath(); fx.arc(mx, my - 6, 12, 0, Math.PI * 2); fx.stroke(); }
      fx.restore();
      faceTex.needsUpdate = true;
    }

    // ---------- camera ----------
    const LOOK_AT = new THREE.Vector3(0, 0.05, 0);
    function resize() {
      const w = canvas.clientWidth || canvas.width, h = canvas.clientHeight || canvas.height;
      renderer.setSize(w, h, false); camera.aspect = w / h;
      if (o.framing === "body") {   // whole bot, head to feet, with room for a jump
        camera.position.set(0, 0.75, 10.2); camera.lookAt(0, 0.38, 0);
      } else if (o.framing === "portrait") {   // head and chest, for app icons
        camera.position.set(0, 1.0, 7.0); camera.lookAt(0, 0.55, 0);
      } else {
        camera.position.set(0, 0.6, w / h < 0.9 ? 12 : 9.4); camera.lookAt(LOOK_AT);
      }
      camera.updateProjectionMatrix();
    }
    if (root.ResizeObserver && !LOOP) new ResizeObserver(resize).observe(canvas);
    resize();

    // ---------- state ----------
    let state = "idle", stateT = 0, returnTo = null, returnAt = 0, spin = 0, t = 0;
    const cur = { size: 1, gap: 1, blush: 0.5, tilt: 0, lean: 0, bounce: 1, tip: 1.2 };
    const listeners = [];
    function setState(s, temp) {
      if (s === "love") s = "proud";
      if (!STATES[s] || (s === state && !temp)) return;
      if ((s === "happy" || s === "proud") && state !== s && !LOOP) { burst(); if (s === "happy") spin = reduce ? 0 : Math.PI * 2; }
      state = s; stateT = 0; if (!temp) returnTo = null;
      listeners.forEach((f) => f(s));
    }
    function celebrate(ms = 2400) { if (state === "happy") return; const prev = state; setState("happy", true); returnTo = prev; returnAt = t + ms / 1000; }

    const look = { x: 0, y: 0, tx: 0, ty: 0, last: -10 };
    const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
    let squash = 0;
    if (o.interactive) {
      const toNdc = (e) => { const r = canvas.getBoundingClientRect(); ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1); };
      root.addEventListener("pointermove", (e) => { toNdc(e); look.tx = THREE.MathUtils.clamp(ndc.x, -1, 1); look.ty = THREE.MathUtils.clamp(ndc.y * 0.9, -1, 1); look.last = t; }, { passive: true });
      canvas.addEventListener("pointerdown", (e) => { toNdc(e); ray.setFromCamera(ndc, camera); if (ray.intersectObject(rig, true).length) { squash = 1; celebrate(); } });
    }

    // ---------- life ----------
    let blinkAt = 1.2, blinkPhase = -1, winkAt = 9, glanceAt = 2, curiousAt = 6, curiousUntil = 0, hopAt = 11, hopT = -1; const glance = { x: 0, y: 0 };
    const damp = (a, b, k, dt) => THREE.MathUtils.lerp(a, b, 1 - Math.exp(-k * dt));
    let antAngle = 0, antVel = 0, lastYaw = 0, loopIndex = -1;
    const F = { eyes: "round", mouth: "smile", open: 1, leftOpen: 1, size: 1, gap: 1, blush: 0.5, lx: 0, ly: 0, talk: 0 };

    // Loop mode: the same events on the same frame of every loop (integer frame counter, no float drift).
    let steps = 0;
    function loopEvents(dt) {
      const n = Math.round(LOOP / dt), k = steps % n, idx = Math.floor(steps / n);
      const at = (sec) => k === Math.round(sec / dt) % n;
      if (idx !== loopIndex) { loopIndex = idx; rand = mulberry32(o.seed); if (state === "happy") { burst(); spin = reduce ? 0 : Math.PI * 2; } }
      if (state !== "sleep" && state !== "happy" && (at(LOOP * 0.3) || at(LOOP * 0.3 + 0.24))) blinkPhase = 0;
      if (state === "idle" && at(LOOP * 0.55)) curiousUntil = t + Math.min(1.6, LOOP * 0.3);
      steps += 1;
    }

    function advance(dt) {
      if (LOOP) { loopEvents(dt); t = steps * dt; stateT = t; } else { t += dt; stateT += dt; }
      if (returnTo && t > returnAt) { const r = returnTo; returnTo = null; setState(r); }
      const S = STATES[state], m = reduce ? 0.35 : 1;
      for (const k in cur) cur[k] = damp(cur[k], S[k], 5, dt);

      let lx = look.tx, ly = look.ty;
      if (LOOP) { lx = 0; ly = 0; }
      else if (t - look.last > 2.5) { if (t > glanceAt) { glance.x = (rand() - 0.5) * 1.2; glance.y = (rand() - 0.4) * 0.7; glanceAt = t + 1.3 + rand() * 2.6; } lx = glance.x; ly = glance.y; }
      if (state === "think") { lx = 0.6; ly = 0.75; } if (state === "sleep") { lx = 0; ly = -0.2; } if (state === "gentle") { lx *= 0.3; ly = -0.3; }
      look.x = damp(look.x, lx, 6, dt); look.y = damp(look.y, ly, 6, dt);

      if (!LOOP && state === "idle" && t > curiousAt) { curiousUntil = t + 1.6; curiousAt = t + 7 + rand() * 6; }
      if (!LOOP && state === "idle" && t > hopAt) { hopT = 0; hopAt = t + 10 + rand() * 8; }
      const curious = state === "idle" && t < curiousUntil ? 0.16 : 0;

      let y = Math.abs(Math.sin(t * W(2.2))) * 0.03 * cur.bounce * m;
      if (hopT >= 0) { hopT += dt; const pr = hopT / 0.5; y += Math.sin(Math.min(1, pr) * Math.PI) * 0.28 * m; if (pr >= 1) hopT = -1; }
      if (state === "happy") y = Math.abs(Math.sin(stateT * W(6.5))) * 0.4 * m;
      rig.position.y = damp(rig.position.y, y, 18, dt);
      squash = Math.max(0, squash - dt * 3); const sq = squash > 0 ? Math.sin(squash * Math.PI) * 0.1 : 0;
      const breathe = Math.sin(t * W(1.9)) * 0.012 * m;
      bot.scale.set(1 + sq * 0.6, 1 - sq + breathe, 1 + sq * 0.6);
      if (spin > 0) { const st = Math.min(spin, dt * 9); spin -= st; rig.rotation.y += st; } else rig.rotation.y = damp(rig.rotation.y % (Math.PI * 2), look.x * 0.25, 4, dt);
      bot.rotation.z = damp(bot.rotation.z, (state === "happy" || state === "talk") ? Math.sin(t * W(state === "happy" ? 13 : 5)) * 0.06 * m : Math.sin(t * W(0.7)) * 0.02 * m, 8, dt);
      blob.scale.setScalar(1 - rig.position.y * 0.6); blob.material.opacity = THREE.MathUtils.clamp(1 - rig.position.y * 1.6, 0.35, 1);

      legs.forEach((l, i) => { const s = i ? 1 : -1; let lift = 0; if (state === "talk") lift = Math.max(0, Math.sin(t * W(5) + i * Math.PI)) * 0.06 * m;
        l.position.y = damp(l.position.y, -1.15 + lift, 12, dt); l.rotation.x = damp(l.rotation.x, state === "happy" ? -0.35 : 0, 8, dt); l.rotation.z = damp(l.rotation.z, state === "happy" ? 0.15 * s : 0, 8, dt); });

      head.rotation.y = damp(head.rotation.y, look.x * 0.45, 5, dt);
      head.rotation.x = damp(head.rotation.x, -look.y * 0.25 + cur.lean + (state === "talk" ? Math.sin(t * W(7)) * 0.04 * m : 0), 5, dt);
      head.rotation.z = damp(head.rotation.z, cur.tilt + curious + (state === "happy" ? Math.sin(stateT * W(6.5)) * 0.1 * m : 0), 5, dt);

      const yaw = head.rotation.y; const force = (-(yaw - lastYaw) / Math.max(dt, 0.001)) * 0.3; lastYaw = yaw;
      antVel += (force - antAngle * 40 - antVel * 4.5) * dt; antAngle += antVel * dt;
      antenna.rotation.z = -0.15 + antAngle + Math.sin(t * W(2.1)) * 0.05 * m;
      let ti = cur.tip; if (state === "listen") ti *= 0.7 + 0.4 * Math.sin(t * W(8)); if (state === "think") ti *= Math.sin(t * W(9)) > 0 ? 1 : 0.3;
      tipM.emissiveIntensity = ti; tipLight.intensity = ti * 0.4;

      const [L, R] = arms; let zl = -0.3, zr = 0.3, xl = 0, xr = 0;
      zl += Math.sin(t * W(1.9)) * 0.04 * m; zr -= Math.sin(t * W(1.9) + 1) * 0.04 * m;
      if (state === "listen") { zl = -2.55; xl = -0.35; }
      if (state === "think") { zr = 2.1; xr = -0.9; }
      if (state === "talk") { zl = -0.7 - Math.abs(Math.sin(t * W(4))) * 0.6 * m; zr = 0.7 + Math.abs(Math.sin(t * W(4) + 1.4)) * 0.6 * m; xl = xr = -0.3; }
      if (state === "happy") { zr = 2.6 + Math.sin(t * W(12)) * 0.35 * m; zl = -2.3 - Math.sin(t * W(9)) * 0.3 * m; }
      if (state === "proud") { zl = -1.0; zr = 1.0; xl = xr = -1.1; }
      if (state === "idle" && Math.sin(t * W(0.37)) > 0.985) zr = 2.5 + Math.sin(t * W(12)) * 0.3;
      if (state === "sleep") { zl = -0.12; zr = 0.12; }
      L.p.rotation.z = damp(L.p.rotation.z, zl, 8, dt); R.p.rotation.z = damp(R.p.rotation.z, zr, 8, dt);
      L.p.rotation.x = damp(L.p.rotation.x, xl, 8, dt); R.p.rotation.x = damp(R.p.rotation.x, xr, 8, dt);

      heartM.emissiveIntensity = 0.7 + 0.5 * Math.max(0, Math.sin(t * W(state === "proud" ? 6 : 2.6)));
      heart.scale.setScalar(state === "proud" ? 1.2 + 0.2 * Math.sin(t * W(6)) : 1);

      if (!LOOP && t > blinkAt && blinkPhase < 0) blinkPhase = 0;
      let blink = 1; if (blinkPhase >= 0) { blinkPhase += dt / 0.13; blink = Math.abs(Math.cos(blinkPhase * Math.PI)); if (blinkPhase >= 1) { blinkPhase = -1; blinkAt = t + (rand() < 0.2 ? 0.22 : 2 + rand() * 3.5); blink = 1; } }
      let leftOpen = 1; if (!LOOP && state === "idle" && t > winkAt) { if (t < winkAt + 0.35) leftOpen = 0.12; else winkAt = t + 14 + rand() * 10; }
      Object.assign(F, { eyes: S.eyes, mouth: S.mouth, open: blink, leftOpen, size: cur.size, gap: cur.gap, blush: cur.blush, lx: look.x, ly: look.y });
      F.talk = 0.2 + 0.8 * Math.abs(Math.sin(t * W(11)) * 0.6 + Math.sin(t * W(17.3)) * 0.4);
      if (state === "think") F.open *= 0.85;
      drawFace(F, t);

      tip.getWorldPosition(tmp);
      rings.forEach((r, i) => { if (state !== "listen") { r.material.opacity = damp(r.material.opacity, 0, 8, dt); return; } const p = (t * C(0.9) + i / 3) % 1; r.position.copy(tmp); r.scale.setScalar(1 + p * 4.5); r.material.opacity = (1 - p) * 0.8; r.lookAt(camera.position); });
      zs.forEach((z, i) => { if (state !== "sleep") { z.material.opacity = damp(z.material.opacity, 0, 8, dt); return; } const p = (t * C(0.35) + i / 3) % 1; z.position.set(tmp.x + 0.3 + p * 0.7, tmp.y + p * 1.0, tmp.z); z.material.opacity = Math.sin(p * Math.PI) * 0.95; z.scale.setScalar(0.22 + p * 0.28); });
      confetti.forEach((c) => { const u = c.userData; if (u.life <= 0) { c.material.opacity = 0; return; } u.life -= dt; u.v.y -= 5.5 * dt; c.position.addScaledVector(u.v, dt); c.rotation.x += u.w.x * dt; c.rotation.y += u.w.y * dt; c.material.opacity = Math.min(1, u.life); });
    }

    function render() { renderer.render(scene, camera); }

    // Where the round eyes sit in the rendered image (pixels, top-left origin), for the Mac's live eye layer.
    function eyeAnchors() {
      scene.updateMatrixWorld(true);
      const [r, , , phiStart, phiLen, thetaStart, thetaLen] = SCREEN;
      const proj = (px, py) => {
        const phi = phiStart + (px / FC.width) * phiLen, theta = thetaStart + (py / FC.height) * thetaLen;
        const v = new THREE.Vector3(-r * Math.cos(phi) * Math.sin(theta), r * Math.cos(theta), r * Math.sin(phi) * Math.sin(theta));
        screen.localToWorld(v).project(camera);
        const w = renderer.domElement.width, h = renderer.domElement.height;
        return { x: (v.x + 1) / 2 * w, y: (1 - v.y) / 2 * h };
      };
      const e = eyeSpot, out = {};
      [["left", -1], ["right", 1]].forEach(([k, s]) => {
        const c = proj(e.cx + s * e.gap, e.cy), edge = proj(e.cx + s * e.gap + e.ew / 2, e.cy), top = proj(e.cx + s * e.gap, e.cy - e.eh / 2);
        out[k] = { x: +c.x.toFixed(2), y: +c.y.toFixed(2), w: +(2 * Math.hypot(edge.x - c.x, edge.y - c.y)).toFixed(2), h: +(2 * Math.hypot(top.x - c.x, top.y - c.y)).toFixed(2) };
      });
      out.color = EYE;
      return out;
    }

    // ---------- live loop (30 fps cap, pauses when hidden) ----------
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

    setLook(o.look);
    advance(1 / 60); render();
    return {
      setState, setLook, celebrate, start, stop, advance, render, eyeAnchors, resize,
      get state() { return state; },
      onState(f) { listeners.push(f); },
      domElement: renderer.domElement,
      dispose() { stop(); document.removeEventListener("visibilitychange", onVis); renderer.dispose(); },
    };
  }

  root.ClaudeMascot = { create, LOOKS, STATES };
})(typeof window !== "undefined" ? window : globalThis);
