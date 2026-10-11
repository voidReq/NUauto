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
function ordinal(n) { const t = n % 100, s = ["th", "st", "nd", "rd"][(t - 20) % 10] || ["th", "st", "nd", "rd"][t] || "th"; return n + s; }

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
  // the sheet was read again (a run finished, a row was applied, another window changed it): lists show it at once
  const sheetChanged = S.rowsAt !== undefined && S.state.rows_at !== S.rowsAt;
  S.rowsAt = S.state.rows_at;
  // an assistant started or ended (in its own terminal window): the Company sites lists say which rows have one
  const assistantsChanged = S.assistants !== undefined && S.state.assistants !== S.assistants;
  S.assistants = S.state.assistants;
  if ((sheetChanged || assistantsChanged) && S.screen && S.screen.refresh) S.screen.refresh();
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
    el("b", { text: "Demo mode. " }), "Nothing here is real: a fake sheet, fake NUworks pages, fake Claude. Nothing is sent anywhere.") :
    st.view ? el("div", { class: "demo-banner", testid: "view-banner" },
      el("b", { text: "View only. " }), "This is your real data, read-only: nothing can be changed or started, and saved answers and run screenshots are hidden.") : "");
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
  const pop = strip.querySelector(".popover");  // an open chip popover stays open (and updates itself)
  fill(strip, ...dots);
  if (pop) strip.append(pop);
}

// The check groups (gui.App.GROUPS) a status chip's Check now runs again
const RECHECK = { google: ["quick", "sheet"], sheet: ["sheet"], nuworks: ["quick", "nuworks"], claude: ["claude"], homelab: ["homelab"] };

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
        el("a", { href: "#/settings", class: "btn small ghost", text: "All checks", onclick: () => pop.remove() })));
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
const TITLES = { home: "Today", review: "Review", apply: "Apply", sheet: "Sheet", insights: "Insights", answers: "Answers",
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

function todoList(items) {
  if (!items.length) return el("p", { class: "empty", text: "Nothing waiting on you." });
  return el("ul", { class: "list" }, ...items.map((t) => {
    if (t.kind === "site") return el("li", { testid: `todo-site-${t.row}` },
      el("div", { class: "what" }, el("b", { text: t.company }), " · apply on their own site too ", el("span", { class: "muted", text: `(row ${t.row})` })),
      el("div", { class: "row" }, dueChip(t), linkOut(t.url, "Open job"), el("button", { class: "btn small", text: "Mark done",
        onclick: () => markRow("site", t) })));
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
    try { await api.post("/api/mark", { action: kind, row: t.row, url: t.url, tab }); toast("Sheet updated."); pollState(); if (S.screen && S.screen.reload) S.screen.reload(); }
    catch (e) { fail(e); }
  });
}

// ---- Review (approve, or rate only). Two sources when internships are on (state.internships, intern.py): NUworks co-ops,
// and the term's internships from Simplify's list, whose Approve adds a row to the sheet's Other jobs tab.
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
  const search = el("input", { type: "search", class: "grow", testid: "review-search", value: q, placeholder: 'Search: words or "a phrase"',
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

  async function load() {
    const mine = ++loadSeq;  // only the newest load may show its list (an older, slower answer is dropped)
    jobs = []; i = 0; card = null; shown++;  // while it loads, no key decides on the old list (and no card of it comes back)
    fill(body, el("p", { class: "empty", text: "Loading the review list…" }));
    await queue;  // decisions still being written go first: the server decides on its last list, which this one replaces
    const params = new URLSearchParams({ mode, q, category, source });
    try {
      const r = await api.get(`/api/review?${params}`);
      if (mine !== loadSeq || !body.isConnected) return;
      jobs = r.jobs; total = r.total; i = 0; listed = 0; cards.clear(); drawCategories(r.categories);
      if (!q && resumeId) { i = Math.max(0, jobs.findIndex((x) => x.id === resumeId)); resumeId = null; }
    } catch (e) { if (mine === loadSeq && body.isConnected) fill(body, el("div", { class: "note fail", text: e.message })); return; }
    show();
  }

  async function show() {
    const filtered = !!(q || category);
    if (listMode()) return showList(filtered);
    $("#review-pos").textContent = jobs.length ? `Job ${Math.min(i + 1, jobs.length)} of ${jobs.length}${filtered ? ` (${total} without the search)` : ""} · j/k scroll` : "";
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
          toSites ? "Approved internships are in the Other jobs tab. Apply to them in Apply > Company sites." :
          "Approved jobs are in your sheet. Apply to them next." }),
        el("div", { class: "row" }, jobs.length ? el("button", { class: "btn", text: "Back one", onclick: () => { i = Math.max(0, i - 1); show(); } }) : null,
          el("a", { class: "btn primary", href: toSites ? "#/company" : mode === "approve" ? "#/apply" : "#/home",
            text: toSites ? "Go to Company sites" : mode === "approve" ? "Go to Apply" : "Done" }))));
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
  function showList(filtered) {
    shown++;
    $("#review-pos").textContent = jobs.length ? `${jobs.length} ${jobs.length === 1 ? "match" : "matches"} (${total} without the search) · j/k scroll` : "";
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
    next();
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

// ---- Apply: three tabs. NUworks (the Approved rows Start works through), Company sites (the assistant, Other jobs,
// sites still owed, the NUworks side to send again), and rows only you can finish on NUworks.
screens.apply = async (view) => {
  let data = null;
  let sites = null, others = null;
  const TABS = [["nuworks", "NUworks"], ["company", "Company sites"], ["manual", "By hand on NUworks"]];
  let tab = sessionStorage.getItem("applyTab");
  if (!TABS.some(([k]) => k === tab)) tab = "nuworks";
  if (S.focus === "company") { S.focus = null; tab = "company"; }  // Today's buttons and old #/company, #/other links
  const SORT_OPTS = { nuworks: ["default", "score", "match", "closes", "pay"], company: ["closes", "sheet", "score", "match", "pay"],
    manual: ["closes", "sheet", "score", "match", "pay"] };
  const sortOf = (k) => { const v = sessionStorage.getItem("applySort:" + k); return SORT_OPTS[k].includes(v) ? v : SORT_OPTS[k][0]; };
  let kind = sessionStorage.getItem("applyKind") || "";  // one kind of work, or "" = all (shared by the three tabs)
  const shown = (rows) => sortRows(kind ? rows.filter((r) => kindOf(r) === kind) : rows, sortOf(tab));
  let drawnRunning = null;
  // The "At most" box is made once and moved into each redraw, so what you typed (and the focus) is never lost.
  const atMost = el("input", { type: "number", min: "1", id: "apply-n", testid: "apply-n", "aria-label": "At most this many", placeholder: "all" });
  const head = el("div"), runBox = el("div"), tabs = el("div", { class: "tabs", role: "tablist", "aria-label": "Apply" }),
    pane = el("div", { role: "tabpanel", testid: "apply-pane" });
  fill(view, head, runBox, tabs, pane);

  async function reload() {
    const [a, c, o] = await Promise.allSettled([api.get("/api/apply?order=" + sortOf("nuworks")), api.get("/api/company"), api.get("/api/other")]);
    sites = c.status === "fulfilled" ? c.value : { error: c.reason.message };
    others = o.status === "fulfilled" ? o.value : { error: o.reason.message };
    if (a.status === "rejected") { fill(pane, el("div", { class: "note fail", text: a.reason.message })); drawTabs(); return; }
    data = a.value;
    draw();
  }

  function running() { return S.task && S.task.kind === "apply" && S.task.state === "running"; }

  function counts() {
    const ok = sites && !sites.error;
    return { nuworks: data ? data.rows.length : 0, company: (ok ? sites.agent.length + sites.site.length + sites.retry.length : 0) + (others && others.ready ? others.ready.length : 0), manual: ok ? sites.other.length : 0 };
  }

  function drawTabs() {
    const n = counts();
    fill(tabs, ...TABS.map(([k, label]) => el("button", { role: "tab", testid: `tab-${k}`, "aria-selected": String(tab === k), "aria-controls": "apply-pane",
      onclick: () => { tab = k; sessionStorage.setItem("applyTab", k); drawTabs(); drawPane(); } },
      label, el("span", { class: "count", text: String(n[k]) }))));
  }

  // Sort by and Kind of work for this tab's rows (counts: this tab's NUworks rows of each kind)
  function toolbar(rows) {
    const how = sortOf(tab);
    const sel = el("select", { testid: "apply-sort", "aria-label": tab === "nuworks" ? "Apply in this order" : "Sort the list by",
      title: tab === "nuworks" ? "Start (and At most) goes down this list in this order." : null,
      onchange: (e) => { sessionStorage.setItem("applySort:" + tab, e.target.value); if (tab === "nuworks") reload(); else drawPane(); } },
      ...SORT_OPTS[tab].map((k) => el("option", { value: k, text: SORTS[k][0], selected: k === how })));
    const n = {};
    rows.forEach((r) => { n[kindOf(r)] = (n[kindOf(r)] || 0) + 1; });
    const keys = Object.keys(n).sort((a, b) => n[b] - n[a] || a.localeCompare(b));
    if (kind && !n[kind]) keys.push(kind);
    const cat = el("select", { testid: "apply-kind", "aria-label": "Kind of work",
      onchange: (e) => { kind = e.target.value; sessionStorage.setItem("applyKind", kind); drawPane(); } },
      el("option", { value: "", text: `All kinds of work (${rows.length})` }),
      ...keys.map((k) => el("option", { value: k, text: `${kindLabel(k, rows)} (${n[k] || 0})`, selected: k === kind })));
    return el("div", { class: "row end" }, el("label", { class: "small muted" }, "Show ", cat),
      el("label", { class: "small muted" }, tab === "nuworks" ? "Apply in this order: " : "Sort the list by ", sel));
  }

  function drawPane() {
    if (!data && tab === "nuworks") return;
    if (tab === "nuworks") return fill(pane, toolbar(data.rows), nuworksCard(), pastCard());
    if (!sites) return fill(pane, "");
    if (sites.error) return fill(pane, el("div", { class: "note fail", text: sites.error }));
    const lists = tab === "company" ? ["agent", "site", "retry"] : ["other"];
    const sorted = { ...sites };
    lists.forEach((k) => { sorted[k] = shown(sites[k]); });
    const all = lists.flatMap((k) => sites[k]);
    const hidden = all.length - lists.reduce((n, k) => n + sorted[k].length, 0);
    const cards = tab === "company" ? companyCards(sorted).concat([otherCard(others)]) : manualCards(sorted);
    const note = hidden ? el("p", { class: "small muted", testid: "apply-hidden",
      text: `${plural(hidden, "row")} of other kinds of work not shown.` }) : null;
    fill(pane, ...(cards.some(Boolean) ? [all.length ? toolbar(all) : null, note, ...cards] : [all.length ? toolbar(all) : null, note,
      el("div", { class: "card" }, el("p", { class: "empty", text: hidden ? "Nothing of this kind of work here." : "Nothing that only you can finish on NUworks." }))]));
  }

  function nuworksCard() {
    // already in the order Start goes (the server sorted them); place: where Start takes it
    const rows = data.rows.map((r, k) => ({ ...r, place: k + 1 })).filter((r) => !kind || kindOf(r) === kind);
    const total = data.rows.length;
    return el("div", { class: "card" }, el("div", { class: "row between" },
        el("h2", { text: rows.length === total ? `Approved (${total})` : `Approved: ${rows.length} of ${total} shown` }),
        rows.length !== total ? el("span", { class: "small muted", testid: "apply-filter-note", text: `Start still goes through all ${total}, in this order.` }) : null),
      rows.length ? el("ul", { class: "list", testid: "approved-list" }, ...rows.map((r) => el("li", { testid: `approved-${r.row}` },
        el("div", { class: "what" }, el("span", { class: "muted", text: `${r.place}. `, title: `Start applies to this one ${ordinal(r.place)}` }), el("b", { text: r.company }), " · ", r.title,
          jobFacts(r)),
        el("div", { class: "row" }, r.company_site ? el("span", { class: "chip", text: "may also want the company's site", title: r.company_site }) : null,
          dueChip(r), linkOut(r.url, "Open"),
          el("button", { class: "btn small", testid: `btn-apply-row-${r.row}`, disabled: !!data.why_not || !!(S.task && S.task.state === "running"),
            text: "Apply", title: "Apply to just this job now", onclick: () => applyRow(r) }))))) :
        el("p", { class: "empty", text: total ? "None of this kind of work." : "Nothing approved yet. Approve jobs in Review." }));
  }

  function pastCard() {
    return data.history.length ? el("div", { class: "card" }, el("div", { class: "row between" }, el("h2", { text: "Recent runs here" }),
      el("a", { href: "#/logs", class: "btn small ghost", text: "All logs and screenshots" })),
      el("ul", { class: "list" }, ...data.history.map((t) => el("li", {}, el("div", { class: "what" }, el("b", { text: t.label }), " · ",
        el("span", { class: "muted", text: `${t.state} · ${ago(t.started)}` })))))) : "";
  }

  function draw() {
    if (!data) return;
    drawnRunning = running();
    const w = data.week;
    fill(head, el("div", { class: "card" },
      el("div", { class: "row between" }, el("div", {},
        el("h2", { text: w.room ? `You can apply to ${plural(w.room, "more job")} this week` : "Weekly limit reached" }),
        el("p", { class: "small muted" }, `${w.applied} of ${w.max} ${w.label} · ${w.total} of ${w.max_total} in total` + (w.next ? ` · new week ${w.next}` : "") + " · ", el("a", { href: "#/settings", text: "change the limit" }))),
      drawnRunning ? null : el("div", { class: "row" },
        el("label", { class: "small muted" }, "At most ", atMost),
        el("button", { class: "btn primary big", testid: "btn-start-apply", disabled: !!data.why_not || !!(S.task && S.task.state === "running"),
          text: "Start applying", onclick: startApply }))),
      data.why_not ? el("div", { class: "note warn", testid: "apply-why", text: data.why_not }) :
        el("p", { class: "small muted", text: "Approved in the sheet is your go-ahead. Each job is filled and submitted in a visible Firefox window, one at a time, 30–60 s apart. Every form is screenshotted before Submit. Questions without a saved answer come up here. A job that sends you to the company's own site, or needs something only you can give (a cover letter, a transcript), stops (nothing submitted) and shows up under the Company sites and By hand tabs. Stop works like Ctrl+C." })));
    drawTabs();
    drawPane();
    drawRun();
  }

  function drawRun() {
    const t = S.task;
    if (!t || t.kind !== "apply") { fill(runBox, ); return; }
    const live = t.state === "running";
    const now = Date.now() / 1000;
    const waiting = live && t.wait_until && t.wait_until > now;
    fill(runBox, el("div", { class: "card", testid: "apply-run" },
      el("div", { class: "row between runtop" }, el("div", { class: "row" }, live ? el("span", { class: "spinner" }) : null,
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

  function applyRow(r) {
    confirmBox(`Apply to ${r.company}?`, `${r.title} is filled and submitted now in a visible Firefox window, the same way Start does it. Only this job.`,
      "Apply now", async () => { if (await action("apply", { row: r.row })) draw(); });
  }

  async function startApply() {
    const n = atMost.value;
    const r = await action("apply", n ? { n, order: sortOf("nuworks") } : { order: sortOf("nuworks") });
    if (r) draw();
  }

  await reload();
  if (S.focus === "company") S.focus = null;
  // refresh: the sheet changed (a run finished, a row was applied, something was done elsewhere): read the lists again
  return { task: () => { drawRun(); if (S.task && S.task.state !== "running") reload(); },
    update: () => { if (running() !== drawnRunning) draw(); }, refresh: reload, reload };
};

// ---- Company sites (a tab of the Apply screen): Needs Human rows for the assistant, company sites still owed, and
// the NUworks side to send again (only the sections that have rows show); then Other jobs (always shown).
// Assistant sessions run side by side (up to d.slots, each in its own terminal window and browser): a row with one
// running says so instead of offering another; with every slot in use, Start waits.
function assistantStart(d, r, tab, testid, args) {
  const running = (d.sessions || []).find((s) => s.tab === tab && s.row === r.row);
  if (running) return el("span", { class: "chip ok", testid: `${testid}-running`, text: `assistant running (slot ${running.slot})` });
  const full = (d.sessions || []).length >= (d.slots || 1);
  return el("button", { class: "btn small primary", text: "Start assistant", testid, disabled: full || (args.disabled || false),
    title: full ? `${d.slots} assistants are running (the most at once): finish one first` : "", onclick: (e) => {
      // a few seconds greyed out: a second click would start the same job twice (and each start reads the sheet)
      const btn = e.currentTarget;
      btn.disabled = true;
      btn.textContent = "Starting…";
      setTimeout(() => { if (btn.isConnected) { btn.disabled = false; btn.textContent = "Start assistant"; } }, 5000);
      action("assist", args.body);
    } });
}

function assistantsLine(d) {
  const n = (d.sessions || []).length;
  return n ? el("p", { class: "small", testid: "assistants-running", text: `${n} of ${d.slots} assistants running` +
    (n >= d.slots ? ": finish one (type /exit in its window) to start another." : "; you can start another.") }) : null;
}

function companyCards(d) {
  return [
    d.agent.length ? el("div", { class: "card", testid: "company-agent" }, el("h2", { text: "Company sites: ready for the assistant" }),
      el("p", { class: "small muted", text: "Jobs that send you to the company's own site. The assistant (Claude, in a terminal window) fills the application in a visible browser; it asks you before anything is submitted. You solve captchas, open email codes and approve Submit. Several can run at once, each in its own window." }),
      assistantsLine(d),
      el("ul", { class: "list" }, ...d.agent.map((r) => el("li", { testid: `company-${r.row}` },
        el("div", { class: "what" }, el("b", { text: r.company }), " · ", r.title, jobFacts(r), el("div", { class: "small muted", text: r.host })),
        el("div", { class: "row" }, dueChip(r), assistantStart(d, r, "jobs", `assist-${r.row}`, { body: { row: r.row } }),
          linkOut(r.target, "Open site"),
          el("button", { class: "btn small", text: "I applied myself", onclick: () => markRow("applied", r) })))))) : null,
    d.site.length ? el("div", { class: "card" }, el("h2", { text: "Submitted on NUworks; the company site still wants you" }),
      el("ul", { class: "list" }, ...d.site.map((r) => el("li", { testid: `site-${r.row}` }, el("div", { class: "what" }, el("b", { text: r.company }), " · ", r.title,
        jobFacts(r), el("div", { class: "small muted", text: r.notes.slice(0, 160) })), el("div", { class: "row" }, dueChip(r), linkOut(r.url, "Open job"),
        el("button", { class: "btn small", text: "Mark done", onclick: () => markRow("site", r) })))))) : null,
    d.retry.length ? el("div", { class: "card" }, el("h2", { text: "Company site done; the NUworks side did not go out" }),
      el("p", { class: "small muted", text: "Submits the same job on NUworks too, with the usual NUworks checks (a visible browser; questions come up here)." }),
      el("ul", { class: "list" }, ...d.retry.map((r) => el("li", { testid: `retry-${r.row}` }, el("div", { class: "what" }, el("b", { text: r.company }), " · ", r.title,
        jobFacts(r), el("div", { class: "small muted", text: r.notes.slice(-160) })), el("div", { class: "row" }, linkOut(r.url, "Open job"),
        el("button", { class: "btn small primary", text: "Submit on NUworks too", testid: `retry-btn-${r.row}`,
          onclick: () => confirmBox("Submit on NUworks too?", `${r.company} · ${r.title} is Applied on the company site. This submits the same job on NUworks.`,
            "Submit on NUworks", () => action("nuworks_side", { row: r.row })) }))))))  : null];
}

// ---- By hand on NUworks (a tab of the Apply screen): rows only you can finish there (a cover letter, a transcript...)
function manualCards(d) {
  return [d.other.length ? el("div", { class: "card", testid: "manual-nuworks" }, el("h2", { text: "Needs you on NUworks" }),
    el("ul", { class: "list" }, ...d.other.map((r) => el("li", {}, el("div", { class: "what" }, el("b", { text: r.company }), " · ", r.title,
      jobFacts(r), el("div", { class: "small muted", text: r.notes || r.why })), el("div", { class: "row" }, dueChip(r), linkOut(r.url, "Open job")))))) : null];
}

// ---- Other jobs (a card in Company sites): the Approved rows of the sheet's Other jobs tab, jobs that are not on
// NUworks (the same assistant; nothing is sent to NUworks). Their Applied and other rows are on the Sheet screen.
function otherCard(d) {
  if (!d) return null;
  const head = el("div", { class: "row between" }, el("h2", { text: "Other jobs (not on NUworks)" }),
    el("button", { class: "btn small", text: "Add a job", testid: "other-add",
      onclick: () => { sessionStorage.setItem("sheetAddTab", "other"); location.hash = "#/sheet"; } }));
  if (d.error) return el("div", { class: "card", testid: "other-jobs" }, head, el("div", { class: "note fail", text: d.error }));
  return el("div", { class: "card", testid: "other-jobs" }, head,
    el("p", { class: "small muted", text: `Approved rows of your sheet's "${d.tab}" tab; no weekly or total limit. The assistant answers from your Other jobs answers first, then your NUworks ones (not the NUworks-only ones: co-op dates and term). When you tell it the application went through, the row becomes Applied; nothing is sent to NUworks. Applied and other rows: the Sheet screen.` }),
    assistantsLine(d),
    d.ready.length ? el("ul", { class: "list" }, ...d.ready.map((r) => el("li", { testid: `other-${r.row}` },
      el("div", { class: "what" }, el("b", { text: r.company }), " · ", r.title,
        r.match != null ? jobFacts(r) : null,  // an internship from Simplify's list: its match % and pay
        r.posted_text ? el("div", { class: "small muted", text: r.posted_text }) : null,
        el("div", { class: "small muted", text: r.host || r.why })),
      el("div", { class: "row" },
        assistantStart(d, r, "other", `other-assist-${r.row}`, { disabled: !r.target, body: { row: r.row, tab: "other" } }),
        r.target ? linkOut(r.target, "Open site") : null,
        el("button", { class: "btn small", text: "I applied myself", testid: `other-mark-${r.row}`, onclick: () => markRow("applied", r, "other") }))))) :
      el("p", { class: "empty", text: d.exists ? `Nothing Approved in the ${d.tab} tab.` : "No jobs yet. Add one with Add a job." }));
}

// ---- Sheet: add a job to either tab by hand; then the two tabs (NUworks, Other jobs) as tabs, like Apply's: move rows
// between them; remove them
screens.sheet = async (view) => {
  const asked = sessionStorage.getItem("sheetAddTab");  // Apply's "Add a job" (Other jobs)
  const form = { tab: asked || "nuworks", url: "", company: "", title: "", status: "Approved" };
  sessionStorage.removeItem("sheetAddTab");
  let shown = asked || sessionStorage.getItem("sheetTab");  // the tab whose rows show
  if (shown !== "nuworks" && shown !== "other") shown = "nuworks";
  let d, info = null, seen = "";
  async function reload() {
    try { d = await api.get("/api/sheet"); } catch (e) { fill(view, el("div", { class: "note fail", text: e.message })); return; }
    draw();
  }
  async function look() {  // what your job data knows about this link (NUworks links: company, title, match)
    const url = form.url.trim();
    if (!url || url === seen) return;
    seen = url;
    try { info = await api.get("/api/sheet/lookup?url=" + encodeURIComponent(url)); } catch (e) { info = null; }
    if (info && info.tab) form.tab = info.tab;
    if (info && info.known) { form.company = form.company || info.company; form.title = form.title || info.title; }
    const focused = document.activeElement && document.activeElement.dataset.testid;  // keep the cursor where it went
    draw();
    if (focused) { const n = view.querySelector(`[data-testid="${focused}"]`); if (n) n.focus(); }
  }
  async function add(e) {
    e.preventDefault();
    try {
      const r = await api.post("/api/sheet", { action: "add", ...form });
      toast(`Added as row ${r.row} of the ${d.names[r.tab]} tab (${form.status}).`);
      Object.assign(form, { url: "", company: "", title: "" });
      shown = r.tab; sessionStorage.setItem("sheetTab", shown);
      info = null; seen = "";
      pollState();
      reload();
    } catch (err) { fail(err); }
  }
  function move(tab, r) {
    const to = tab === "nuworks" ? "other" : "nuworks";
    const isNu = (u) => /^https:\/\/northeastern-csm\.symplicity\.com\//.test(u || "");
    let link = (to === "nuworks") === isNu(r.url) ? r.url : "";  // the old link only if it fits the new tab
    modal((box, close) => box.append(
      el("h2", { text: `Move to ${d.names[to]}?` }),
      el("p", { class: "muted", text: `${r.company} · ${r.title} (${r.status || "no status"}). Its status, notes and date go with it; the row here is cleared.` +
        (to === "nuworks" ? " The NUworks tab needs the job's NUworks link (copy it from the job's page on NUworks)." : " Other jobs needs the company's own posting (not NUworks).") }),
      r.status === "Applied" ? el("div", { class: "note warn", text: "It is Applied: in the NUworks tab it counts toward your weekly and total limits." }) : null,
      el("label", { class: "field" }, `Link in ${d.names[to]}`, el("input", { type: "url", value: link, testid: "move-url", required: true,
        placeholder: to === "nuworks" ? "https://northeastern-csm.symplicity.com/students/app/jobs/detail/…" : "https://", oninput: (e) => { link = e.target.value; } })),
      el("div", { class: "foot" }, el("span"), el("div", { class: "row" },
        el("button", { class: "btn", text: "Cancel", onclick: close }),
        el("button", { class: "btn primary", text: "Move", testid: "move-yes", onclick: async () => {
          try {
            const out = await api.post("/api/sheet", { action: "move", tab, row: r.row, url: r.url, new_url: link });
            close(); toast(`Moved to row ${out.row} of the ${d.names[out.tab]} tab.`); pollState(); reload();
          } catch (err) { fail(err); }
        } })))), { label: "Move a job", testid: "move-dialog" });
  }
  function remove(tab, r) {
    confirmBox("Remove this job?", `${r.company} · ${r.title} (${d.names[tab]} row ${r.row}). Its cells are cleared; the other rows keep their numbers.`,
      "Remove", async () => {
        try { await api.post("/api/sheet", { action: "remove", tab, row: r.row, url: r.url }); toast("Removed."); pollState(); reload(); }
        catch (err) { fail(err); }
      }, { danger: true });
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
    const count = (tab) => d.tabs[tab].rows.length;
    const tabs = el("div", { class: "tabs", role: "tablist", "aria-label": "Sheet tabs" },
      ...["nuworks", "other"].map((k) => el("button", { role: "tab", testid: `sheet-tab-${k}`, "aria-selected": String(shown === k),
        "aria-controls": "sheet-pane", onclick: () => { shown = k; sessionStorage.setItem("sheetTab", k); draw(); } },
        d.names[k], el("span", { class: "count", text: String(count(k)) }))));
    const tabCard = (tab) => {
      const t = d.tabs[tab];
      return el("div", { class: "card", testid: `sheet-${tab}` }, el("h2", { text: `${d.names[tab]} (${t.rows.length})` }),
        !t.exists ? el("p", { class: "empty", text: "Not made yet: adding the first job makes it." }) :
        !t.rows.length ? el("p", { class: "empty", text: "No jobs." }) :
        el("ul", { class: "list" }, ...t.rows.map((r) => el("li", { testid: `sheet-${tab}-${r.row}` },
          el("div", { class: "what" }, el("b", { text: r.company }), " · ", r.title,
            el("div", { class: "small muted", text: `Row ${r.row}${r.date ? " · " + r.date : ""}${r.notes ? " · " + r.notes.slice(0, 140) : ""}` })),
          el("div", { class: "row" }, el("span", { class: "chip", text: r.status || "no status" }), linkOut(r.url, "Open"),
            el("button", { class: "btn small", text: `Move to ${tab === "nuworks" ? "Other jobs" : "NUworks"}`, testid: `move-${tab}-${r.row}`,
              disabled: !!r.locked || d.busy, title: r.locked || (d.busy ? "A run is using the sheet" : null), onclick: () => move(tab, r) }),
            el("button", { class: "btn small", text: "Remove", testid: `remove-${tab}-${r.row}`,
              disabled: !!r.remove_locked || d.busy, title: r.remove_locked || (d.busy ? "A run is using the sheet" : null), onclick: () => remove(tab, r) }))))));
    };
    fill(view,
      el("div", { class: "card", testid: "add-form" }, el("h2", { text: "Add a job" }),
        el("form", { class: "stack", onsubmit: add },
          el("div", { class: "row" }, seg("tab", [["nuworks", "NUworks co-op"], ["other", "Other job (not on NUworks)"]], "Which tab"),
            seg("status", [["Approved", "Approved"], ["Proposed", "Proposed"]], "Status")),
          el("label", { class: "field" }, "Link to the posting", input("url", { type: "url", placeholder: "https://", required: true, testid: "add-url",
            onchange: look })),
          el("p", { class: "small muted", testid: "add-hint", text: hint }),
          el("div", { class: "grid2" },
            el("label", { class: "field" }, "Company", input("company", { type: "text", testid: "add-company" })),
            el("label", { class: "field" }, "Job title", input("title", { type: "text", testid: "add-title" }))),
          el("p", { class: "small muted", text: form.status === "Approved" ?
            (form.tab === "nuworks" ? "Approved NUworks jobs are submitted by Apply (your weekly limit applies)." : "Approved Other jobs are ready for the assistant (no limits there).") :
            "Proposed: it waits in the sheet (NUworks ones show first in Review) until you approve it." }),
          el("div", {}, el("button", { class: "btn primary", type: "submit", text: "Add", testid: "add-submit" })))),
      tabs,
      el("div", { role: "tabpanel", id: "sheet-pane", testid: "sheet-pane" },
        el("p", { class: "small muted", text: "Applied rows can't be removed, and NUworks ones can't be moved (they count toward your limits); an Applied Other job can move to NUworks. Removing clears the row's cells, so no other row changes number." }),
        tabCard(shown)));
  }
  await reload();
  return { reload };
};

// ---- Insights: pay, places and kinds of work in the pool / what you applied to; where the sheet stands
const SVGNS = "http://www.w3.org/2000/svg";
function svg(tag, attrs, ...kids) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) n.setAttribute(k, String(v));
  for (const kid of kids.flat()) if (kid) n.append(kid);
  return n;
}
// Status colors (state, not series): each always shown with its label and count in the legend.
const STATUS_COLOR = { Proposed: "var(--off)", Approved: "var(--accent)", Applied: "var(--ok)", "Needs Human": "var(--warn)", Failed: "var(--fail)" };
// Ring (and legend) order, chosen with the dataviz palette validator so no two neighbors are the hard-to-tell pairs
// (red/amber, green/amber next to each other); labels, counts and 2px gaps carry identity too.
const RING = ["Applied", "Needs Human", "Proposed", "Failed", "Approved"];
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

screens.insights = async (view) => {
  let d, which = "pool";
  try { d = await api.get("/api/insights"); } catch (e) { fill(view, el("div", { class: "note fail", text: e.message })); return {}; }
  if (d.statuses) d.statuses.sort((x, y) => RING.indexOf(x.label) - RING.indexOf(y.label));
  function draw() {
    const g = d[which], p = g.pay;
    const seg = el("div", { class: "seg", role: "group", "aria-label": "Which jobs" },
      ...[["pool", `Your pool (${d.pool.count})`], ["applied", `Applied (${d.applied.count})`]].map(([k, t]) =>
        el("button", { "aria-pressed": String(which === k), testid: `ins-${k}`, text: t, onclick: () => { which = k; draw(); } })));
    const status = d.statuses === null ? el("div", { class: "card" }, el("h2", { text: "Your sheet" }),
      el("p", { class: "empty", text: "Can't read the sheet right now; the pool numbers below still work." })) :
      el("div", { class: "card", testid: "ins-status" }, el("h2", { text: "Where your applications stand" }),
        d.rows ? el("div", { class: "donut-wrap" }, donut(d.statuses, d.rows),
          el("ul", { class: "legend" }, ...d.statuses.map((s) => el("li", {},
            el("span", { class: "swatch", "data-status": s.label }), el("span", { text: s.label }), el("b", { text: String(s.count) })))))
          : el("p", { class: "empty", text: "Nothing in your sheet yet." }));
    const body = !g.count ? [el("p", { class: "empty", testid: "ins-empty", text: which === "applied" ? "Nothing applied yet. Applied rows show up here." : "Your pool is empty. Check for new jobs on Today." })] : [
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
    fill(view, status, el("div", { class: "row between ins-head" }, el("h2", { text: which === "pool" ? "Jobs in your pool" : "Jobs you applied to" }), seg), ...body);
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
    rows = data.entries.map((e) => ({ ...e, aliases: (e.aliases || []).join(", ") }));
    draw();
  }
  function setBank(b) { bank = b; sessionStorage.setItem("answersBank", b); filter = ""; reload(); }
  function draw() {
    const shown = rows.map((r, i) => [r, i]).filter(([r]) => !filter || (r.question + " " + r.answer + " " + r.aliases).toLowerCase().includes(filter));
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
      el("td", {}, el("input", { type: "text", value: r.aliases, placeholder: "other wordings, comma-separated", "aria-label": "Aliases",
        oninput: (e) => { r.aliases = e.target.value; } })),
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
      el("div", { class: "row between" }, el("input", { type: "text", placeholder: "Search", value: filter, "aria-label": "Search answers",
        oninput: (e) => { filter = e.target.value.toLowerCase(); draw(); $("input[aria-label='Search answers']").focus(); } }),
        el("div", { class: "row" }, el("button", { class: "btn", text: "Add a question", onclick: () => { rows.unshift({ question: "", answer: "", field_type: "", always_ask: false, nuworks_only: false, aliases: "" }); filter = ""; draw(); } }),
          el("button", { class: "btn primary", text: "Save", testid: "answers-save", disabled: data.locked, onclick: save }))),
      el("div", { class: "table-scroll" }, el("table", { class: "grid" }, el("thead", {}, el("tr", {}, ...["Question", "Answer", "Type", "Always ask", ...(bank === "nuworks" ? ["NUworks only"] : []), "Aliases"].map((h) => el("th", { text: h })),
        el("th", {}, el("span", { class: "sr-only", text: "Remove" })))), tbody))));
  }
  async function save() {
    const entries = rows.map((r) => ({ ...r, aliases: r.aliases.split(",").map((a) => a.trim()).filter(Boolean) }));
    try { data = await api.post("/api/answers", { bank, entries, version: data.version }); rows = data.entries.map((e) => ({ ...e, aliases: (e.aliases || []).join(", ") })); draw(); toast("Answer bank saved."); }
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
    const cap = el("input", { type: "number", min: "1", max: String(d.max_per_week_ceiling), step: "1", value: String(d.max_per_week),
      "aria-label": "Applications per week", testid: "set-max-per-week" });
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
          el("label", { class: "field" }, "Applications per week", el("span", { class: "muted small", text: `The most NUauto will submit in one week (1 to ${d.max_per_week_ceiling}). It stops at this number; Approved jobs wait for the next week.` }), cap),
          el("label", { class: "field" }, "Weekly limit counts from", el("span", { class: "muted small", text: "Fixed 7-day weeks from this day. Empty: any rolling 7 days." }), week),
          el("div", {}, el("button", { class: "btn primary", text: "Save", testid: "settings-save", onclick: async () => {
            try { d = await api.post("/api/settings", { resume_label: label.value, week_start: week.value, max_per_week: cap.value }); toast("Saved."); pollState(); draw(); } catch (e) { fail(e); } } })))),
      assistantCard(),
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
  // The company-site assistant (nuauto assist): the email for the accounts it makes, how hard it thinks, the folders it
  // may read, and its logins (one per job site; the passwords never reach this page) as Bitwarden's import file.
  function assistantCard() {
    const s = d.settings;
    const email = el("input", { type: "email", value: s.accounts_email || "", placeholder: "you@example.com",
      "aria-label": "Account email", testid: "set-accounts-email" });
    const effort = el("select", { "aria-label": "Assistant effort", testid: "set-assist-effort" },
      ...d.efforts.map((e) => el("option", { value: e, text: e })));
    effort.value = d.efforts.includes(s.assist_effort) ? s.assist_effort : d.efforts[0];
    const folders = el("textarea", { rows: "3", "aria-label": "Folders the assistant may read", testid: "set-read-paths" });
    folders.value = (s.assist_read_paths || []).join("\n");
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
    return el("div", { class: "card stack", testid: "assistant-settings" }, el("h2", { text: "Company-site assistant" }),
      el("label", { class: "field" }, "Account email", el("span", { class: "muted small", text: "For the accounts it makes on job sites (Workday and others). Each site gets its own random password; the assistant never sees it." }), email),
      el("label", { class: "field" }, "Effort", el("span", { class: "muted small", text: "How hard it thinks (Claude Code's effort). Nothing is submitted without your review either way." }), effort),
      el("label", { class: "field" }, "Folders it may read", el("span", { class: "muted small", text: "Your projects and writeups, one per line: for longer answers. Keys, tokens, .env files and NUauto's own logins are never read." }), folders),
      el("div", { class: "row between" }, el("span", { class: "small muted", testid: "logins-count",
        text: L.sites ? `Logins it made: ${plural(L.sites, "site")}${L.new ? `, ${L.new} not exported yet` : ", all exported"}` : "No logins made yet." }), exportBtn),
      el("div", {}, el("button", { class: "btn primary", text: "Save", testid: "assistant-save", onclick: async () => {
        try {
          d = await api.post("/api/settings", { accounts_email: email.value, assist_effort: effort.value,
            assist_read_paths: folders.value.split("\n").map((x) => x.trim()).filter(Boolean) });
          toast("Saved."); draw();
        } catch (e) { fail(e); }
      } })));
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
  let name = (location.hash.replace(/^#\/?/, "").split("?")[0]) || "home";
  if (name === "company" || name === "other") {  // Company sites (Other jobs is in it) is a part of Apply now
    S.focus = "company";
    history.replaceState(null, "", "#/apply");
    name = "apply";
  }
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
window.NUauto = { el, fill, api, action, toast, fail, modal, confirmBox, S, pollState, pollHealth, linkOut, plural, ago };

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
