/* Maxie's fur toolkit: shell-texture fur. Shared by the live character (web/maxie.js), the notch
 * loops and icons (scripts/render_mascot.py, scripts/make_icons.py) and the concept renders (scripts/maxie/).
 * A furry part is SHELLS copies of one geometry pushed out along its normals; each shell keeps only the pixels
 * inside a strand (a jittered 3-D cell pattern), so strands taper, droop with gravity, feather at the tips
 * (alpha to coverage, strand space) and sway like hair when he moves (animateFur). */
/* global THREE */
(function (root) {
'use strict';

const FUR_VERT = `
uniform float h, furLen, clearR;
uniform vec3 gravity, motion;
uniform vec4 wind;
uniform vec2 windPhase;  // the breeze's two phases, wrapped to 0..2pi on the CPU (bit-identical every loop)
uniform vec3 clearPts[4];
varying vec3 vBase, vNormal, vViewPos;
varying float vScale;
float clearScale(vec3 p) {
  float s = 1.0;
  for (int i = 0; i < 4; i++) s = min(s, smoothstep(clearR * 0.55, clearR * 1.5, distance(p, clearPts[i])));
  return s;
}
void main() {
  vec3 n = normalize(normal);
  float s = clearScale(position);
  // Hair, not dots: strands lean with a slow two-sine breeze and trail behind his motion. The offset grows with
  // h^2, so roots stay put and tips flow; wind.w (how much he is moving) makes it settle when he is still.
  float wave = sin(windPhase.x + dot(position, vec3(3.1, 1.7, 2.3))) * 0.6
             + sin(windPhase.y + dot(position, vec3(-1.3, 2.9, 0.7))) * 0.4;
  vec3 sway = wind.xyz * wave * wind.w - motion;
  sway -= n * dot(sway, n) * 0.6;  // mostly along the surface
  vec3 p = position + n * furLen * h * s + (gravity * pow(h, 2.2) + sway * h * h) * furLen * s;
  vBase = position;
  vScale = s;
  vNormal = normalize(normalMatrix * n);
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  vViewPos = mv.xyz;
  gl_Position = projectionMatrix * mv;
}`;

// Strands come from a jittered 3-D cell pattern (Worley). The per-cell data (feature point, random length) is
// baked once into a 64^3 texture with the same hash the shader used to compute per fragment, so the pattern
// is unchanged and each fragment does 27 texture reads instead of 108 hashes.
const FUR_FRAG = `
precision highp sampler3D;
uniform float h, density, thickness, waviness;
uniform sampler3D cells;
uniform vec3 colRoot, colTip, rimColor, glow;
uniform vec3 tintC[4];
uniform vec4 tintCol[4];
uniform float tintR[4];
varying vec3 vBase, vNormal, vViewPos;
varying float vScale;
void main() {
  vec3 q = vBase * density;
  q += h * waviness * vec3(sin(vBase.y * 11.0 + vBase.z * 5.0), cos(vBase.x * 9.0 + vBase.y * 4.0), sin(vBase.x * 7.0));
  vec3 cell = floor(q);
  float best = 9.0, rnd = 0.0;
  for (int x = -1; x <= 1; x++) for (int y = -1; y <= 1; y++) for (int z = -1; z <= 1; z++) {
    vec3 c = cell + vec3(float(x), float(y), float(z));
    vec4 v = texture(cells, (mod(c, 64.0) + 0.5) / 64.0);
    float d = length(q - (c + v.xyz));
    if (d < best) { best = d; rnd = v.w; }
  }
  float len = (0.55 + 0.45 * rnd) * max(vScale, 0.18);
  float alpha = 1.0;
  if (h > 0.0) {
    if (h > len) discard;
    // Soft strands in strand space (never screen space): a soft edge across the strand and a fade to the tip,
    // turned into coverage by MSAA (alpha to coverage), so tips feather instead of popping in and out as dots.
    float edge = thickness * pow(1.0 - h / len, 0.35);
    // Antialiased across the strand by its own screen-space footprint (fwidth), so a strand moving by a fraction
    // of a pixel shifts smoothly instead of flickering between a dot and nothing.
    // Strands never get thinner than about a pixel, so they read as hairs (as bright as before), not dots.
    float aa = max(fwidth(best), 1e-4);
    float w = max(edge, aa * 0.875);  // 0.875 px keeps his overall colour the same as before (fur_check rgb)
    alpha = 1.0 - smoothstep(w - aa * 0.5, w + aa * 0.5, best);
    float tip = clamp(1.0 - smoothstep(0.55, 1.0, h / len) + 0.15, 0.0, 1.0);  // the old tips' average coverage
    alpha *= mix(1.0, tip, step(0.6, vScale));  // soft tips, not on the velvet (eyes, muzzle)
    if (alpha < 0.04) discard;
  }
  float velvet = 1.0 - smoothstep(0.15, 0.7, vScale);  // short fur where it is cleared (eyes, muzzle)
  vec3 base = mix(colRoot, colTip, smoothstep(0.0, 1.0, h));
  base = mix(base, mix(colTip, base, 0.35), velvet);
  for (int i = 0; i < 4; i++) {
    float t = 1.0 - smoothstep(tintR[i] * 0.6, tintR[i], distance(vBase, tintC[i]));
    base = mix(base, tintCol[i].rgb * mix(0.82, 1.0, max(h, velvet)), t * tintCol[i].a);
  }
  base *= 0.88 + 0.24 * rnd;
  vec3 N = normalize(vNormal), V = normalize(-vViewPos), L = normalize(vec3(-0.45, 0.75, 0.55));
  float diff = 0.68 + 0.36 * (dot(N, L) * 0.5 + 0.5);
  float ao = mix(0.6, 1.0, pow(clamp(h / max(len, 0.05), 0.0, 1.0), 0.6));
  ao = mix(ao, 0.97, velvet);
  float rim = pow(1.0 - max(dot(N, V), 0.0), 2.6) * h;
  float sheen = pow(max(dot(N, normalize(L + V)), 0.0), 20.0) * 0.12 * h;
  vec3 col = base * diff * ao + rimColor * rim * 0.4 + sheen + glow * (0.35 + 0.65 * h);
  gl_FragColor = vec4(col * alpha, alpha);  // premultiplied, so feathered edges never glow
}`;

const hex = (c) => new THREE.Color(c);

/** The shader's old per-fragment hash, run once per cell here (float32 steps, like the GPU). */
function hash3(x, y, z) {
  const f = Math.fround, fr = (v) => f(v - Math.floor(v));
  let a = fr(f(f(x * 0.3183099) + 0.1)), b = fr(f(f(y * 0.3183099) + 0.1)), c = fr(f(f(z * 0.3183099) + 0.1));
  a = f(a * 17); b = f(b * 17); c = f(c * 17);
  return fr(f(f(f(a * b) * c) * f(f(a + b) + c)));
}

let CELLS = null;
/** 64^3 cells, RGBA8: the feature point inside the cell (xyz) and the strand's random length (w). */
function cellTexture() {
  if (CELLS) return CELLS;
  const N = 64, data = new Uint8Array(N * N * N * 4);
  for (let z = 0; z < N; z++) for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
    const cx = x >= 32 ? x - 64 : x, cy = y >= 32 ? y - 64 : y, cz = z >= 32 ? z - 64 : z;  // cells -32..31
    const i = ((z * N + y) * N + x) * 4;
    data[i] = Math.round(hash3(cx, cy, cz) * 255);
    data[i + 1] = Math.round(hash3(cx + 11.13, cy + 11.13, cz + 11.13) * 255);
    data[i + 2] = Math.round(hash3(cx + 27.71, cy + 27.71, cz + 27.71) * 255);
    data[i + 3] = Math.round(hash3(cx + 5.31, cy + 5.31, cz + 5.31) * 255);
  }
  const tex = new THREE.DataTexture3D(data, N, N, N);
  tex.format = THREE.RGBAFormat;
  tex.type = THREE.UnsignedByteType;
  tex.minFilter = tex.magFilter = THREE.NearestFilter;
  tex.wrapS = tex.wrapT = tex.wrapR = THREE.RepeatWrapping;
  tex.unpackAlignment = 1;
  tex.needsUpdate = true;
  CELLS = tex;
  return tex;
}

/** A fur-covered part: SHELLS copies of one geometry pushed out along the normals. Gravity, time, wind and
 *  motion are one set of uniforms shared by every shell, so a frame updates them once per part. */
function furry(geometry, fur, shells) {
  const group = new THREE.Group();
  const clear = (fur.clear || []).concat([[99, 99, 99], [99, 99, 99], [99, 99, 99], [99, 99, 99]]).slice(0, 4);
  const tints = (fur.tints || []).concat(Array(4).fill({ at: [99, 99, 99], r: 0.01, color: '#000', amount: 0 })).slice(0, 4);
  const shared = {
    gravity: { value: new THREE.Vector3(0, -fur.droop, 0) }, motion: { value: new THREE.Vector3() },
    wind: { value: new THREE.Vector4(0.5, 0.15, 0.3, 0) }, windPhase: { value: new THREE.Vector2() },
    glow: { value: new THREE.Vector3() }, cells: { value: cellTexture() },
  };
  for (let i = 0; i < shells; i++) {
    const m = new THREE.ShaderMaterial({
      vertexShader: FUR_VERT, fragmentShader: FUR_FRAG,
      uniforms: {
        ...shared,
        h: { value: i / (shells - 1) }, furLen: { value: fur.len }, density: { value: fur.density || 30 },
        thickness: { value: fur.thickness || 0.34 }, waviness: { value: fur.wave ?? 0.5 },
        colRoot: { value: hex(fur.root) }, colTip: { value: hex(fur.tip) }, rimColor: { value: hex(fur.rim || '#fff4e6') },
        clearPts: { value: clear.map((p) => new THREE.Vector3(...p)) }, clearR: { value: fur.clearR || 0.3 },
        tintC: { value: tints.map((t) => new THREE.Vector3(...t.at)) }, tintR: { value: tints.map((t) => t.r) },
        tintCol: { value: tints.map((t) => { const c = hex(t.color); return new THREE.Vector4(c.r, c.g, c.b, t.amount); }) },
      },
    });
    // Feathered strand edges: MSAA coverage from alpha, and premultiplied "over" blending onto the shells
    // underneath (drawn inner to outer), so the body keeps its colour and the outline has no glow or specks.
    m.alphaToCoverage = true;
    m.blending = THREE.CustomBlending;
    m.blendSrc = m.blendSrcAlpha = THREE.OneFactor;
    m.blendDst = m.blendDstAlpha = THREE.OneMinusSrcAlphaFactor;
    group.add(new THREE.Mesh(geometry, m));
  }
  group.userData.fur = fur;
  group.userData.shared = shared;
  return group;
}

const _inv = new THREE.Matrix4(), _p = new THREE.Vector3(), _q = new THREE.Vector3(), _v = new THREE.Vector3();

/** Gravity is given in world space; each part needs it in its own coordinates. */
function settleGravity(root) {
  root.updateMatrixWorld(true);
  root.traverse((o) => {
    if (!o.userData.fur) return;
    const inv = _inv.copy(o.matrixWorld).invert();
    const g = new THREE.Vector3(0, -1, 0).transformDirection(inv).multiplyScalar(o.userData.fur.droop);
    const fwd = new THREE.Vector3(0, 0, 1).transformDirection(inv).multiplyScalar(o.userData.fur.lean || 0);
    o.userData.shared.gravity.value.copy(g.add(fwd));
  });
}

/** Moves the fur like hair: each part's tips trail behind how fast it moves (a point on its top, so turns and
 *  wags count too) and a slow breeze ripples through, scaled by that motion so still fur settles.
 *  `W` maps a frequency to whole cycles per loop (seamless notch loops); `calm` scales it all (reduced motion). */
function animateFur(root, t, dt, W, calm = 1) {
  settleGravity(root);
  const TAU = Math.PI * 2, ph1 = (t * W(0.35)) % TAU, ph2 = (t * W(0.6)) % TAU;
  root.traverse((o) => {
    if (!o.userData.fur || dt <= 0) return;
    const u = o.userData, sh = u.shared;
    o.localToWorld(_p.set(0, 0, 0));
    o.localToWorld(_q.set(0, 1, 0));
    _p.add(_q).multiplyScalar(0.5);
    if (!u.lastPos) u.lastPos = _p.clone();
    _v.copy(_p).sub(u.lastPos).divideScalar(dt);
    u.lastPos.copy(_p);
    u.vel = (u.vel || new THREE.Vector3()).lerp(_v, 1 - Math.exp(-6 * dt));  // smoothed, so it flows
    const speed = Math.min(u.vel.length(), 3);
    const local = _q.copy(u.vel).transformDirection(_inv.copy(o.matrixWorld).invert()).multiplyScalar(Math.min(speed, 1.5) * 0.35 * calm);
    sh.motion.value.copy(local);
    sh.windPhase.value.set(ph1, ph2);
    sh.wind.value.w = (0.04 + Math.min(speed, 1.2) * 0.5) * calm;
  });
}

/** The glow uniform (listening): shared per part. */
function setFurGlow(root, rgb) {
  root.traverse((o) => { if (o.userData.shared) o.userData.shared.glow.value.set(rgb[0], rgb[1], rgb[2]); });
}

function sphere(r, w = 96, hgt = 64) { return new THREE.SphereGeometry(r, w, hgt); }

function eye(r, look = [0, 0]) {
  const g = new THREE.Group();
  const ball = new THREE.Mesh(sphere(r, 48, 32), new THREE.MeshPhongMaterial({ color: '#17100d', specular: '#ffffff', shininess: 140 }));
  ball.scale.set(1, 1.12, 0.62);
  g.add(ball);
  const iris = new THREE.Mesh(new THREE.CircleGeometry(r * 0.62, 40), new THREE.MeshBasicMaterial({ color: '#3a2418', transparent: true, opacity: 0.55 }));
  iris.position.set(look[0] * r, look[1] * r - r * 0.12, r * 0.6);
  g.add(iris);
  const big = new THREE.Mesh(sphere(r * 0.3, 24, 16), new THREE.MeshBasicMaterial({ color: '#ffffff' }));
  big.position.set(-r * 0.32, r * 0.38, r * 0.55);
  g.add(big);
  const small = new THREE.Mesh(sphere(r * 0.13, 16, 12), new THREE.MeshBasicMaterial({ color: '#ffffff' }));
  small.position.set(r * 0.3, -r * 0.3, r * 0.58);
  g.add(small);
  return g;
}

function smile(width, color = '#3a2420') {
  const arc = Math.PI * 0.7;
  const t = new THREE.Mesh(new THREE.TorusGeometry(width, width * 0.13, 10, 32, arc), new THREE.MeshPhongMaterial({ color, shininess: 30 }));
  t.rotation.z = -Math.PI / 2 - arc / 2;
  return t;
}

function headphones(radius, cupR, colors) {
  const g = new THREE.Group();
  const shell = new THREE.MeshStandardMaterial({ color: colors.cup, roughness: 0.38, metalness: 0.08 });
  const band = new THREE.Mesh(new THREE.TorusGeometry(radius, cupR * 0.17, 16, 64, Math.PI),
    new THREE.MeshStandardMaterial({ color: colors.band, roughness: 0.45, metalness: 0.15 }));
  g.add(band);
  const pad = new THREE.Mesh(new THREE.TorusGeometry(radius * 0.62, cupR * 0.16, 12, 40, Math.PI * 0.7),
    new THREE.MeshStandardMaterial({ color: colors.cushion || colors.band, roughness: 0.9 }));
  pad.rotation.z = Math.PI * 0.15;
  pad.position.y = radius * 0.4;
  pad.scale.set(1.15, 1, 1.4);
  if (colors.topPad) g.add(pad);
  for (const side of [-1, 1]) {
    const cup = new THREE.Group();
    const body = new THREE.Mesh(new THREE.CylinderGeometry(cupR, cupR * 0.94, cupR * 0.7, 48), shell);
    body.rotation.z = Math.PI / 2;
    cup.add(body);
    const cushion = new THREE.Mesh(new THREE.TorusGeometry(cupR * 0.86, cupR * 0.22, 14, 40),
      new THREE.MeshStandardMaterial({ color: colors.cushion || '#2b2b33', roughness: 0.95 }));
    cushion.rotation.y = Math.PI / 2;
    cushion.position.x = -side * cupR * 0.36;
    cup.add(cushion);
    const glow = new THREE.Mesh(new THREE.TorusGeometry(cupR * 0.62, cupR * 0.07, 10, 48), new THREE.MeshBasicMaterial({ color: colors.glow }));
    glow.rotation.y = Math.PI / 2;
    glow.position.x = side * cupR * 0.36;
    cup.add(glow);
    const cap = new THREE.Mesh(new THREE.CircleGeometry(cupR * 0.5, 40), new THREE.MeshStandardMaterial({ color: colors.capColor || colors.cup, roughness: 0.3 }));
    cap.rotation.y = side * Math.PI / 2;
    cap.position.x = side * cupR * 0.36;
    cup.add(cap);
    cup.position.x = side * (radius + cupR * 0.18);  // sits on the fur, not in it
    g.add(cup);
  }
  return g;
}

root.MaxieFur = { FUR_VERT, FUR_FRAG, hex, furry, settleGravity, animateFur, setFurGlow, sphere, eye, smile, headphones };
})(typeof window !== 'undefined' ? window : globalThis);
