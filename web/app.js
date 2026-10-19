// Maxie PWA: chat (typed or hold-to-talk), spoken replies, confirmations, memory, activity, settings, push.
"use strict";

const $ = (s) => document.querySelector(s);
const store = {
  get: (k, d = null) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};
const CONV = "phone";
// A browser signs in with the passcode (/signin.html) and carries a session cookie; nothing is kept here.
let signedIn = false;

// ---- status: a word (colour is only a second signal: signal-blue / ember / red / quiet) ----
const STATUS = {  // colour, word
  ready: ["blue", "Here"], listening: ["blue", "Listening"], thinking: ["orange", "Thinking"],
  speaking: ["blue", "Speaking"], waiting: ["orange", "Needs your OK"], offline: ["red", "Can't reach the hub"],
  token: ["red", "Signed out"], muted: ["grey", "Muted"], connecting: ["grey", "Connecting"],
};
function setStatus(key) {
  const [color, word] = STATUS[key];
  const el = $("#status");
  el.className = `status s-${color}`;
  el.querySelector(".s-word").textContent = word;
  document.body.dataset.mode = key;  // drives the Thread (styles.css)
  if (key === "offline") botState("sleep");
}

// ---- Maxie (the default look) or the Classic robot, live from the hub's /api/events ----
// The look comes from Settings › Maxie (GET /api/maxie), cached here so the right bot shows before the hub answers.
let bot = null, holdTimer = 0;
function botConfig() {
  try { return JSON.parse(store.get("maxie", "{}")) || {}; } catch { return {}; }
}
function makeBot(cfg) {
  const old = $("#bot");
  if (!old) return;
  const state = bot?.state || "idle";
  bot?.dispose();
  const canvas = old.cloneNode(false);  // a fresh WebGL context for the new character
  old.replaceWith(canvas);
  try {
    bot = MaxieBots.create(canvas, { character: cfg.character || "maxie", look: cfg.look || {}, framing: "body",
                                     floor: false, maxFps: 30, pixelRatio: Math.min(devicePixelRatio || 1, 2) });
    bot.setState(state);
    bot.start();
  } catch (e) {
    bot = null;
    const f = document.createElement("div"); f.className = "bot-fallback"; canvas.replaceWith(f);  // no WebGL
  }
}
makeBot(botConfig());
async function syncMaxie() {
  try {
    const m = await getJSON("/api/maxie");
    const cfg = { character: m.character, look: m.look };
    if (JSON.stringify(cfg) !== JSON.stringify(botConfig())) { store.set("maxie", JSON.stringify(cfg)); makeBot(cfg); }
    fillLookPicker(cfg);
  } catch { /* offline: keep the cached look */ }
}
function botState(state, hold = 0, base = "idle") {
  if (!bot) return;
  clearTimeout(holdTimer);
  bot.setState(state);
  if (hold > 0) holdTimer = setTimeout(() => bot.setState(base), hold * 1000);
}

// A rare idle moment from the hub (maxie.mood): a short hop of joy, or a quick glance.
const MOMENT_SECONDS = { hop: 2, glance: 1 };
function moment(kind) {
  if (document.body.dataset.mode !== "ready") return;
  botState("happy", MOMENT_SECONDS[kind] || 1);
}

async function followEvents() {
  for (let wait = 1000; ; wait = Math.min(wait * 2, 30000)) {
    if (signedIn) {
      try {
        const res = await api("/api/events");
        const reader = res.body.getReader(), dec = new TextDecoder();
        let buf = ""; wait = 1000;
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf("\n\n")) >= 0) {
            const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
            const line = chunk.split("\n").find((l) => l.startsWith("data: "));
            if (!line) continue;
            const ev = JSON.parse(line.slice(6));
            if (ev.type === "state") botState(ev.state, ev.hold, ev.base);
            if (ev.type === "moment") moment(ev.kind);
            if (ev.type === "confirm") addConfirm(ev);
            if (ev.type === "task" && !$("#tab-activity").hidden) loadTasks().catch(() => {});
          }
        }
      } catch { /* hub asleep or signed out: retry */ }
    }
    await new Promise((r) => setTimeout(r, wait));
  }
}

// ---- api ----
async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, credentials: "same-origin", headers: {
    "Content-Type": "application/json", ...(opts.headers || {}) } });
  if (res.status === 401) { setStatus("token"); location.replace("/signin.html"); throw new Error("bad token"); }
  if (!res.ok) throw new Error(`${res.status}`);
  return res;
}
const getJSON = async (p) => (await api(p)).json();

async function checkHealth() {
  try {
    const h = await (await fetch("/health", { cache: "no-store" })).json();
    $("#health").textContent = (h.demo ? "Demo hub, fake data. " : "The hub is up. ") +
      `Laya ${h.laya}, budget ${h.budget}, Google: ${h.google}, brain: ${h.brain}.`;
    const s = await getJSON("/api/settings");
    signedIn = true;
    setStatus(s.proactive_muted ? "muted" : "ready");
  } catch (e) {
    if (e.message !== "bad token") setStatus("offline");
  }
}

// ---- chat: every turn is a knot on the Thread; hover (or tap) a turn for Copy ----
function bubble(role, text = "", meta = "") {
  const li = document.createElement("li");
  li.className = `${role} knot`;
  const words = el("span", "t", text);
  const m = el("span", "meta", meta);
  const copy = el("button", "copy", "Copy");
  copy.type = "button";
  copy.onclick = (e) => { e.stopPropagation(); copyText(words.textContent, copy); };
  li.append(words, m, copy);
  li.onclick = () => { if (!String(getSelection())) li.classList.toggle("picked"); };
  $("#log").append(li);
  window.scrollTo(0, document.body.scrollHeight);  // the newest turn sits just above the composer
  return li;
}
const turnText = (li) => li.querySelector(".t").textContent;

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const t = el("textarea"); t.value = text; document.body.append(t); t.select();
    document.execCommand("copy"); t.remove();  // http:// on the LAN has no async clipboard
  }
  if (!button) return;
  const was = button.textContent;
  button.textContent = "Copied";
  setTimeout(() => { button.textContent = was; }, 1200);
}
function conversationText() {
  return [...$("#log").children].map((li) => (li.classList.contains("user") ? "You: " : "Maxie: ") + turnText(li)).join("\n\n");
}
$("#copy-all").onclick = () => copyText(conversationText(), $("#copy-all"));

const MODEL_NAMES = /(haiku|sonnet|opus|fable|mythos)/i;
const modelName = (id) => { const m = (id || "").match(MODEL_NAMES); return m ? m[1][0].toUpperCase() + m[1].slice(1).toLowerCase() : ""; };
function replyMeta(ev) {
  if (ev.direct) return "Done on your Mac";
  const name = modelName(ev.model);
  return name ? `Answered with ${name}` : "";
}

// First-run hint under the composer: shown until the first message, then never again.
const HINT_KEY = "composerHintSeen";
if (store.get(HINT_KEY) !== "1") $("#composer-hint").hidden = false;
function hintSeen() {
  if (store.get(HINT_KEY) === "1") return;
  store.set(HINT_KEY, "1");
  $("#composer-hint").classList.add("gone");
  setTimeout(() => { $("#composer-hint").hidden = true; }, 700);
}

let speakQueue = "";
function speak(text, flush = false) {
  if (!$("#speak").checked || !("speechSynthesis" in window)) return;
  speakQueue += text;
  // Speak whole sentences as they stream in, so the first words come out early.
  const parts = speakQueue.split(/(?<=[.!?])\s+/);
  speakQueue = flush ? "" : parts.pop();
  for (const p of flush ? parts.concat(speakQueue ? [speakQueue] : []) : parts) {
    if (!p.trim()) continue;
    const u = new SpeechSynthesisUtterance(p);
    u.onstart = () => { setStatus("speaking"); botState("talk"); speakingKnot(true); };
    u.onend = () => {
      if (speechSynthesis.pending) return;
      botState("idle"); speakingKnot(false);
      if (!$("#confirms").children.length) setStatus("ready");
    };
    speechSynthesis.speak(u);
  }
}

// While Maxie speaks, the knot of the reply being spoken glows.
function speakingKnot(on) {
  document.querySelectorAll("#log li.now").forEach((n) => n.classList.remove("now"));
  const last = [...$("#log").querySelectorAll("li.assistant")].pop();
  if (on && last) last.classList.add("now");
}

async function send(text) {
  text = text.trim();
  if (!text) return;
  if ("speechSynthesis" in window) speechSynthesis.cancel(); // barge-in: talking stops the voice
  hintSeen();
  bubble("user", text);
  const turn = bubble("assistant", "…");
  const out = turn.querySelector(".t");
  setStatus("thinking");
  let reply = "", meta = "";
  try {
    const res = await api("/api/chat", { method: "POST", body: JSON.stringify({ text, conversation: CONV, device: "pwa" }) });
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const ev = JSON.parse(buf.slice(0, nl)); buf = buf.slice(nl + 1);
        if (ev.type === "route") meta = replyMeta(ev);
        if (ev.type === "ack") speak(ev.text + " ");
        if (ev.type === "text") { reply += ev.text; out.textContent = reply; speak(ev.text); }
        if (ev.type === "confirm") { addConfirm(ev); setStatus("waiting"); }
        if (ev.type === "phone_action") runOnPhone(ev);
        if (ev.type === "error") { reply = ev.text; out.textContent = reply; setStatus("offline"); }
        if (ev.type === "done" && ev.text && !reply) { reply = ev.text; out.textContent = reply; speak(reply); }
      }
    }
    speak("", true);
    turn.querySelector(".meta").textContent = meta;
    setStatus($("#confirms").children.length ? "waiting" : "ready");
  } catch (e) {
    out.textContent = e.message === "bad token" ? "Signed out. Sign in again." :
      "Can't reach the hub. Is it running, and is this device on its network?";
    setStatus(e.message === "bad token" ? "token" : "offline");
  }
}

function runOnPhone(ev) {
  // The web app can only open links itself; the hub runs other direct actions on the Mac,
  // and the iOS Shortcut handles phone timers / music / apps.
  if (ev.action === "open_url") window.open(ev.args.url, "_blank");
}

$("#composer").addEventListener("submit", (e) => { e.preventDefault(); const t = $("#text").value; $("#text").value = ""; send(t); });

// ---- hold to talk (Web Speech where Safari allows; otherwise the keyboard's dictation mic) ----
const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null, heard = "";
function startTalk(e) {
  e.preventDefault();
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  if (!Rec) { $("#text").focus(); $("#text").placeholder = "Tap the mic on your keyboard to dictate"; return; }
  heard = "";
  rec = new Rec();
  rec.lang = "en-US"; rec.interimResults = true; rec.continuous = true;
  rec.onresult = (ev) => { heard = Array.from(ev.results).map((r) => r[0].transcript).join(" "); $("#text").value = heard; };
  rec.onerror = () => setStatus("ready");
  rec.start();
  $("#mic").classList.add("on");
  setStatus("listening");
  botState("listen");
  api("/api/events/state", { method: "POST", body: JSON.stringify({ state: "listen" }) }).catch(() => {});
}
function endTalk() {
  if (!rec) return;
  $("#mic").classList.remove("on");
  rec.onend = () => { const t = heard; $("#text").value = ""; rec = null; t ? send(t) : setStatus("ready"); };
  rec.stop();
}
$("#mic").addEventListener("pointerdown", startTalk);
$("#mic").addEventListener("pointerup", endTalk);
$("#mic").addEventListener("pointerleave", endTalk);

// ---- confirmations ----
function addConfirm(c) {
  if (document.querySelector(`[data-cid="${c.id}"]`)) return;
  for (const box of [$("#confirms"), $("#pending")]) {
    const card = document.createElement("div");
    card.className = "confirm knot now"; card.dataset.cid = c.id;
    card.innerHTML = `<div class="head">Needs your OK</div><div class="what"></div>
      ${c.after_untrusted ? `<div class="warn">This came up after reading an email or a web page. Check it carefully.</div>` : ""}
      <div class="actions"><button class="primary">Approve</button><button>Cancel</button></div>`;
    card.querySelector(".what").textContent = c.summary;
    const [yes, no] = card.querySelectorAll("button");
    yes.onclick = () => resolve(c.id, true);
    no.onclick = () => resolve(c.id, false);
    box.append(card);
  }
}
async function resolve(id, approve) {
  try {
    const r = await (await api(`/api/confirm/${id}`, { method: "POST", body: JSON.stringify({ approve }) })).json();
    bubble("assistant", approve ? `Done: ${r.result}` : "Cancelled.");
  } catch (e) { bubble("assistant", "That confirmation expired or was already answered."); }
  document.querySelectorAll(`[data-cid="${id}"]`).forEach((n) => n.remove());
  setStatus("ready");
}

// ---- tabs ----
document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("nav button").forEach((x) => x.setAttribute("aria-selected", x === b));
  document.querySelectorAll(".tab").forEach((t) => { t.hidden = t.id !== `tab-${b.dataset.tab}`; });
  ({ memory: loadMemory, activity: loadActivity, settings: loadSettings }[b.dataset.tab] || (() => {}))();
}));

async function loadHistory() {
  try {
    for (const m of await getJSON(`/api/history?conversation=${CONV}&limit=40`)) bubble(m.role, m.text);
    for (const c of await getJSON("/api/confirmations")) addConfirm(c);
  } catch { /* offline: health shows it */ }
}

function el(tag, cls = "", text = "") {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text) n.textContent = text;
  return n;
}
let FACTS = [];
const FRESH_FACT_S = 120;  // facts newer than this glow once ("remembered")
async function loadMemory() {
  const m = await getJSON("/api/memory");
  $("#memory").value = m.markdown || "## me\n- \n\n## people\n- \n\n## goals\n- \n\n## decisions\n- \n\n## preferences\n- ";
  FACTS = m.facts || [];
  renderFacts();
}
function factDate(ts) {
  return ts ? new Date(ts * 1000).toLocaleDateString([], { month: "short", day: "numeric" }) : "";
}
function renderFacts() {
  const q = $("#memory-q").value.trim().toLowerCase();
  const shown = q ? FACTS.filter((f) => f.text.toLowerCase().includes(q)) : FACTS;
  const fresh = Date.now() / 1000 - FRESH_FACT_S;
  $("#facts").replaceChildren(...shown.map((f) => {
    const d = el("div", `fact knot small${f.created > fresh ? " glow" : ""}`);
    const when = factDate(f.created);
    d.append(el("div", "", f.text), el("div", "meta", `${f.source || "You told me"}${when ? ` on ${when}` : ""}, under ${f.topic}`));
    return d;
  }));
  if (!shown.length) $("#facts").append(el("div", "meta", FACTS.length ? "No match." : "Nothing yet."));
}
$("#memory-q").oninput = renderFacts;
$("#save-memory").onclick = async () => {
  const r = await (await api("/api/memory", { method: "PUT", body: JSON.stringify({ markdown: $("#memory").value }) })).json();
  $("#memory").value = r.markdown;
  $("#memory-note").textContent = r.refused.length ? `Not saved (private): ${r.refused.join("; ")}` : "Saved.";
  loadMemory();
};

// A task is a knot on the Thread; open it and its steps fork off as small knots.
const TASK_WORD = { done: "Done", running: "Running", paused: "Paused", stopped: "Stopped", error: "Didn't finish" };
function taskLine(t) {
  const n = t.steps.length;
  const model = modelName(t.model);
  const parts = [
    TASK_WORD[t.status] || TASK_WORD.error,
    n ? `${n} step${n === 1 ? "" : "s"}` : "",
    t.total_ms >= 100 ? `${(t.total_ms / 1000).toFixed(1)} s` : "",
    model ? `with ${model}` : "",
    t.cost > 0 ? `$${t.cost.toFixed(4)}` : "",
  ].filter(Boolean);
  return `${parts.join(", ")}, at ${new Date(t.ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
}
const stepNode = (s) => el("div", `knot small${s.ok ? "" : " failed"}`, `${s.name.replace(/_/g, " ")}${s.ok ? "" : ", failed"}, ${s.ms} ms`);
function taskNode(t, current = false) {
  const li = el("li", `task knot${current ? " open now" : ""}`);
  const head = el("div", "head");
  head.append(el("div", "what", t.text), el("div", `meta word-${t.status}`, taskLine(t)));
  const steps = el("div", "steps");
  steps.append(...(t.steps.length ? t.steps.map(stepNode) : [el("div", "", "No tool steps")]));
  li.append(head, steps, el("div", "reply", t.reply || ""));
  if (current) {
    const act = el("div", "actions");
    const pause = el("button", "", t.status === "paused" ? "Resume" : "Pause");
    pause.onclick = () => taskControl(t.id, t.status === "paused" ? "resume" : "pause");
    const cancel = el("button", "danger", "Cancel");
    cancel.onclick = () => taskControl(t.id, "cancel");
    act.append(pause, cancel); li.append(act);
  } else {
    head.onclick = () => li.classList.toggle("open");
  }
  return li;
}
async function taskControl(id, verb) { await api(`/api/tasks/${id}/${verb}`, { method: "POST" }).catch(() => {}); loadTasks(); }
async function loadTasks() {
  const r = await getJSON("/api/tasks?limit=30");
  const cur = $("#current-task"); cur.innerHTML = "";
  if (r.current) { const box = el("ol", "plain"); box.append(taskNode(r.current, true)); cur.append(box); }
  $("#tasks").innerHTML = "";
  for (const t of r.tasks.filter((t) => !r.current || t.id !== r.current.id)) $("#tasks").append(taskNode(t));
}
async function loadRules() {
  const r = await getJSON("/api/rules");
  const box = $("#rules"); box.innerHTML = "";
  let group = "";
  for (const rule of r.rules) {
    if (rule.group !== group) { group = rule.group; const h = document.createElement("div"); h.className = "rule-group"; h.textContent = group; box.append(h); }
    const row = document.createElement("div"); row.className = "rule";
    row.innerHTML = `<div class="name"></div>`;
    const name = row.querySelector(".name"); name.textContent = rule.name.replace(/_/g, " ");
    const small = document.createElement("small"); small.textContent = rule.description; name.append(small);
    const sel = document.createElement("select");
    for (const [v, label] of [["allow", "Allow"], ["ask", "Ask me"], ["block", "Block"]]) sel.append(new Option(label, v, false, v === rule.rule));
    sel.onchange = () => api("/api/rules", { method: "PUT", body: JSON.stringify({ name: rule.name, rule: sel.value }) }).catch(() => loadRules());
    row.append(sel); box.append(row);
  }
}

async function loadActivity() {
  loadTasks().catch(() => {});
  const [routes, audit] = await Promise.all([getJSON("/api/routes?limit=30"), getJSON("/api/audit?limit=60")]);
  $("#routes").innerHTML = "";
  for (const r of routes.recent) {
    const li = document.createElement("li");
    const sel = document.createElement("select");
    for (const name of routes.routes) sel.append(new Option(name.replace(/_/g, " "), name, false, name === (r.label || r.route)));
    sel.onchange = () => api("/api/routes/label", { method: "POST", body: JSON.stringify({ id: r.id, route: sel.value }) });
    li.textContent = `“${r.text}” goes to`; li.append(sel);
    if (r.label) li.append(el("span", "meta", " labelled"));
    $("#routes").append(li);
  }
  $("#audit").innerHTML = "";
  for (const a of audit) {
    const li = document.createElement("li");
    const when = new Date(a.ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    li.append(el("div", "", `${a.actor} ${a.action.replace(/_/g, " ")}: ${a.outcome}${a.why ? `, ${a.why}` : ""}`), el("div", "audit-when", when));
    $("#audit").append(li);
  }
}

// Server lines use middle dots; the design reads them as plain phrases.
const plain = (line) => String(line).replace(/ · /g, ", ").replace(/ ✓(?=,|$)/g, ", ok").replace(/ ✓ /g, " ok, ");

async function loadSettings() {
  loadRules().catch(() => {});
  try {
    const [s, b, m] = await Promise.all([getJSON("/api/settings"), getJSON("/api/budget"), getJSON("/api/models")]);
    $("#budget").value = s.monthly_budget_usd; $("#quiet-start").value = s.quiet_start;
    $("#quiet-end").value = s.quiet_end; $("#muted").checked = s.proactive_muted;
    $("#budget-line").textContent = plain(b.line);
    $("#auto-upgrade").checked = s.auto_upgrade_models;
    const active = Object.entries(m.active).map(([t, id]) => `${t}: ${id}${m.price_guessed[t] ? " (new model, price guessed)" : ""}`);
    $("#models-line").textContent = [...m.lines, ...active, s.auto_upgrade_models ? "" : "Pinned."].filter(Boolean).join("\n");
    const sp = await getJSON("/api/latency");
    $("#speed-line").textContent = plain(sp.line) + (Object.values(sp.today).some((k) => k.n && !k.ok) ? "\nOver target today." : "");
    $("#speed-line").classList.toggle("bad", Object.values(sp.today).some((k) => k.n && !k.ok));
    const bk = await getJSON("/api/backup");
    $("#backup-line").textContent = [bk.line, ...bk.app_lines, ...bk.warnings].map(plain).join("\n");
    $("#backup-line").classList.toggle("bad", bk.stale || !bk.last_ok);
  } catch { /* shown in status */ }
}
$("#save-settings").onclick = async () => {
  await api("/api/settings", { method: "PUT", body: JSON.stringify({ monthly_budget_usd: Number($("#budget").value),
    quiet_start: $("#quiet-start").value, quiet_end: $("#quiet-end").value, proactive_muted: $("#muted").checked,
    auto_upgrade_models: $("#auto-upgrade").checked }) });
  loadSettings(); checkHealth();
};
$("#pin-models").onclick = async () => { await api("/api/models/pin", { method: "POST" }); loadSettings(); };
$("#speak").checked = store.get("speak", "1") === "1";
$("#speak").onchange = () => store.set("speak", $("#speak").checked ? "1" : "0");
$("#stop").onclick = async () => { if ("speechSynthesis" in window) speechSynthesis.cancel(); await api("/api/stop", { method: "POST" }); bubble("assistant", "Stopped."); };

// ---- push (iOS 16.4+, only when installed to the home screen) ----
$("#enable-push").onclick = async () => {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    alert("Add Maxie to your Home Screen first: Share, then Add to Home Screen. Then open it from there."); return;
  }
  if (await Notification.requestPermission() !== "granted") return;
  const { key } = await getJSON("/api/push/key");
  if (!key) { alert("Push keys aren't set on the hub yet."); return; }
  const reg = await navigator.serviceWorker.ready;
  const raw = Uint8Array.from(atob(key.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - key.length % 4) % 4)), (c) => c.charCodeAt(0));
  const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: raw });
  await api("/api/push/subscribe", { method: "POST", body: JSON.stringify(sub.toJSON()) });
  $("#enable-push").textContent = "Notifications on";
};

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js");
navigator.serviceWorker?.addEventListener("message", (e) => {
  if (e.data?.mood === "happy" || e.data?.mood === "proud") botState(e.data.mood, 8);
});

// Maxie's look (the same setting as the Mac's Settings › Maxie; the Mac re-renders its notch loops).
const lookSel = $("#look");
function fillLookPicker(cfg) {
  const now = cfg.character === "classic" ? "classic" : (cfg.look?.fur || "mochi");
  lookSel.replaceChildren(...MaxieBots.choices().map((c) => new Option(c.name, c.value, false, c.value === now)));
}
fillLookPicker(botConfig());
lookSel.onchange = async () => {
  const v = lookSel.value;
  const body = v === "classic" ? { character: "classic" } : { character: "maxie", look: { ...(botConfig().look || {}), fur: v } };
  try { await api("/api/maxie", { method: "PUT", body: JSON.stringify(body) }); } catch { /* offline: next sync */ }
  syncMaxie();
};

// The phone in your hand means you're up: Maxie sleeps when you sleep, not by the clock.
function sayActive() {
  if (document.visibilityState !== "visible") return;
  api("/api/presence/active", { method: "POST", body: JSON.stringify({ source: "phone" }) }).catch(() => { /* offline */ });
}
document.addEventListener("visibilitychange", sayActive);

checkHealth(); loadHistory(); followEvents(); syncMaxie(); sayActive();
setInterval(checkHealth, 30000);
