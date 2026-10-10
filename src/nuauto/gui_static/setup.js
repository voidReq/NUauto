// The setup wizard (the Setup screen). Each step checks itself on the server (onboard.py); this file only shows
// the steps and sends the buttons' actions. Same rules as app.js: text only via textContent.
"use strict";

(function () {
  const { el, fill, api, action, toast, fail, confirmBox, S, pollState, pollHealth, linkOut } = window.NUauto;
  let open = null;  // the step shown open (default: the first one not done)

  function chip(status) {
    return el("span", { class: "chip " + (status === "done" ? "ok" : status === "optional" ? "" : "warn"),
      text: status === "done" ? "Done" : status === "optional" ? "Optional" : "To do" });
  }

  async function post(body, ok) {
    try {
      const r = await api.post("/api/setup", body);
      if (ok) toast(ok);
      return r;
    } catch (e) { fail(e); return null; }
  }

  function startTask(kind) { return action(kind); }

  // ---- one body per step
  const BODY = {
    welcome(s, redraw) {
      const box = el("input", { type: "checkbox", id: "tos", testid: "setup-tos" });
      return [
        el("p", { text: "NUauto finds NUworks co-op postings that fit your resume. You approve each job; it then fills " +
          "and submits the NUworks application for you, one at a time, in a visible browser." }),
        el("ul", {}, el("li", { text: `At most ${s.data.week} applications a week and ${s.data.total} in total, enforced in code.` }),
          el("li", { text: "It never writes essays or cover letters, and never guesses an answer: unknown questions come to you." }),
          el("li", { text: "Jobs on a company's own site go to you (or to the assistant, which asks you before submitting)." })),
        el("p", { class: "small muted" }, "NUworks is Northeastern's job site, run by Symplicity. NUauto is not affiliated with either. Read NUworks' terms of use (linked at the bottom of ",
          linkOut("https://northeastern-csm.symplicity.com/", "NUworks"), ") before you use it."),
        el("label", { class: "row" }, box, "I read NUworks' terms of use, and I understand the limits above."),
        el("div", { class: "row" }, el("button", { class: "btn primary", text: "Continue", testid: "setup-ack",
          onclick: async () => { if (!box.checked) return toast("Tick the box first.", { error: true }); if (await post({ action: "ack" })) redraw(); } })),
      ];
    },

    tools(s) {
      const d = s.data;
      const line = (c, fallback) => el("li", {}, el("div", { class: "what" }, el("b", { text: c ? c.title : fallback }), " ",
        el("span", { class: "muted", text: c ? c.detail : "checking…" })), c && c.status !== "ok" && c.fix ? el("button", {
        class: "btn small primary", text: c.fix_label, testid: `setup-fix-${c.id}`, onclick: () => {
          if (c.fix === "install_claude") return window.NUauto.confirmBox("Install Claude Code?",
            "Runs Claude Code's official installer (claude.ai/install.sh) for your user. You need a Claude subscription or an API key.",
            "Install", () => startTask("install_claude"));
          startTask(c.fix);
        } }) : null);
      return [
        el("ul", { class: "list" }, line(d.firefox, "Browser"), line(d.claude, "Claude Code"),
          el("li", {}, el("div", { class: "what" }, el("b", { text: "For the company-site assistant (optional)" }), " ",
            el("span", { class: "muted", text: `Node.js: ${d.node ? "found" : "not found (nodejs.org)"} · Google Chrome: ${d.chrome ? "found" : "not found"}` })))),
        el("p", { class: "small muted", text: "Claude Code reads each posting and your resume to score the match. It needs a Claude subscription or an API key; logging in opens Claude's own page." }),
      ];
    },

    google_client(s, redraw) {
      const L = s.data.links;
      const file = el("input", { type: "file", accept: ".json,application/json", testid: "setup-client-file", hidden: true });
      const drop = el("div", { class: "drop", testid: "setup-client-drop" }, "Drop the downloaded file here, or ",
        el("button", { class: "btn small", text: "choose it", onclick: () => file.click() }));
      const send = async (f) => {
        if (!f) return;
        const text = await f.text();
        if (await post({ action: "client_upload", text }, "Google client saved.")) redraw();
      };
      file.addEventListener("change", () => send(file.files[0]));
      drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
      drop.addEventListener("dragleave", () => drop.classList.remove("over"));
      drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); send(e.dataTransfer.files[0]); });
      const step = (text, link, label) => el("li", {}, text, " ", link ? linkOut(link, label || "Open") : null);
      return [
        el("p", { text: "Google only lets apps open your sheet with a sign-in client you create. It is free and takes about five minutes; you do it once." }),
        el("ol", { class: "howto" },
          step("Create a Google Cloud project (any name, e.g. NUauto).", L.project),
          step("Turn on the Google Sheets API for it: press Enable.", L.sheets_api),
          step("Set up the sign-in screen: Get started, app name NUauto, your email; Audience: External.", L.consent),
          step("Add yourself as a test user: Audience, Test users, Add users, your Google address.", L.audience),
          step("Create the client: Application type Desktop app, Create, then Download JSON.", L.client)),
        s.data.downloads ? el("div", { class: "note accent row between" }, el("span", {}, "Found ", el("code", { text: s.data.downloads }), " in Downloads."),
          el("button", { class: "btn small primary", text: "Use it", testid: "setup-client-downloads",
            onclick: async () => { if (await post({ action: "client_downloads" }, "Google client saved.")) redraw(); } })) : null,
        drop, file,
        el("p", { class: "small muted", text: "The file is saved in NUauto's private folder (local/client_secret.json, readable only by you). Your app stays in Testing mode, so Google asks you to log in again every 7 days; NUauto reminds you the day before." }),
      ];
    },

    google_login(s) {
      return [
        el("p", { text: "Your browser opens Google's own login page. Pick the Google account that should hold your job sheet." }),
        el("p", { class: "small muted", text: "Google may say it hasn't verified this app: it is your own app from the step above. Choose Continue." }),
        el("button", { class: "btn primary", text: s.status === "done" ? "Log in again" : "Log in to Google", testid: "setup-google-login",
          onclick: () => startTask("login_google") }),
      ];
    },

    sheet(s, redraw) {
      const link = el("input", { type: "text", placeholder: "https://docs.google.com/spreadsheets/d/…", "aria-label": "Sheet address", testid: "setup-sheet-url" });
      return [
        s.status === "done" && s.data.url ? el("p", {}, "Your sheet: ", linkOut(s.data.url, "open it in Google Sheets")) : null,
        el("p", { text: "NUauto keeps every job you approve in a Google Sheet: one row per job with its status. You can edit it by hand too." }),
        el("div", { class: "row" }, el("button", { class: "btn primary", text: "Create a new sheet for me", testid: "setup-sheet-create",
          onclick: async () => { if (await post({ action: "sheet_create" }, "Sheet created and set up.")) redraw(); } })),
        el("p", { class: "small muted", text: "Or use a sheet you made (it must be empty, or one NUauto set up before): paste its address." }),
        el("div", { class: "row" }, link, el("button", { class: "btn", text: "Use this sheet", testid: "setup-sheet-link",
          onclick: async () => { if (await post({ action: "sheet_link", text: link.value }, "Sheet connected.")) redraw(); } })),
      ];
    },

    resume(s, redraw) {
      const path = el("input", { type: "text", value: s.data.path || "", placeholder: "/home/you/Documents/resume.pdf", "aria-label": "Resume file", testid: "setup-resume-path" });
      const use = async () => { if (await post({ action: "resume_set", path: path.value }, "Resume saved.")) redraw(); };
      return [
        s.data.preview ? el("div", { class: "note small", testid: "setup-resume-preview" }, el("b", { text: "What Claude reads: " }), s.data.preview + "…") : null,
        el("p", { text: "The PDF you use on NUworks. Claude compares each posting with it; keep it at this path and NUauto picks up changes." }),
        el("div", { class: "row" }, path,
          s.data.dialog ? el("button", { class: "btn", text: "Choose file…", testid: "setup-resume-pick", onclick: async () => {
            const r = await post({ action: "resume_pick" });
            if (r && r.path) { path.value = r.path; use(); }
          } }) : null,
          el("button", { class: "btn primary", text: "Use this file", testid: "setup-resume-use", onclick: use })),
        s.data.suggestions.length ? el("div", { class: "small" }, el("span", { class: "muted", text: "Found: " }),
          ...s.data.suggestions.map((p) => el("button", { class: "btn small ghost", text: p.split("/").pop(), title: p,
            onclick: () => { path.value = p; } }))) : null,
      ];
    },

    nuworks(s, redraw) {
      const d = s.data;
      const session = d.session;
      const typed = el("input", { type: "text", value: d.label || "", placeholder: "exactly as in the Apply popup", "aria-label": "Resume label", testid: "setup-label-typed" });
      const pick = d.labels && d.labels.length ? el("div", { class: "choices stack" }, ...d.labels.map((l) => el("label", { class: "row" },
        el("input", { type: "radio", name: "label", value: l, checked: l === d.label, onchange: () => { typed.value = l; } }), l))) : null;
      return [
        el("div", { class: "row between" }, el("span", {}, el("b", { text: "Login: " }),
          el("span", { class: "muted", text: session ? session.detail : "checking…" })),
          el("div", { class: "row" }, el("button", { class: "btn small", text: "Check again", onclick: () => { action("check_nuworks"); toast("Checking NUworks…"); } }),
            el("button", { class: "btn small primary", text: "Log in to NUworks", testid: "setup-nuworks-login", onclick: () => startTask("login_nuworks") }))),
        el("p", { class: "small muted", text: "A Firefox window opens on NUworks: log in with your Northeastern account (and Duo), then close that window. NUauto never sees your password; after this it stays logged in with a one-click re-login." }),
        el("hr", { class: "sep" }),
        el("p", {}, el("b", { text: "Which resume NUauto picks: " }), "the exact name of your resume in NUworks' Apply popup."),
        el("div", { class: "row" }, el("button", { class: "btn", text: "Read my resumes from NUworks", testid: "setup-labels-read",
          onclick: () => post({ action: "labels_read" }).then((r) => { if (r && r.task) { S.task = r.task; S.taskLog = []; S.taskAfter = 0; window.NUauto.pollState(); } }) }),
          el("span", { class: "small muted", text: "opens one job's Apply popup in a hidden browser and closes it; nothing is filled or sent" })),
        d.labels_error ? el("div", { class: "note warn", text: d.labels_error }) : null,
        pick,
        el("div", { class: "row" }, typed, el("button", { class: "btn primary", text: "Use this resume", testid: "setup-label-use",
          onclick: async () => { if (await post({ action: "label_set", label: typed.value }, "Resume label saved.")) redraw(); } })),
      ];
    },

    preferences(s, redraw) {
      // p: the form's values. Picking what you study fills in that field's starting values (onboard: fields.preset)
      // and draws the step again from p; nothing is saved until Save.
      const p = { ...s.data.prefs, field: s.data.prefs.field || "engineering" };
      const FIELDS = s.data.fields;
      const box = el("div", { class: "stack" });
      let f = {}, year, kinds = [], tagRows = [];
      const slug = (t) => t.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 30);
      const keyOf = (k) => k.key || slug(k.label.value);

      function collect() {  // the form, as check_prefs takes it
        const live = kinds.filter((k) => k.label.value.trim() || k.desc.value.trim());
        return { field: p.field, term: f.term.value, term_id: f.term_id.value, class_year: year.value, threshold: f.threshold.value,
          threshold_above: f.threshold_above.value, grad_year: f.grad_year.value, major_words: f.major_words.value,
          home_state: f.home_state.value, home_label: f.home_label.value, home_bonus: f.home_bonus.value,
          student: f.student.value, keep_roles: f.keep_roles.value, drop_roles: f.drop_roles.value,
          categories: live.map((k) => ({ key: keyOf(k), label: k.label.value, description: k.desc.value })),
          category_bonus: Object.fromEntries(live.filter((k) => k.bonus.value !== "").map((k) => [keyOf(k), k.bonus.value])),
          category_threshold: Object.fromEntries(live.filter((k) => k.bar.value !== "").map((k) => [keyOf(k), k.bar.value])),
          rank_last: live.filter((k) => k.last.checked).map(keyOf),
          tags: tagRows.map((r) => ({ name: r.name.value, bonus: r.bonus.value, phrases: r.phrases.value })) };
      }

      function pick(key) {
        if (key === p.field) return;
        confirmBox(`Use the ${FIELDS[key].label} starting values?`, "This fills in: about you, the roles that fit and that don't, " +
          "the words in your major's name, and the kinds of work jobs are sorted into (with their bonuses). Your term, year, " +
          "match bars, home state and tags stay. You can change all of it before you save.", "Fill them in", () => {
            const now = collect();  // what you typed stays (the tags' phrases back to lists, as draw takes them)
            now.tags = now.tags.map((t) => ({ ...t, phrases: t.phrases.split(",").map((x) => x.trim()).filter(Boolean) }));
            Object.assign(p, now, FIELDS[key], { field: key });
            delete p.label;
            draw();
          });
      }

      function draw() {
        f = {}; kinds = []; tagRows = [];
        const input = (key, attrs = {}) => (f[key] = el("input", { type: "text", value: p[key] ?? "", "aria-label": key, testid: `pref-${key}`, ...attrs }));
        const area = (key) => (f[key] = el("textarea", { rows: "4", "aria-label": key, testid: `pref-${key}` }, String(p[key] || "")));
        const terms = s.data.terms || [];
        const termSel = terms.length ? el("select", { testid: "pref-term-select", "aria-label": "Term", onchange: (e) => {
          const t = terms.find((x) => x.id === e.target.value); if (t) { f.term.value = t.label; f.term_id.value = t.id; } } },
          el("option", { value: "", text: "Choose your co-op term…" }), ...terms.map((t) => el("option", { value: t.id, text: t.label, selected: t.id === p.term_id }))) : null;
        year = el("select", { testid: "pref-class_year", "aria-label": "Your year" },
          ...s.data.years.map((y) => el("option", { value: y, text: y[0].toUpperCase() + y.slice(1), selected: y === p.class_year })));
        // the kinds of work: name, what counts (Claude reads it), bonus, lower bar, always last
        const kindBody = el("tbody", { testid: "pref-kinds" });
        const addKind = (c = { key: "", label: "", description: "" }) => {
          const other = c.key === "other";
          const k = { key: c.key, label: el("input", { type: "text", value: c.label, placeholder: "name", "aria-label": "Kind of work", disabled: other }),
            desc: el("textarea", { rows: "2", "aria-label": `What counts as ${c.label || "this kind"}`, placeholder: "what counts: the roles, in a line" }, c.description),
            bonus: el("input", { type: "number", value: p.category_bonus[c.key] ?? "", placeholder: "0", "aria-label": `${c.label} bonus` }),
            bar: el("input", { type: "number", value: p.category_threshold[c.key] ?? "", placeholder: "usual", "aria-label": `${c.label} lower bar` }),
            last: el("input", { type: "checkbox", checked: (p.rank_last || []).includes(c.key), "aria-label": `${c.label} rank last` }) };
          const tr = el("tr", {}, el("td", { class: "tagname" }, k.label), el("td", { class: "wide" }, k.desc), el("td", {}, k.bonus), el("td", {}, k.bar),
            el("td", { class: "narrow" }, k.last), el("td", { class: "narrow" }, other ? null :
              el("button", { class: "btn small", text: "Remove", onclick: () => { kinds.splice(kinds.indexOf(k), 1); tr.remove(); } })));
          k.tr = tr;
          const last = kinds.find((x) => x.key === "other");  // Other stays at the end
          if (last && !other) { kinds.splice(kinds.indexOf(last), 0, k); kindBody.insertBefore(tr, last.tr); } else { kinds.push(k); kindBody.append(tr); }
        };
        (p.categories || []).forEach((c) => addKind(c));
        if (!(p.categories || []).some((c) => c.key === "other")) addKind({ key: "other", label: "Other", description: "anything else." });
        const tagBody = el("tbody", { testid: "pref-tags" });
        const addTag = (t = { name: "", bonus: "", phrases: [] }) => {
          const row = { name: el("input", { type: "text", value: t.name, placeholder: "name", "aria-label": "Tag name" }),
            bonus: el("input", { type: "number", value: t.bonus, placeholder: "0", "aria-label": "Tag bonus" }),
            phrases: el("textarea", { rows: "3", placeholder: "automotive, EV", "aria-label": "Tag phrases" }, t.phrases.join(", ")) };
          const tr = el("tr", {}, el("td", { class: "tagname" }, row.name), el("td", { class: "wide" }, row.phrases), el("td", {}, row.bonus),
            el("td", { class: "narrow" }, el("button", { class: "btn small", text: "Remove", onclick: () => { tagRows.splice(tagRows.indexOf(row), 1); tr.remove(); } })));
          tagRows.push(row);
          tagBody.append(tr);
        };
        (p.tags || []).forEach((t) => addTag(t));
        const save = async () => {
          if (await post({ action: "prefs_save", prefs: collect() }, "Preferences saved.")) redraw();
        };
        const field = (label, help, node) => el("label", { class: "field" }, label, help ? el("span", { class: "muted small", text: help }) : null, node);
        const study = el("div", { class: "seg wrap", role: "group", "aria-label": "What you study", testid: "pref-field" },
          ...Object.entries(FIELDS).map(([k, x]) => el("button", { type: "button", "aria-pressed": String(p.field === k), testid: `pref-field-${k}`,
            text: x.label, onclick: () => pick(k) })));
        fill(box,
          s.data.using_defaults ? el("div", { class: "note warn", testid: "prefs-defaults", text: "These are NUauto's original settings (a 2nd-year ECE student aiming at Spring 2027, with security and embedded roles first). Pick what you study, make them yours, then Save." }) : null,
          field("What do you study?", "Fills in starting values for the rest (you can change them): who you are and the roles for Claude, the words in your major's name, and the kinds of work.", study),
          el("div", { class: "grid2" },
            el("div", { class: "stack" },
              field("Co-op term", "As NUworks names it.", el("div", { class: "stack" }, termSel, el("div", { class: "row" }, input("term", { placeholder: "2027 - Spring" }),
                el("button", { class: "btn small", text: "Read the list from NUworks", testid: "pref-terms-read",
                  onclick: () => post({ action: "terms_read" }).then((r) => { if (r && r.task) { S.task = r.task; S.taskLog = []; S.taskAfter = 0; pollState(); } }) })))),
              s.data.terms_error ? el("div", { class: "note warn small", text: s.data.terms_error }) : null,
              field("Term ID", "NUworks' ID for that term (filled in when you pick from the list).", input("term_id")),
              field("Your year", "", year),
              field("Graduation year", "", input("grad_year", { type: "number" })),
              field("Words in your major's name", "Comma-separated. A job for majors without them is flagged, not dropped.",
                (f.major_words = el("input", { type: "text", value: (p.major_words || []).join(", "), testid: "pref-major_words", "aria-label": "Major words" })))),
            el("div", { class: "stack" },
              field("About you, for Claude", "Year, major, interests: one or two sentences.", area("student")),
              field("Roles that fit you", "Claude keeps these in its first pass.", area("keep_roles")),
              field("Roles that clearly don't", "Claude drops only these.", area("drop_roles")))),
          el("div", { class: "row" }, field("Match needed", "% for jobs open to your year", input("threshold", { type: "number" })),
            field("…for the year above yours", "% (two years up: dropped)", input("threshold_above", { type: "number" }))),
          el("details", { testid: "pref-kinds-box" }, el("summary", { text: `Kinds of work: ${(p.categories || []).map((c) => c.label).join(", ")}` }),
            el("p", { class: "small muted", text: "Claude sorts every job into one of these (Review and Apply can show one kind at a time). Change the list and the jobs are sorted again on the next update. A ranking bonus moves that kind up; a lower match bar lets more of it into your pool; always last keeps it in the pool but at the end." }),
            el("div", { class: "table-scroll" }, el("table", { class: "grid" }, el("thead", {}, el("tr", {}, ...["Kind of work", "What counts (Claude reads this)", "Ranking bonus", "Lower match bar", "Always last", ""].map((h) => el("th", { text: h })))),
              kindBody)),
            el("div", { class: "row" }, el("button", { class: "btn small", text: "Add a kind of work", testid: "pref-kind-add", onclick: () => addKind() }))),
          el("details", {}, el("summary", { text: "Fine-tuning: home state, tags" }),
            el("div", { class: "row" }, field("Home state", "2 letters", input("home_state")), field("Its name", "shown in notes", input("home_label")),
              field("Ranking bonus", "points", input("home_bonus", { type: "number" }))),
            el("p", { class: "small muted", text: "Tags: extra ranking points when any of a tag's phrases is in the job (whole words, any case). A job gets only the first tag that matches, from the top." }),
            el("table", { class: "grid" }, el("thead", {}, el("tr", {}, ...["Tag", "Phrases (comma-separated)", "Ranking bonus", ""].map((h) => el("th", { text: h })))),
              tagBody),
            el("div", { class: "row" }, el("button", { class: "btn small", text: "Add a tag", testid: "pref-tag-add", onclick: () => addTag() }))),
          el("div", { class: "row" }, el("button", { class: "btn primary", text: "Save preferences", testid: "pref-save", onclick: save })));
      }
      draw();
      return [box];
    },

    extras(s, redraw) {
      const d = s.data;
      const url = el("input", { type: "text", placeholder: "https://discord.com/api/webhooks/…", "aria-label": "Discord webhook", testid: "setup-discord-url" });
      return [
        el("h2", { text: "Discord" }),
        el("p", { class: "small muted", text: "NUauto can post to a Discord channel: new jobs, deadlines, a Google login about to expire. In Discord: channel settings, Integrations, Webhooks, New webhook, Copy URL." }),
        d.discord ? el("div", { class: "row" }, el("span", { class: "chip ok", text: "set up" }),
          el("button", { class: "btn small", text: "Send a test message", onclick: async () => { const r = await post({ action: "discord_test" }); if (r && r.message) toast(r.message); } }),
          el("button", { class: "btn small danger", text: "Remove", onclick: async () => { if (await post({ action: "discord_remove" }, "Removed.")) redraw(); } })) :
          el("div", { class: "row" }, url, el("button", { class: "btn", text: "Save", testid: "setup-discord-save",
            onclick: async () => { if (await post({ action: "discord_save", url: url.value }, "Discord set up.")) redraw(); } })),
        el("hr", { class: "sep" }),
        el("h2", { text: "Automatic updates" }),
        d.homelab ? el("p", { class: "small muted", text: "Your homelab checks for new jobs twice a day." }) :
          d.scheduler ? el("div", { class: "row between" }, el("span", { class: "small muted", text: "Check NUworks for new jobs at 08:00 and 18:00 while you are logged in (a missed run happens at the next start)." }),
            el("button", { class: "btn " + (d.scheduled ? "" : "primary"), testid: "setup-schedule", text: d.scheduled ? "Turn off" : "Turn on",
              onclick: async () => { if (await post({ action: d.scheduled ? "schedule_off" : "schedule_on" }, d.scheduled ? "Automatic updates off." : "Automatic updates on.")) redraw(); } })) :
            el("p", { class: "small muted", text: "Not available on this system (needs systemd or macOS): use Check for new jobs on Today." }),
        el("hr", { class: "sep" }),
        el("h2", { text: "App icon" }),
        d.mac_app ? el("p", { class: "small muted", text: "NUauto is already an app: keep it in your Applications folder and open it from there or the Dock." }) :
        el("div", { class: "row between" }, el("span", { class: "small muted", text: d.mac ? "NUauto in your Applications folder (~/Applications)." : "NUauto in your apps menu." }),
          el("button", { class: "btn" + (d.launcher ? "" : " primary"), testid: "setup-launcher", text: d.launcher ? "Make it again" : "Add it",
            onclick: async () => { if (await post({ action: "launcher" }, "App icon added.")) redraw(); } })),
      ];
    },
  };

  window.NUautoSetup = async function (view) {
    let data = null;
    let dirty = false;  // you changed a field and haven't saved: background refreshes must not redraw over it
    let drawn = "";     // what the page shows (check times left out): a background refresh with nothing new keeps it
    view.addEventListener("input", () => { dirty = true; });
    view.addEventListener("change", () => { dirty = true; });
    async function load(force) {
      if (dirty && !force) return;
      const before = data ? Object.fromEntries(data.steps.map((s) => [s.id, s.status])) : {};
      let fresh;
      try { fresh = await api.get("/api/setup"); } catch (e) { drawn = ""; view.replaceChildren(el("div", { class: "note fail", text: e.message })); return; }
      if (dirty && !force) return;  // you started editing while it loaded: keep your changes
      const shown = JSON.stringify(fresh, (k, v) => (k === "at" ? undefined : v));
      if (!force && shown === drawn) return;  // nothing new: don't rebuild the page under your cursor
      drawn = shown;
      data = fresh;
      const now = data.steps.find((s) => s.id === open);
      if (now && now.status === "done" && before[open] && before[open] !== "done") open = null;  // just finished: next step
      dirty = false;
      draw();
    }
    function draw() {
      const steps = data.steps;
      const done = steps.filter((s) => s.status === "done").length;
      const required = steps.filter((s) => s.status !== "optional").length;
      if (!open || !steps.find((s) => s.id === open)) open = (steps.find((s) => s.status === "todo") || steps[steps.length - 1]).id;
      const list = el("ol", { class: "steps", testid: "setup-steps" }, ...steps.map((s, i) => {
        const isOpen = s.id === open;
        return el("li", { class: (s.status === "done" ? "done " : "") + (isOpen ? "open" : ""), testid: `step-${s.id}` },
          el("div", { class: "head", role: "button", tabindex: "0", "aria-expanded": String(isOpen),
            onclick: () => { open = isOpen ? null : s.id; dirty = false; draw(); },
            onkeydown: (e) => { if (e.key === "Enter") { open = isOpen ? null : s.id; dirty = false; draw(); } } },
            el("span", { class: "num", text: s.status === "done" ? "✓" : String(i + 1) }),
            el("div", { class: "what" }, el("b", { text: s.title }), el("div", { class: "small muted", text: s.status === "done" || !isOpen ? s.detail : "" })),
            chip(s.status)),
          isOpen ? el("div", { class: "body" }, ...BODY[s.id](s, () => load(true)).filter(Boolean)) : null);
      }));
      view.replaceChildren(
        el("div", { class: "card" }, el("div", { class: "row between" }, el("div", {},
          el("h2", { text: data.complete ? "You're set up" : "Set up NUauto" }),
          el("p", { class: "small muted", text: `${done} of ${steps.length} steps done. Each step checks itself; come back any time from Settings.` })),
          data.complete ? el("a", { class: "btn primary", href: "#/home", text: "Go to Today", testid: "setup-finish" }) : null),
          el("div", { class: "bar" }, el("span", { testid: "setup-bar" }))),
        list);
      view.querySelector("[data-testid=setup-bar]").style.width = Math.round((100 * done) / steps.length) + "%";
    }
    await load(true);
    // re-check the steps when a run ends, or when a check they show changes (not on every poll: typing is kept)
    const watched = () => S.checks.filter((c) => ["firefox", "claude", "nuworks", "google", "resume"].includes(c.id))
      .map((c) => `${c.id}:${c.status}`).join(",");
    let seen = watched();
    return {
      task: (t) => { if (t && t.state !== "running") { setTimeout(load, 300); setTimeout(() => { pollHealth(); load(); }, 2500); } },
      health: () => { const now = watched(); if (now !== seen) { seen = now; load(); } },
    };
  };
})();
