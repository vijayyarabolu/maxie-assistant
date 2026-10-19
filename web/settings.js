// Maxie Settings: the status panel with Test buttons, keys, Connect Google, About me, access and the
// optional parts. Every call goes through the hub; keys and tokens are never read back from it.
"use strict";

const $ = (s) => document.querySelector(s);
const STATE_WORD = { ok: "Connected", missing: "Missing", failing: "Failing", off: "Off", demo: "Demo" };
const STATE_COLOR = { ok: "blue", missing: "orange", failing: "red", off: "grey", demo: "grey" };
let setup = null;

async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) } });
  if (res.status === 401) { location.replace(`/signin.html?next=${encodeURIComponent(location.pathname + location.hash)}`); throw new Error("signed out"); }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `The hub said ${res.status}.`);
  return body;
}
const post = (path, body = {}) => api(path, { method: "POST", body: JSON.stringify(body) });
const put = (path, body = {}) => api(path, { method: "PUT", body: JSON.stringify(body) });

function el(tag, cls = "", text = "") {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}
function note(id, text, bad = false) { const n = $(id); n.textContent = text; n.classList.toggle("bad", bad); }
function banner(text, bad = false) { const b = $("#banner"); b.textContent = text; b.hidden = !text; b.classList.toggle("bad", bad); }
const when = (ts) => ts ? new Date(ts * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "never";

// ---- status ----
function renderItems(items) {
  const list = $("#items");
  list.replaceChildren(...items.map((it) => {
    const li = el("li", `item st-${it.state}`);
    const head = el("div", "item-head");
    const name = el("span", "item-name", it.name);
    const word = el("span", `status s-${STATE_COLOR[it.state]}`, STATE_WORD[it.state] + (it.optional && it.state === "off" ? ", optional" : ""));
    head.append(name, word);
    if (it.testable) {
      const b = el("button", "small", "Test");
      b.onclick = () => test(it.id, b);
      head.append(b);
    }
    li.append(head, el("p", "hint", it.line));
    if (it.tested) li.append(el("p", `hint ${it.state === "failing" ? "bad" : ""}`, `Last test: ${it.tested}`));
    return li;
  }));
}

async function test(id, button) {
  if (button) button.textContent = "Testing";
  try {
    const r = await post(`/api/setup/test/${id}`);
    renderItems(r.items);
    banner(r.ok ? "" : r.line, !r.ok);
  } catch (e) { banner(e.message, true); }
  if (button) button.textContent = "Test";
}

async function load() {
  setup = await api("/api/setup");
  renderItems(setup.items);
  $("#where").textContent = setup.demo ? "Demo hub: fake data, nothing real is called" : `Hub on ${setup.system === "darwin" ? "macOS" : setup.system}`;
  $("#callback").textContent = `${location.protocol}//127.0.0.1:${location.port || 8780}${setup.google.callback}`;
  $("#data-dir").textContent = `Settings and keys are stored on this computer in ${setup.data_dir} (secrets.json is readable only by you). Nothing goes into the repo.`;
  const mac = setup.items.filter((i) => i.mac_only);
  $("#mac-note").textContent = setup.system === "darwin"
    ? "This hub runs on a Mac, so the Mac actions, the voice helper and the notch app are available. deploy/install.sh sets them up as services."
    : `This hub runs on ${setup.system === "linux" ? "Linux" : setup.system}, so these are off: ${mac.map((i) => i.name).join(", ")}. Everything else works the same.`;
  const g = setup.google;
  $("#client-state").textContent = g.client ? "OAuth client saved." : "No OAuth client yet.";
  renderAccounts(g.accounts);
  const b = setup.budget;
  $("#budget-line").textContent = `$${b.month_usd.toFixed(2)} spent this month of a $${b.cap_usd.toFixed(0)} cap. ` +
    "At 80% the brief warns you; at 100% Sonnet and Opus stop and only cheap Haiku routing keeps going, up to 5% over.";
  const s = await api("/api/settings");
  $("#budget").value = s.monthly_budget_usd;
  $("#user-name").value = s.user_name; $("#work-start").value = s.work_start; $("#work-end").value = s.work_end;
  $("#place-line").textContent = s.weather_place ? `${s.weather_place}, clock ${s.timezone}.` : `No city yet. Clock: ${s.timezone}.`;
  $("#laya-url").value = s.laya_url; $("#jobhunt-url").value = s.jobhunt_url; $("#resumegen-url").value = s.resumegen_url;
  $("#jobhunt-file").value = s.jobhunt_token_file; $("#resumegen-file").value = s.resumegen_token_file;
  loadAccess().catch(() => {});
  const q = new URLSearchParams(location.search);
  if (q.get("connected")) banner(`Connected the ${q.get("connected")} Google account.`);
  if (q.get("welcome")) banner("Passcode set. Next: add your Claude API key, then connect Google if you want mail and calendar.");
}

// ---- keys ----
document.querySelectorAll("[data-save-secret]").forEach((b) => {
  b.onclick = async () => {
    const input = $(`#${b.dataset.input}`);
    if (!input.value.trim()) { banner("Paste the value first.", true); return; }
    try {
      const r = await post("/api/setup/secret", { name: b.dataset.saveSecret, value: input.value });
      input.value = ""; renderItems(r.items); banner("Saved on this computer.");
      if (b.dataset.saveSecret === "ANTHROPIC_API_KEY") test("anthropic");
    } catch (e) { banner(e.message, true); }
  };
});
document.querySelectorAll("[data-remove-secret]").forEach((b) => {
  b.onclick = async () => {
    if (!confirm("Remove it from this computer?")) return;
    try { const r = await api(`/api/setup/secret/${b.dataset.removeSecret}`, { method: "DELETE" }); renderItems(r.items); banner("Removed."); }
    catch (e) { banner(e.message, true); }
  };
});
document.querySelectorAll("[data-test]").forEach((b) => { b.onclick = () => test(b.dataset.test, b); });
$("#save-budget").onclick = async () => {
  try { await put("/api/settings", { monthly_budget_usd: Number($("#budget").value) }); banner("Cap saved."); load(); }
  catch (e) { banner(e.message, true); }
};

// ---- Google ----
function renderAccounts(accounts) {
  const list = $("#accounts");
  if (!accounts.length) { list.replaceChildren(el("li", "hint", setup?.demo ? "The demo hub uses made-up accounts." : "No accounts connected.")); return; }
  list.replaceChildren(...accounts.map((a) => {
    const li = el("li", "account");
    const who = el("span", "item-name", `${a.label}: ${a.email}`);
    const what = el("span", `status s-${a.error ? "red" : "blue"}`, a.error ? "Failing" : (a.access === "full" ? "Mail, Calendar, Drive" : "Mail, read-only"));
    const again = el("button", "small", "Reconnect"); again.onclick = () => connect(a.label);
    const off = el("button", "small", "Disconnect");
    off.onclick = async () => {
      if (!confirm(`Disconnect ${a.email}? Maxie revokes its access at Google and forgets the token.`)) return;
      try { await post(`/api/google/accounts/${a.label}/disconnect`); load(); } catch (e) { banner(e.message, true); }
    };
    li.append(who, what, again, off);
    if (a.error) li.append(el("p", "hint bad", a.error));
    return li;
  }));
}
async function connect(label) {
  note("#google-note", "Opening Google's consent page.");
  try { const r = await post("/api/google/connect", { label }); location.assign(r.url); }
  catch (e) { note("#google-note", e.message, true); }
}
document.querySelectorAll("[data-connect]").forEach((b) => { b.onclick = () => connect(b.dataset.connect); });
$("#connect-other").onclick = () => connect($("#other-label").value.trim().toLowerCase());
$("#save-client").onclick = async () => {
  try {
    await post("/api/google/client", { json_text: $("#client-json").value, client_id: $("#client-id").value,
                                       client_secret: $("#client-secret").value });
    $("#client-json").value = ""; $("#client-secret").value = "";
    banner("OAuth client saved. Now press Connect main account."); load();
  } catch (e) { banner(e.message, true); }
};
$("#remove-client").onclick = async () => {
  if (!confirm("Remove the OAuth client? Connected accounts stop working until you add it again.")) return;
  await api("/api/google/client", { method: "DELETE" }).catch((e) => banner(e.message, true)); load();
};

// ---- About me ----
$("#find-city").onclick = async () => {
  try { const r = await post("/api/place", { query: $("#city").value }); $("#place-line").textContent = `${r.place}, clock ${r.timezone}.`; load(); }
  catch (e) { note("#place-line", e.message, true); }
};
$("#save-about").onclick = async () => {
  try { await put("/api/settings", { user_name: $("#user-name").value.trim(), work_start: $("#work-start").value, work_end: $("#work-end").value }); banner("Saved."); }
  catch (e) { banner(e.message, true); }
};

// ---- Access ----
async function loadAccess() {
  const a = await api("/api/auth/sessions");
  $("#mac-app").hidden = !a.mac_app;
  $("#tokens").replaceChildren(...(a.tokens.length ? a.tokens.map((t) => {
    const li = el("li", "account", `${t.label}, made ${when(t.created)}, last used ${when(t.last_used)}`);
    const b = el("button", "small", "Revoke");
    b.onclick = async () => { await api(`/api/auth/tokens/${t.id}`, { method: "DELETE" }); loadAccess(); };
    li.append(" ", b); return li;
  }) : [el("li", "hint", "No app tokens yet.")]));
  $("#sessions").replaceChildren(...a.sessions.map((x) => {
    const li = el("li", "account", `Since ${when(x.created)}: ${(x.device || "a browser").slice(0, 60)}`);
    const b = el("button", "small", "Sign out");
    b.onclick = async () => { await api(`/api/auth/sessions/${x.id}`, { method: "DELETE" }); loadAccess(); };
    li.append(" ", b); return li;
  }));
}
$("#new-token").onclick = async () => {
  try {
    const r = await post("/api/auth/tokens", { label: $("#token-label").value.trim() || "iPhone Shortcut" });
    const p = $("#token-once"); p.hidden = false;
    p.textContent = `Copy it now, it won't be shown again: ${r.token}\nIn the Shortcut, send it as the header Authorization: Bearer <token> (docs/iphone.md).`;
    $("#token-label").value = ""; loadAccess();
  } catch (e) { banner(e.message, true); }
};
$("#mac-app").onclick = async () => {
  try { await post("/api/auth/mac-app"); banner("The Mac app's token is in your login Keychain. Restart the notch app."); loadAccess(); }
  catch (e) { banner(e.message, true); }
};
$("#change-pass").onclick = async () => {
  try { await post("/api/auth/passcode", { old: $("#old-pass").value, new: $("#new-pass").value }); location.replace("/signin.html"); }
  catch (e) { banner(e.message, true); }
};
$("#sign-out").onclick = async () => { await post("/api/auth/logout").catch(() => {}); location.replace("/signin.html"); };

// ---- Optional ----
$("#save-laya").onclick = async () => {
  try {
    await put("/api/settings", { laya_url: $("#laya-url").value.trim() });
    if ($("#laya-key").value.trim()) await post("/api/setup/secret", { name: "LAYA_API_KEY", value: $("#laya-key").value });
    $("#laya-key").value = ""; banner("Saved."); load();
  } catch (e) { banner(e.message, true); }
};
$("#save-jobs").onclick = async () => {
  try {
    await put("/api/settings", { jobhunt_url: $("#jobhunt-url").value.trim(), resumegen_url: $("#resumegen-url").value.trim(),
                                 jobhunt_token_file: $("#jobhunt-file").value.trim(), resumegen_token_file: $("#resumegen-file").value.trim() });
    if ($("#jobhunt-token").value.trim()) await post("/api/setup/secret", { name: "JOBHUNT_TOKEN", value: $("#jobhunt-token").value });
    $("#jobhunt-token").value = "";
    banner("Saved. Restart the hub to switch the job apps on or off."); load();
  } catch (e) { banner(e.message, true); }
};

load().catch((e) => banner(e.message === "signed out" ? "" : `Couldn't load Settings: ${e.message}`, true));
