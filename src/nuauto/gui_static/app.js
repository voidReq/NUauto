// NUauto window. Plain JS, no build step. Every bit of text from the server goes in with textContent (never as
// HTML): job descriptions come from NUworks. data-testid marks what tests and agents click.
"use strict";

// ------------------------------------------------------------------ helpers

const PROPS = new Set(["value", "checked", "disabled", "hidden", "selected", "indeterminate", "readOnly"]);

function safeHref(u) {
  u = String(u || "");
  return /^(https?:\/\/|#|\/)/i.test(u) ? u : null;
}

// A button (or form) whose handler starts something slower (it returns a promise) shows that at once, and takes no second
// click until it is done
const PENDING = new WeakSet();
function busyWhile(n, handler) {
  return (e) => {
    if (PENDING.has(n)) { e.preventDefault(); return undefined; }
    const r = handler(e);
    if (r && typeof r.then === "function") {
      const b = n.tagName === "FORM" ? n.querySelector("[type=submit]") || n : n;
      PENDING.add(n);
      b.setAttribute("aria-busy", "true");
      const done = () => { PENDING.delete(n); b.removeAttribute("aria-busy"); };
      r.then(done, done);
    }
    return r;
  };
}

function el(tag, props, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k === "testid") n.dataset.testid = v;
    else if (k.startsWith("on") && typeof v === "function") {
      n.addEventListener(k.slice(2), (k === "onclick" && tag === "button") || (k === "onsubmit" && tag === "form") ? busyWhile(n, v) : v);
    }
    else if (PROPS.has(k)) n[k] = v;
    else if (k === "href" || k === "src") { const h = safeHref(v); if (h) n.setAttribute(k, h); }
    else n.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids.flat(9)) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.append(kid instanceof Node ? kid : String(kid));
  }
  return n;
}

const $ = (sel, root = document) => root.querySelector(sel);

function fill(node, ...kids) {
  node.replaceChildren(...kids.flat(9).filter((k) => k !== null && k !== undefined && k !== false && k !== ""));
  return node;
}

async function call(method, path, body) {
  const opts = { method, headers: {}, credentials: "same-origin" };
  if (method === "POST") {
    opts.headers["Content-Type"] = "application/json";
    opts.headers["X-NUauto"] = "1";
    opts.body = JSON.stringify(body || {});
  }
  let r;
  try {
    r = await fetch(path, opts);
  } catch (e) {
    if (homelabPage()) {  // a phone off Tailscale, or asleep for a while: the page keeps asking, and says so meanwhile
      offline(true);
      throw new Error("NUauto on your homelab can't be reached right now.");
    }
    gone("NUauto has stopped. Start it again from your apps menu, or run nuauto gui in a terminal.");
    throw new Error("NUauto is not running.");
  }
  offline(false);
  // A body that can't be read (cut short by a reload, or not JSON) is an error, never an empty answer: a screen must
  // not draw a job card (or anything) from {}
  const data = await r.json().catch(() => null);
  if (r.status === 403) {
    if (data && data.pair) gone(data.error, "This browser is not paired");  // the homelab's page: pair it first
    else gone("This window belongs to an earlier start of NUauto. Open NUauto again from its icon.");
    throw new Error("Not allowed.");
  }
  if (!r.ok) throw new Error((data && data.error) || `Error ${r.status}`);
  if (data === null) throw new Error("NUauto's answer could not be read. Try again.");
  return data;
}
const api = { get: (p) => call("GET", p), post: (p, b) => call("POST", p, b) };

// Which machine this page runs on: the laptop (`nuauto gui`) or the homelab (NUauto's page there, for your phone and your
// other computers: state.where). On the homelab, what needs the laptop itself (a login window, a terminal window, a file
// there) is explained instead of done: state.laptop_only.
function onHomelab() { return !!(S.state && S.state.where === "homelab"); }
function homelabPage() { return S.state ? onHomelab() : !["127.0.0.1", "localhost"].includes(location.hostname); }
function offline(on) {
  if (!!S.offline === on) return;
  S.offline = on;
  if (S.state) renderShell();
}
function laptopOnlyBox(why) {
  modal((box, close) => box.append(el("h2", { text: "On your laptop" }), el("p", { class: "muted", text: why }),
    el("div", { class: "foot" }, el("span"), el("button", { class: "btn primary", text: "OK", onclick: close }))),
  { label: "On your laptop", testid: "laptop-only" });
}

function toast(msg, opts = {}) {
  const t = el("div", { class: "toast" + (opts.error ? " error" : ""), role: "status", testid: "toast" },
    el("span", { text: msg }),
    opts.action ? el("button", { class: "btn small", text: opts.action, onclick: () => { t.remove(); opts.onaction(); } }) : null);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), opts.ms || (opts.action ? 9000 : 4500));
}

function fail(e) { toast(e.message || String(e), { error: true }); }

// A change that takes a moment (the sheet): says so at once, until its answer comes
async function working(text, p) {
  const t = el("div", { class: "toast", role: "status", testid: "toast-working" }, el("span", { class: "spinner" }), el("span", { text }));
  $("#toasts").append(t);
  try { return await p; } finally { t.remove(); }
}

function modal(build, { onclose, label = "Dialog", testid = "dialog" } = {}) {
  const prev = document.activeElement;
  const box = el("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": label, testid });
  const back = el("div", { class: "backdrop" }, box);
  const close = () => { back.remove(); document.removeEventListener("keydown", esc); if (onclose) onclose(); if (prev) prev.focus(); };
  const esc = (e) => { if (e.key === "Escape" && !box.dataset.noEscape) close(); };
  build(box, close);
  $("#modal-root").append(back);
  document.addEventListener("keydown", esc);
  const first = box.querySelector("input, select, button.primary, button");
  if (first) first.focus();
  return close;
}

function confirmBox(title, text, yes, onyes, { danger = false } = {}) {
  modal((box, close) => box.append(
    el("h2", { text: title }), el("p", { class: "muted", text }),
    el("div", { class: "foot" }, el("span"), el("div", { class: "row" },
      el("button", { class: "btn", text: "Cancel", onclick: close }),
      el("button", { class: "btn " + (danger ? "danger" : "primary"), text: yes, testid: "confirm-yes",
        onclick: () => { close(); onyes(); } })))));
}

function ago(iso) {
  const t = typeof iso === "number" ? iso * 1000 : Date.parse(iso);
  if (!t) return "";
  const m = Math.round((Date.now() - t) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 36) return `${h} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

function openExternal(url) {  // in NUauto's own window this opens your browser (window.open is not reliable there)
  const a = el("a", { href: url, target: "_blank", rel: "noopener noreferrer" });
  document.body.append(a);
  a.click();
  a.remove();
}

function linkOut(url, text) {
  return el("a", { href: url, target: "_blank", rel: "noopener noreferrer", text });
}

function plural(n, word) { return `${n} ${word}${n === 1 ? "" : "s"}`; }

// Your resume and transcript: a PDF you drop or choose goes to NUauto (/api/files), which keeps its own copy in local/
// and pushes it to the homelab. Setup's resume step and Settings > Applying > Your files.
const MAX_PDF = 10000000;  // onboard.MAX_DOCUMENT
async function sendPdf(kind, file) {
  if (file.size > MAX_PDF) throw new Error(`${file.name} is over 10 MB.`);
  const bytes = new Uint8Array(await file.arrayBuffer());
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return api.post("/api/files", { kind, data: btoa(bin) });
}
function pdfDrop(testid, text, onfile) {  // a box to drop the PDF on, with a button that opens the file chooser
  const input = el("input", { type: "file", accept: ".pdf,application/pdf", hidden: true, testid: `${testid}-file` });
  const drop = el("div", { class: "drop", testid }, text, " ", el("button", { class: "btn small", text: "choose it", onclick: () => input.click() }), input);
  input.addEventListener("change", () => { if (input.files[0]) onfile(input.files[0]); input.value = ""; });
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files[0]) onfile(e.dataTransfer.files[0]); });
  return drop;
}
function pdfLine(f) {  // what NUauto uses now: name, size, when it changed
  return f ? `${f.name} · ${Math.max(1, Math.round(f.size / 1024))} KB · updated ${ago(f.modified)}` : "None yet.";
}
function ordinal(n) { const t = n % 100, s = ["th", "st", "nd", "rd"][(t - 20) % 10] || ["th", "st", "nd", "rd"][t] || "th"; return n + s; }

function gone(msg, title) {
  if (S.gone) return;
  S.gone = true;
  fill($("#view"), el("div", { class: "card", testid: "gone" }, el("h2", { text: title || "NUauto is not available" }),
    el("p", { class: "muted", text: msg })));
}

// ------------------------------------------------------------------ shared state + polling

const S = { state: null, checks: [], route: null, screen: null, task: null, taskAfter: 0, taskLog: [],
            openQuestion: null, gone: false, lastTaskState: {}, routes: 0 };

async function pollState() {
  if (S.gone) return;
  try {
    S.state = await api.get("/api/state");
  } catch (e) { return; }
  renderShell();
  // a read of the sheet found it changed (a run finished, a row was applied, another window changed it): lists show it at once
  const sheetChanged = S.sheetVersion !== undefined && S.state.sheet_version !== S.sheetVersion;
  S.sheetVersion = S.state.sheet_version;
  // an assistant started or ended (in its own terminal window): the Apply lists say which rows have one
  const assistantsChanged = S.assistants !== undefined && S.state.assistants !== S.assistants;
  S.assistants = S.state.assistants;
  if ((sheetChanged || assistantsChanged) && S.screen && S.screen.refresh) S.screen.refresh();
  if (S.screen && S.screen.update) S.screen.update(S.state);
  const t = S.state.task;  // this machine's run: followed unless a run on the homelab is being followed and still going
  if (t && t.state === "running" && !S.taskTimer && (S.taskWhere !== "homelab" || !(S.task && S.task.state === "running"))) {
    S.taskWhere = "here";
    pollTask();
  }
}

async function pollHealth() {
  if (S.gone) return;
  try {
    const h = await api.get("/api/health");
    S.checks = h.checks;
    S.healthRunning = h.running;
  } catch (e) { return; }
  renderHealth();
  if (S.screen && S.screen.health) S.screen.health(S.checks);
}

async function pollTask() {
  clearTimeout(S.taskTimer);
  S.taskTimer = null;
  if (S.gone) return;
  let t;
  try {
    const known = S.task ? S.task.id : null;
    const r = await api.get(taskPath(`/api/task?after=${S.taskAfter}`));
    t = r.task;
    if (t && t.id !== known) { S.taskLog = []; S.taskAfter = 0; if (known) return pollTask(); }
  } catch (e) { return; }
  if (t) {
    S.taskLog.push(...t.log);
    S.taskAfter = t.log_total;
    const doneBefore = S.task && S.task.id === t.id ? S.task.done_rows.length : 0;
    S.task = t;
    if (t.done_rows.length > doneBefore) setTimeout(pollState, 1500);  // a row finished: the lists read the sheet again now
    const was = S.lastTaskState[t.id];
    S.lastTaskState[t.id] = t.state;
    if (was === "running" && t.state !== "running") taskFinished(t);
  }
  renderTaskbar();
  renderQuestion();
  if (S.screen && S.screen.task) S.screen.task(S.task, S.taskLog);
  if (t && t.state === "running") S.taskTimer = setTimeout(pollTask, 700);
}

function taskFinished(t) {
  const how = { done: "done", failed: "did not finish", stopped: "stopped" }[t.state] || t.state;
  toast(`${t.label}: ${how}.`, t.state === "failed" ? { error: true, action: "View log", onaction: () => showLog(t) } :
    { action: "View log", onaction: () => showLog(t) });
  pollState();
  pollHealth();
}

// A run the server just started (its reply's task): the page follows it (the taskbar, its log, its questions)
function adoptTask(r) {
  if (r && r.task) {
    S.taskWhere = r.task.where === "homelab" ? "homelab" : "here";  // a run on the homelab: followed through the link
    S.task = r.task; S.taskLog = []; S.taskAfter = 0; S.lastTaskState[r.task.id] = "running"; pollTask();
  }
}

// (laptop, linked) where a NUworks run you start goes: this computer (its Firefox window) or the homelab (its virtual
// screen; you follow it here). Remembered for the session.
function canRunThere() { return !!(S.state && S.state.homelab_link && S.state.homelab_link.on); }
function runOn() { return canRunThere() && sessionStorage.getItem("runOn") === "homelab" ? "homelab" : "here"; }
function runArgs(args) { return runOn() === "homelab" ? { ...args, on: "homelab" } : args; }

// The followed run's own API: this machine's, or (the laptop's app, through its link) the homelab's
function taskPath(path) { return S.taskWhere === "homelab" ? path.replace("/api/", "/api/homelab/") : path; }

// (laptop) follow the run going on the homelab (Apply there, or the NUworks side)
function followHomelabTask() {
  const t = S.state && S.state.homelab_link && S.state.homelab_link.task;
  if (!t) return;
  S.taskWhere = "homelab";
  S.task = { ...t, log: [], log_total: 0 }; S.taskLog = []; S.taskAfter = 0; S.lastTaskState[t.id] = t.state;
  pollTask();
}

async function action(kind, args) {
  const why = S.state && S.state.laptop_only && S.state.laptop_only[kind];
  if (why) { laptopOnlyBox(why); return null; }
  try {
    const r = await api.post("/api/action", { kind, args: args || {} });
    if (r.opened !== undefined) terminalResult(r);
    if (r.message) toast(r.message);
    adoptTask(r);
    pollState();
    return r;
  } catch (e) { fail(e); return null; }
}

function terminalResult(r) {
  if (r.opened) return toast("Opened a terminal window. Follow it there.");
  modal((box, close) => box.append(
    el("h2", { text: r.demo ? "Demo: this opens a terminal" : "Run this in a terminal" }),
    el("p", { class: "muted", text: r.demo ? "In real use NUauto opens a terminal window and runs this command there." :
      "No terminal app was found. Open one yourself and run:" }),
    el("pre", { class: "log", testid: "terminal-command", tabindex: "0", text: r.command }),
    el("div", { class: "foot" }, el("span"), el("div", { class: "row" },
      el("button", { class: "btn", text: "Copy", onclick: () => navigator.clipboard.writeText(r.command).then(() => toast("Copied.")) }),
      el("button", { class: "btn primary", text: "Close", onclick: close })))), { label: "Terminal command" });
}

const FIXES = {
  setup: () => { location.hash = "#/setup"; },
  login_google: () => action("login_google"),
  login_nuworks: () => action("login_nuworks"),
  login_claude: () => action("login_claude"),
  homelab_claude: async () => { const r = await api.post("/api/homelab", { action: "claude" }).catch(fail); if (r) terminalResult(r); },
  install_claude: () => confirmBox("Install Claude Code?", "This runs Claude Code's official installer (claude.ai/install.sh) for your user.",
    "Install", () => action("install_claude")),
  install_firefox: () => action("install_firefox"),
  fix_permissions: () => action("fix_permissions").then(() => toast("Permissions fixed.")),
  open_sheet: () => { if (S.state && S.state.sheet_url) openExternal(S.state.sheet_url); },
  update: () => action("update"),
  open_release: () => openExternal("https://github.com/voidReq/NUauto/releases/latest"),
  bitwarden_add: () => action("bitwarden_add"),
  bitwarden_login: () => action("bitwarden_login"),
  bitwarden_install: () => confirmBox("Install Bitwarden's command line?", "This runs npm install -g @bitwarden/cli in a terminal window, then bw login (you type your Bitwarden email and master password into Bitwarden there, never into NUauto).",
    "Install", () => action("bitwarden_install")),
};

function fixButton(c, cls = "btn small") {
  if (!c.fix || !FIXES[c.fix]) return null;
  return el("button", { class: cls, text: c.fix_label || "Fix", testid: `fix-${c.id}`, onclick: FIXES[c.fix] });
}

// ------------------------------------------------------------------ shell: nav, health strip, banner, taskbar

function renderShell() {
  const st = S.state;
  for (const [k, v] of Object.entries(st.counts)) {
    const b = $(`[data-count="${k}"]`);
    if (b) b.textContent = v ? String(v) : "";
  }
  fill($("#banner"), S.offline ? el("div", { class: "note warn", testid: "offline-banner" },
    el("b", { text: "Can't reach NUauto on your homelab. " }), "Is Tailscale on, on this device? This page tries again by itself.") : null,
    st.demo ? el("div", { class: "demo-banner", testid: "demo-banner" },
    el("b", { text: "Demo mode. " }), "Nothing here is real: a fake sheet, fake NUworks pages, fake Claude. Nothing is sent anywhere.") :
    st.view ? el("div", { class: "demo-banner", testid: "view-banner" },
      el("b", { text: "View only. " }), "This is your real data, read-only: nothing can be changed or started, and saved answers and run screenshots are hidden.") : "");
  fill($("#side-foot"), el("div", { testid: "side-version", title: st.commit ? `The code's commit: ${st.commit} (the same on both machines when each runs main)` : null,
    text: `v${st.version}` + (st.commit ? ` · ${st.commit}` : "") + (st.where === "homelab" ? " · on your homelab" : st.mode === "homelab" ? " · homelab" : "") }));
}

const STRIP = ["google", "sheet", "nuworks", "claude", "homelab", "bitwarden"];
const HIDDEN_WHEN_OFF = new Set(["homelab", "bitwarden"]);  // optional parts: no chip until you use them
const SHORT = { ok: "OK", warn: "Check", fail: "Problem", busy: "Busy", unknown: "…", off: "Off" };

function renderHealth() {
  const strip = $("#health");
  const by = Object.fromEntries(S.checks.map((c) => [c.id, c]));
  const dots = STRIP.filter((id) => by[id] && !(HIDDEN_WHEN_OFF.has(id) && by[id].status === "off")).map((id) => {
    const c = by[id];
    return el("button", { class: `dot ${c.status}`, testid: `health-${id}`, title: c.detail, "aria-label": `${c.title}: ${c.detail}`,
      onclick: (e) => { e.stopPropagation(); healthPopover(c); } }, el("i"), el("span", { text: c.title }));
  });
  const pop = strip.querySelector(".popover");  // an open chip popover stays open (and updates itself)
  fill(strip, ...dots);
  if (pop) strip.append(pop);
}

// The check groups (gui.App.GROUPS) a status chip's Check now runs again
const RECHECK = { google: ["quick", "sheet"], sheet: ["sheet"], nuworks: ["quick", "nuworks"], claude: ["claude"], homelab: ["homelab"],
  bitwarden: ["bitwarden"] };

function healthPopover(c) {
  document.querySelectorAll(".popover").forEach((p) => p.remove());
  const pop = el("div", { class: "popover", testid: "health-popover" });
  const groups = RECHECK[c.id] || [];
  let checking = groups.some((g) => (S.healthRunning || []).includes(g));
  function draw() {
    const now = S.checks.find((x) => x.id === c.id) || c;
    fill(pop, el("div", { class: "row between" }, el("h2", { text: now.title }),
        el("span", { class: `chip ${now.status}`, text: checking ? "Checking…" : SHORT[now.status] || now.status })),
      el("p", { text: now.detail }),
      el("div", { class: "row" }, fixButton(now, "btn small primary"),
        groups.length && !(S.state && S.state.view) ? el("button", { class: "btn small", testid: `recheck-${c.id}`, disabled: checking,
          text: checking ? "Checking…" : "Check now", onclick: recheck }) : null,
        el("a", { href: "#/settings/status", class: "btn small ghost", text: "All checks", onclick: () => pop.remove() })));
  }
  async function recheck(e) {
    e.stopPropagation();
    checking = true; draw();
    try { await api.post("/api/health/run", { groups }); } catch (err) { checking = false; draw(); fail(err); return; }
    const until = Date.now() + 180000;  // the NUworks check opens a hidden browser: it can take a minute or two
    await new Promise((r) => setTimeout(r, 800));
    while (pop.isConnected && Date.now() < until) {
      await pollHealth();
      if (!groups.some((g) => (S.healthRunning || []).includes(g))) break;
      await new Promise((r) => setTimeout(r, 1500));
    }
    checking = false;
    if (pop.isConnected) draw();
  }
  draw();
  $("#health").append(pop);
  const off = (e) => { if (!pop.contains(e.target)) { pop.remove(); document.removeEventListener("click", off); } };
  setTimeout(() => document.addEventListener("click", off), 0);
}

function renderTaskbar() {
  const bar = $("#taskbar");
  const t = S.task;
  const onApply = S.route === "apply" && t && t.kind === "apply";
  if (!t || t.state !== "running" || t.background || onApply) { bar.hidden = true; return; }
  bar.hidden = false;
  fill(bar, el("span", { class: "spinner" }), el("span", { text: `${t.label}${S.taskWhere === "homelab" ? " (on the homelab)" : ""}…`, testid: "taskbar-label" }),
    el("button", { class: "btn small ghost", text: "Log", onclick: () => showLog(t) }),
    t.kind === "apply" ? el("a", { class: "btn small ghost", href: "#/apply", text: "View" }) : null,
    el("button", { class: "btn small danger", text: "Stop", testid: "taskbar-stop", onclick: () => stopTask(t) }));
}

async function stopTask(t, force) {
  try {
    await api.post(taskPath("/api/stop"), { task: t.id, force: !!force });
    toast(force ? "Force-stopped." : "Stopping (like Ctrl+C)…");
  } catch (e) { fail(e); }
}

function showLog(t) {
  const pre = el("pre", { class: "log", testid: "log-modal", tabindex: "0", "aria-label": "Log" });
  const refresh = () => { pre.textContent = (S.task && S.task.id === t.id ? S.taskLog : []).join("\n") || "(no output)"; pre.scrollTop = pre.scrollHeight; };
  refresh();
  const timer = setInterval(refresh, 800);
  modal((box, close) => box.append(el("h2", { text: t.label }), pre,
    el("div", { class: "foot" }, el("span"), el("button", { class: "btn primary", text: "Close", onclick: close }))),
  { onclose: () => clearInterval(timer), label: "Log" });
}

// ------------------------------------------------------------------ questions from a run (answer bank, "did it submit?")

function renderQuestion() {
  const t = S.task;
  const q = t && t.state === "running" ? t.question : null;
  if (!q) { if (S.closeQuestion) { S.closeQuestion(); S.closeQuestion = null; S.openQuestion = null; } return; }
  if (S.openQuestion === `${t.id}:${q.id}`) return;
  if (S.closeQuestion) S.closeQuestion();
  S.openQuestion = `${t.id}:${q.id}`;
  const reply = async (r) => {
    try {
      await api.post(taskPath("/api/answer"), { task: t.id, id: q.id, reply: r });
      S.closeQuestion && S.closeQuestion();
      S.closeQuestion = null;
      pollTask();
    } catch (e) { fail(e); }
  };
  S.closeQuestion = modal((box) => {
    box.dataset.noEscape = "1";
    const context = (q.context || []).map((c) => c.trim()).filter(Boolean);
    box.append(el("p", { class: "small muted", text: t.label }));
    if (context.length) box.append(el("div", { class: "note small" }, ...context.map((c) => el("div", { text: c }))));
    if (q.kind === "unknown") questionUnknown(box, q, reply);
    else if (q.kind === "option") questionOption(box, q, reply);
    else if (q.kind === "field" || q.kind === "text") questionText(box, q, reply);
    else if (q.kind === "menu") questionMenu(box, q, reply);
    else box.append(el("p", { text: "An unknown kind of question. Stop the run and use the terminal." }));
  }, { label: "Question", testid: "question" });
}

const PLACEHOLDER = /^(select|select one|choose|choose one|please select|--.*|-)$/i;

function choiceButtons(choices, onpick) {
  const real = choices.filter((c) => c.trim() && !PLACEHOLDER.test(c.trim()));
  return el("div", { class: "choices" }, ...(real.length ? real : choices).map((c, i) => el("button", {
    class: "btn", text: c, testid: `q-choice-${i}`, onclick: () => onpick(c) })));
}

function skipFoot(reply, payload) {
  return el("div", { class: "foot" },
    el("button", { class: "btn danger", text: "Skip this job", testid: "q-skip", title: "Marks the row Needs Human and moves on",
      onclick: () => reply(payload) }), el("span"));
}

function questionUnknown(box, q, reply) {
  box.append(el("h2", { text: "A question with no saved answer" }),
    el("p", {}, "NUworks asks: ", el("b", { text: q.label, testid: "q-label" })),
    el("p", { class: "small muted", text: "Your answer is saved to the answer bank and used whenever this exact question comes up again." }));
  if (q.field_type === "select" && q.choices && q.choices.length) {
    box.append(choiceButtons(q.choices, (c) => reply({ how: "new", answer: c })));
  } else {
    const input = el("input", { type: "text", testid: "q-text", "aria-label": q.label });
    box.append(el("div", { class: "row" }, input,
      el("button", { class: "btn primary", text: "Save answer", testid: "q-submit",
        onclick: () => input.value.trim() ? reply({ how: "new", answer: input.value.trim() }) : input.focus() })));
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && input.value.trim()) reply({ how: "new", answer: input.value.trim() }); });
  }
  if (q.entries && q.entries.length) {
    const sel = el("select", { testid: "q-alias", "aria-label": "Existing question" },
      el("option", { value: "", text: "Same question as one already saved…" }),
      ...q.entries.map((e, i) => el("option", { value: String(i), text: e.question + (e.answered ? "" : " (no answer yet)") })));
    box.append(el("hr", { class: "sep" }), el("div", { class: "row" }, sel,
      el("button", { class: "btn", text: "Use that one", testid: "q-alias-use",
        onclick: () => sel.value !== "" && reply({ how: "alias", index: Number(sel.value) }) })),
      el("p", { class: "small muted", text: "For a question worded differently from one you already answered: it becomes an alias." }));
  }
  box.append(skipFoot(reply, { how: "stop" }));
}

function questionOption(box, q, reply) {
  box.append(el("h2", { text: "Pick an answer" }), el("p", {}, el("b", { text: q.label, testid: "q-label" })),
    choiceButtons(q.choices || [], (c) => reply({ answer: c })), skipFoot(reply, { answer: "" }));
}

function questionText(box, q, reply) {
  const input = el("input", { type: "text", testid: "q-text", "aria-label": q.label || q.prompt });
  box.append(el("h2", { text: "Your answer" }), el("p", {}, el("b", { text: q.label || q.prompt, testid: "q-label" })),
    el("div", { class: "row" }, input, el("button", { class: "btn primary", text: "Send", testid: "q-submit",
      onclick: () => input.value.trim() ? reply({ answer: input.value.trim() }) : input.focus() })),
    skipFoot(reply, { answer: "" }));
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && input.value.trim()) reply({ answer: input.value.trim() }); });
}

function questionMenu(box, q, reply) {
  const title = q.prompt.replace(/\s*\[.*$/, "").trim();
  box.append(el("h2", { text: title || "Choose" }),
    el("div", { class: "choices" }, ...(q.options || []).map((o) => el("button", {
      class: "btn" + (o.key === q.default ? "" : ""), text: o.label, testid: `q-menu-${o.key}`, onclick: () => reply({ answer: o.key }) }))),
    el("p", { class: "small muted", text: "Not sure? Pick Not sure: the row stays Needs Human and you check NUworks yourself." }));
}

// ------------------------------------------------------------------ screens

const screens = {};
const TITLES = { home: "Today", review: "Review", apply: "Apply", insights: "Insights", answers: "Answers",
  settings: "Settings", setup: "Setup", logs: "Past runs", assistant: "Assistant" };

// ---- Today
screens.home = (view) => {
  let last = "";
  function draw(st) {
    if (!st) return;
    const key = JSON.stringify([st, S.checks.map((c) => [c.id, c.status, c.detail])]);
    if (key === last) return;
    last = key;
    const w = st.week;
    const pct = Math.min(100, Math.round((100 * w.applied) / w.max));
    const problems = S.checks.filter((c) => c.status === "fail" || c.status === "warn");
    const lu = st.last_update;
    fill(view,
      st.setup_needed ? el("div", { class: "card", testid: "setup-needed" }, el("h2", { text: "Finish setting up" }),
        el("p", { class: "muted", text: "A few steps (Google, your sheet, NUworks, your resume), each checked as you go." }),
        el("a", { href: "#/setup", class: "btn primary", text: "Open setup" })) : null,
      problems.length ? el("div", { class: "card", testid: "problems" }, el("h2", { text: "Needs attention" }),
        el("ul", { class: "list" }, ...problems.map((c) => el("li", {}, el("div", { class: "what" },
          el("span", { class: `chip ${c.status}`, text: c.title }), " ", el("span", { text: c.detail })), fixButton(c, "btn small primary"))))) : null,
      el("div", { class: "grid2" },
        el("div", { class: "card", testid: "week" }, el("h2", { text: "This week" }),
          el("div", { class: "big" }, `${w.applied} `, el("span", { class: "muted small", text: `of ${w.max} applied ${w.label}` })),
          el("div", { class: "bar" + (w.applied >= w.max ? " full" : "") }, el("span", { style: null, testid: "week-bar" })),
          el("p", { class: "small muted", text: (w.room ? `Room for ${w.room} more` : "Weekly limit reached; Approved jobs wait") +
            (w.next ? ` · new week ${w.next}` : "") + ` · ${w.total} of ${w.max_total} in total` })),
        el("div", { class: "card stack" }, el("h2", { text: "Next steps" }),
          el("a", { class: "btn big" + (st.counts.review ? " primary" : ""), href: "#/review", testid: "go-review",
            onclick: () => sessionStorage.setItem("reviewSource", "nuworks"),  // Review remembers its list: this button is the co-ops
            text: st.counts.review ? `Review ${plural(st.counts.review, "new job")}` : "Nothing new to review" }),
          st.internships && st.internships.on && st.internships.review > 0 ?
            el("a", { class: "btn big" + (st.counts.review || st.counts.approved ? "" : " primary"), href: "#/review", testid: "go-review-intern",
              onclick: () => sessionStorage.setItem("reviewSource", "intern"),
              text: `Review ${plural(st.internships.review, "internship")}` }) : null,
          el("a", { class: "btn big" + (st.counts.approved && !st.counts.review ? " primary" : ""), href: "#/apply", testid: "go-apply",
            text: st.counts.approved ? `Apply to ${st.counts.approved} approved` : "No approved jobs waiting" }),
          el("button", { class: "btn", testid: "btn-update", disabled: !!(st.task && st.task.state === "running"),
            text: "Check for new jobs now", onclick: () => action("update") }),
          el("p", { class: "small muted", text: lu ? `Last check ${ago(lu.time)}: ${lu.listed} new on NUworks, ${lu.pool} made your pool.` :
            "No check for new jobs has run yet." }))),
      el("div", { class: "card", testid: "todo" }, el("h2", { text: "Only you can do these" }), todoList(st.todo)));
    const weekBar = view.querySelector("[data-testid=week-bar]");
    if (weekBar) weekBar.style.width = pct + "%";
  }
  draw(S.state);
  return { update: draw, health: () => draw(S.state) };
};

// When the NUworks posting closes: a chip (red once past, amber within a week), or "no deadline listed".
function dueChip(t) {
  if (!t.closes) return el("span", { class: "chip", text: "no deadline listed", testid: "due" });
  return el("span", { class: "chip" + (t.past ? " fail" : t.soon ? " warn" : ""), testid: "due",
    text: `${t.past ? "closed" : "closes"} ${t.closes_text}` });
}

// Today's one list of what only you can do; each item opens the place you do it (nothing is done twice in two places).
// An assistant that waits for you comes first: its screen (the homelab's page), or its terminal window (the laptop).
function todoList(items) {
  if (!items.length) return el("p", { class: "empty", text: "Nothing waiting on you." });
  return el("ul", { class: "list" }, ...items.map((t) => {
    if (t.kind === "assistant") return el("li", { testid: `todo-assistant-${t.tab}-${t.row}` },
      el("div", { class: "what" }, el("b", { text: t.company || "assistant" }), " · the assistant: ", t.text,
        t.title ? el("div", { class: "small muted", text: t.title }) : null),
      el("div", { class: "row" }, !t.screen ? el("span", { class: "chip warn", text: "in its terminal window" }) :
        t.screen.startsWith("#") ? el("a", { class: "btn small primary", href: t.screen, text: t.status === "approve" ? "Review and approve" : "Open" }) :
          el("button", { class: "btn small primary", text: "Open its screen", title: "In NUauto on your homelab, in your browser",
            onclick: () => openExternal(t.screen) })));
    if (t.kind === "site") return el("li", { testid: `todo-site-${t.row}` },
      el("div", { class: "what" }, el("b", { text: t.company }), " · apply on their own site too ", el("span", { class: "muted", text: `(row ${t.row})` })),
      el("div", { class: "row" }, dueChip(t), el("a", { class: "btn small", href: "#/company", text: "Open" })));
    if (t.kind === "company") return el("li", { testid: `todo-company-${t.row}` },
      el("div", { class: "what" }, el("b", { text: t.company }), " · company-site application ", linkOut(t.target, t.host)),
      el("div", { class: "row" }, dueChip(t), el("a", { class: "btn small", href: "#/company", text: "Open" })));
    return el("li", { testid: `todo-urgent-${t.id}` },
      el("div", { class: "what" }, el("b", { text: t.title }), " · ", t.company, el("span", { class: "muted", text: ` · ${t.match}%` })),
      el("div", { class: "row" }, dueChip(t), el("a", { class: "btn small", href: "#/review", text: "Review",
        onclick: () => sessionStorage.setItem("reviewSource", "nuworks") })));  // these urgent jobs are co-ops
  }));
}

function markRow(kind, t, tab) {  // tab "other": a row of the sheet's Other jobs tab
  const text = kind === "site" ? `Mark the ${t.company} company-site application as done?` :
    `Mark ${tab === "other" ? "Other jobs " : ""}row ${t.row} (${t.company}) as Applied today? Only if you submitted it yourself.`;
  confirmBox(kind === "site" ? "Company site done?" : "You applied yourself?", text, "Yes, mark it", async () => {
    try { await working("Updating the sheet…", api.post("/api/mark", { action: kind, row: t.row, url: t.url, tab })); toast("Sheet updated."); pollState(); if (S.screen && S.screen.reload) S.screen.reload(); }
    catch (e) { fail(e); }
  });
}

// What you heard back on a sent application: Accepted or Rejected (or back to Applied). It still counts toward your
// limits (it went out); the sheet's row says when you marked it.
const SENT = ["Applied", "Accepted", "Rejected"];
async function outcomeRow(t, outcome, tab) {
  try {
    await working("Updating the sheet…", api.post("/api/mark", { action: "outcome", outcome, row: t.row, url: t.url, tab }));
    toast(outcome === "Applied" ? "Back to Applied." : `Marked ${outcome.toLowerCase()}.`);
    pollState();
    if (S.screen && S.screen.reload) S.screen.reload();
  } catch (e) { fail(e); }
}

// Drop: you won't apply (closed, a duplicate, a bad location): the row becomes Failed with the reason in its Notes,
// leaves every queue and stays in the sheet as your record (sheet.drop), so Review never offers the job again.
const DROP_REASONS = [["closed", "It has closed"], ["duplicate", "A duplicate"], ["location", "Bad location"], ["other", "Other reason"]];
function dropRow(t, tab) {  // tab "other": a row of the sheet's Other jobs tab
  modal((box, close) => box.append(
    el("h2", { text: "Not applying?" }),
    el("p", { class: "muted", text: `${t.company} · ${t.title || ""} (${tab === "other" ? "Other jobs " : ""}row ${t.row}) leaves the queue. The sheet keeps it as Failed, with the reason in its Notes.` }),
    el("div", { class: "foot" }, el("button", { class: "btn", text: "Cancel", onclick: close }),
      el("div", { class: "row" }, ...DROP_REASONS.map(([why, label]) => el("button", { class: "btn danger", text: label, testid: `drop-${why}`,
        onclick: async () => {
          close();
          try { await working("Dropping it…", api.post("/api/mark", { action: "drop", why, row: t.row, url: t.url, tab })); toast("Dropped."); pollState(); if (S.screen && S.screen.reload) S.screen.reload(); }
          catch (e) { fail(e); }
        } }))))));
}

// ---- Review (approve, or rate only). Two sources when internships are on (state.internships, intern.py): NUworks co-ops,
// and the term's internships (Simplify's and vanshb03's lists), whose Approve adds a row to the sheet's Other jobs tab.
screens.review = async (view) => {
  let mode = sessionStorage.getItem("reviewMode") || "approve";
  const interns = (S.state && S.state.internships) || { on: false };
  let source = interns.on && sessionStorage.getItem("reviewSource") === "intern" ? "intern" : "nuworks";
  let jobs = [], i = 0, card = null, history = {}, shown = 0, total = 0, resumeId = null, listed = 0, loadSeq = 0;
  let q = sessionStorage.getItem("reviewQ") || "", category = sessionStorage.getItem("reviewCat") || "";
  const cards = new Map();  // job id -> promise of its card (the next ones are fetched ahead)
  let queue = Promise.resolve();  // decisions are sent to the server one at a time, behind the screen
  const getCard = (id) => {
    if (!cards.has(id)) {
      const p = api.get(`/api/job/${encodeURIComponent(id)}`);
      p.catch(() => cards.delete(id));
      cards.set(id, p);
    }
    return cards.get(id);
  };
  const body = el("div");
  const seg = el("div", { class: "seg", role: "group", "aria-label": "Mode" },
    el("button", { "aria-pressed": String(mode === "approve"), testid: "mode-approve", text: "Approve", onclick: () => setMode("approve") }),
    el("button", { "aria-pressed": String(mode === "rate"), testid: "mode-rate", text: "Rate only", onclick: () => setMode("rate") }));
  // where the jobs come from: only there when internships are on; the choice is remembered for the session.
  // "wrap": below ~360 px the two buttons stack instead of breaking a label in the middle
  const sourceSeg = interns.on ? el("div", { class: "seg wrap", role: "group", "aria-label": "Source" },
    el("button", { "aria-pressed": String(source === "nuworks"), testid: "source-nuworks", text: "NUworks co-ops", onclick: () => setSource("nuworks") }),
    el("button", { "aria-pressed": String(source === "intern"), testid: "source-intern", text: `${interns.term} internships`, onclick: () => setSource("intern") })) : null;
  // search: words or "quoted phrases" (all must appear in the posting), and/or one kind of work
  const search = el("input", { type: "search", class: "grow", testid: "review-search", value: q, placeholder: 'Search: words or "a phrase" (Ctrl+F)', "data-find": true,
    "aria-label": "Search the jobs to review" });
  const cat = el("select", { testid: "review-category", "aria-label": "Kind of work" });
  let timer = null;
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => setFilter(search.value.trim(), category), 300); });
  search.addEventListener("keydown", (e) => { if (e.key === "Escape") { search.value = ""; setFilter("", category); search.blur(); } });
  cat.addEventListener("change", () => setFilter(q, cat.value));
  fill(view, el("div", { class: "row between toolbar" }, sourceSeg ? el("div", { class: "row" }, sourceSeg, seg) : seg,
      el("span", { class: "small muted", id: "review-pos" })),
    el("div", { class: "row toolbar" }, search, cat), body);

  const listMode = () => !!q;  // a text search shows every match as a card; no search = one job at a time

  function setFilter(newQ, newCat) {
    if (newQ === q && newCat === category) return;
    if (!q && newQ && jobs[i]) resumeId = jobs[i].id;  // the job you were on stays undecided; clearing the search comes back to it
    q = newQ; category = newCat;
    sessionStorage.setItem("reviewQ", q);
    sessionStorage.setItem("reviewCat", category);
    load();
  }

  function drawCategories(list) {
    if (category && !list.some((c) => c.key === category)) list = list.concat([{ key: category, label: category.replace("_", " "), count: 0 }]);
    fill(cat, el("option", { value: "", text: `All kinds of work (${total})` }),
      ...list.map((c) => el("option", { value: c.key, text: `${c.label} (${c.count})` })));
    cat.value = category;
  }

  function setMode(m) {
    mode = m;
    sessionStorage.setItem("reviewMode", m);
    seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.testid === `mode-${m}`)));
    load();
  }

  function setSource(s) {  // the other list: its own queue, so the resume position (a job of the old list) is dropped
    if (s === source) return;
    source = s;
    sessionStorage.setItem("reviewSource", s);
    resumeId = null;
    sourceSeg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.testid === `source-${s}`)));
    load();
  }

  // The server answers at once with the list it made last (quick); when something changed since, the new list follows
  // (refresh) and takes its place without moving you.
  async function load() {
    const mine = ++loadSeq;  // only the newest load may show its list (an older, slower answer is dropped)
    jobs = []; i = 0; card = null; shown++;  // while it loads, no key decides on the old list (and no card of it comes back)
    const slow = setTimeout(() => { if (mine === loadSeq && body.isConnected) fill(body, el("p", { class: "empty", text: "Loading the review list…" })); }, 150);
    await queue;  // decisions still being written go first: the server decides on its last list, which this one replaces
    const params = new URLSearchParams({ mode, q, category, source });
    let r;
    try { r = await api.get(`/api/review?${params}&quick=1`); }
    catch (e) { clearTimeout(slow); if (mine === loadSeq && body.isConnected) fill(body, el("div", { class: "note fail", text: e.message })); return; }
    clearTimeout(slow);
    if (mine !== loadSeq || !body.isConnected) return;
    jobs = r.jobs; total = r.total; i = 0; listed = 0; cards.clear(); drawCategories(r.categories);
    if (!q && resumeId) { i = Math.max(0, jobs.findIndex((x) => x.id === resumeId)); resumeId = null; }
    show();
    if (r.stale) refresh(mine, params);
  }

  // The list made from what changed since the quick one: the job on screen stays (one that left the list: the next one
  // still in it), a search keeps how many it showed and where you scrolled, and the same jobs draw nothing again.
  async function refresh(mine, params) {
    let r;
    try { await queue; r = await api.get(`/api/review?${params}`); } catch (e) { return; }
    if (mine !== loadSeq || !body.isConnected) return;
    const old = jobs, was = new Map(old.map((j) => [j.id, JSON.stringify({ ...j, rating: null })]));
    const changed = (j) => was.get(j.id) !== JSON.stringify({ ...j, rating: null });  // (a rating made here is not a change)
    total = r.total; drawCategories(r.categories);
    if (r.jobs.length === old.length && r.jobs.every((j, k) => j.id === old[k].id && !changed(j))) return position();
    r.jobs.forEach((j) => { if (changed(j)) cards.delete(j.id); });  // (its card may say something new too)
    jobs = r.jobs;
    if (listMode()) { const had = listed, y = window.scrollY; showList(true, had); window.scrollTo(0, y); return; }
    const at = new Map(jobs.map((j, k) => [j.id, k]));
    const next = old.slice(i).find((j) => at.has(j.id));
    const same = next && old[i] && next.id === old[i].id && !changed(jobs[at.get(next.id)]);
    i = next ? at.get(next.id) : jobs.length;
    if (same) position(); else show();  // (the same job, unchanged: its card stays as it is)
  }

  function position() {
    const filtered = !!(q || category);
    $("#review-pos").textContent = !jobs.length ? "" : listMode() ? `${jobs.length} ${jobs.length === 1 ? "match" : "matches"} (${total} without the search) · j/k scroll` :
      `Job ${Math.min(i + 1, jobs.length)} of ${jobs.length}${filtered ? ` (${total} without the search)` : ""} · j/k scroll`;
  }

  async function show() {
    const filtered = !!(q || category);
    if (listMode()) return showList(filtered);
    position();
    if (!jobs.length && filtered) {
      fill(body, el("div", { class: "card", testid: "review-nomatch" }, el("h2", { text: "No jobs match" }),
        el("p", { class: "muted", text: `None of the ${total} jobs to review match this search.` }),
        el("button", { class: "btn primary", text: "Clear the search", onclick: () => { search.value = ""; setFilter("", ""); } })));
      return;
    }
    if (i >= jobs.length) {
      const toSites = mode === "approve" && source === "intern";  // approved internships wait in the Other jobs tab
      fill(body, el("div", { class: "card", testid: "review-empty" }, el("h2", { text: jobs.length ? "That's all of them" : "Nothing new to review" }),
        el("p", { class: "muted", text: mode !== "approve" ? "Ratings teach the ranking your taste (it needs 5 yes and 5 no)." :
          toSites ? `Approved internships wait in Apply > ${otherTabLabel()}.` :
          "Approved jobs are in your sheet. Apply to them next." }),
        el("div", { class: "row" }, jobs.length ? el("button", { class: "btn", text: "Back one", onclick: () => { i = Math.max(0, i - 1); show(); } }) : null,
          el("a", { class: "btn primary", href: toSites ? "#/other" : mode === "approve" ? "#/apply" : "#/home",
            text: mode === "approve" ? "Go to Apply" : "Done" }))));
      return;
    }
    const j = jobs[i], mine = ++shown;
    try { card = await getCard(j.id); }
    catch (e) { if (mine === shown) fill(body, el("div", { class: "note fail", text: e.message })); return; }
    if (mine !== shown) return;  // you already moved on
    fill(body, jobCard(card, j, history[j.id]), decideBar(j));
    window.scrollTo(0, 0);
    jobs.slice(i + 1, i + 3).forEach((n) => getCard(n.id).catch(() => {}));
  }

  // Search results: every match as its own card with its own buttons (the job you were on is left as it was).
  const PAGE = 8;
  function showList(filtered, upto = PAGE) {
    shown++;
    position();
    if (!jobs.length) {
      fill(body, el("div", { class: "card", testid: "review-nomatch" }, el("h2", { text: "No jobs match" }),
        el("p", { class: "muted", text: `None of the ${total} jobs to review match this search.` }),
        el("button", { class: "btn primary", text: "Clear the search", onclick: () => { search.value = ""; setFilter("", ""); } })));
      return;
    }
    const box = el("div", { class: "joblist", testid: "review-list" });
    const more = el("div", { class: "row center" });
    fill(body, box, more);
    const next = () => {
      jobs.slice(listed, listed + PAGE).forEach((j) => { const slot = el("div", { class: "slot", "data-job": j.id }); box.append(slot); drawItem(j, slot); });
      listed = Math.min(jobs.length, listed + PAGE);
      fill(more, listed < jobs.length ? el("button", { class: "btn", testid: "review-more", text: `Show ${Math.min(PAGE, jobs.length - listed)} more (${jobs.length - listed} left)`, onclick: next }) : "");
    };
    listed = 0;
    do next(); while (listed < Math.min(upto, jobs.length));
  }

  async function drawItem(j, slot) {
    const mine = history[j.id];
    if (mine && !mine.reopen) {  // decided here: a one-line result, and a way to change your mind
      fill(slot, el("div", { class: "card decided", testid: "list-decided" }, el("div", { class: "row between" },
        el("div", {}, el("b", { text: j.title || "" }), el("span", { class: "muted", text: ` · ${j.company || ""}` })),
        el("div", { class: "row" }, el("span", { class: "chip ok", text: mine.decision === "approve" ? "approved" : `you said ${mine.decision}` }),
          el("button", { class: "btn small", text: "Change", onclick: () => { mine.reopen = true; drawItem(j, slot); } })))));
      return;
    }
    fill(slot, el("div", { class: "card" }, el("p", { class: "empty", text: "Loading…" })));
    let c;
    try { c = await getCard(j.id); } catch (e) { fill(slot, el("div", { class: "note fail", text: e.message })); return; }
    const b = (text, decision, cls, testid) => el("button", { class: "btn " + (cls || ""), testid, onclick: () => listDecide(j, decision, slot) }, text);
    const card = jobCard(c, j, mine, true);
    card.append(el("div", { class: "row inline-decide" },
      mode === "approve" ? b("Approve", "approve", "primary", "btn-approve") : b("Would apply", "yes", "primary", "btn-yes"),
      b(mode === "approve" ? "Not for me" : "Wouldn't", "no", "", "btn-no"),
      el("a", { class: "btn ghost", href: c.url, target: "_blank", rel: "noopener noreferrer", testid: "btn-open", text: openLabel(c) })));
    fill(slot, card);
  }

  function listDecide(j, decision, slot) {
    const entry = history[j.id];
    if (entry) entry.reopen = false;
    submit(j, decision, () => drawItem(j, slot));
    drawItem(j, slot);
  }

  function decideBar(j) {
    const b = (text, key, decision, cls, testid) => el("button", { class: "btn " + (cls || ""), testid,
      onclick: () => decide(decision) }, text, el("kbd", { text: key }));
    return el("div", { class: "decide row" },
      mode === "approve" ? b("Approve", "Y", "approve", "primary", "btn-approve") : b("Would apply", "Y", "yes", "primary", "btn-yes"),
      b(mode === "approve" ? "Not for me" : "Wouldn't", "N", "no", "", "btn-no"),
      b("Skip", "S", "skip", "", "btn-skip"), b("Back", "U", "back", "ghost", "btn-back"),
      el("a", { class: "btn ghost", href: card.url, target: "_blank", rel: "noopener noreferrer", testid: "btn-open" }, openLabel(card), el("kbd", { text: "O" })));
  }

  // The next job shows at once; the sheet / ratings write happens behind it (one at a time, in order).
  // If it fails you are taken back to that job with the error (onfail).
  function decide(decision) {
    const j = jobs[i];
    if (!j) return;
    if (decision === "skip") { i++; return show(); }
    if (decision === "back") { i = Math.max(0, i - 1); return show(); }
    const index = i;
    submit(j, decision, () => { i = index; show(); });
    i++;
    show();
  }

  // Sends one decision for job j. Returns false when nothing needed sending (already approved and approved again).
  // An approved internship's row is in the Other jobs tab: the server says so (tab "other") and both undo calls repeat it.
  function submit(j, decision, onfail) {
    const before = history[j.id], oldRating = j.rating;
    if (before && before.decision === "approve" && decision === "approve") return false;  // already in the sheet
    const entry = { decision, row: null, tab: null, previous: before ? before.previous : null };
    history[j.id] = entry;
    j.rating = decision === "no" ? 0 : 1;
    let undone = false;
    queue = queue.then(async () => {
      if (before && before.row && decision !== "approve") {  // changed your mind about an approved job
        await api.post("/api/undo", { id: j.id, row: before.row, previous: before.previous, tab: before.tab || undefined });
        undone = true;
      }
      const r = await api.post("/api/decide", { id: j.id, decision, mode });
      entry.row = r.row;
      entry.tab = r.tab || null;
      if (!before) entry.previous = r.previous;
      pollState();
    }).catch((e) => {
      entry.failed = true;
      if (before && !undone) history[j.id] = before; else delete history[j.id];
      j.rating = oldRating;
      onfail();
      fail(e);
    });
    if (decision === "approve") {
      toast(source === "intern" ? "Approved: adding it to the Other jobs tab." : "Approved: adding it to the sheet.", { action: "Undo", onaction: async () => {
        try {
          await queue;
          if (entry.failed) return;
          await api.post("/api/undo", { id: j.id, row: entry.row, previous: entry.previous, tab: entry.tab || undefined });
          delete history[j.id];
          j.rating = oldRating;
          toast(entry.tab === "other" ? "Undone: the row is back to Proposed (Other jobs tab)." : "Undone: the row is back to Proposed.");
          pollState();
        } catch (e) { fail(e); }
      } });
    }
    return true;
  }

  // vim-style scrolling like the CLI viewer: j/k line, space/b page, g/G top/bottom. Held keys scroll without easing.
  function scrollKey(e) {
    const to = (top) => () => { document.activeElement && document.activeElement.blur && document.activeElement.blur();
      window.scrollTo({ top, behavior: e.repeat ? "auto" : "smooth" }); };
    const by = (dy) => () => { document.activeElement && document.activeElement.blur && document.activeElement.blur();
      window.scrollBy({ top: dy, behavior: e.repeat ? "auto" : "smooth" }); };
    const page = Math.max(120, window.innerHeight - 160);  // the button bar covers the bottom
    if (e.key === "G") return to(document.documentElement.scrollHeight);
    if (e.shiftKey && e.key !== " ") return null;
    if (e.key === "j") return by(70);
    if (e.key === "k") return by(-70);
    if (e.key === " ") return by(e.shiftKey ? -page : page);
    if (e.key === "b") return by(-page);
    if (e.key === "g") return to(0);
    return null;
  }

  const keys = (e) => {
    if (e.target.closest("input, select, textarea") || $("#modal-root").children.length || e.ctrlKey || e.metaKey || e.altKey) return;
    const k = e.key.toLowerCase();
    const scroll = scrollKey(e);
    if (scroll) { scroll(); e.preventDefault(); return; }
    if (listMode()) return;  // in a list the buttons decide: y / n would not say which card
    if (k === "y") decide(mode === "approve" ? "approve" : "yes");
    else if (k === "n") decide("no");
    else if (k === "s") decide("skip");
    else if (k === "u") decide("back");
    else if (k === "o" && card) openExternal(card.url);
    else return;
    e.preventDefault();
  };
  document.addEventListener("keydown", keys);
  await load();
  return { leave: () => {
    document.removeEventListener("keydown", keys);
    // ratings to the homelab; never in view mode (it refuses every POST, which the page would read as a dead server)
    if (!(S.state && S.state.view)) queue.then(() => api.post("/api/review/done", {})).catch(() => {});
  } };
};

// The open-the-posting button: an internship's posting is on the company's own site, a co-op's on NUworks.
const openLabel = (c) => c && c.source === "simplify" ? "Open posting" : "Open on NUworks";

// What a decided job's chip says; an internship's row is in the Other jobs tab.
function decidedText(mine) {
  if (mine.decision !== "approve") return `you said ${mine.decision}`;
  return mine.row ? `approved · ${mine.tab === "other" ? "Other jobs row" : "row"} ${mine.row}` : "approved";
}

function jobCard(c, j, mine, compact) {  // compact: the posting is cut short, with a button for the rest
  // An internship whose posting could not be read has no match, threshold or description (c.unread): say so, no margin math.
  const unscored = c.match === null || c.match === undefined;
  const margin = unscored || c.threshold == null ? null : c.match - c.threshold;
  const facts = [
    ["Match", unscored ? el("span", { class: "muted", text: "not scored (the posting could not be read)" }) :
      el("span", {}, el("span", { class: "match " + (margin !== null && margin >= 15 ? "good" : "ok"), text: `${c.match}%` }),
        c.threshold != null ? el("span", { class: "muted", text: ` · needs ${c.threshold}%` }) : null)],
    unscored ? null : ["Score", el("span", {}, c.score, c.taste !== null && c.taste !== undefined ?
      el("span", { class: "muted", text: ` · your taste ${Math.round(c.taste * 100)}%` }) : null)],
    j.order ? ["Order", el("span", { class: "muted", testid: "job-order", text: j.order })] : null,
    c.posted_text ? ["Posted", c.posted_text.replace(/^(posted|posting) /i, "")] : null,  // "posted 5 days ago" reads "5 days ago" there
    ["Closes", el("span", { class: c.soon ? "chip warn" : "", text: c.closes_text })],
    c.year_text ? ["Who can apply", c.year_text] : null,  // the posting's own words about class year / degree
    ["Pay", c.pay || "not listed"],
    c.external ? ["Apply", el("span", { class: "chip warn", text: `Also on the company site? "${c.external}"` })] : null,
    c.flags.length ? ["Flags", el("span", { class: "row" }, ...c.flags.map((f) => el("span", { class: "chip", text: f })))] : null,
    c.skills.length ? ["Skills", c.skills.join(", ")] : null,
  ].filter(Boolean);
  const desc = (blocks) => {
    const out = [];
    let ul = null;
    for (const b of blocks) {
      if (b.kind === "bullet") { if (!ul) { ul = el("ul"); out.push(ul); } ul.append(el("li", { text: b.text })); continue; }
      ul = null;
      out.push(b.kind === "header" ? el("h3", { text: b.text }) : el("p", { text: b.text }));
    }
    return out;
  };
  return el("article", { class: "card job", testid: "job-card", "data-job": c.id },
    el("div", { class: "row between" }, el("div", { class: "row" }, el("span", { class: "chip accent", text: c.category_label || c.category.replace("_", " ") }),
      c.tag ? el("span", { class: "chip", text: c.tag }) : null, j.soon ? el("span", { class: "chip warn", text: "closing soon" }) : null,
      mine ? el("span", { class: "chip ok", text: decidedText(mine) }) :
        j.rating === 1 ? el("span", { class: "chip ok", text: "rated yes" }) : j.rating === 0 ? el("span", { class: "chip", text: "rated no" }) : null)),
    el("h2", { text: c.title, testid: "job-title" }),
    el("div", { class: "meta", text: `${c.company} · ${c.location || "location not listed"}` }),
    el("dl", { class: "facts" }, ...facts.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", {}, v)])),
    c.why ? el("div", { class: "why" }, el("b", { text: c.unread ? "Could not read it: " : "Why: " }), c.why) : null,
    compact && !c.unread ? clamped(el("div", { class: "desc" }, desc(c.description), c.qualifications.length ? [el("h3", { text: "Qualifications" }), desc(c.qualifications)] : null)) :
    el("div", { class: "desc" }, desc(c.description), c.qualifications.length ? [el("h3", { text: "Qualifications" }), desc(c.qualifications)] : null));
}

function clamped(node) {  // a long posting cut to a few lines until you ask for all of it
  node.classList.add("clamp");
  const btn = el("button", { class: "btn small ghost", testid: "job-expand", text: "Show the whole posting", "aria-expanded": "false", onclick: () => {
    const open = node.classList.toggle("clamp") === false;
    btn.textContent = open ? "Show less" : "Show the whole posting";
    btn.setAttribute("aria-expanded", String(open));
  } });
  return el("div", {}, node, btn);
}

// The match % (or the score Start orders by, when bonuses add to it) and pay a NUworks row shows in the Apply lists.
function jobFacts(r) {
  const fit = r.score != null && r.score !== r.match ? `score ${r.score_text}` : r.match != null ? `${r.match}% match` : "match not scored";
  return el("div", { class: "small muted", testid: "job-facts", text: [fit, r.pay ? `pay ${r.pay}` : "pay not listed"].join(" · ") });
}

// Sorting and filtering the Apply tabs' lists. On the NUworks tab the sort is the order Start goes (the server sorts:
// apply.ORDERS, `nuauto apply --order`); elsewhere it is the view only. The kind-of-work filter is the view only.
// Rows missing the value go last, ties keep the list's own order.
const SORTS = {
  default: ["closing within a week first, then score", null],
  sheet: ["sheet order", null],
  score: ["score (match + bonuses)", (r) => r.score == null ? null : -r.score],
  match: ["match %", (r) => r.match == null ? null : -r.match],
  closes: ["closing soonest", (r) => r.closes || null],
  pay: ["pay (per hour)", (r) => r.pay_hour == null ? null : -r.pay_hour],
  // the homelab's page: the queue's own order (what it starts next first; NUworks-tab rows)
  queue: ["the queue's order", (r) => { const q = S.queue && S.queue.rows && S.queue.rows[r.qkey || `jobs-${r.row}`]; return q ? q.order : null; }],
};
const NO_KIND = "-";  // the kind-of-work filter's key for rows not sorted into one yet
const kindOf = (r) => r.category || NO_KIND;
const kindLabel = (k, rows) => k === NO_KIND ? "not sorted yet" :
  ((rows || []).find((r) => r.category === k) || {}).category_label || k.replace(/_/g, " ");
function sortRows(rows, how) {
  const key = SORTS[how] && SORTS[how][1];
  if (!key) return rows;
  return rows.map((r, i) => [key(r), i, r]).sort((a, b) => (a[0] === null) - (b[0] === null) ||
    (a[0] === null ? 0 : a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0) || a[1] - b[1]).map((x) => x[2]);
}

// ---- Apply: your sheet's two tabs, each a tab here (NUworks co-ops: the weekly limit, Start goes through its Approved
// rows; the Other jobs tab: Summer internships and other jobs not on NUworks, no limit, the assistant), and the
// assistants running now. A sheet tab shows every one of its rows in sections, what is left to do first (the rest
// folded); every row has the same buttons: what to do next, Open, and More (I applied myself, Drop, Move, Remove).
const NU_SECTIONS = [
  ["approved", "Ready to apply", "Start goes down this list in this order, and stops at your weekly limit."],
  ["agent", "Company site needed", "These send you to the company's own site. The assistant (Claude) applies there with you, and asks you before anything is submitted."],
  ["manual", "By hand on NUworks", "NUworks wants something only you can give (a cover letter, a transcript, an answer in your words). The assistant can do these with you on NUworks: it drafts the cover letter for you to approve, uploads it and your transcript, and asks you before it submits."],
  ["owed", "Still owed", "Submitted on one side; the other side still needs doing."],
  ["applied", "Applied", null],
  ["accepted", "Accepted", null],
  ["rejected", "Rejected", null],
  ["proposed", "Proposed", "Waiting for your OK: Review shows them first."],
  ["closed", "Dropped or failed", null],
  ["rest", "Other rows", null],
];
const OTHER_SECTIONS = [
  ["ready", "Ready to apply", "Apply starts the assistant (Claude) on that job's own site; nothing is submitted without your review."],
  ["applied", "Applied", null],
  ["accepted", "Accepted", null],
  ["rejected", "Rejected", null],
  ["proposed", "Proposed", "Waiting for your OK: Review shows them first."],
  ["closed", "Dropped or failed", null],
  ["rest", "Other rows", null],
];
const TODO = new Set(["approved", "agent", "manual", "owed", "ready"]);  // open unless you fold them; the rest start folded
const findText = (r) => [r.company, r.title, r.notes, r.host, r.category_label, r.status, r.pay].join(" ").toLowerCase();

// The Other jobs tab's name here: what it holds when Summer internships are on (state.internships).
function otherTabLabel() {
  const ii = S.state && S.state.internships;
  return ii && ii.on && ii.term ? `${ii.term} internships` : "Other jobs";
}

screens.apply = async (view) => {
  const TABS = ["nuworks", "other", "assistants"];
  let tab = sessionStorage.getItem("applyTab");
  if (!TABS.includes(tab)) tab = "nuworks";
  if (S.focus) { tab = S.focus === "other" ? "other" : "nuworks"; S.focus = null; }  // Today's buttons, #/company, #/other
  const data = { nuworks: null, other: null };
  const SORT_OPTS = { nuworks: ["default", "score", "match", "closes", "pay"],
    other: (onHomelab() ? ["queue"] : []).concat(["score", "match", "pay", "sheet"]) };
  const sortOf = (k) => { const v = sessionStorage.getItem("applySort:" + k); return SORT_OPTS[k].includes(v) ? v : SORT_OPTS[k][0]; };
  let kind = sessionStorage.getItem("applyKind") || "";  // one kind of work, or "" = all (shared by the tabs)
  let words = [];  // the search box's words: a row shows when it has every one
  let folded = {};  // the sections you opened or folded, "tab:section" -> open
  try { folded = JSON.parse(sessionStorage.getItem("applyOpen") || "{}"); } catch (e) { folded = {}; }
  let drawnRunning = null, drawnHomelab = "", drawnQueue = "";
  // Made once and moved into each redraw, so what you typed (and the focus) is never lost.
  const atMost = el("input", { type: "number", min: "1", id: "apply-n", testid: "apply-n", "aria-label": "At most this many", placeholder: "all" });
  const find = el("input", { type: "search", class: "grow", testid: "apply-search", "data-find": true,
    placeholder: "Search this tab (Ctrl+F)", "aria-label": "Search the jobs in this tab" });
  find.addEventListener("input", () => { words = find.value.toLowerCase().split(/\s+/).filter(Boolean); drawPane(); });
  find.addEventListener("keydown", (e) => { if (e.key === "Escape") { find.value = ""; words = []; drawPane(); } });
  // the pane: the tab's head (the week, Start), the toolbar (stays put while you type in its search), the sections
  const runBox = el("div"), tabs = el("div", { class: "tabs", role: "tablist", "aria-label": "Apply" }),
    headBox = el("div"), bar = el("div"), listBox = el("div"),
    pane = el("div", { role: "tabpanel", id: "apply-pane", testid: "apply-pane" }, headBox, bar, listBox);
  fill(view, runBox, tabs, pane);

  let reloads = 0, loadedAt = 0;  // the newest reload wins: an older one answered last (read before a change) would undo it
  async function reload(opening) {  // opening: the server answers from the rows read last and reads the sheet again behind it
    const mine = ++reloads, again = opening === true ? "&recheck=1" : "";
    loadedAt = Date.now();
    // each tab is drawn as soon as its own answer comes (the other one may still be reading the sheet)
    const one = (t, path) => api.get(path).catch((e) => ({ error: e.message })).then((d) => {
      if (mine !== reloads) return;
      // these rows are as new as that version: the poll does not reload the lists again for it (an open menu stays open)
      if (ok(d) && d.sheet_version !== undefined) S.sheetVersion = Math.max(S.sheetVersion || 0, d.sheet_version);
      if (ok(d)) Object.values(d.sections).flat().forEach((r) => { r.qkey = `${t === "other" ? "other" : "jobs"}-${r.row}`; });  // its key in the homelab's queue
      data[t] = d;
      S.queue = (data.nuworks && data.nuworks.queue) || (data.other && data.other.queue) || null;  // (the homelab's queue) each company-site row's place in it
      draw();
    });
    await Promise.all([one("nuworks", `/api/jobs?tab=nuworks&order=${sortOf("nuworks")}${again}`), one("other", `/api/jobs?tab=other${again}`)]);
  }

  function running() { return S.task && S.task.kind === "apply" && S.task.state === "running"; }
  const ok = (d) => d && !d.error;
  const names = () => ({ nuworks: "NUworks co-ops", other: otherTabLabel() });  // the tabs as this screen names them
  function sessions() {  // the assistants running now: here (or, on the homelab's page, there) and on the homelab
    const d = ok(data.nuworks) ? data.nuworks : ok(data.other) ? data.other : {};
    return { d, here: d.sessions || [], there: d.homelab || [] };
  }
  const todo = (d, keys) => ok(d) ? keys.reduce((n, k) => n + d.sections[k].length, 0) : d ? 0 : null;  // null: not read yet
  function counts() {
    const s = sessions();
    return { nuworks: todo(data.nuworks, ["approved", "agent", "manual", "owed"]), other: todo(data.other, ["ready"]),
      assistants: s.here.length + s.there.length };
  }

  function drawTabs() {
    const n = counts(), label = { nuworks: "NUworks co-ops", other: otherTabLabel(), assistants: "Assistants" };
    fill(tabs, ...TABS.map((k) => el("button", { role: "tab", testid: `tab-${k}`, "aria-selected": String(tab === k), "aria-controls": "apply-pane",
      onclick: () => { tab = k; sessionStorage.setItem("applyTab", k); drawTabs(); drawPane(); } },
      label[k], el("span", { class: "count", text: n[k] === null ? "…" : String(n[k]) }))));
  }

  // Search, kind of work, the order, Add a job: the same for both sheet tabs
  function drawBar() {
    const d = data[tab];
    if (tab === "assistants" || !ok(d)) return fill(bar);
    if (find === document.activeElement) return;  // typing in the search: its box stays as it is
    const rows = Object.values(d.sections).flat();
    const n = {};
    rows.forEach((r) => { n[kindOf(r)] = (n[kindOf(r)] || 0) + 1; });
    const keys = Object.keys(n).filter((k) => k !== NO_KIND || Object.keys(n).length > 1).sort((a, b) => n[b] - n[a] || a.localeCompare(b));
    if (kind && !n[kind]) keys.push(kind);
    const cat = keys.length ? el("select", { testid: "apply-kind", "aria-label": "Kind of work",
      onchange: (e) => { kind = e.target.value; sessionStorage.setItem("applyKind", kind); drawPane(); } },
      el("option", { value: "", text: "All kinds of work" }),
      ...keys.map((k) => el("option", { value: k, text: `${kindLabel(k, rows)} (${n[k] || 0})`, selected: k === kind }))) : null;
    const how = sortOf(tab);
    const sel = el("select", { testid: "apply-sort", "aria-label": tab === "nuworks" ? "Apply in this order" : "Sort the list by",
      title: tab === "nuworks" ? "Start (and At most) goes down Ready to apply in this order." : null,
      onchange: (e) => { sessionStorage.setItem("applySort:" + tab, e.target.value); if (tab === "nuworks") reload(); else drawPane(); } },
      ...SORT_OPTS[tab].map((k) => el("option", { value: k, text: "Sort: " + SORTS[k][0], selected: k === how })));
    fill(bar, el("div", { class: "row toolbar", testid: "apply-toolbar" }, find, cat, sel,
      el("button", { class: "btn", testid: "add-job", text: "Add a job", onclick: () => addJob(tab === "other" ? "other" : "nuworks", (t) => { tab = t; sessionStorage.setItem("applyTab", t); reload(); }) })));
  }

  function drawPane() {
    drawnRunning = running();
    drawnQueue = queueKey();
    drawBar();
    const d = data[tab];
    fill(headBox, !ok(d) ? null : tab === "nuworks" ? weekCard(d) : tab === "other" ? otherCard(d) : null);
    if (tab === "assistants") return fill(listBox, assistantsPane(sessions()));
    if (!d) return fill(listBox, el("p", {}, el("span", { class: "spinner" })));
    if (d.error) return fill(listBox, el("div", { class: "note fail", text: d.error }));
    const filtering = !!(words.length || kind);
    const fits = (r) => (!kind || kindOf(r) === kind) && words.every((w) => findText(r).includes(w));
    let found = 0;
    const cards = (tab === "nuworks" ? NU_SECTIONS : OTHER_SECTIONS).map(([key, title, note]) => {
      let all = d.sections[key];
      if (key === "approved") all = all.map((r, k) => ({ ...r, place: k + 1 }));  // where Start takes it
      const first = key === "approved" || key === "ready";
      if (!all.length && !first) return null;
      const rows = all.filter(fits);
      found += rows.length;
      if (filtering && !rows.length) return null;
      return section(d, key, title, note, all.length, key === "approved" ? rows : sortRows(rows, sortOf(tab)), filtering);
    });
    fill(listBox, filtering && !found ? el("div", { class: "card", testid: "apply-nothing" }, el("p", { class: "empty", text: "Nothing in this tab matches." }),
        el("div", { class: "row center" }, el("button", { class: "btn", text: "Clear the search", onclick: () => {
          find.value = ""; words = []; kind = ""; sessionStorage.setItem("applyKind", ""); drawBar(); drawPane(); } }))) : null,
      ...cards, tab === "nuworks" ? pastCard(d) : null);
  }

  function section(d, key, title, note, total, rows, filtering) {
    const id = `${tab}:${key}`;
    const open = filtering || (id in folded ? folded[id] : TODO.has(key));
    const box = el("details", { class: "card section", testid: `section-${tab}-${key}`, open });
    box.addEventListener("toggle", () => {
      if (filtering) return;  // a search opens every section with a match; your own choice stays for later
      folded[id] = box.open;
      sessionStorage.setItem("applyOpen", JSON.stringify(folded));
    });
    const empty = key === "approved" ? "Nothing approved yet. Approve jobs in Review." :
      key === "ready" ? (d.exists ? "Nothing approved here. Approve internships in Review, or Add a job." : "No jobs yet. Approve internships in Review, or Add a job.") : "None.";
    box.append(...[el("summary", {}, el("h2", { text: title }), el("span", { class: "count", text: rows.length === total ? String(total) : `${rows.length} of ${total}` })),
      note ? el("p", { class: "small muted", text: note }) : null,
      key === "approved" && rows.length !== total ? el("p", { class: "small muted", testid: "apply-filter-note", text: `Start still goes through all ${total}, in this order.` }) : null,
      rows.length ? el("ul", { class: "list", testid: key === "approved" ? "approved-list" : `list-${tab}-${key}` }, ...rows.map((r) => jobRow(d, key, r))) :
        el("p", { class: "empty", text: empty })].filter(Boolean));
    return box;
  }

  // One row: who and what, its facts, a line about it; then its chips, what to do next, Open, and More.
  function jobRow(d, key, r) {
    const other = tab === "other";
    let chips = [], next = null, open = linkOut(r.url, "Open"), about = null;
    if (key === "approved") {
      chips = [r.company_site ? el("span", { class: "chip", text: "may also want the company's site", title: r.company_site }) : null, dueChip(r)];
      next = applyButton(d, r);
    } else if (key === "agent") {
      chips = [dueChip(r)];
      about = r.host;
      next = assistantStart(d, r, "jobs", `assist-${r.row}`, { body: { row: r.row } });
      open = linkOut(r.target, "Open");
    } else if (key === "manual") {  // the assistant works it on NUworks itself (your cover letter, the transcript...)
      chips = [dueChip(r)];
      about = r.notes || r.why;
      next = r.hand ? assistantStart(d, r, "jobs", `assist-${r.row}`, { body: { row: r.row } }) :
        el("span", { class: "chip", testid: `assist-${r.row}-why`, text: `no assistant: ${r.hand_why}` });
    } else if (key === "owed" && r.owed === "site") {
      chips = [dueChip(r)];
      about = (r.notes || "").slice(0, 160);
      next = el("button", { class: "btn small primary", testid: `site-btn-${r.row}`, text: "Mark the company site done", onclick: () => markRow("site", r) });
    } else if (key === "owed") {
      about = (r.notes || "").slice(-160);
      next = el("button", { class: "btn small primary", text: "Submit on NUworks too", testid: `retry-btn-${r.row}`,
        onclick: () => confirmBox("Submit on NUworks too?", `${r.company} · ${r.title} is Applied on the company site. This submits the same job on NUworks.`,
          "Submit on NUworks", () => action("nuworks_side", runArgs({ row: r.row }))) });
    } else if (key === "ready") {  // (when it was posted where NUworks rows say when they close)
      chips = [r.posted_text ? el("span", { class: "chip", testid: "posted", text: r.posted_text }) : null];
      about = r.host || r.why;
      next = assistantStart(d, r, "other", `other-assist-${r.row}`, { disabled: !r.target, body: { row: r.row, tab: "other" } });
      open = r.target ? linkOut(r.target, "Open") : null;
    } else {
      about = [SENT.includes(r.status) && r.date ? `applied ${r.date}` : null, r.notes ? r.notes.slice(0, 140) : null].filter(Boolean).join(" · ");
      const nu = r.nuworks;  // what NUworks said at the last check (the twice-daily update)
      if (nu && nu.current && nu.applied === false) chips.push(el("span", { class: "chip warn", testid: `not-on-nuworks-${r.row}`,
        text: "not on NUworks", title: `NUworks has no application for this job (checked ${nu.checked}): it doesn't count toward your total.` }));
      if (nu && nu.applied && nu.interviewing) chips.push(el("span", { class: "chip accent", text: "interviewing (NUworks)" }));
    }
    const facts = r.match != null || r.score != null || r.pay ? jobFacts(r) : null;
    return el("li", { class: "job-row", testid: `job-${tab}-${r.row}` },
      el("div", { class: "what" }, key === "approved" ? el("span", { class: "muted", text: `${r.place}. `, title: `Start applies to this one ${ordinal(r.place)}` }) : null,
        el("b", { text: r.company || "(no company)" }), r.title ? ` · ${r.title}` : "", facts,
        about ? el("div", { class: "small muted", text: about }) : null),
      el("div", { class: "row" }, ...chips, el("span", { class: "acts" }, next, el("span", { class: "tail" }, open, rowMenu(d, key, r, other)))));
  }

  // More: the same four for every row (each greyed out with the reason when it can't be done to this one)
  function rowMenu(d, key, r, other) {
    const t = other ? "other" : undefined, to = other ? "nuworks" : "other", busy = d.busy ? "A run is using the sheet: wait for it to end" : null;
    const live = r.status === "Approved" || r.status === "Needs Human";
    // what you heard back, on a sent row: Accepted / Rejected (the one it is now: back to Applied)
    const heard = !SENT.includes(r.status) ? [] : ["Accepted", "Rejected"].map((o) => r.status === o ?
      ["Back to Applied", "applied", () => outcomeRow(r, "Applied", t)] :
      [`Mark ${o.toLowerCase()}`, o.toLowerCase(), () => outcomeRow(r, o, t)]);
    return menuButton(`More for ${r.company}`, `more-${tab}-${r.row}`, [
      ...heard,
      ["I applied myself", "mark", () => markRow("applied", r, t), live ? null : "Only for rows still to do (Approved or Needs Human)"],
      ["Drop (not applying)…", "drop", () => dropRow(r, t), live ? null : "Only Approved or Needs Human rows are dropped"],
      [`Move to ${names()[to]}…`, "move", () => moveRow(names(), other ? "other" : "nuworks", r, reload), r.locked || busy],
      ["Remove from the sheet…", "remove", () => removeRow(names(), other ? "other" : "nuworks", r, reload), r.remove_locked || busy, true],
    ]);
  }

  function weekCard(d) {
    const w = d.week;
    return el("div", { class: "card", testid: "apply-week" },
      el("div", { class: "row between" }, el("div", {},
        el("h2", { text: w.room ? `You can apply to ${plural(w.room, "more job")} this week` : "Weekly limit reached" }),
        el("p", { class: "small muted" }, `${w.applied} of ${w.max} ${w.label} · ${w.total} of ${w.max_total} in total` + (w.next ? ` · new week ${w.next}` : "") + " · ", el("a", { href: "#/settings/applying", text: "change the limit" }))),
      drawnRunning ? null : el("div", { class: "row" },
        runOnSwitch(),
        el("label", { class: "small muted" }, "At most ", atMost),
        el("button", { class: "btn primary big", testid: "btn-start-apply", disabled: !!d.why_not || !!(S.task && S.task.state === "running"),
          text: runOn() === "homelab" ? "Start on the homelab" : "Start applying", onclick: startApply }))),
      followNote(),
      d.why_not ? el("div", { class: "note warn", testid: "apply-why", text: d.why_not }) :
        el("details", { class: "how" }, el("summary", { text: "How Start works" }),
          el("p", { class: "small muted", text: (onHomelab() ? "Approved in the sheet is your go-ahead. Each job is filled and submitted in a browser on the homelab, which you watch and use from here (its link shows when it starts), one at a time, 10–20 s apart. Only one machine applies at a time." : "Approved in the sheet is your go-ahead. Each job is filled and submitted in a visible Firefox window, one at a time, 10–20 s apart.") + " Every form is screenshotted before Submit. Questions without a saved answer come up here. A job that sends you to the company's own site, or needs something only you can give (a cover letter, a transcript), stops (nothing submitted) and moves to Company site needed or By hand on NUworks below. While a run goes, a row's Apply adds it to the run's queue: queued rows are done next, in the order you clicked. Stop works like Ctrl+C (and empties the queue)." })));
  }

  // Run on: this computer / the homelab (the laptop linked to it): where Start, and each row's Apply, run. Both tabs.
  function runOnSwitch() {
    return canRunThere() ? el("div", { class: "seg", role: "group", "aria-label": "Run on", testid: "run-on" },
      ...[["here", "This computer"], ["homelab", "The homelab"]].map(([k, label]) => el("button", { "aria-pressed": String(runOn() === k),
        testid: `run-on-${k}`, text: label, onclick: () => { sessionStorage.setItem("runOn", k); drawPane(); } }))) : null;
  }

  // The other tab's head, laid out like the week's: no limit here, how many are ready and sent, Run on, how Apply works
  function otherCard(d) {
    const s = d.sections, sent = s.applied.length + s.accepted.length + s.rejected.length;
    const where = onHomelab() ? "in a browser on the homelab, whose screen you watch and answer from here" :
      "in its own terminal window and browser here" + (canRunThere() ? " (Run on: the homelab: on the homelab, you answer it on its screen)" : "");
    return el("div", { class: "card", testid: "apply-other-head" },
      el("h2", { text: "You can apply to as many as you like" }),
      el("p", { class: "small muted", text: `${s.ready.length} ready · ${sent} applied · no weekly or total limit here` }),
      canRunThere() ? el("div", { class: "row" }, runOnSwitch()) : null,
      el("details", { class: "how" }, el("summary", { text: "How Apply works" }),
        el("p", { class: "small muted", text: `Approved in the sheet is your go-ahead. Apply on a row starts the assistant (Claude) on that job's own site, ${where}; up to ${d.slots || 1} run side by side. It fills what your saved answers cover, guesses the rest and records each guess, then asks you everything at once before Submit. Nothing is submitted without your review.` })));
  }

  function pastCard(d) {
    if (!d.history.length) return null;
    const box = el("details", { class: "card section", testid: "section-nuworks-runs" },
      el("summary", {}, el("h2", { text: "Recent runs here" }), el("span", { class: "count", text: String(d.history.length) })),
      el("ul", { class: "list" }, ...d.history.map((t) => el("li", {}, el("div", { class: "what" }, el("b", { text: t.label }), " · ",
        el("span", { class: "muted", text: `${t.state} · ${ago(t.started)}` }))))),
      el("p", {}, el("a", { href: "#/logs", text: "All logs and screenshots" })));
    return box;
  }

  function draw() {
    drawTabs();
    drawPane();
    drawRun();
  }

  function drawRun() {
    const t = S.task;
    const live = t && t.state === "running";
    // a finished run's box stays until you close it (×; remembered for the session, per run)
    if (!t || t.kind !== "apply" || (!live && sessionStorage.getItem("runClosed") === String(t.id))) { fill(runBox, ); return; }
    const now = Date.now() / 1000;
    const waiting = live && t.wait_until && t.wait_until > now;
    fill(runBox, el("div", { class: "card", testid: "apply-run" },
      el("div", { class: "row between runtop" }, el("div", { class: "row" }, live ? el("span", { class: "spinner" }) : null,
        el("h2", { text: live ? (waiting ? `Next job in ${Math.ceil(t.wait_until - now)} s` : S.taskWhere === "homelab" ? "Applying on the homelab…" : "Applying…") : `Last run${S.taskWhere === "homelab" ? " on the homelab" : ""}: ${t.state}` })),
        live ? el("div", { class: "row" }, el("button", { class: "btn danger", testid: "btn-stop", text: "Stop", onclick: () => stopTask(t) }),
          el("button", { class: "btn ghost small", text: "Force stop", title: "Only if Stop does nothing", onclick: () =>
            confirmBox("Force stop?", "Kills the run at once. If Submit was already clicked, that row stays Needs Human: check NUworks.", "Force stop", () => stopTask(t, true), { danger: true }) })) :
          el("button", { class: "btn ghost small", testid: "btn-run-close", text: "×", title: "Close", "aria-label": "Close the last run's box",
            onclick: () => { sessionStorage.setItem("runClosed", String(t.id)); fill(runBox, ); } })),
      t.browser_view ? el("p", { class: "note accent", testid: "apply-browser-view" }, el("b", { text: "Its browser: " }),
        linkOut(t.browser_view.url, "watch and use it here"), " · password ", el("code", { text: t.browser_view.password }),
        " (the homelab's screen; for this run only)") : null,
      el("div", { class: "runhead" },
        el("div", {}, live && t.row ? el("p", {}, el("span", { class: "muted", text: "Now: " }), el("b", { text: t.row.company }), " · ", t.row.title) : null,
          live ? (t.done_rows.length ? el("p", { class: "small muted", text: `${plural(t.done_rows.length, "job")} done in this run` }) : null) :
            el("p", { class: "small muted", text: `${plural(t.done_rows.length, "job")} handled. Applied rows are under Applied; anything that needs you is in its own section, and on Today.` })),
        t.screenshot ? el("div", {}, el("img", { class: "shot thumb", src: t.screenshot, alt: "Latest screenshot of the form", testid: "apply-shot",
          onclick: () => modal((box, close) => box.append(el("img", { class: "full", src: t.screenshot, alt: "Screenshot" }),
            el("div", { class: "foot" }, el("span"), el("button", { class: "btn primary", text: "Close", onclick: close }))), { label: "Screenshot" }) })) : null),
      el("pre", { class: "log", testid: "task-log", tabindex: "0", "aria-label": "Run log", text: S.taskLog.slice(-400).join("\n") || "Starting…" })));
    const pre = runBox.querySelector(".log");
    pre.scrollTop = pre.scrollHeight;
  }

  // A row's Apply: just this job now. While an Apply run goes, it joins that run's queue instead (done next, in the
  // order you clicked; the button again takes it out).
  function applyButton(d, r) {
    const t = S.task, live = running(), q = (live && t.queue) || [], at = q.indexOf(r.row), testid = `btn-apply-row-${r.row}`;
    if (live && t.row && t.row.row === r.row && !t.done_rows.some((x) => x.row === r.row)) return el("button", { class: "btn small", testid, disabled: true, text: "Applying…" });
    if (at >= 0) return el("button", { class: "btn small", testid, text: `Queued #${at + 1}`, title: "Take it out of the queue",
      onclick: () => queueRow(r, false) });
    return el("button", { class: "btn small", testid, disabled: !!d.why_not || !!(S.task && S.task.state === "running" && !live),
      text: live ? "Add to queue" : "Apply", title: live ? "Apply to it next in this run" : "Apply to just this job now",
      onclick: () => live ? queueRow(r, true) : applyRow(r) });
  }

  function queueKey() { return running() ? `${(S.task.row || {}).row}:${(S.task.queue || []).join(",")}` : ""; }

  async function queueRow(r, add) {  // the run's own machine (a run followed from the homelab: there)
    const res = await action(add ? "apply" : "apply_unqueue", S.taskWhere === "homelab" ? { row: r.row, on: "homelab" } : { row: r.row });
    if (res && res.queue && running()) { S.task.queue = res.queue; drawPane(); }
  }

  function applyRow(r) {
    confirmBox(`Apply to ${r.company}?`, `${r.title} is filled and submitted now in ${onHomelab() || runOn() === "homelab" ? "a browser on the homelab you can watch from here" : "a visible Firefox window"}, the same way Start does it. Only this job.`,
      "Apply now", async () => { if (await action("apply", runArgs({ row: r.row }))) drawPane(); });
  }

  async function startApply() {
    const n = atMost.value;
    const r = await action("apply", runArgs(n ? { n, order: sortOf("nuworks") } : { order: sortOf("nuworks") }));
    if (r) drawPane();
  }

  // (laptop, linked) a run going on the homelab that this app is not following yet: one tap follows it here
  function followNote() {
    const t = S.state && S.state.homelab_link && S.state.homelab_link.task;
    if (!t || t.state !== "running" || (S.taskWhere === "homelab" && S.task && S.task.id === t.id)) return null;
    return el("div", { class: "note accent", testid: "homelab-run" }, el("b", { text: "Running on the homelab: " }), t.label, " ",
      el("button", { class: "btn small", text: "Follow it here", testid: "btn-follow-homelab", onclick: () => { followHomelabTask(); drawPane(); } }));
  }

  await reload(true);
  // refresh: the sheet changed (a run finished, a row was applied, something was done elsewhere): read the lists again
  return { task: () => { drawRun(); if (S.task && S.task.state !== "running") reload(); else if (queueKey() !== drawnQueue) drawPane(); },
    update: () => {  // redraw when a run starts or ends here, or one starts or ends on the homelab (its "Follow it here")
      const ht = S.state && S.state.homelab_link && S.state.homelab_link.task, key = ht ? `${ht.id}:${ht.state}` : "";
      if (running() !== drawnRunning || key !== drawnHomelab) { drawnHomelab = key; drawPane(); }
      if (S.queue && Date.now() - loadedAt > 30000) reload();  // the homelab's queue moves on by itself (its hours, its waits)
    }, refresh: reload, reload };
};

// A small menu of row actions: [label, key, do it, why it can't (or null), danger]. One open at a time; Escape or a
// click elsewhere closes it.
function closeMenus() { document.querySelectorAll(".menu").forEach((m) => { m.previousSibling.setAttribute("aria-expanded", "false"); m.remove(); }); }
document.addEventListener("click", (e) => { if (!e.target.closest(".more")) closeMenus(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && document.querySelector(".menu")) closeMenus(); });
function menuButton(label, testid, items) {
  const wrap = el("span", { class: "more" });
  const btn = el("button", { class: "btn small ghost", text: "⋯", title: "More", "aria-label": label, "aria-haspopup": "menu",
    "aria-expanded": "false", testid, onclick: () => {
      const was = !!wrap.querySelector(".menu");
      closeMenus();
      if (was) return;
      btn.setAttribute("aria-expanded", "true");
      const menu = el("div", { class: "menu", role: "menu", "aria-label": label },
        ...items.map(([text, key, run, why, danger]) => el("button", { role: "menuitem", class: danger ? "danger" : null, text, testid: `${testid}-${key}`,
          disabled: !!why, title: why || null, onclick: () => { closeMenus(); run(); } })));
      wrap.append(menu);
      const first = menu.querySelector("button:not(:disabled)");
      if (first) first.focus();
    } });
  wrap.append(btn);
  return wrap;
}

// Add a job by hand (manage.py): to either sheet tab, Approved or Proposed; a NUworks link fills the company, title and
// match from your job data. done(tab): the tab it went to.
function addJob(tab, done) {
  const form = { tab, url: "", company: "", title: "", status: "Approved" };
  let info = null, seen = "";
  modal((box, close) => {
    const body = el("div");
    async function look() {  // what your job data knows about this link (NUworks links: company, title, match)
      const url = form.url.trim();
      if (!url || url === seen) return;
      seen = url;
      try { info = await api.get("/api/sheet/lookup?url=" + encodeURIComponent(url)); } catch (e) { info = null; }
      if (info && info.tab) form.tab = info.tab;
      if (info && info.known) { form.company = form.company || info.company; form.title = form.title || info.title; }
      const focused = document.activeElement && document.activeElement.dataset.testid;  // keep the cursor where it went
      draw();
      if (focused) { const n = body.querySelector(`[data-testid="${focused}"]`); if (n) n.focus(); }
    }
    async function add(e) {
      e.preventDefault();
      try {
        const r = await api.post("/api/sheet", { action: "add", ...form });
        close();
        toast(`Added as row ${r.row} (${form.status}).`);
        pollState();
        done(r.tab);
      } catch (err) { fail(err); }
    }
    function draw() {
      const input = (k, props) => el("input", { ...props, value: form[k], oninput: (e) => { form[k] = e.target.value; } });
      const seg = (k, opts, label) => el("div", { class: "seg", role: "group", "aria-label": label },
        ...opts.map(([v, t]) => el("button", { type: "button", "aria-pressed": String(form[k] === v), testid: `add-${k}-${v}`, text: t,
          onclick: () => { form[k] = v; draw(); } })));
      const hint = !info ? (form.tab === "nuworks" ? "Paste a NUworks job link (…/students/app/jobs/detail/…): the company, title and match come from your job data when it has the job." :
        "Paste the company's own posting (any https link that is not NUworks).") :
        info.tab === null ? info.why :
        info.tab === "other" ? "Not a NUworks job link: it goes in Other jobs." :
        !info.known ? "A NUworks job your job data doesn't have yet: type the company and title." :
        info.in_pool ? `In your pool: ${info.notes}.` : `In your job data: ${info.notes}.`;
      fill(body, el("form", { class: "stack", onsubmit: add, testid: "add-form" },
        el("div", { class: "row" }, seg("tab", [["nuworks", "NUworks co-op"], ["other", "Other job (not on NUworks)"]], "Which tab"),
          seg("status", [["Approved", "Approved"], ["Proposed", "Proposed"]], "Status")),
        el("label", { class: "field" }, "Link to the posting", input("url", { type: "url", placeholder: "https://", required: true, testid: "add-url", onchange: look })),
        el("p", { class: "small muted", testid: "add-hint", text: hint }),
        el("div", { class: "grid2" },
          el("label", { class: "field" }, "Company", input("company", { type: "text", testid: "add-company" })),
          el("label", { class: "field" }, "Job title", input("title", { type: "text", testid: "add-title" }))),
        el("p", { class: "small muted", text: form.status === "Approved" ?
          (form.tab === "nuworks" ? "Approved NUworks jobs are submitted by Start (your weekly limit applies)." : "Approved Other jobs are ready for the assistant (no limits there).") :
          "Proposed: it waits in the sheet (Review shows it first) until you approve it." }),
        el("div", { class: "foot" }, el("span"), el("div", { class: "row" }, el("button", { class: "btn", type: "button", text: "Cancel", onclick: close }),
          el("button", { class: "btn primary", type: "submit", text: "Add", testid: "add-submit" })))));
    }
    draw();
    box.append(el("h2", { text: "Add a job" }), body);
    setTimeout(() => { const u = body.querySelector("[data-testid=add-url]"); if (u) u.focus(); }, 0);
  }, { label: "Add a job", testid: "add-dialog" });
}

// Move a row to the sheet's other tab (its status, notes and date go with it), or remove it (its cells cleared: no row
// is renumbered). names: {nuworks, other} the two tabs' names.
function moveRow(names, tab, r, done) {
  const to = tab === "nuworks" ? "other" : "nuworks";
  const isNu = (u) => /^https:\/\/northeastern-csm\.symplicity\.com\//.test(u || "");
  let link = (to === "nuworks") === isNu(r.url) ? r.url : "";  // the old link only if it fits the new tab
  modal((box, close) => box.append(
    el("h2", { text: `Move to ${names[to]}?` }),
    el("p", { class: "muted", text: `${r.company} · ${r.title} (${r.status || "no status"}). Its status, notes and date go with it; the row here is cleared.` +
      (to === "nuworks" ? " The NUworks tab needs the job's NUworks link (copy it from the job's page on NUworks)." : " Other jobs needs the company's own posting (not NUworks).") }),
    r.status === "Applied" ? el("div", { class: "note warn", text: "It is Applied: in the NUworks tab it counts toward your weekly and total limits." }) : null,
    el("label", { class: "field" }, `Link in ${names[to]}`, el("input", { type: "url", value: link, testid: "move-url", required: true,
      placeholder: to === "nuworks" ? "https://northeastern-csm.symplicity.com/students/app/jobs/detail/…" : "https://", oninput: (e) => { link = e.target.value; } })),
    el("div", { class: "foot" }, el("span"), el("div", { class: "row" },
      el("button", { class: "btn", text: "Cancel", onclick: close }),
      el("button", { class: "btn primary", text: "Move", testid: "move-yes", onclick: async () => {
        try {
          const out = await api.post("/api/sheet", { action: "move", tab, row: r.row, url: r.url, new_url: link });
          close(); toast(`Moved to row ${out.row} of the ${names[out.tab]} tab.`); pollState(); done();
        } catch (err) { fail(err); }
      } })))), { label: "Move a job", testid: "move-dialog" });
}

function removeRow(names, tab, r, done) {
  confirmBox("Remove this job?", `${r.company} · ${r.title} (${names[tab]} row ${r.row}). Its cells are cleared; the other rows keep their numbers.`,
    "Remove", async () => {
      try { await working("Removing it…", api.post("/api/sheet", { action: "remove", tab, row: r.row, url: r.url })); toast("Removed."); pollState(); done(); }
      catch (err) { fail(err); }
    }, { danger: true });
}

// Apply's Assistants tab: every session running now, here and on the homelab, each with where you answer it; the
// queue's line on top (the homelab's page, or the laptop's app linked to it).
function assistantsPane({ d, here, there }) {
  const homelab = onHomelab();
  const all = [...here.map((s) => ({ ...s, where: homelab ? "homelab" : "here", own: true })), ...there.map((s) => ({ ...s, where: "homelab", own: false }))];
  const status = (s) => {
    const k = s.attention && s.attention.kind;
    return el("span", { class: "chip" + (k === "approve" ? " warn" : k ? " accent" : " ok"), testid: "assistant-status",
      text: k === "approve" ? "wants to Submit: approve" : k ? "waiting for you" : "working" });
  };
  const open = (s) => {
    const wait = s.attention && s.attention.kind;
    if (s.own && homelab) return el("a", { class: "btn small" + (wait ? " primary" : ""), href: `#/assistant/${s.slot}`, text: "Open its screen" });
    if (s.own) return el("span", { class: "chip", text: "in its terminal window" });
    if (d.linked) return el("a", { class: "btn small" + (wait ? " primary" : ""), href: `#/assistant/h${s.slot}`, text: "Open its screen" });
    return d.homelab_review ? el("button", { class: "btn small" + (wait ? " primary" : ""), text: "Open its screen", title: "In NUauto on your homelab, in your browser",
      onclick: () => openExternal(`${d.homelab_review}/#/assistant/${s.slot}`) }) : null;
  };
  return [assistantsBar(d),
    el("div", { class: "card", testid: "assistants-list" }, el("h2", { text: "Running now" }),
      all.length ? el("ul", { class: "list" }, ...all.map((s) => el("li", { testid: `assistant-${s.where}-${s.slot}` },
        el("div", { class: "what" }, el("b", { text: s.company || "a job" }), ` · ${s.tab === "other" ? otherTabLabel().replace(/s$/, "") : "NUworks co-op"}, row ${s.row}`,
          el("div", { class: "small muted", text: [s.host, s.where === "here" ? `this computer, slot ${s.slot}` : `the homelab, slot ${s.slot}`,
            s.since && ago(s.since) ? `started ${ago(s.since)}` : null].filter(Boolean).join(" · ") }),
          s.attention && s.attention.said ? el("div", { class: "small", text: s.attention.said }) : null),
        el("div", { class: "row" }, status(s), open(s))))) :
        el("p", { class: "empty", text: `No assistant is running. Start one from a row under Company site needed (NUworks co-ops) or ${otherTabLabel()}.` }))];
}

// ---- The assistant's Start on a row (Apply's Company site needed, and the Other jobs tab's rows):
// up to d.slots run side by side, each in its own terminal window and browser; a row with one running says so instead
// of offering another; with every slot in use, Start waits. On the homelab's page (and the laptop linked to it): the
// queue's place for the row; its Apply runs where Run on says (the homelab's page: there).
function assistantStart(d, r, tab, testid, args) {
  // the homelab's browser was refused there (on the homelab's own page the queue's chip says so)
  if (r.on_laptop && !onHomelab() && !(d.homelab || []).find((s) => s.tab === tab && s.row === r.row)) {
    return el("span", { class: "row" }, el("span", { class: "chip warn", testid: `${testid}-laptop`, title: "The site showed the homelab's browser a sign-in page with no way in. The homelab won't start it again.",
      text: "do it on the laptop" }), assistantStartButton(d, r, tab, testid, args));
  }
  return assistantStartButton(d, r, tab, testid, args);
}

// Rows started on the homelab whose session doesn't show yet ("tab-row" -> until when): their button stays "Starting…"
// through every redraw until it shows (the lists reload by themselves then), or 45 s pass
const starting = {};

function assistantStartButton(d, r, tab, testid, args) {
  const key = `${tab}-${r.row}`;
  const remote = (d.homelab || []).find((s) => s.tab === tab && s.row === r.row);
  if (remote || (d.sessions || []).some((s) => s.tab === tab && s.row === r.row)) delete starting[key];
  if (remote) {  // the homelab's assistant has this row: its screen, in this app (linked) or in NUauto on the homelab
    const wait = remote.attention && remote.attention.kind;
    const text = wait ? "assistant on the homelab: waiting for you" : "assistant running on the homelab";
    if (d.linked) return el("a", { class: "btn small" + (wait ? " primary" : ""), href: `#/assistant/h${remote.slot}`,
      testid: `${testid}-homelab`, text: `${text}: open` });
    return d.homelab_review ? el("button", { class: "btn small" + (wait ? " primary" : ""), testid: `${testid}-homelab`,
      text: `${text}: open`, title: "Its screen in NUauto on your homelab, in your browser",
      onclick: () => openExternal(`${d.homelab_review}/#/assistant/${remote.slot}`) }) :
      el("span", { class: wait ? "chip warn" : "chip ok", testid: `${testid}-homelab`, text });
  }
  const running = (d.sessions || []).find((s) => s.tab === tab && s.row === r.row);
  if (running && onHomelab()) {  // here: its screen, a tap away
    const wait = running.attention && running.attention.kind;
    return el("a", { class: "btn small" + (wait ? " primary" : ""), href: `#/assistant/${running.slot}`, testid: `${testid}-running`,
      text: wait === "approve" ? "Approve its Submit" : wait ? "It waits for you: open" : `Assistant running: open` });
  }
  if (running) {  // a session waiting for you says so (its terminal window has the question or the Submit to approve)
    const wait = running.attention && running.attention.kind;
    return el("span", { class: wait ? "chip warn" : "chip ok", testid: `${testid}-running`,
      text: wait === "approve" ? `assistant: approve in its window (slot ${running.slot})`
        : wait ? `assistant waiting for you (slot ${running.slot})` : `assistant running (slot ${running.slot})` });
  }
  // the homelab's queue (its own page, or the laptop's app through the link): this row's place in it
  const q = S.queue && S.queue.rows && (onHomelab() || runOn() === "homelab") ? S.queue.rows[`${tab}-${r.row}`] : null;  // (Run on: here: no queue)
  const said = q && q.group === "laptop" && r.on_laptop && !onHomelab();  // (the "do it on the laptop" chip says it already)
  const chip = q && (q.group === "next" || !q.start) && !said ? el("span", { class: "chip" + (q.group === "next" ? " accent" :
    q.group === "check" || q.group === "laptop" ? " warn" : ""), testid: `${testid}-queue`, text: q.text }) : null;
  // Apply, the same button as a NUworks row's: on the homelab when this is its page, or Run on says the homelab (the
  // laptop's app, through its link; the queue says when it can't); otherwise here
  const there = onHomelab() || d.linked && S.queue;
  const pending = starting[key] > Date.now();
  if (there && (onHomelab() || runOn() === "homelab")) return el("span", { class: "row" }, pending ? null : chip, el("button", { class: "btn small",
    testid: onHomelab() ? testid : `${testid}-homelab-start`, text: pending ? "Starting…" : "Apply", disabled: pending || args.disabled || !!(q && !q.start),
    title: pending ? "Its session shows up here in a few seconds" : q && !q.start ? q.text :
      "Starts the assistant on the homelab now, for just this job (you answer it on its screen)", onclick: async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      btn.textContent = "Starting…";
      starting[key] = Date.now() + 45000;
      const ok = await action("assist", onHomelab() ? args.body : { ...args.body, on: "homelab" });
      if (!ok) delete starting[key];
      const redraw = () => { if (S.screen && S.screen.reload) S.screen.reload(); };
      redraw();  // the queue says it runs now; the button stays "Starting…" until its session shows
      if (ok) setTimeout(() => { if (starting[key]) { delete starting[key]; redraw(); } }, 45500);  // it never showed: Apply again
    } }));
  const full = (d.sessions || []).length >= (d.slots || 1);
  const startHere = el("button", { class: "btn small", text: "Apply", testid, disabled: full || (args.disabled || false),
    title: full ? `${d.slots} assistants are running (the most at once): finish one first` :
      "Starts the assistant (Claude) here, for just this job; it asks you before anything is submitted", onclick: (e) => {
      // a few seconds greyed out: a second click would start the same job twice (and each start reads the sheet)
      const btn = e.currentTarget;
      const label = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Starting…";
      setTimeout(() => { if (btn.isConnected) { btn.disabled = false; btn.textContent = label; } }, 5000);
      action("assist", args.body);
    } });
  return chip ? el("span", { class: "row" }, chip, startHere) : startHere;
}

// One line at the top of Apply's Assistants tab, the same on both machines: how many assistants run (here, and on the homelab),
// how many wait for you (Today lists them), and on the homelab's page what its queue starts by itself. Each session is
// reached from its own row (its screen, or its terminal window); pairing and opening the homelab's page are in Settings.
function assistantsBar(d) {
  const here = (d.sessions || []).length, there = (d.homelab || []).length;
  if (!here && !there && !onHomelab()) return null;
  const waits = [...(d.sessions || []), ...(d.homelab || [])].filter((s) => s.attention && s.attention.kind).length;
  // (laptop) with every one on the homelab, the line says that, not "0 of 3 running here"
  const parts = here || !there ? [`Assistants: ${here} of ${d.slots} running${onHomelab() ? "" : " here"}`] : [];
  if (there) parts.push(`${parts.length ? "" : "Assistants: "}${there} running on the homelab`);
  if (waits) parts.push(`${waits} ${waits === 1 ? "waits" : "wait"} for you (Today lists ${waits === 1 ? "it" : "them"})`);
  if (here >= d.slots) parts.push(onHomelab() ? "end one (on its screen) to start another" : "finish one (type /exit in its window) to start another");
  const q = d.queue || {};
  const tabs = (q.auto || []).map((t) => (t === "jobs" ? "NUworks rows" : "Other jobs rows"));
  const queue = q.error || (q.hours ? (tabs.length ? `Starts by itself: ${tabs.join(" and ")}, ${hour(q.hours[0])}–${hour(q.hours[1])} New York time.` :
    "Nothing starts by itself (Settings).") + ` ${q.free ? plural(q.free, "slot") + " free" : "Every slot is in use"}.` +
    (q.left != null ? ` NUworks applications left now: ${Math.max(0, q.left)}.` : "") : null);
  return el("div", { class: "note", testid: "assistants-bar" }, el("span", { testid: "assistants-running", text: parts.join(" · ") + "." }),
    queue ? el("div", { class: "small muted", testid: "queue-line", text: queue }) : null);
}

async function pairThisComputer() {
  const r = await action("review_pair");
  if (r && r.url) { openExternal(r.url); toast("Opened a pairing link in your browser: press Pair there."); }
}

// ---- Insights: pay, places and kinds of work in the pool / what you applied to; where the sheet stands
const SVGNS = "http://www.w3.org/2000/svg";
function svg(tag, attrs, ...kids) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) n.setAttribute(k, String(v));
  for (const kid of kids.flat()) if (kid) n.append(kid);
  return n;
}
// Status colors (state, not series): each always shown with its label and count in the legend.
const STATUS_COLOR = { Proposed: "var(--off)", Approved: "var(--accent)", Applied: "var(--ok)", "Needs Human": "var(--warn)", Failed: "var(--fail)",
  Accepted: "var(--win)", Rejected: "var(--faint)" };
// Ring (and legend) order, chosen with the dataviz palette validator so no two neighbors are the hard-to-tell pairs
// (red/amber, green/amber next to each other); labels, counts and 2px gaps carry identity too.
const RING = ["Applied", "Needs Human", "Accepted", "Proposed", "Failed", "Rejected", "Approved"];
const usd = (x) => x === null || x === undefined ? "—" : `$${x >= 100 || Number.isInteger(x) ? Math.round(x) : x.toFixed(2)}`;

function donut(items, total) {
  // A ring of arcs (one per status) with a 2px surface gap between them; the total in the middle.
  const r = 52, c = 2 * Math.PI * r, gap = items.length > 1 ? 2 : 0;
  let at = 0;
  const arcs = items.map((it) => {
    const len = (c * it.count) / total;
    const arc = svg("circle", { cx: 70, cy: 70, r, fill: "none", stroke: STATUS_COLOR[it.label] || "var(--off)", "stroke-width": 18,
      "stroke-dasharray": `${Math.max(0, len - gap)} ${c}`, "stroke-dashoffset": -at, transform: "rotate(-90 70 70)" },
      svg("title", {}, `${it.label}: ${it.count}`));
    at += len;
    return arc;
  });
  const mid = svg("text", { x: 70, y: 66, "text-anchor": "middle", class: "donut-total" });
  mid.textContent = String(total);
  const sub = svg("text", { x: 70, y: 86, "text-anchor": "middle", class: "donut-sub" });
  sub.textContent = total === 1 ? "row" : "rows";
  return svg("svg", { viewBox: "0 0 140 140", width: 140, height: 140, role: "img", "aria-label": items.map((i) => `${i.label} ${i.count}`).join(", ") },
    ...arcs, mid, sub);
}

function hbars(items, testid) {
  // One series: one hue, the count beside each bar (text in ink, not the bar color).
  const top = Math.max(1, ...items.map((i) => i.count));
  return el("ul", { class: "hbars", testid }, ...items.map((i) => el("li", { title: `${i.label}: ${i.count}` },
    el("span", { class: "hb-label", text: i.label }),
    el("span", { class: "hb-track" }, el("span", { class: "hb-bar", style: null, "data-w": Math.max(2, (100 * i.count) / top) })),
    el("span", { class: "hb-n", text: String(i.count) }))));
}

function vbars(items, testid) {
  const top = Math.max(1, ...items.map((i) => i.count));
  return el("div", { class: "vbars", testid }, ...items.map((i) => el("div", { class: "vb", title: `${i.label}: ${i.count}` },
    el("span", { class: "vb-n", text: i.count ? String(i.count) : "" }),
    el("span", { class: "vb-col" }, el("span", { class: "vb-bar", "data-h": i.count ? Math.max(3, (100 * i.count) / top) : 0 })),
    el("span", { class: "vb-label", text: i.label }))));
}

function sizeBars(root) {  // widths/heights from data-* (the CSP allows no inline style attributes from markup)
  root.querySelectorAll("[data-w]").forEach((b) => { b.style.width = b.dataset.w + "%"; });
  root.querySelectorAll("[data-h]").forEach((b) => { b.style.height = b.dataset.h + "%"; });
}

function tile(label, value, note, testid) {
  return el("div", { class: "tile", testid }, el("div", { class: "tile-label", text: label }),
    el("div", { class: "tile-value", text: value }), note ? el("div", { class: "tile-note", text: note }) : null);
}

// Two sets when Summer internships are on (state.internships): the NUworks co-ops and the internships, each with its own
// pool, applications and sheet tab; the choice is remembered for the session.
screens.insights = async (view) => {
  let d, which = "pool";
  let source = sessionStorage.getItem("insightsSource") === "intern" ? "intern" : "nuworks";
  async function load() {
    try { d = await api.get(`/api/insights?source=${source}`); } catch (e) { fill(view, el("div", { class: "note fail", text: e.message })); return false; }
    if (d.statuses) d.statuses.sort((x, y) => RING.indexOf(x.label) - RING.indexOf(y.label));
    return true;
  }
  if (!await load()) return {};
  function draw() {
    const g = d[which], p = g.pay, intern = d.source === "intern", ii = d.internships;  // ii: internships on (its term)
    source = d.source;
    const sources = ii ? el("div", { class: "seg", role: "group", "aria-label": "Which jobs", testid: "ins-source" },
      ...[["nuworks", "NUworks co-ops"], ["intern", `${ii.term} internships`]].map(([k, t]) => el("button", { "aria-pressed": String(source === k),
        testid: `ins-source-${k}`, text: t, onclick: async () => { source = k; sessionStorage.setItem("insightsSource", k); if (await load()) draw(); } }))) : null;
    const seg = el("div", { class: "seg", role: "group", "aria-label": "Which jobs" },
      ...[["pool", `Your pool (${d.pool.count})`], ["applied", `Applied (${d.applied.count})`]].map(([k, t]) =>
        el("button", { "aria-pressed": String(which === k), testid: `ins-${k}`, text: t, onclick: () => { which = k; draw(); } })));
    const status = d.statuses === null ? el("div", { class: "card" }, el("h2", { text: "Your sheet" }),
      el("p", { class: "empty", text: "Can't read the sheet right now; the pool numbers below still work." })) :
      el("div", { class: "card", testid: "ins-status" }, el("h2", { text: intern ? "Where your internship applications stand" : "Where your applications stand" }),
        d.rows ? el("div", { class: "donut-wrap" }, donut(d.statuses, d.rows),
          el("ul", { class: "legend" }, ...d.statuses.map((s) => el("li", {},
            el("span", { class: "swatch", "data-status": s.label }), el("span", { text: s.label }), el("b", { text: String(s.count) })))))
          : el("p", { class: "empty", text: "Nothing in your sheet yet." }));
    const body = !g.count ? [el("p", { class: "empty", testid: "ins-empty", text: which === "applied" ? "Nothing applied yet. Applied rows show up here." :
      intern ? "Your internship pool is empty: the next internship update fills it." : "Your pool is empty. Check for new jobs on Today." })] : [
      el("div", { class: "tiles" },
        tile("Median pay", p.median === null ? "—" : `${usd(p.median)}/h`, p.mid_low !== null ? `most ${usd(p.mid_low)}–${usd(p.mid_high)}` : null, "tile-pay"),
        tile("In Massachusetts", `${g.in_ma} of ${g.count}`, `${g.states} state${g.states === 1 ? "" : "s"} in all`, "tile-ma"),
        which === "pool" ? tile("Closing this week", String(g.closing_week), "in your pool", "tile-closing") :
          tile("Pay listed", `${p.listed} of ${g.count}`, null, "tile-listed")),
      el("div", { class: "card", testid: "ins-pay" }, el("div", { class: "row between" }, el("h2", { text: "Hourly pay" }),
        el("span", { class: "small muted", text: p.listed ? `${p.listed} of ${g.count} list pay · all ${usd(p.low)}–${usd(p.high)}` : "none listed" })),
        p.listed ? vbars(p.buckets, "pay-bars") : null),
      el("div", { class: "grid2" },
        el("div", { class: "card", testid: "ins-where" }, el("h2", { text: "Where" }), hbars(g.places, "where-bars"),
          g.cities.length ? el("p", { class: "small muted", text: "Top cities: " + g.cities.map((c) => `${c.label} (${c.count})`).join(", ") }) : null),
        g.categories.length ? el("div", { class: "card", testid: "ins-kind" }, el("h2", { text: "Kind of work" }), hbars(g.categories, "kind-bars")) : null)];
    fill(view, sources ? el("div", { class: "row toolbar" }, sources) : null, status,
      el("div", { class: "row between ins-head" }, el("h2", { text: which === "pool" ? `${intern ? "Internships" : "Jobs"} in your pool` : `${intern ? "Internships" : "Jobs"} you applied to` }), seg), ...body);
    view.querySelectorAll(".swatch").forEach((s) => { s.style.background = STATUS_COLOR[s.dataset.status] || "var(--off)"; });
    sizeBars(view);
  }
  draw();
  return {};
};

// ---- Answers
screens.answers = async (view) => {
  let data, rows = [], filter = "", bank = sessionStorage.getItem("answersBank") || "nuworks";  // or "other": the Other jobs tab's
  async function reload() {
    try { data = await api.get(`/api/answers?bank=${bank}`); } catch (e) { fill(view, el("div", { class: "note fail", text: e.message })); return; }
    rows = data.entries.map((e) => ({ ...e, aliases: [...(e.aliases || [])] }));
    draw();
  }
  // Other wordings, one per line (a wording may hold commas: "…on CPT, OPT, or STEM OPT…")
  function wordings(r) {
    const box = el("textarea", { rows: "8", testid: "aliases-text", "aria-label": `Other wordings of ${r.question}`, value: null });
    box.value = r.aliases.join("\n");
    modal((m, close) => m.append(el("h2", { text: "Other wordings" }),
      el("p", { class: "muted", text: `Questions that get the same answer as “${r.question}”. One per line; capital letters and spaces at the ends don't matter.` }),
      el("label", { class: "field" }, box),
      el("div", { class: "foot" }, el("span", { class: "small muted", text: "Saved with the rest when you press Save." }), el("div", { class: "row" },
        el("button", { class: "btn", text: "Cancel", onclick: close }),
        el("button", { class: "btn primary", text: "Done", testid: "aliases-done", onclick: () => {
          r.aliases = box.value.split("\n").map((a) => a.trim()).filter(Boolean);
          close();
          draw();
        } })))), { label: "Other wordings", testid: "aliases-dialog" });
    setTimeout(() => box.focus(), 0);
  }
  function setBank(b) { bank = b; sessionStorage.setItem("answersBank", b); filter = ""; reload(); }
  function draw() {
    const shown = rows.map((r, i) => [r, i]).filter(([r]) => !filter || [r.question, r.answer, ...r.aliases].join(" ").toLowerCase().includes(filter));
    const tbody = el("tbody", {}, ...shown.map(([r, i]) => el("tr", { testid: `answer-row-${i}` },
      el("td", {}, el("input", { type: "text", value: r.question, "aria-label": "Question", oninput: (e) => { r.question = e.target.value; } })),
      el("td", {}, el("input", { type: "text", value: r.answer, "aria-label": "Answer", disabled: r.always_ask,
        placeholder: r.always_ask ? "asked every time" : r.leave_blank ? "always left blank" : "", oninput: (e) => { r.answer = e.target.value; } })),
      el("td", { class: "narrow" }, el("select", { "aria-label": "Field type", onchange: (e) => { r.field_type = e.target.value; } },
        ...[["", "any"], ["text", "text"], ["select", "dropdown"]].map(([v, t]) => el("option", { value: v, text: t, selected: r.field_type === v })))),
      el("td", { class: "narrow" }, el("label", { class: "row small" }, el("input", { type: "checkbox", checked: !!r.always_ask,
        onchange: (e) => { r.always_ask = e.target.checked; draw(); } }), "always ask")),
      bank === "nuworks" ? el("td", { class: "narrow" }, el("label", { class: "row small", title: "Never used for jobs in your Other jobs tab" },
        el("input", { type: "checkbox", checked: !!r.nuworks_only, onchange: (e) => { r.nuworks_only = e.target.checked; } }), "NUworks only")) : null,
      el("td", { class: "narrow" }, el("button", { class: "btn small", testid: `aliases-${i}`, "aria-label": `Other wordings of ${r.question}`,
        title: r.aliases.join("\n") || null, text: r.aliases.length ? plural(r.aliases.length, "other wording") : "Add a wording", onclick: () => wordings(r) })),
      el("td", { class: "narrow" }, el("button", { class: "btn small ghost", text: "Remove", "aria-label": `Remove ${r.question}`,
        onclick: () => { rows.splice(i, 1); draw(); } })))));
    const seg = el("div", { class: "seg", role: "group", "aria-label": "Which answers" },
      ...[["nuworks", "NUworks"], ["other", "Other jobs"]].map(([k, t]) =>
        el("button", { "aria-pressed": String(bank === k), testid: `bank-${k}`, text: t, onclick: () => setBank(k) })));
    fill(view, el("div", { class: "card" },
      el("div", { class: "row between" }, el("h2", { text: bank === "other" ? "Answers for Other jobs" : "Your answers" }), seg),
      el("p", { class: "small muted", text: bank === "other" ?
        "For jobs in your sheet's Other jobs tab (not on NUworks). The assistant looks here first, then in your NUworks answers, except the ones marked NUworks only (co-op dates and term). What it saves during an Other jobs application goes here, never into your NUworks answers." :
        "When a form asks a question, NUauto fills it only from here, and only on an exact match (capital letters and spaces at the ends don't matter). Always-ask entries (salary, work authorization…) are never filled: you're asked each time. NUworks only: never used for jobs in your Other jobs tab (they get their own answer, under Other jobs)." }),
      data.locked ? el("div", { class: "note warn", text: "A run is using the answer bank right now. You can edit when it is over." }) : null,
      el("div", { class: "row between" }, el("input", { type: "search", placeholder: "Search (Ctrl+F)", value: filter, "aria-label": "Search answers", "data-find": true, testid: "answers-search",
        oninput: (e) => { filter = e.target.value.toLowerCase(); draw(); $("input[aria-label='Search answers']").focus(); } }),
        el("div", { class: "row" }, el("button", { class: "btn", text: "Add a question", onclick: () => { rows.unshift({ question: "", answer: "", field_type: "", always_ask: false, nuworks_only: false, aliases: [] }); filter = ""; draw(); } }),
          el("button", { class: "btn primary", text: "Save", testid: "answers-save", disabled: data.locked, onclick: save }))),
      el("div", { class: "table-scroll" }, el("table", { class: "grid" }, el("thead", {}, el("tr", {}, ...["Question", "Answer", "Type", "Always ask", ...(bank === "nuworks" ? ["NUworks only"] : []), "Other wordings"].map((h) => el("th", { text: h })),
        el("th", {}, el("span", { class: "sr-only", text: "Remove" })))), tbody))));
  }
  async function save() {
    const entries = rows.map((r) => ({ ...r, aliases: [...r.aliases] }));
    try { data = await api.post("/api/answers", { bank, entries, version: data.version }); rows = data.entries.map((e) => ({ ...e, aliases: [...(e.aliases || [])] })); draw(); toast("Answer bank saved."); }
    catch (e) { fail(e); }
  }
  await reload();
  return {};
};

// ---- Past runs (logs and screenshots)
screens.logs = async (view) => {
  let d;
  try { d = await api.get("/api/logs"); } catch (e) { fill(view, el("div", { class: "note fail", text: e.message })); return {}; }
  const root = d.runs.length ? d.runs : [];
  fill(view, el("div", { class: "card" }, el("p", { class: "small muted", text: "One folder per run under logs/: every action, and the screenshots taken before and after Submit." }),
    root.length ? el("ul", { class: "list" }, ...root.map((r) => el("li", {}, el("div", { class: "what" }, el("b", { text: r.name })),
      el("div", { class: "row" }, ...r.files.filter((f) => /\.(png|log|txt)$/.test(f)).map((f) => el("button", { class: "btn small", text: f,
        onclick: () => openLogFile(d, r.name, f) })))))) : el("p", { class: "empty", text: "No runs yet." })));
  return {};
};

async function openLogFile(d, run, file) {
  const path = `${S.logsDir}/${run}/${file}`;
  const url = `/api/file?path=${encodeURIComponent(path)}`;
  if (file.endsWith(".png")) {
    modal((box, close) => box.append(el("h2", { text: file }), el("img", { class: "full", src: url, alt: file }),
      el("div", { class: "foot" }, el("span"), el("button", { class: "btn primary", text: "Close", onclick: close }))), { label: file });
    return;
  }
  const text = await fetch(url, { credentials: "same-origin" }).then((r) => r.ok ? r.text() : "Could not open it.");
  modal((box, close) => box.append(el("h2", { text: file }), el("pre", { class: "log", tabindex: "0", text }),
    el("div", { class: "foot" }, el("span"), el("button", { class: "btn primary", text: "Close", onclick: close }))), { label: file });
}

// ---- An assistant session on the homelab (#/assistant/<slot>): what it wants from you, read like a chat with the newest
// at the bottom: the browser and its terminal, its latest message, then the Submit to approve (or the end question),
// then the reply box. It asks again every few seconds; what you are typing is kept.
screens.assistant = async (view) => {
  // #/assistant/<slot>: a session here (the homelab's page); #/assistant/h<slot>: the homelab's, in the laptop's app (its link)
  const there = String(S.param || "").startsWith("h"), slot = String(S.param || "").replace(/^h/, "");
  const api_ = there ? "/api/homelab/assistant" : "/api/assistant";
  let d = null, version = null, timer = null, first = true;
  const note = el("input", { type: "text", placeholder: "A note for the agent (when you decline)", "aria-label": "Note", testid: "as-note" });
  const reply = el("textarea", { rows: "4", maxlength: "4000", "aria-label": "Reply", testid: "as-reply" });
  const term = el("pre", { class: "log term", testid: "as-terminal", tabindex: "0", "aria-label": "Its terminal" });
  const live = el("img", { class: "shot", alt: "The screen now", testid: "as-live" });
  live.onclick = () => { if (d && d.browser) openExternal(d.browser.url); };
  live.onerror = () => { live.hidden = true; };  // no screen to show (yet)
  live.onload = () => { live.hidden = false; };

  async function send(body, busy) {
    if (busy) { busy.disabled = true; busy.textContent = busy.dataset.busy || "Working…"; }
    try {
      const r = await api.post(api_, { slot: Number(slot), ...body });
      toast(r.message);
      if (body.do === "reply") reply.value = "";
      if (r.closing) { sessionStorage.setItem("applyTab", "assistants"); location.hash = "#/apply"; return; }
    } catch (e) { fail(e); }
    version = null;
    load();
  }
  const btn = (text, cls, body, busy, testid) => el("button", { class: cls, text, testid, "data-busy": busy,
    onclick: (e) => send(typeof body === "function" ? body() : body, e.currentTarget) });

  function draw() {
    const a = d.approval;
    const word = d.status;
    const chat = !["finish", "prompt", "answered"].includes(word);
    const parts = [el("div", { class: "row between" }, el("a", { href: "#/apply", text: "← Assistants", onclick: () => sessionStorage.setItem("applyTab", "assistants") }),
      el("span", { class: `chip ${word === "working" ? "ok" : word === "answered" ? "accent" : "warn"}`, testid: "as-status", text: d.text })),
    el("div", { class: "card" }, el("h2", { text: d.company || "Assistant" }),
      el("p", { class: "small muted", text: [d.title, d.where, d.host, `slot ${d.slot}`, there ? "on the homelab" : null].filter(Boolean).join(" · ") }))];
    if (d.browser) {
      parts.push(el("div", { class: "card stack", testid: "as-browser" }, el("div", { class: "row between" }, el("h2", { text: "The browser" }),
        el("div", { class: "row" }, el("span", { class: "small muted" }, "password ", el("code", { text: d.browser.password })),
          el("button", { class: "btn small", text: "Copy", onclick: () => navigator.clipboard.writeText(d.browser.password).then(() => toast("Copied.")) }),
          linkOut(d.browser.url, "Open it"))),
        el("p", { class: "small muted", text: "Captchas are yours: solve them in the browser (Open it), then reply \"done\". It updates every few seconds." }),
        live));
    }
    parts.push(el("div", { class: "card stack" }, el("div", { class: "row between" }, el("h2", { text: "Its terminal" }),
      btn("Interrupt (Esc)", "btn small", { do: "interrupt" }, "Interrupting…", "as-interrupt")), term));
    if (d.message) parts.push(el("div", { class: "card" + (word === "waiting" ? " attn" : ""), testid: "as-message" },
      el("h2", { text: "Its latest message" }), el("pre", { class: "log", text: d.message })));
    if (word === "answered") parts.push(el("div", { class: "note ok", testid: "as-answered" }, el("b", { text: d.text }), "… this session closes in a moment."));
    if (word === "prompt") parts.push(el("div", { class: "card attn", testid: "as-prompt" }, el("h2", { text: "Claude Code is asking something in its terminal" }),
      el("p", { class: "muted", text: "A permission question of its own, not a Submit of NUauto's guard. The reply box is off until it is answered, because Enter would pick its first option. Answer it in its terminal:" }),
      el("pre", { class: "log", text: `ssh -t <your homelab> tmux attach -t ${d.tmux || "?"}` })));
    if (a && a.answered) {
      parts.push(el("div", { class: "card", id: "as-ask", testid: "as-approval" }, el("h2", { text: a.answered === "approve" ? "Approved: it is submitting now" : "Declined: it will not submit" }),
        el("p", { class: "small muted", text: `Your answer (${a.by}) is with the assistant; this page updates by itself.` })));
    } else if (a) {
      parts.push(el("div", { class: "card attn", id: "as-ask", testid: "as-approval" }, el("h2", { text: "Ready to submit" }),
        el("p", {}, "It wants to ", el("b", { text: a.what }), el("br"), el("span", { class: "small muted", text: `on ${a.url}` })),
        el("pre", { class: "log", text: a.why }),
        a.shot ? el("img", { class: "shot", src: `${api_}/${slot}/shot.png?a=${encodeURIComponent(a.id)}`, alt: "The screen when it asked" }) : null,
        a.waiting ? null : el("p", { class: "small muted", text: "The assistant stopped waiting for this; Approve tells it to do it now (good for 10 minutes, this action on this page only)." }),
        el("div", { class: "row" }, btn("Approve: submit", "btn primary", { do: "approve", id: a.id }, "Approving…", "as-approve"),
          note, btn("Decline", "btn danger", () => ({ do: "decline", id: a.id, note: note.value }), "Declining…", "as-decline"))));
    }
    if (word === "finish") {
      parts.push(el("div", { class: "card attn", id: "as-ask", testid: "as-finish" },
        el("h2", { text: "The session ended. Did you approve Submit, and did the site confirm the application?" }),
        el("p", { class: "small muted", text: "Yes marks the row Applied (dated today)." }),
        el("div", { class: "row" }, btn("Yes, it went through", "btn primary", { do: "finish", answer: "y" }, "Saving…", "as-yes"),
          btn("No", "btn", { do: "finish", answer: "n" }, "Saving…", "as-no"),
          btn("No: do it on the laptop", "btn", { do: "finish", answer: "laptop" }, "Saving…", "as-no-laptop"))));
    }
    if (chat) {
      parts.push(el("div", { class: "card stack reply", id: "as-reply-card", testid: "as-reply-card" },
        word === "working" ? el("p", { class: "row small muted" }, el("span", { class: "spinner" }), "Working on it… (this page updates by itself when it answers)") :
          word === "waiting" ? el("p", { class: "small", text: "Waiting for your answer." }) : null,
        el("label", { class: "field" }, "Reply", el("span", { class: "muted small", text: "Typed into its terminal, as if you typed it there. Several lines go as one message." }), reply),
        el("div", { class: "row" }, btn("Send", "btn primary", () => ({ do: "reply", text: reply.value }), "Sending…", "as-send")),
        el("hr", { class: "sep" }),
        el("p", { class: "small muted", text: "Done (it submitted, or you stop here)? End the session; then it asks whether it went through. A site that refuses the homelab's browser (a sign-in page with no way in): End: do it on the laptop. The row is marked in your sheet, and the homelab never starts it again." }),
        el("div", { class: "row" }, btn("End session", "btn", { do: "exit" }, "Ending…", "as-exit"),
          btn("End: do it on the laptop", "btn", { do: "laptop" }, "Ending…", "as-laptop"))));
    }
    fill(view, ...parts);
    term.textContent = d.terminal || "(not readable)";
    term.scrollTop = term.scrollHeight;
    if (first) {  // open at what it wants from you
      first = false;
      const at = view.querySelector("#as-ask") || view.querySelector("#as-reply-card");
      if (at) at.scrollIntoView({ block: "start" });
    }
  }

  async function load() {
    clearTimeout(timer);
    try {
      d = await api.get(`${api_}/${encodeURIComponent(slot)}`);
    } catch (e) {
      if (!view.isConnected || S.gone) return;
      if (S.offline) { timer = setTimeout(load, 4000); return; }  // the homelab can't be reached a moment: ask again
      fill(view, el("div", { class: "card", testid: "as-gone" }, el("h2", { text: "No session there" }),
        el("p", { class: "muted", text: "It has ended (or it never ran in that slot)." }), el("a", { class: "btn", href: "#/apply", text: "Apply", onclick: () => sessionStorage.setItem("applyTab", "assistants") })));
      return;
    }
    if (!view.isConnected) return;
    if (d.version !== version) { version = d.version; draw(); } else { term.textContent = d.terminal || "(not readable)"; }
    if (d.browser && ["working", "approve", "waiting"].includes(d.status)) live.src = `${api_}/${slot}/shot.png?live=${Date.now()}`;
    timer = setTimeout(load, 4000);
  }
  await load();
  return { leave: () => clearTimeout(timer) };
};

// ---- Settings
// Settings, in tabs (#/settings/<tab>), the same on the homelab's page: Status (the health checks, your logins), Jobs
// (what you're looking for, Summer internships), Applying (the weekly limit, the resume NUworks gets, your sheet and
// files), Assistant (the company-site assistant, your password manager), Homelab, App (notifications, setup, about).
const SETTINGS_TABS = [["status", "Status"], ["jobs", "Jobs"], ["applying", "Applying"], ["assistant", "Assistant"],
  ["homelab", "Homelab"], ["app", "App"]];

screens.settings = async (view) => {
  let d;
  let setup = null;  // /api/setup's steps: the Jobs and App tabs show some of their bodies (the same forms as Setup)
  let tab = S.param || S.settingsTab || "status";
  let healthList = null;  // the Status tab's list of checks: a health poll redraws only it, never a form you're typing in
  const tabs = el("div", { class: "tabs", role: "tablist", "aria-label": "Settings" });
  const pane = el("div", { role: "tabpanel", id: "settings-pane", testid: "settings-pane" });
  fill(view, tabs, pane);
  async function reload() {
    try { d = await api.get("/api/settings"); S.logsDir = d.paths.logs; } catch (e) { fill(pane, el("div", { class: "note fail", text: e.message })); return; }
    draw();
  }
  let setupLoading = null;  // one /api/setup at a time (it reads your sheet: seconds), shared by every draw waiting on it
  function loadSetup() {
    return setupLoading || (setupLoading = api.get("/api/setup").then((r) => { setup = r; }, (e) => { setup = { error: e.message }; })
      .finally(() => { setupLoading = null; }));
  }
  // one Setup step's body ("as": another body with that step's data, like Discord alone from the extras step)
  function stepBody(id, as = id) {
    if (setup.error) return el("div", { class: "note fail", text: setup.error });
    const s = (setup.steps || []).find((x) => x.id === id);
    return s ? el("div", { class: "stack" }, ...window.NUautoSetupBody(as, s, () => { setup = null; draw(); })) : null;
  }
  const shown = () => SETTINGS_TABS.filter(([k]) => k !== "homelab" || onHomelab() || (d.homelab && d.homelab.available));
  function pick(k) {
    tab = k;
    S.settingsTab = k;
    history.replaceState(null, "", `#/settings/${k}`);
    draw();
  }
  let drawn = 0;  // the newest draw wins: one still waiting for /api/setup must not replace a tab drawn since
  async function draw() {
    const mine = ++drawn;
    const list = shown();
    if (!list.some(([k]) => k === tab)) tab = "status";
    fill(tabs, ...list.map(([k, label]) => el("button", { role: "tab", testid: `settings-tab-${k}`, "aria-selected": String(tab === k),
      "aria-controls": "settings-pane", text: label, onclick: () => pick(k) })));
    healthList = null;
    if ((tab === "jobs" || tab === "app") && (!setup || setup.error)) {  // (an error: tried again on the next draw)
      fill(pane, el("p", { class: "row muted" }, el("span", { class: "spinner" }), "Loading…"));
      await loadSetup();
      if (mine !== drawn) return;
    }
    fill(pane, ...PANES[tab]());
  }
  function drawHealth() {
    if (healthList) fill(healthList, ...S.checks.map((c) => el("li", { testid: `check-${c.id}` }, el("div", { class: "what" },
      el("span", { class: `chip ${c.status}`, text: SHORT[c.status] || c.status }), " ", el("b", { text: c.title }), " ",
      el("span", { class: "muted", text: c.detail }), el("div", { class: "small muted", text: `checked ${ago(c.at)}` })), fixButton(c))));
  }
  const PANES = {
    status: () => {
      healthList = el("ul", { class: "list" });
      drawHealth();
      return [
        el("div", { class: "card", testid: "all-checks" }, el("div", { class: "row between" }, el("h2", { text: "Health" }),
          el("button", { class: "btn small", text: "Run all checks now", testid: "btn-run-checks", onclick: async () => {
            await api.post("/api/health/run", { groups: ["quick", "sheet", "claude", "firefox", "discord", "homelab", "bitwarden", "nuworks"] }).catch(fail);
            toast("Checking…"); setTimeout(pollHealth, 1500); setTimeout(pollHealth, 6000); } })), healthList),
        loginsCard()];
    },
    jobs: () => [
      el("div", { class: "card stack", testid: "prefs-settings" }, el("h2", { text: "What you're looking for" }),
        el("p", { class: "small muted", text: "Your term and year, the match a job needs to enter your pool, and the kinds of work with their ranking bonuses. Saving rebuilds your pool at once." }),
        stepBody("preferences")),
      internCard()],
    applying: () => [applyingCard(), filesCard()],
    assistant: () => [assistantCard(),
      // Bitwarden unlocks in a terminal window on the laptop: its card is the laptop's only
      onHomelab() ? null : el("div", { class: "card stack", testid: "pm-settings" }, el("h2", { text: "Password manager" }),
        passwordManagerPanel(d.password_manager, (r) => { if (r) { d = r; draw(); } else reload(); }))],
    homelab: () => homelabCards(),
    app: () => [
      el("div", { class: "card stack", testid: "extras-settings" }, stepBody("extras", onHomelab() ? "discord" : "extras")),
      el("div", { class: "card stack" }, el("h2", { text: "Setup" }),
        el("p", { class: "small muted", text: onHomelab() ? "Setup is done in NUauto on your laptop (the homelab gets its settings from there)." :
          "Every step again, each checked: Google, your sheet, resume, NUworks, preferences, notifications." }),
        el("div", { class: "row" }, el("a", { class: "btn", href: "#/setup", text: "Open setup" }),
          el("button", { class: "btn", text: "Run a self-test", testid: "btn-selftest", title: "Demo mode end to end in a hidden browser: nothing real is touched",
            onclick: () => action("selftest") }),
          el("a", { class: "btn ghost", href: "#/logs", text: "Logs and screenshots" }))),
      el("div", { class: "card stack" }, el("h2", { text: "About" }),
        el("p", { class: "small muted" }, `Version ${S.state ? S.state.version : ""} · ${onHomelab() ? "on your homelab, " : ""}your files: `, el("code", { text: d.paths.state }),
          d.tools.claude ? [" · claude: ", el("code", { text: d.tools.claude })] : " · claude not found"),
        onHomelab() ? el("p", { class: "small muted", text: "NUauto on your homelab is always on (it is part of nuauto-web there)." }) :
          el("div", {}, el("button", { class: "btn danger", text: "Quit NUauto", testid: "btn-quit", onclick: () => confirmBox("Quit NUauto?",
            "Closes this window's server. A running job is stopped first (like Ctrl+C).", "Quit", async () => { await api.post("/api/quit", {}).catch(() => {}); gone("NUauto has quit. Start it again from your apps menu, or run nuauto gui."); }, { danger: true }) })))],
  };
  // redrawn by itself after a change, so the preferences form above keeps what you typed
  function internCard() {
    const card = el("div", { class: "card stack", testid: "intern-settings" }, el("h2", { text: "Summer internships" }),
      internshipsPanel(d.internships, async (r) => {
        if (r) d = r;
        else try { d = await api.get("/api/settings"); } catch (e) { fail(e); return; }
        card.replaceWith(internCard());
      }));
    return card;
  }
  function loginsCard() {
    return el("div", { class: "card stack" }, el("h2", { text: "Logins" }),
      el("div", { class: "row between" }, el("span", { text: "Google" }), el("button", { class: "btn small", text: d.google_login ? "Log in again" : "Log in", onclick: () => action("login_google") })),
      el("div", { class: "row between" }, el("span", { text: "NUworks" }), el("div", { class: "row" },
        el("button", { class: "btn small", text: "Check now", onclick: () => { action("check_nuworks"); toast("Checking NUworks in the background…"); } }),
        el("button", { class: "btn small", text: "Log in", onclick: () => action("login_nuworks") }))),
      el("div", { class: "row between" }, el("span", { text: "Claude Code" }), el("button", { class: "btn small", text: "Log in", onclick: () => action("login_claude") })),
      el("div", { class: "row between", testid: "gmail-login" }, el("div", {}, el("span", { text: "Gmail (optional)" }),
        el("div", { class: "small muted", text: d.gmail.ready ? (d.gmail.days_left != null ? `read-only; ${Math.max(0, d.gmail.days_left)} days left` : "read-only") :
          "read-only, for the assistant's email codes and links" })),
        el("button", { class: "btn small", text: d.gmail.ready ? "Log in again" : "Log in", testid: "btn-login-gmail", onclick: () => action("login_gmail") })),
      d.gmail.ready ? null : el("p", { class: "small muted" }, "Gmail first needs its API turned on in your Google Cloud project: ",
        linkOut("https://console.cloud.google.com/apis/library/gmail.googleapis.com", "Gmail API"), ". Use the inbox of the assistant's account email (Assistant tab)."),
      el("p", { class: "small muted", text: "Google logins last 7 days (Testing-mode apps); NUauto warns you a day before. Log in to Google again renews Gmail's too." }));
  }
  // the weekly limit, the resume NUworks' Apply popup picks, and your sheet
  function applyingCard() {
    const s = d.settings;
    const label = el("input", { type: "text", value: d.resume_label, "aria-label": "Resume label", testid: "set-resume-label" });
    const week = el("input", { type: "date", value: s.week_start || "", "aria-label": "Week start", testid: "set-week-start" });
    const cap = el("input", { type: "number", min: "1", max: String(d.max_per_week_ceiling), step: "1", value: String(d.max_per_week),
      "aria-label": "Applications per week", testid: "set-max-per-week" });
    return el("div", { class: "card stack", testid: "applying-settings" }, el("h2", { text: "Applying on NUworks" }),
      el("label", { class: "field" }, "Applications per week", el("span", { class: "muted small", text: `The most NUauto will submit in one week (1 to ${d.max_per_week_ceiling}). It stops at this number; Approved jobs wait for the next week.` }), cap),
      el("label", { class: "field" }, "Week starts on", el("span", { class: "muted small", text: "Your weekly count starts over every 7 days from this date. Empty: it counts the last 7 days." }), week),
      el("label", { class: "field" }, "Resume NUauto picks", el("span", { class: "muted small", text: "Its name exactly as in the Resume dropdown of NUworks' Apply popup (Setup > NUworks can read the list for you)." }), label),
      s.sheet_id ? el("p", { class: "small", testid: "set-sheet" }, el("b", { text: "Your sheet: " }),
        linkOut(`https://docs.google.com/spreadsheets/d/${encodeURIComponent(s.sheet_id)}`, "open it in Google Sheets"),
        el("span", { class: "muted", text: " · to use another one: Setup > Your Google Sheet." })) : null,
      el("div", {}, el("button", { class: "btn primary", text: "Save", testid: "settings-save", onclick: async () => {
        try { d = await api.post("/api/settings", { resume_label: label.value, week_start: week.value, max_per_week: cap.value }); toast("Saved."); pollState(); draw(); } catch (e) { fail(e); } } })));
  }
  // The laptop: which homelab (connection, setup), then NUauto there (its page, its assistant), each its own card. The
  // homelab's own page: the devices paired with it, and its assistant's queue.
  function homelabCards() {
    const ha = d.homelab_assistant;
    const done = (r) => { if (r) { d = r; draw(); } else reload(); };
    if (onHomelab()) {
      return [el("div", { class: "card stack", testid: "homelab-settings" }, el("h2", { text: "This homelab" }), devicesPanel()),
        el("div", { class: "card stack" }, el("h2", { text: "Its assistant" }), homelabAssistantPanel(ha, done))];
    }
    return [el("div", { class: "card stack", testid: "homelab-settings" }, el("h2", { text: "Homelab" }), homelabLinkPanel(d.homelab, () => reload())),
      ha.available ? el("div", { class: "card stack", testid: "ha-settings" }, el("h2", { text: "NUauto on your homelab" }), homelabAssistantPanel(ha, done)) : null];
  }
  // (the homelab's page) the devices paired with it: each one's cookie is its key (the homelab keeps only its hash)
  function devicesPanel() {
    const name = el("input", { type: "text", placeholder: "e.g. phone", maxlength: "40", "aria-label": "Name of the device", testid: "pair-name" });
    return el("div", { class: "stack", testid: "devices" }, el("h3", { text: "Paired devices" }),
      el("p", { class: "small muted", text: "The browsers that can use NUauto on your homelab (over Tailscale only). A browser is paired once; unpair one you no longer use, or lost." }),
      el("ul", { class: "list" }, ...(d.devices || []).map((x) => el("li", { testid: `device-${x.id}` },
        el("div", { class: "what" }, el("b", { text: x.name || "device" }), x.id === d.this ? el("span", { class: "chip accent", text: "this one" }) : null,
          el("div", { class: "small muted", text: `paired ${String(x.paired || "").replace("T", " ")}` })),
        x.id === d.this ? null : el("button", { class: "btn small danger", text: "Unpair", testid: `unpair-${x.id}`, onclick: () =>
          confirmBox(`Unpair ${x.name || "this device"}?`, "Its browser can't use NUauto on your homelab any more (pair it again any time).", "Unpair", async () => {
            const r = await action("unpair", { id: x.id });
            if (r) { d.devices = r.devices; draw(); toast("Unpaired."); }
          }, { danger: true }) })))),
      el("div", { class: "row" }, el("label", { class: "small muted" }, "Pair another device: ", name),
        el("button", { class: "btn small primary", text: "Make a pairing link", testid: "pair-device", onclick: async () => {
          const r = await action("pair_device", { name: name.value });
          if (!r || !r.link) return;
          modal((box, close) => box.append(el("h2", { text: "Pairing link" }),
            el("p", { class: "muted", text: `Open it in the browser you want to pair (its own browser: Safari, Chrome; not inside another app), within ${r.minutes} minutes; it works once. Then press Pair there.` }),
            el("pre", { class: "log", testid: "pair-link", tabindex: "0", text: r.link }),
            el("div", { class: "foot" }, el("span"), el("div", { class: "row" },
              el("button", { class: "btn", text: "Copy", onclick: () => navigator.clipboard.writeText(r.link).then(() => toast("Copied.")) }),
              el("button", { class: "btn primary", text: "Done", onclick: close })))), { label: "Pairing link", testid: "pair-dialog" });
        } })));
  }
  // The company-site assistant (nuauto assist): the email for the accounts it makes, how hard it thinks, the folders it
  // may read, and its logins (one per job site; the passwords never reach this page) as Bitwarden's import file.
  function filesCard() {  // the resume and the transcript NUauto keeps (on the homelab's page: the copies it has)
    const here = onHomelab();
    const give = (kind, done) => async (file) => {
      try { d = await sendPdf(kind, file); toast(done); pollHealth(); draw(); } catch (e) { fail(e); }
    };
    const part = (kind, title, help, done) => el("div", { class: "stack", testid: `file-${kind}` },
      el("div", { class: "row between" }, el("div", {}, el("b", { text: title }), el("div", { class: "small muted", testid: `file-${kind}-now`, text: pdfLine(d.files[kind]) })),
        !here && kind === "transcript" && d.files.transcript ? el("button", { class: "btn small ghost", text: "Remove", testid: "file-transcript-remove",
          onclick: () => confirmBox("Remove your transcript?", "The assistant then has none to upload (on your homelab too).", "Remove", async () => {
            try { d = await api.post("/api/files", { kind, remove: true }); toast("Transcript removed."); draw(); } catch (e) { fail(e); } }) }) : null),
      el("span", { class: "muted small", text: help }),
      here ? null : pdfDrop(`file-${kind}-drop`, d.files[kind] ? "Drop a new PDF here, or" : "Drop the PDF here, or", give(kind, done)));
    return el("div", { class: "card stack", testid: "files-settings" }, el("h2", { text: "Your files" }),
      el("p", { class: "small muted", text: here ? "Copies from your laptop. Give NUauto there a new one (Settings > Applying > Your files): it copies it here at once." :
        "NUauto keeps its own copy of each (in its private folder, readable only by you; your homelab, if you use one, gets a copy at once). To change one, drop the new PDF here." }),
      part("resume", "Resume", "The PDF you use on NUworks: Claude compares each posting with it, and the assistant uploads it on company sites.", "Resume saved."),
      part("transcript", "Transcript (optional)", "Your unofficial transcript: the assistant uploads it only where an application asks for one.", "Transcript saved."));
  }

  function assistantCard() {
    const s = d.settings;
    const email = el("input", { type: "email", value: s.accounts_email || "", placeholder: "you@example.com",
      "aria-label": "Account email", testid: "set-accounts-email" });
    const effort = el("select", { "aria-label": "Assistant effort", testid: "set-assist-effort" },
      ...d.efforts.map((e) => el("option", { value: e, text: e })));
    effort.value = d.efforts.includes(s.assist_effort) ? s.assist_effort : d.efforts[0];
    const folders = el("textarea", { rows: "3", "aria-label": "Folders the assistant may read", testid: "set-read-paths" });
    folders.value = (s.assist_read_paths || []).join("\n");
    const writeups = el("textarea", { rows: "2", "aria-label": "Your writeups", testid: "set-writeups" });
    writeups.value = (s.assist_writeups || []).join("\n");
    const slots = el("select", { "aria-label": "Assistants at once", testid: "set-assist-slots" }, ...[1, 2, 3].map((n) => el("option", { value: String(n), text: String(n) })));
    slots.value = String(d.assist_slots);
    const lines = (t) => t.value.split("\n").map((x) => x.trim()).filter(Boolean);
    const L = d.logins;
    const exportBtn = el("button", { class: "btn small", testid: "btn-export-logins", disabled: !L.new,
      text: L.new ? `Export ${plural(L.new, "new login")} for Bitwarden` : "No new logins to export", onclick: async () => {
        try {
          const r = await api.post("/api/logins/export", {});
          d.logins = r.logins;
          modal((box, close) => box.append(el("h2", { text: `${plural(r.count, "login")} exported` }),
            el("p", {}, "Saved to ", el("code", { text: r.path }), " (only you can read it). Import it in Bitwarden: the web vault, ",
              el("b", { text: "Tools > Import data" }), ", format ", el("b", { text: "Bitwarden (json)" }), ". Then delete the file:"),
            el("pre", { class: "log", text: `rm ${r.path}` }),
            el("div", { class: "foot" }, el("span"), el("button", { class: "btn primary", text: "Done", onclick: close }))), { label: "Logins exported", testid: "export-dialog" });
          draw();
        } catch (e) { fail(e); }
      } });
    const here = onHomelab();  // the homelab's page: the folders and the export file are the laptop's
    const pmOn = !here && d.password_manager && d.password_manager.on;  // with Bitwarden on, its Add button is the way in
    return el("div", { class: "card stack", testid: "assistant-settings" }, el("h2", { text: "Company-site assistant" }),
      el("label", { class: "field" }, "Email for job-site accounts", el("span", { class: "muted small", text: "When a company's site needs an account (Workday and others), the assistant makes one with this email. Each site gets its own random password; the assistant never sees it." }), email),
      el("label", { class: "field" }, "Effort", el("span", { class: "muted small", text: "How hard it thinks (Claude Code's effort). Nothing is submitted without your review either way." }), effort),
      here ? el("p", { class: "small muted", text: "The folders it may read are on your laptop: set them in NUauto there (the homelab gets copies)." }) : [
        el("label", { class: "field" }, "Folders it may read", el("span", { class: "muted small", text: "Your projects, one per line: it reads them for longer answers. Keys, tokens, .env files and NUauto's own logins are never read." }), folders),
        el("label", { class: "field" }, "Writeups (optional)", el("span", { class: "muted small", text: "Folders of your own project or security writeups, one per line: where it looks first for material for longer answers (copied to your homelab if its assistant runs there)." }), writeups)],
      el("label", { class: "field" }, "Assistants at once", el("span", { class: "muted small", text: "How many company-site applications may run side by side, each in its own browser (1 to 3, on each computer)." }), slots),
      el("div", { class: "row between" }, el("span", { class: "small muted", testid: "logins-count",
        text: !L.sites ? "No logins made yet." : pmOn ? `Logins it made: ${plural(L.sites, "site")}. Bitwarden is on: they go into your vault from Password manager, below.` :
          `Logins it made: ${plural(L.sites, "site")}${L.new ? `, ${L.new} not exported yet` : ", all exported"}` }), here || pmOn ? null : exportBtn),
      el("div", {}, el("button", { class: "btn primary", text: "Save", testid: "assistant-save", onclick: async () => {
        try {
          const both = { accounts_email: email.value, assist_effort: effort.value, assist_slots: Number(slots.value) };
          d = await api.post("/api/settings", here ? both :
            { ...both, assist_read_paths: lines(folders), assist_writeups: lines(writeups) });
          toast("Saved."); draw();
        } catch (e) { fail(e); }
      } })));
  }
  await reload();
  let seenTask = null;
  return {
    health: () => drawHealth(),
    // Jobs > Read the list from NUworks is a task: once it ends, the term list (or why not) comes with fresh steps
    task: (t) => {
      if (!t || t.kind !== "onboard_terms" || t.state === "running" || t.id === seenTask) return;
      seenTask = t.id;
      setup = null;
      if (tab === "jobs") draw();
    },
  };
};

// ---- Optional parts, the same in Settings and in setup: your password manager, and the assistant on a homelab.
// done(newSettings) after a change (Settings redraws with it; setup reloads its steps); done() to reload.

async function saveSettings(body, message, done) {
  try { const r = await api.post("/api/settings", body); toast(message); done(r); } catch (e) { fail(e); }
}

function passwordManagerPanel(pm, done) {
  const chk = pm.check;
  const only = el("input", { type: "text", value: (pm.only || []).join(", "), placeholder: "myworkday, amazon.jobs",
    "aria-label": "Your own logins: words in their addresses", testid: "pm-only" });
  return el("div", { class: "stack", testid: "pm-panel" },
    el("p", { class: "small muted", text: "Optional. With Bitwarden, the assistant signs in with your own job-site logins (your Workday accounts, say) instead of making new ones, and the logins it does make go into your vault. NUauto uses Bitwarden's command line (bw): you unlock it yourself in a terminal window, so NUauto never sees your master password." }),
    el("div", { class: "row between" }, el("div", { class: "row" }, el("b", { text: "Bitwarden" }),
      el("span", { class: `chip ${pm.on ? (chk ? chk.status : "unknown") : "off"}`, testid: "pm-status",
        text: pm.on ? (chk ? SHORT[chk.status] || chk.status : "checking…") : "not used" })),
      el("button", { class: "btn small" + (pm.on ? "" : " primary"), testid: "pm-toggle", text: pm.on ? "Stop using it" : "Use Bitwarden",
        onclick: () => saveSettings({ password_manager: pm.on ? "" : "bitwarden" }, pm.on ? "Bitwarden: not used." : "Bitwarden: on.", done) })),
    pm.on && chk ? el("p", { class: "small", text: chk.detail }) : null,
    pm.on ? [
      !pm.bw ? el("div", { class: "row between" }, el("span", { class: "small", text: "First, Bitwarden's command line (bw)." }),
        el("button", { class: "btn small primary", text: "Install it", testid: "pm-install", onclick: FIXES.bitwarden_install })) : null,
      el("div", { class: "row between" }, el("span", { class: "small", testid: "pm-waiting", text: pm.waiting ?
          `${plural(pm.waiting, "login")} NUauto made ${pm.waiting === 1 ? "waits" : "wait"} to go into your vault.` : "Every login NUauto made is in your vault." }),
        el("button", { class: "btn small" + (pm.waiting ? " primary" : ""), testid: "pm-add", disabled: !pm.bw || !pm.waiting,
          text: pm.waiting ? `Add ${pm.waiting} to Bitwarden` : "Nothing to add", onclick: () => action("bitwarden_add") })),
      el("label", { class: "field" }, "Your own logins",
        el("span", { class: "muted small", text: "Sites where you already have an account saved in Bitwarden: words their addresses contain, comma-separated (myworkday for Workday, amazon.jobs). Only those logins are taken, never the rest of your vault." +
          (pm.own ? ` You have ${plural(pm.own, "login")} of yours in NUauto now.` : "") }), only),
      el("div", { class: "row" },
        el("button", { class: "btn small", testid: "pm-import", disabled: !pm.bw, text: "Import my logins", onclick: () =>
          action("bitwarden_import", { only: only.value.split(",").map((t) => t.trim()).filter(Boolean) }) }),
        el("button", { class: "btn small ghost", text: "Check now", onclick: async () => {
          await api.post("/api/health/run", { groups: ["bitwarden"] }).catch(fail);
          setTimeout(() => { pollHealth(); done(); }, 3000); } })),
    ] : null);
}

function hour(h) { return `${String(h).padStart(2, "0")}:00`; }

// NUauto on your homelab (homelab mode): its page, for your phone and other computers (remote_app), and its assistant
// (remote_assist: company-site applications there too, which needs the page). On the homelab's own page: only the
// assistant's queue settings (it can't switch itself off from there).
function homelabAssistantPanel(ha, done) {
  if (!ha.available) return null;
  const here = onHomelab();
  const from = el("input", { type: "number", min: "0", max: "23", step: "1", value: String(ha.hours[0]), "aria-label": "From (hour)", testid: "ha-from" });
  const to = el("input", { type: "number", min: "1", max: "24", step: "1", value: String(ha.hours[1]), "aria-label": "To (hour)", testid: "ha-to" });
  const queue = ha.on ? [
    ...[["nuworks", "NUworks rows", "company sites of NUworks jobs; only under the weekly cap"],
        ["other", "Other jobs rows", "the Other jobs tab: Summer internships and jobs you added"]].map(([tab, name, what]) => {
      const on = (ha.auto_tabs || {})[tab];
      return el("div", { class: "row between" }, el("span", { class: "small", testid: `ha-auto-text-${tab}`, text: on ?
          `${name} (${what}): it starts sessions by itself, only between ${hour(ha.hours[0])} and ${hour(ha.hours[1])} New York time (up to "Assistants at once" at a time).` :
          `${name} (${what}): you start each session yourself (Apply, in NUauto on your homelab).` }),
        el("button", { class: "btn small", testid: `ha-auto-${tab}`, text: on ? "Stop starting by itself" : "Start by itself",
          onclick: () => saveSettings({ remote_auto: { [tab]: !on } }, on ? `${name} no longer start by themselves.` :
            `${name} start by themselves now, between ${hour(ha.hours[0])} and ${hour(ha.hours[1])} New York time.`, done) }));
    }),
    el("label", { class: "field" }, "Its hours", el("span", { class: "muted small", text: "The only hours it starts sessions by itself (New York time, 24-hour clock), so no pings at night. A session already running goes on; you can start one yourself any time." }),
      el("div", { class: "row" }, "between", from, ":00 and", to, ":00")),
    el("div", { class: "row" }, el("button", { class: "btn small primary", text: "Save", testid: "ha-save", onclick: () =>
      saveSettings({ remote_hours: [Number(from.value), Number(to.value)] }, "Saved.", done) })),
  ] : null;
  if (here) {
    return el("div", { class: "stack", testid: "ha-panel" }, ha.on ?
      el("p", { class: "small muted", text: "Company-site applications run here in a virtual browser; you answer each session on its screen (Apply > Assistants). Turning the assistant on or off is done in NUauto on your laptop." }) :
      el("p", { class: "small muted", text: "The assistant is off on this homelab: turn it on in NUauto on your laptop (Settings > Homelab > NUauto on your homelab)." }), queue);
  }
  return el("div", { class: "stack", testid: "ha-panel" },
    el("p", { class: "small muted", text: "Optional, with a homelab. Its page is this same NUauto, on the homelab: open it from your phone or another computer (over Tailscale; each browser is paired once) to review, apply and answer the assistant while this computer is off. NUauto here works the same either way." }),
    el("div", { class: "row between" }, el("div", { class: "row" }, el("b", { text: "Its page" }),
      el("span", { class: `chip ${ha.app ? "ok" : "off"}`, text: ha.app ? "on" : "off", testid: "ha-app-status" })),
      el("button", { class: "btn small" + (ha.app ? "" : " primary"), testid: "ha-app-toggle", text: ha.app ? "Turn off" : "Turn on",
        disabled: ha.on && ha.app, title: ha.on && ha.app ? "The assistant on the homelab needs it: turn that off first" : null,
        onclick: () => saveSettings({ remote_app: !ha.app }, ha.app ? "Off: the homelab has no NUauto page." : "On: NUauto's page starts on the homelab in a moment.", done) })),
    ha.app ? el("div", { class: "row" },
      el("button", { class: "btn small", text: "Pair this computer", testid: "ha-pair", onclick: pairThisComputer }),
      ha.review_url ? el("button", { class: "btn small ghost", text: "Open it", testid: "ha-open", onclick: () => openExternal(`${ha.review_url}/`) }) : null,
      el("span", { class: "small muted", text: "A phone: on the homelab, nuauto review pair --discord (or Settings > Homelab > Paired devices on its page)." })) : null,
    ha.app ? linkRow(done) : null,
    el("hr", { class: "sep" }),
    el("div", { class: "row between" }, el("div", { class: "row" }, el("b", { text: "Its assistant" }),
      el("span", { class: `chip ${ha.on ? "ok" : "off"}`, text: ha.on ? "on" : "off", testid: "ha-status" })),
      el("button", { class: "btn small" + (ha.on ? "" : " primary"), testid: "ha-toggle", text: ha.on ? "Turn off" : "Turn on",
        onclick: () => saveSettings({ remote_assist: !ha.on }, ha.on ? "Off: the homelab runs no assistant." : "On: the homelab can run the assistant (and has its page).", done) })),
    el("p", { class: "small muted", text: "Company-site applications run there too, in a virtual browser, so they go on with this computer off. Discord tells you when one needs you; you answer it, approve each Submit and solve captchas on its page. The homelab needs a few packages once: docs/REMOTE.md." }),
    queue);
}

// This app's link to the homelab's page (link.py): with it, Start on the homelab, its sessions' screens and its Apply
// runs work inside this window. Linking pairs this app as one of the page's devices (its key stays on this computer).
function linkRow(done) {
  const l = S.state && S.state.homelab_link;
  if (!l) return null;
  return el("div", { class: "stack", testid: "ha-link" },
    el("div", { class: "row between" }, el("div", { class: "row" }, el("b", { text: "This app" }),
      el("span", { class: `chip ${l.on ? (l.error ? "warn" : "ok") : "off"}`, testid: "ha-link-status",
        text: l.on ? (l.error ? "linked: can't reach it now" : "linked") : "not linked" })),
      el("button", { class: "btn small" + (l.on ? "" : " primary"), testid: "ha-link-toggle", text: l.on ? "Unlink" : "Link this app",
        onclick: async () => { if (await action(l.on ? "homelab_unlink" : "homelab_link")) { await pollState(); done(); } } })),
    el("p", { class: "small muted", text: l.on ?
      "The homelab's assistant sessions open here (approve its Submit, reply), and Run on: the homelab (Start, and each row's Apply) works from this window." :
      "Link it to start sessions and Apply runs on the homelab from this window, and open its sessions here instead of in your browser. It pairs this app as one of the page's devices (over ssh, once)." }),
    l.on && l.error ? el("div", { class: "note warn small", testid: "ha-link-error", text: l.error }) : null);
}

// Settings > Homelab (the laptop, a source install): which homelab this computer works with. Check asks it over ssh
// (read-only) and fills in what it found; Use this homelab saves that (NUauto then needs a restart); Set it up opens a
// terminal window doing docs/DEPLOY.md's steps, each asked first. Without one, everything runs on this computer.
function homelabLinkPanel(h, done) {
  const ssh = el("input", { type: "text", value: h.ssh || h.hostname || "", placeholder: "homelab (an ssh alias, or user@host)",
    "aria-label": "Its ssh name", testid: "hl-ssh" });
  const folder = el("input", { type: "text", value: h.dir || h.default_dir, "aria-label": "Folder there", testid: "hl-folder" });
  const out = el("div", { testid: "hl-result" });
  const note = el("div");  // the restart note: outside the folded parts, so it shows wherever you pressed
  const restartNote = () => el("div", { class: "note warn", testid: "hl-restart" }, el("b", { text: "Saved. " }),
    "Quit NUauto and open it again to switch. ",
    el("button", { class: "btn small", text: "Quit NUauto", onclick: async () => { await api.post("/api/quit", {}).catch(() => {});
      gone("NUauto has quit. Open it again from your apps menu (or run nuauto gui) to use the new setting."); } }));
  async function post(body) {
    try { return await api.post("/api/homelab", body); } catch (e) { fail(e); return null; }
  }
  async function check() {
    fill(out, el("p", { class: "row small muted" }, el("span", { class: "spinner" }), "Asking it over ssh…"));
    const f = await post({ action: "check", ssh: ssh.value, folder: folder.value });
    if (!f) { fill(out); return; }
    if (!f.ok) { fill(out, el("div", { class: "note fail", testid: "hl-fail", text: f.why })); return; }
    const on = Object.entries(f.units || {}).filter(([, v]) => v === "enabled").map(([k]) => k.replace(/^nuauto-/, ""));
    const facts = [["Its hostname", f.hostname], ["Its Tailscale address", f.tailscale || "none found: is Tailscale on there?"],
      ["NUauto there", f.venv ? "installed" : f.dir ? "its folder is there; not installed yet" : "not yet"],
      ["Claude Code there", f.claude ? "installed" : "not yet"], ["Timers and web page", on.length ? on.join(", ") : "none on yet"],
      ["Runs with nobody logged in", f.linger ? "yes" : "not yet"],
      ["The assistant's tools", f.assist_tools_missing.length ? `not there yet: ${f.assist_tools_missing.join(", ")} (only its assistant needs them)` : "all there"]];
    fill(out, el("ul", { class: "list", testid: "hl-facts" }, ...facts.map(([k, v]) => el("li", {}, el("span", { class: "muted", text: k }), el("b", { text: v })))),
      el("div", { class: "row" }, el("button", { class: "btn primary", testid: "hl-use", disabled: !f.tailscale || !f.hostname,
        text: h.on && h.hostname === f.hostname ? "Save again" : "Use this homelab", onclick: async () => {
          const r = await post({ action: "save", ssh: ssh.value, folder: folder.value, hostname: f.hostname, listen: f.tailscale });
          if (r) { fill(out); fill(note, restartNote()); }
        } }), el("span", { class: "small muted", text: "Then open NUauto again, and Set it up (each step asks first)." })));
  }
  const connect = [
    el("div", { class: "row" }, el("label", { class: "field grow" }, "Its ssh name", el("span", { class: "muted small", text: "What you type after ssh (a key, no password)." }), ssh),
      el("label", { class: "field grow" }, "NUauto's folder there", el("span", { class: "muted small", text: "Under its home folder." }), folder)),
    el("div", {}, el("button", { class: "btn", text: "Check", testid: "hl-check", onclick: check })),
    out];
  return el("div", { class: "stack", testid: "hl-panel" },
    el("p", { class: "small muted", text: "Optional. An always-on Linux computer you reach with ssh over Tailscale: it checks for new jobs twice a day and sends your Discord messages, so this computer can be off, and NUauto's page can run there for your phone. Without one, everything runs on this computer, the same way." }),
    h.on ? el("div", { class: "row between" }, el("div", { class: "row" }, el("b", { text: "Using" }),
      el("span", { class: "chip ok", testid: "hl-status", text: h.ssh || h.hostname })),
      el("div", { class: "row" },
        el("button", { class: "btn small primary", text: "Set it up", testid: "hl-setup", title: "A terminal window: copies NUauto there, installs what it needs, turns its timers on (each step asked first)",
          onclick: async () => { const r = await post({ action: "setup" }); if (r) terminalResult(r); } }),
        el("button", { class: "btn small", text: "Log in to Claude there", testid: "hl-claude", onclick: async () => { const r = await post({ action: "claude" }); if (r) terminalResult(r); } }),
        el("button", { class: "btn small danger", text: "Stop using it", testid: "hl-off", onclick: () => confirmBox("Stop using the homelab?",
          "Everything runs on this computer again (after a restart of NUauto): its updates, Discord messages and NUauto's page there stop being used from here. Nothing on the homelab is deleted.",
          "Stop using it", async () => { const r = await post({ action: "off" }); if (r) fill(note, restartNote()); }, { danger: true }) }))) :
      h.saved ? restartNote() : null,
    note,
    // in use: which one (its ssh name, folder) and the advanced parts fold away; not yet: the fields come first
    h.on ? el("details", { testid: "hl-change" }, el("summary", { text: "Use another homelab: its ssh name and folder" }), el("div", { class: "stack" }, ...connect)) : connect,
    h.on ? el("details", { testid: "hl-advanced" }, el("summary", { text: "Advanced: where its code comes from, Mark-done links" }), deploysPanel(h, post)) : null);
}

// The homelab in use: where its code comes from (GitHub's main by itself, or this computer at each push:
// deploy_from_git, a run with its log), and the Mark-done links' public address (web_base_url: your tunnel to it).
function deploysPanel(h, post) {
  const fork = el("input", { type: "text", value: h.deploy_repo, placeholder: "this project's GitHub repo",
    "aria-label": "Your fork's address", testid: "hl-fork" });
  const mark = el("input", { type: "text", value: h.web_base_url, placeholder: "https://nuauto.example.com",
    "aria-label": "The Mark-done page's address", testid: "hl-mark-url" });
  const on = h.deploy_from_git;
  return el("div", { class: "stack", testid: "hl-deploys" },
    el("div", { class: "row between" }, el("div", {}, el("b", { text: "Its code" }), el("div", { class: "small muted", text: on ?
        "From GitHub's main, by itself (every 5 minutes): merging to main deploys it." : "From this computer, each time it pushes (what is checked out here)." })),
      el("button", { class: "btn small", testid: "hl-deploy", text: on ? "From this computer instead" : "From GitHub's main",
        onclick: () => confirmBox(on ? "Code from this computer?" : "Code from GitHub's main?", on ?
          "The homelab's deploy timer is turned off; from now on it gets the code checked out here, at each push." :
          "This computer's code is copied there once more (with the deploy tool), then the homelab's deploy timer is turned on: from then on it runs GitHub's main (or your fork's), and branches here never reach it.",
          "Switch", async () => adoptTask(await post({ action: "deploy", on: !on, repo: fork.value }))) })),
    el("label", { class: "field" }, "Your fork (optional)", el("span", { class: "muted small", text: "The GitHub address to deploy from, if you run your own copy of NUauto: https://github.com/you/NUauto.git. Empty: this project's." }), fork),
    el("label", { class: "field" }, "Mark-done links (optional)", el("span", { class: "muted small", text: "The public address of the homelab's Mark-done page, so Discord's links work from your phone: a tunnel or reverse proxy you set up to its port 8765 (docs/DEPLOY.md). Empty: no Mark links." }), mark),
    el("div", {}, el("button", { class: "btn small primary", text: "Save", testid: "hl-mark-save", onclick: () =>
      saveSettings({ web_base_url: mark.value }, "Saved: the homelab's web page restarts with it.", () => {}) })));
}

// Summer internships (optional; local_config "internships", intern.py): on / off, the term as the lists name it, the
// bar to enter your pool (empty: your co-ops'), and the lists' addresses. Settings and setup's step are the same.
function internshipsPanel(ii, done) {
  const term = el("input", { type: "text", value: ii.term, maxlength: "40", "aria-label": "Term", testid: "in-term" });
  const bar = el("input", { type: "number", min: "1", max: "100", value: ii.threshold == null ? "" : String(ii.threshold),
    placeholder: `${ii.co_op_bar} (your co-ops')`, "aria-label": "Match % to enter your pool", testid: "in-bar" });
  const lists = Object.entries(ii.names).map(([k, name]) => [k, name, el("input", { type: "text", value: ii.lists[k] || "",
    "aria-label": `${name}'s address`, testid: `in-list-${k}` })]);
  // a new term (Summer 2028): the lists' addresses follow it (each year's lists are their own repos)
  term.addEventListener("change", () => {
    const y = (term.value.match(/(\d{4})/) || [])[1];
    if (y) lists.forEach(([, , inp]) => { inp.value = inp.value.replace(/Summer\d{4}/, `Summer${y}`); });
  });
  return el("div", { class: "stack", testid: "in-panel" },
    el("p", { class: "small muted", text: "Optional. Summer internships from public lists (not on NUworks): each posting is read from the company's own site and scored like your co-ops. Review gets a second list; approved ones go in your sheet's Other jobs tab, with no weekly limit, for the company-site assistant. Checked with each update (and every 2 hours on a homelab)." }),
    el("div", { class: "row between" }, el("div", { class: "row" }, el("b", { text: "Summer internships" }),
      el("span", { class: `chip ${ii.on ? "ok" : "off"}`, text: ii.on ? "on" : "off", testid: "in-status" })),
      el("button", { class: "btn small" + (ii.on ? "" : " primary"), testid: "in-toggle", text: ii.on ? "Turn off" : "Turn on",
        onclick: () => saveSettings({ internships: { on: !ii.on } }, ii.on ? "Internships: off." : "Internships: on (the next update reads the lists).", done) })),
    ii.on ? [
      el("label", { class: "field" }, "Term", el("span", { class: "muted small", text: "As the lists name it, like Summer 2027." }), term),
      el("label", { class: "field" }, "Match % to enter your pool", el("span", { class: "muted small", text: "Empty: the same bar as your co-ops (a kind of work's lower bar still counts)." }), bar),
      ...lists.map(([k, name, inp]) => el("label", { class: "field" }, `${name}'s address`,
        el("span", { class: "muted small", text: "Its listings.json (empty: not read)." }), inp)),
      el("div", {}, el("button", { class: "btn small primary", text: "Save", testid: "in-save", onclick: () => saveSettings({ internships: {
        on: true, term: term.value, threshold: bar.value, lists: Object.fromEntries(lists.map(([k, , inp]) => [k, inp.value.trim()])) } },
      "Saved: the next update reads them.", done) })),
    ] : null);
}

// ---- Setup (filled in by the wizard code in setup.js)
screens.setup = async (view) => {
  if (window.NUautoSetup) return window.NUautoSetup(view);
  fill(view, el("p", { class: "empty", text: "Setup is not available in this build." }));
  return {};
};

// ------------------------------------------------------------------ router

async function route() {
  if (S.gone) return;  // what gone() says stays on screen (an unpaired browser, a stopped NUauto)
  let name = (location.hash.replace(/^#\/?/, "").split("?")[0]) || "home";
  if (name === "company" || name === "other" || name === "sheet") {  // old screens, all a part of Apply now
    S.focus = name === "other" ? "other" : name === "company" ? "nuworks" : null;
    history.replaceState(null, "", "#/apply");
    name = "apply";
  }
  [name, S.param] = name.split("/");  // #/assistant/2: the screen, and which one
  const key = screens[name] ? name : "home";
  if (S.screen && S.screen.leave) S.screen.leave();
  S.screen = null;
  S.route = key;
  const nav = key === "assistant" ? "apply" : key;  // an assistant's screen is part of Apply (its Assistants tab)
  document.querySelectorAll(".side nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === nav));
  $("#page-title").textContent = TITLES[key];
  document.title = `${TITLES[key]} · NUauto`;
  const view = $("#view"), mine = ++S.routes;
  fill(view, el("p", { class: "empty", testid: "screen-loading" }, el("span", { class: "spinner" }), " Loading…"));
  let screen;
  try { screen = (await screens[key](view)) || {}; } catch (e) { if (mine === S.routes) fail(e); screen = {}; }
  if (mine !== S.routes) { if (screen.leave) screen.leave(); return; }  // you went on to another screen meanwhile
  S.screen = screen;
  renderTaskbar();
  pollState();  // fresh counts for the new screen, not the last poll's
  view.focus({ preventScroll: true });
}

window.addEventListener("hashchange", route);
// Ctrl+F (Cmd+F on a Mac) goes to the screen's own search box (Apply, Review, Answers: [data-find]); on a screen without
// one, or in a dialog, the browser's own find
document.addEventListener("keydown", (e) => {
  if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || e.key.toLowerCase() !== "f") return;
  const box = $("#modal-root").children.length ? null : $("#view [data-find]");
  if (!box) return;
  e.preventDefault();
  box.focus();
  box.select();
});
window.NUauto = { el, fill, api, action, toast, fail, modal, confirmBox, S, pollState, pollHealth, linkOut, plural, ago, sendPdf, pdfDrop, pdfLine };

(async function start() {
  await pollState();
  await pollHealth();
  try { S.logsDir = (await api.get("/api/settings")).paths.logs; } catch (e) { /* settings screen will say */ }
  if (S.gone) return;  // not paired (the homelab's page), or NUauto has stopped: its message stays
  if (S.state && S.state.setup_needed && !location.hash) location.hash = "#/setup";
  await route();
  setInterval(pollState, 3000);
  setInterval(pollHealth, 8000);
  pollTask();
})();
