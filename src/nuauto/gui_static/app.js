// NUauto window. Plain JS, no build step. Every bit of text from the server goes in with textContent (never as
// HTML): job descriptions come from NUworks. data-testid marks what tests and agents click.
"use strict";

// ------------------------------------------------------------------ helpers

const PROPS = new Set(["value", "checked", "disabled", "hidden", "selected", "indeterminate", "readOnly"]);

function safeHref(u) {
  u = String(u || "");
  return /^(https?:\/\/|#|\/)/i.test(u) ? u : null;
}

function el(tag, props, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k === "testid") n.dataset.testid = v;
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
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
    gone("NUauto has stopped. Start it again from your apps menu, or run nuauto gui in a terminal.");
    throw new Error("NUauto is not running.");
  }
  const data = await r.json().catch(() => ({}));
  if (r.status === 403) {
    gone("This window belongs to an earlier start of NUauto. Open NUauto again from its icon.");
    throw new Error("Not allowed.");
  }
  if (!r.ok) throw new Error(data.error || `Error ${r.status}`);
  return data;
}
const api = { get: (p) => call("GET", p), post: (p, b) => call("POST", p, b) };

function toast(msg, opts = {}) {
  const t = el("div", { class: "toast" + (opts.error ? " error" : ""), role: "status", testid: "toast" },
    el("span", { text: msg }),
    opts.action ? el("button", { class: "btn small", text: opts.action, onclick: () => { t.remove(); opts.onaction(); } }) : null);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), opts.ms || (opts.action ? 9000 : 4500));
}

function fail(e) { toast(e.message || String(e), { error: true }); }

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

function gone(msg) {
  if (S.gone) return;
  S.gone = true;
  fill($("#view"), el("div", { class: "card", testid: "gone" }, el("h2", { text: "NUauto is not available" }),
    el("p", { class: "muted", text: msg })));
}

// ------------------------------------------------------------------ shared state + polling

const S = { state: null, checks: [], route: null, screen: null, task: null, taskAfter: 0, taskLog: [],
            openQuestion: null, gone: false, lastTaskState: {} };

async function pollState() {
  if (S.gone) return;
  try {
    S.state = await api.get("/api/state");
  } catch (e) { return; }
  renderShell();
  if (S.screen && S.screen.update) S.screen.update(S.state);
  const t = S.state.task;
  if (t && t.state === "running" && !S.taskTimer) pollTask();
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
    const r = await api.get(`/api/task?after=${S.taskAfter}`);
    t = r.task;
    if (t && t.id !== known) { S.taskLog = []; S.taskAfter = 0; if (known) return pollTask(); }
  } catch (e) { return; }
  if (t) {
    S.taskLog.push(...t.log);
    S.taskAfter = t.log_total;
    S.task = t;
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

async function action(kind, args) {
  try {
    const r = await api.post("/api/action", { kind, args: args || {} });
    if (r.opened !== undefined) terminalResult(r);
    if (r.task) { S.task = r.task; S.taskLog = []; S.taskAfter = 0; S.lastTaskState[r.task.id] = "running"; pollTask(); }
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
  install_claude: () => confirmBox("Install Claude Code?", "This runs Claude Code's official installer (claude.ai/install.sh) for your user.",
    "Install", () => action("install_claude")),
  install_firefox: () => action("install_firefox"),
  fix_permissions: () => action("fix_permissions").then(() => toast("Permissions fixed.")),
  open_sheet: () => { if (S.state && S.state.sheet_url) openExternal(S.state.sheet_url); },
  update: () => action("update"),
  open_release: () => openExternal("https://github.com/voidReq/NUauto/releases/latest"),
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
  fill($("#banner"), st.demo ? el("div", { class: "demo-banner", testid: "demo-banner" },
    el("b", { text: "Demo mode. " }), "Nothing here is real: a fake sheet, fake NUworks pages, fake Claude. Nothing is sent anywhere.") : "");
  fill($("#side-foot"), el("div", { text: `v${st.version}` + (st.mode === "homelab" ? " · homelab" : "") }));
}

const STRIP = ["google", "sheet", "nuworks", "claude", "homelab"];
const SHORT = { ok: "OK", warn: "Check", fail: "Problem", busy: "Busy", unknown: "…", off: "Off" };

function renderHealth() {
  const strip = $("#health");
  const by = Object.fromEntries(S.checks.map((c) => [c.id, c]));
  const dots = STRIP.filter((id) => by[id] && !(id === "homelab" && by[id].status === "off")).map((id) => {
    const c = by[id];
    return el("button", { class: `dot ${c.status}`, testid: `health-${id}`, title: c.detail, "aria-label": `${c.title}: ${c.detail}`,
      onclick: (e) => { e.stopPropagation(); healthPopover(c); } }, el("i"), el("span", { text: c.title }));
  });
  fill(strip, ...dots);
}

function healthPopover(c) {
  document.querySelectorAll(".popover").forEach((p) => p.remove());
  const pop = el("div", { class: "popover", testid: "health-popover" },
    el("div", { class: "row between" }, el("h2", { text: c.title }), el("span", { class: `chip ${c.status}`, text: SHORT[c.status] || c.status })),
    el("p", { text: c.detail }),
    el("div", { class: "row" }, fixButton(c, "btn small primary"),
      el("a", { href: "#/settings", class: "btn small ghost", text: "All checks", onclick: () => pop.remove() })));
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
  fill(bar, el("span", { class: "spinner" }), el("span", { text: `${t.label}…`, testid: "taskbar-label" }),
    el("button", { class: "btn small ghost", text: "Log", onclick: () => showLog(t) }),
    t.kind === "apply" ? el("a", { class: "btn small ghost", href: "#/apply", text: "View" }) : null,
    el("button", { class: "btn small danger", text: "Stop", testid: "taskbar-stop", onclick: () => stopTask(t) }));
}

async function stopTask(t, force) {
  try {
    await api.post("/api/stop", { task: t.id, force: !!force });
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
      await api.post("/api/answer", { task: t.id, id: q.id, reply: r });
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
const TITLES = { home: "Today", review: "Review", apply: "Apply", company: "Company sites", answers: "Answers",
  settings: "Settings", setup: "Setup", logs: "Past runs" };

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
            text: st.counts.review ? `Review ${plural(st.counts.review, "new job")}` : "Nothing new to review" }),
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

function todoList(items) {
  if (!items.length) return el("p", { class: "empty", text: "Nothing waiting on you." });
  return el("ul", { class: "list" }, ...items.map((t) => {
    if (t.kind === "site") return el("li", { testid: `todo-site-${t.row}` },
      el("div", { class: "what" }, el("b", { text: t.company }), " · apply on their own site too ", el("span", { class: "muted", text: `(row ${t.row})` })),
      el("div", { class: "row" }, linkOut(t.url, "Open job"), el("button", { class: "btn small", text: "Mark done",
        onclick: () => markRow("site", t) })));
    if (t.kind === "company") return el("li", { testid: `todo-company-${t.row}` },
      el("div", { class: "what" }, el("b", { text: t.company }), " · company-site application ", el("span", { class: "muted", text: t.host })),
      el("a", { class: "btn small", href: "#/company", text: "Open" }));
    return el("li", { testid: `todo-urgent-${t.id}` },
      el("div", { class: "what" }, el("span", { class: "chip warn", text: `closes ${t.closes}` }), " ", el("b", { text: t.title }), " · ", t.company,
        el("span", { class: "muted", text: ` · ${t.match}%` })),
      el("a", { class: "btn small", href: "#/review", text: "Review" }));
  }));
}

function markRow(kind, t) {
  const text = kind === "site" ? `Mark the ${t.company} company-site application as done?` :
    `Mark row ${t.row} (${t.company}) as Applied today? Only if you submitted it yourself.`;
  confirmBox(kind === "site" ? "Company site done?" : "You applied yourself?", text, "Yes, mark it", async () => {
    try { await api.post("/api/mark", { action: kind, row: t.row, url: t.url }); toast("Sheet updated."); pollState(); if (S.screen && S.screen.reload) S.screen.reload(); }
    catch (e) { fail(e); }
  });
}

// ---- Review (approve, or rate only)
screens.review = async (view) => {
  let mode = sessionStorage.getItem("reviewMode") || "approve";
  let jobs = [], i = 0, card = null, history = {};
  const body = el("div");
  const seg = el("div", { class: "seg", role: "group", "aria-label": "Mode" },
    el("button", { "aria-pressed": String(mode === "approve"), testid: "mode-approve", text: "Approve", onclick: () => setMode("approve") }),
    el("button", { "aria-pressed": String(mode === "rate"), testid: "mode-rate", text: "Rate only", onclick: () => setMode("rate") }));
  fill(view, el("div", { class: "row between" }, seg, el("span", { class: "small muted", id: "review-pos" })), body);

  function setMode(m) {
    mode = m;
    sessionStorage.setItem("reviewMode", m);
    seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.testid === `mode-${m}`)));
    load();
  }

  async function load() {
    fill(body, el("p", { class: "empty", text: "Loading the review list…" }));
    try { const r = await api.get(`/api/review?mode=${mode}`); jobs = r.jobs; i = 0; }
    catch (e) { fill(body, el("div", { class: "note fail", text: e.message })); return; }
    show();
  }

  async function show() {
    $("#review-pos").textContent = jobs.length ? `Job ${Math.min(i + 1, jobs.length)} of ${jobs.length}` : "";
    if (i >= jobs.length) {
      fill(body, el("div", { class: "card", testid: "review-empty" }, el("h2", { text: jobs.length ? "That's all of them" : "Nothing new to review" }),
        el("p", { class: "muted", text: mode === "approve" ? "Approved jobs are in your sheet. Apply to them next." :
          "Ratings teach the ranking your taste (it needs 5 yes and 5 no)." }),
        el("div", { class: "row" }, jobs.length ? el("button", { class: "btn", text: "Back one", onclick: () => { i = Math.max(0, i - 1); show(); } }) : null,
          el("a", { class: "btn primary", href: mode === "approve" ? "#/apply" : "#/home", text: mode === "approve" ? "Go to Apply" : "Done" }))));
      return;
    }
    const j = jobs[i];
    try { card = await api.get(`/api/job/${encodeURIComponent(j.id)}`); }
    catch (e) { fill(body, el("div", { class: "note fail", text: e.message })); return; }
    fill(body, jobCard(card, j, history[j.id]), decideBar(j));
    window.scrollTo(0, 0);
  }

  function decideBar(j) {
    const b = (text, key, decision, cls, testid) => el("button", { class: "btn " + (cls || ""), testid,
      onclick: () => decide(decision) }, text, el("kbd", { text: key }));
    return el("div", { class: "decide row" },
      mode === "approve" ? b("Approve", "Y", "approve", "primary", "btn-approve") : b("Would apply", "Y", "yes", "primary", "btn-yes"),
      b(mode === "approve" ? "Not for me" : "Wouldn't", "N", "no", "", "btn-no"),
      b("Skip", "S", "skip", "", "btn-skip"), b("Back", "U", "back", "ghost", "btn-back"),
      el("a", { class: "btn ghost", href: card.url, target: "_blank", rel: "noopener noreferrer", testid: "btn-open" }, "Open on NUworks", el("kbd", { text: "O" })));
  }

  async function decide(decision) {
    const j = jobs[i];
    if (!j) return;
    if (decision === "skip") { i++; return show(); }
    if (decision === "back") { i = Math.max(0, i - 1); return show(); }
    const before = history[j.id];
    try {
      if (before && before.row && decision !== "approve") {  // changed your mind about an approved job
        await api.post("/api/undo", { id: j.id, row: before.row, previous: before.previous });
        delete history[j.id];
      }
      if (before && before.row && decision === "approve") { i++; return show(); }  // already in the sheet
      const r = await api.post("/api/decide", { id: j.id, decision, mode });
      history[j.id] = { decision, row: r.row, previous: before ? before.previous : r.previous };
      j.rating = decision === "no" ? 0 : 1;
      if (decision === "approve") {
        const id = j.id, row = r.row, previous = history[id].previous;
        toast(`Approved: added to the sheet${row ? ` (row ${row})` : ""}.`, { action: "Undo", onaction: async () => {
          try { await api.post("/api/undo", { id, row, previous }); delete history[id]; toast("Undone: the row is back to Proposed."); pollState(); }
          catch (e) { fail(e); }
        } });
      }
      i++;
      show();
      pollState();
    } catch (e) { fail(e); }
  }

  const keys = (e) => {
    if (e.target.closest("input, select, textarea") || $("#modal-root").children.length || e.ctrlKey || e.metaKey || e.altKey) return;
    const k = e.key.toLowerCase();
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
  return { leave: () => { document.removeEventListener("keydown", keys); api.post("/api/review/done", {}).catch(() => {}); } };
};

function jobCard(c, j, mine) {
  const margin = c.match - c.threshold;
  const facts = [
    ["Match", el("span", {}, el("span", { class: "match " + (margin >= 15 ? "good" : "ok"), text: `${c.match}%` }),
      el("span", { class: "muted", text: ` · needs ${c.threshold}%` + (c.bonus ? ` · +${c.bonus} ${c.bonus_label} for ranking` : "") +
        (c.taste !== null && c.taste !== undefined ? ` · your taste ${Math.round(c.taste * 100)}%` : "") }))],
    ["Closes", el("span", { class: c.soon ? "chip warn" : "", text: c.closes_text })],
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
    el("div", { class: "row between" }, el("div", { class: "row" }, el("span", { class: "chip accent", text: c.category.replace("_", " ") }),
      c.tag ? el("span", { class: "chip", text: c.tag }) : null, j.soon ? el("span", { class: "chip warn", text: "closing soon" }) : null,
      mine ? el("span", { class: "chip ok", text: mine.decision === "approve" ? `approved · row ${mine.row}` : `you said ${mine.decision}` }) :
        j.rating === 1 ? el("span", { class: "chip ok", text: "rated yes" }) : j.rating === 0 ? el("span", { class: "chip", text: "rated no" }) : null)),
    el("h2", { text: c.title, testid: "job-title" }),
    el("div", { class: "meta", text: `${c.company} · ${c.location || "location not listed"}` }),
    el("dl", { class: "facts" }, ...facts.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", {}, v)])),
    c.why ? el("div", { class: "why" }, el("b", { text: "Why: " }), c.why) : null,
    el("div", { class: "desc" }, desc(c.description), c.qualifications.length ? [el("h3", { text: "Qualifications" }), desc(c.qualifications)] : null));
}

// ---- Apply
screens.apply = async (view) => {
  let data = null;
  const head = el("div"), runBox = el("div"), list = el("div"), past = el("div");
  fill(view, head, runBox, list, past);

  async function reload() {
    try { data = await api.get("/api/apply"); } catch (e) { fill(list, el("div", { class: "note fail", text: e.message })); return; }
    draw();
  }

  function running() { return S.task && S.task.kind === "apply" && S.task.state === "running"; }

  function draw() {
    if (!data) return;
    const w = data.week;
    fill(head, el("div", { class: "card" },
      el("div", { class: "row between" }, el("div", {},
        el("h2", { text: w.room ? `You can apply to ${plural(w.room, "more job")} this week` : "Weekly limit reached" }),
        el("p", { class: "small muted", text: `${w.applied} of ${w.max} ${w.label} · ${w.total} of ${w.max_total} in total` + (w.next ? ` · new week ${w.next}` : "") })),
      running() ? null : el("div", { class: "row" },
        el("label", { class: "small muted" }, "At most ", el("input", { type: "number", min: "1", id: "apply-n", testid: "apply-n", style: null, "aria-label": "At most this many", placeholder: "all" })),
        el("button", { class: "btn primary big", testid: "btn-start-apply", disabled: !!data.why_not || !!(S.task && S.task.state === "running"),
          text: "Start applying", onclick: startApply }))),
      data.why_not ? el("div", { class: "note warn", testid: "apply-why", text: data.why_not }) :
        el("p", { class: "small muted", text: "Approved in the sheet is your go-ahead. Each job is filled and submitted in a visible Firefox window, one at a time, 30–60 s apart. Every form is screenshotted before Submit. Questions without a saved answer come up here. Stop works like Ctrl+C." })));
    fill(list, el("div", { class: "card" }, el("h2", { text: `Approved (${data.rows.length})` }),
      data.rows.length ? el("ul", { class: "list", testid: "approved-list" }, ...data.rows.map((r) => el("li", { testid: `approved-${r.row}` },
        el("div", { class: "what" }, el("b", { text: r.company }), " · ", r.title, el("div", { class: "small muted", text: `Row ${r.row} · closes ${r.closes_text}` })),
        el("div", { class: "row" }, r.past ? el("span", { class: "chip fail", text: "deadline passed" }) : r.soon ? el("span", { class: "chip warn", text: "closing soon" }) : null,
          linkOut(r.url, "Open"))))) : el("p", { class: "empty", text: "Nothing approved yet. Approve jobs in Review." })));
    fill(past, data.history.length ? el("div", { class: "card" }, el("div", { class: "row between" }, el("h2", { text: "Recent runs here" }),
      el("a", { href: "#/logs", class: "btn small ghost", text: "All logs and screenshots" })),
      el("ul", { class: "list" }, ...data.history.map((t) => el("li", {}, el("div", { class: "what" }, el("b", { text: t.label }), " · ",
        el("span", { class: "muted", text: `${t.state} · ${ago(t.started)}` })))))) : "");
    drawRun();
  }

  function drawRun() {
    const t = S.task;
    if (!t || t.kind !== "apply") { fill(runBox, ); return; }
    const live = t.state === "running";
    const now = Date.now() / 1000;
    const waiting = live && t.wait_until && t.wait_until > now;
    fill(runBox, el("div", { class: "card", testid: "apply-run" },
      el("div", { class: "row between" }, el("div", { class: "row" }, live ? el("span", { class: "spinner" }) : null,
        el("h2", { text: live ? (waiting ? `Next job in ${Math.ceil(t.wait_until - now)} s` : "Applying…") : `Last run: ${t.state}` })),
        live ? el("div", { class: "row" }, el("button", { class: "btn danger", testid: "btn-stop", text: "Stop", onclick: () => stopTask(t) }),
          el("button", { class: "btn ghost small", text: "Force stop", title: "Only if Stop does nothing", onclick: () =>
            confirmBox("Force stop?", "Kills the run at once. If Submit was already clicked, that row stays Needs Human: check NUworks.", "Force stop", () => stopTask(t, true), { danger: true }) })) : null),
      el("div", { class: "runhead" },
        el("div", {}, live && t.row ? el("p", {}, el("span", { class: "muted", text: "Now: " }), el("b", { text: t.row.company }), " · ", t.row.title) : null,
          live ? (t.done_rows.length ? el("p", { class: "small muted", text: `${plural(t.done_rows.length, "job")} done in this run` }) : null) :
            el("p", { class: "small muted", text: `${plural(t.done_rows.length, "job")} handled. Applied rows are in your sheet; anything that needs you is under Company sites or Today.` })),
        t.screenshot ? el("div", {}, el("img", { class: "shot thumb", src: t.screenshot, alt: "Latest screenshot of the form", testid: "apply-shot",
          onclick: () => modal((box, close) => box.append(el("img", { class: "full", src: t.screenshot, alt: "Screenshot" }),
            el("div", { class: "foot" }, el("span"), el("button", { class: "btn primary", text: "Close", onclick: close }))), { label: "Screenshot" }) })) : null),
      el("pre", { class: "log", testid: "task-log", tabindex: "0", "aria-label": "Run log", text: S.taskLog.slice(-400).join("\n") || "Starting…" })));
    const pre = runBox.querySelector(".log");
    pre.scrollTop = pre.scrollHeight;
  }

  async function startApply() {
    const n = ($("#apply-n") || {}).value;
    const r = await action("apply", n ? { n } : {});
    if (r) draw();
  }

  await reload();
  return { task: () => { drawRun(); if (S.task && S.task.state !== "running") reload(); }, update: () => { if (!running()) draw(); }, reload };
};

// ---- Company sites
screens.company = async (view) => {
  async function reload() {
    let d;
    try { d = await api.get("/api/company"); } catch (e) { fill(view, el("div", { class: "note fail", text: e.message })); return; }
    fill(view, 
      el("div", { class: "card" }, el("h2", { text: "Ready for the assistant" }),
        el("p", { class: "small muted", text: "Jobs that send you to the company's own site. The assistant (Claude, in a terminal window) fills the application in a visible browser; it asks you before anything is submitted. You sign in, solve captchas and approve Submit." }),
        d.agent.length ? el("ul", { class: "list" }, ...d.agent.map((r) => el("li", { testid: `company-${r.row}` },
          el("div", { class: "what" }, el("b", { text: r.company }), " · ", r.title, el("div", { class: "small muted", text: `Row ${r.row} · ${r.host}` })),
          el("div", { class: "row" }, el("button", { class: "btn small primary", text: "Start assistant", testid: `assist-${r.row}`, onclick: () => action("assist", { row: r.row }) }),
            linkOut(r.target, "Open site"),
            el("button", { class: "btn small", text: "I applied myself", onclick: () => markRow("applied", r) }))))) :
          el("p", { class: "empty", text: "None waiting." })),
      d.site.length ? el("div", { class: "card" }, el("h2", { text: "Submitted on NUworks; the company site still wants you" }),
        el("ul", { class: "list" }, ...d.site.map((r) => el("li", { testid: `site-${r.row}` }, el("div", { class: "what" }, el("b", { text: r.company }), " · ", r.title,
          el("div", { class: "small muted", text: r.notes.slice(0, 160) })), el("div", { class: "row" }, linkOut(r.url, "Open job"),
          el("button", { class: "btn small", text: "Mark done", onclick: () => markRow("site", r) })))))) : null,
      d.retry.length ? el("div", { class: "card" }, el("h2", { text: "Company site done; the NUworks side did not go out" }),
        el("p", { class: "small muted", text: "Submits the same job on NUworks too, with the usual NUworks checks (a visible browser; questions come up here)." }),
        el("ul", { class: "list" }, ...d.retry.map((r) => el("li", { testid: `retry-${r.row}` }, el("div", { class: "what" }, el("b", { text: r.company }), " · ", r.title,
          el("div", { class: "small muted", text: r.notes.slice(-160) })), el("div", { class: "row" }, linkOut(r.url, "Open job"),
          el("button", { class: "btn small primary", text: "Submit on NUworks too", testid: `retry-btn-${r.row}`,
            onclick: () => confirmBox("Submit on NUworks too?", `Row ${r.row} (${r.company}) is Applied on the company site. This submits the same job on NUworks.`,
              "Submit on NUworks", () => action("nuworks_side", { row: r.row })) })))))) : null,
      d.other.length ? el("div", { class: "card" }, el("h2", { text: "Needs you on NUworks" }),
        el("ul", { class: "list" }, ...d.other.map((r) => el("li", {}, el("div", { class: "what" }, el("b", { text: r.company }), " · ", r.title,
          el("div", { class: "small muted", text: r.notes || r.why })), linkOut(r.url, "Open job"))))) : null);
  }
  await reload();
  return { reload };
};

// ---- Answers
screens.answers = async (view) => {
  let data, rows = [], filter = "";
  async function reload() {
    try { data = await api.get("/api/answers"); } catch (e) { fill(view, el("div", { class: "note fail", text: e.message })); return; }
    rows = data.entries.map((e) => ({ ...e, aliases: (e.aliases || []).join(", ") }));
    draw();
  }
  function draw() {
    const shown = rows.map((r, i) => [r, i]).filter(([r]) => !filter || (r.question + " " + r.answer + " " + r.aliases).toLowerCase().includes(filter));
    const tbody = el("tbody", {}, ...shown.map(([r, i]) => el("tr", { testid: `answer-row-${i}` },
      el("td", {}, el("input", { type: "text", value: r.question, "aria-label": "Question", oninput: (e) => { r.question = e.target.value; } })),
      el("td", {}, el("input", { type: "text", value: r.answer, "aria-label": "Answer", disabled: r.always_ask, placeholder: r.always_ask ? "asked every time" : "",
        oninput: (e) => { r.answer = e.target.value; } })),
      el("td", { class: "narrow" }, el("select", { "aria-label": "Field type", onchange: (e) => { r.field_type = e.target.value; } },
        ...[["", "any"], ["text", "text"], ["select", "dropdown"]].map(([v, t]) => el("option", { value: v, text: t, selected: r.field_type === v })))),
      el("td", { class: "narrow" }, el("label", { class: "row small" }, el("input", { type: "checkbox", checked: !!r.always_ask,
        onchange: (e) => { r.always_ask = e.target.checked; draw(); } }), "always ask")),
      el("td", {}, el("input", { type: "text", value: r.aliases, placeholder: "other wordings, comma-separated", "aria-label": "Aliases",
        oninput: (e) => { r.aliases = e.target.value; } })),
      el("td", { class: "narrow" }, el("button", { class: "btn small ghost", text: "Remove", "aria-label": `Remove ${r.question}`,
        onclick: () => { rows.splice(i, 1); draw(); } })))));
    fill(view, el("div", { class: "card" },
      el("p", { class: "small muted", text: "When a form asks a question, NUauto fills it only from here, and only on an exact match (capital letters and spaces at the ends don't matter). Always-ask entries (salary, work authorization…) are never filled: you're asked each time." }),
      data.locked ? el("div", { class: "note warn", text: "A run is using the answer bank right now. You can edit when it is over." }) : null,
      el("div", { class: "row between" }, el("input", { type: "text", placeholder: "Search", value: filter, "aria-label": "Search answers",
        oninput: (e) => { filter = e.target.value.toLowerCase(); draw(); $("input[aria-label='Search answers']").focus(); } }),
        el("div", { class: "row" }, el("button", { class: "btn", text: "Add a question", onclick: () => { rows.unshift({ question: "", answer: "", field_type: "", always_ask: false, aliases: "" }); filter = ""; draw(); } }),
          el("button", { class: "btn primary", text: "Save", testid: "answers-save", disabled: data.locked, onclick: save }))),
      el("table", { class: "grid" }, el("thead", {}, el("tr", {}, ...["Question", "Answer", "Type", "Always ask", "Aliases"].map((h) => el("th", { text: h })),
        el("th", {}, el("span", { class: "sr-only", text: "Remove" })))), tbody)));
  }
  async function save() {
    const entries = rows.map((r) => ({ ...r, aliases: r.aliases.split(",").map((a) => a.trim()).filter(Boolean) }));
    try { data = await api.post("/api/answers", { entries, version: data.version }); rows = data.entries.map((e) => ({ ...e, aliases: (e.aliases || []).join(", ") })); draw(); toast("Answer bank saved."); }
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

// ---- Settings
screens.settings = async (view) => {
  let d;
  async function reload() {
    try { d = await api.get("/api/settings"); S.logsDir = d.paths.logs; } catch (e) { fill(view, el("div", { class: "note fail", text: e.message })); return; }
    draw();
  }
  function draw() {
    const s = d.settings;
    const label = el("input", { type: "text", value: d.resume_label, "aria-label": "Resume label", testid: "set-resume-label" });
    const week = el("input", { type: "date", value: s.week_start || "", "aria-label": "Week start", testid: "set-week-start" });
    fill(view, 
      el("div", { class: "card", testid: "all-checks" }, el("div", { class: "row between" }, el("h2", { text: "Health" }),
        el("button", { class: "btn small", text: "Run all checks now", testid: "btn-run-checks", onclick: async () => {
          await api.post("/api/health/run", { groups: ["quick", "sheet", "claude", "firefox", "discord", "homelab", "nuworks"] }).catch(fail);
          toast("Checking…"); setTimeout(pollHealth, 1500); setTimeout(pollHealth, 6000); } })),
        el("ul", { class: "list" }, ...S.checks.map((c) => el("li", { testid: `check-${c.id}` }, el("div", { class: "what" },
          el("span", { class: `chip ${c.status}`, text: SHORT[c.status] || c.status }), " ", el("b", { text: c.title }), " ",
          el("span", { class: "muted", text: c.detail }), el("div", { class: "small muted", text: `checked ${ago(c.at)}` })), fixButton(c))))),
      el("div", { class: "grid2" },
        el("div", { class: "card stack" }, el("h2", { text: "Logins" }),
          el("div", { class: "row between" }, el("span", { text: "Google" }), el("button", { class: "btn small", text: d.google_login ? "Log in again" : "Log in", onclick: () => action("login_google") })),
          el("div", { class: "row between" }, el("span", { text: "NUworks" }), el("div", { class: "row" },
            el("button", { class: "btn small", text: "Check now", onclick: () => { action("check_nuworks"); toast("Checking NUworks in the background…"); } }),
            el("button", { class: "btn small", text: "Log in", onclick: () => action("login_nuworks") }))),
          el("div", { class: "row between" }, el("span", { text: "Claude Code" }), el("button", { class: "btn small", text: "Log in", onclick: () => action("login_claude") })),
          el("p", { class: "small muted", text: "Google logins last 7 days (Testing-mode apps); NUauto warns you a day before." })),
        el("div", { class: "card stack" }, el("h2", { text: "Applying" }),
          el("label", { class: "field" }, "NUworks resume label", el("span", { class: "muted small", text: "Exactly as in the Apply popup's Resume dropdown." }), label),
          el("label", { class: "field" }, "Weekly limit counts from", el("span", { class: "muted small", text: "Fixed 7-day weeks from this day. Empty: any rolling 7 days." }), week),
          el("div", {}, el("button", { class: "btn primary", text: "Save", testid: "settings-save", onclick: async () => {
            try { d = await api.post("/api/settings", { resume_label: label.value, week_start: week.value }); toast("Saved."); pollState(); draw(); } catch (e) { fail(e); } } })))),
      el("div", { class: "card stack" }, el("h2", { text: "Setup" }),
        el("p", { class: "small muted", text: "Google client, sheet, resume, NUworks, preferences, notifications: each step checked." }),
        el("div", { class: "row" }, el("a", { class: "btn", href: "#/setup", text: "Open setup" }),
          el("button", { class: "btn", text: "Run a self-test", testid: "btn-selftest", title: "Demo mode end to end in a hidden browser: nothing real is touched",
            onclick: () => action("selftest") }),
          el("a", { class: "btn ghost", href: "#/logs", text: "Logs and screenshots" }))),
      el("div", { class: "card stack" }, el("h2", { text: "About" }),
        el("p", { class: "small muted" }, `Version ${S.state ? S.state.version : ""} · your files: `, el("code", { text: d.paths.state }),
          d.tools.claude ? [" · claude: ", el("code", { text: d.tools.claude })] : " · claude not found"),
        el("div", {}, el("button", { class: "btn danger", text: "Quit NUauto", testid: "btn-quit", onclick: () => confirmBox("Quit NUauto?",
          "Closes this window's server. A running job is stopped first (like Ctrl+C).", "Quit", async () => { await api.post("/api/quit", {}).catch(() => {}); gone("NUauto has quit. Start it again from your apps menu, or run nuauto gui."); }, { danger: true }) }))));
  }
  await reload();
  return { health: draw };
};

// ---- Setup (filled in by the wizard code in setup.js)
screens.setup = async (view) => {
  if (window.NUautoSetup) return window.NUautoSetup(view);
  fill(view, el("p", { class: "empty", text: "Setup is not available in this build." }));
  return {};
};

// ------------------------------------------------------------------ router

async function route() {
  const name = (location.hash.replace(/^#\/?/, "").split("?")[0]) || "home";
  const key = screens[name] ? name : "home";
  if (S.screen && S.screen.leave) S.screen.leave();
  S.screen = null;
  S.route = key;
  document.querySelectorAll(".side nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === key));
  $("#page-title").textContent = TITLES[key];
  document.title = `${TITLES[key]} · NUauto`;
  const view = $("#view");
  fill(view, );
  try { S.screen = (await screens[key](view)) || {}; } catch (e) { fail(e); S.screen = {}; }
  renderTaskbar();
  pollState();  // fresh counts for the new screen, not the last poll's
  view.focus({ preventScroll: true });
}

window.addEventListener("hashchange", route);
window.NUauto = { el, api, action, toast, fail, modal, confirmBox, S, pollState, pollHealth, linkOut, plural, ago };

(async function start() {
  await pollState();
  await pollHealth();
  try { S.logsDir = (await api.get("/api/settings")).paths.logs; } catch (e) { /* settings screen will say */ }
  if (S.state && S.state.setup_needed && !location.hash) location.hash = "#/setup";
  await route();
  setInterval(pollState, 3000);
  setInterval(pollHealth, 8000);
  pollTask();
})();
