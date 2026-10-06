# NUauto

Finds Northeastern co-op postings on NUworks that fit your resume, lets you approve them, and
submits the approved ones for you. Not a mass-apply bot: you approve every job, and it applies
to at most 11 per week (99 total). Not affiliated with Northeastern, NUworks or Symplicity.

## How it works

1. NUauto checks NUworks for new postings, has Claude score them against your resume, and ranks them.
2. You review the best ones. Approving a job adds it to your Google Sheet as Approved.
3. NUauto opens NUworks in Firefox and submits each Approved job, one at a time, while you watch.

It stops and asks you when a form has a question with no saved answer, and never writes free text (essays,
cover letters). Jobs that send you to the company's own site are left to you; `nuauto assist <row>` can fill
those with a Claude agent in a visible browser. It drafts longer answers for your approval and always asks you
before it submits anything.

## Install

Linux or macOS, a NUworks account, a Google account, and [Claude Code](https://claude.com/claude-code) (a Claude
subscription or API key; scoring uses Sonnet). `nuauto assist` also needs Node.js (`npx`) and Google Chrome.
Used for real on Fedora and Ubuntu; macOS passes the automated tests (demo mode) but hasn't been used for real yet.

```sh
curl -fsSL https://raw.githubusercontent.com/voidReq/NUauto/main/install.sh | sh
```

It installs everything in your home folder (no sudo): the code in `~/NUauto`, Python 3.12 through
[uv](https://docs.astral.sh/uv/) (your system's Python version doesn't matter), Playwright's Firefox, the `nuauto`
command and an app icon. Then the NUauto window opens on its **setup** screen, which walks you through the rest,
checking each step: Claude Code, your own free Google sign-in client, your sheet (it can create one), your
resume, the NUworks login and what you are looking for. Run the installer again any time to update.

Your personal files all go in `~/NUauto/local/` (gitignored, readable only by you).

### Without the window (command line only)

```sh
git clone https://github.com/voidReq/NUauto.git && cd NUauto
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python -m playwright install firefox
mkdir -m 700 local && cp local_config.example.json local/local_config.json
```

Then: a Google OAuth client ("Desktop app", Sheets API on) saved as `local/client_secret.json`; a blank sheet
whose ID goes in `local/local_config.json` as `sheet_id` (then `nuauto setup-sheet`); your resume PDF as
`resume_path`; `local/profile.json` with `{"resume_label": "..."}` (the resume's exact name in NUworks' Apply
popup); `nuauto login`; `nuauto doctor`. Your preferences go in `local_config.json` `"preferences"` (see
`docs/PIPELINE.md`; without them NUauto uses its original settings).

## Use it

`nuauto gui` (or the app icon) opens the window: **Today** (what needs you, this week's count), **Review**
(approve jobs: `y`/`n`/`s`), **Apply** (submit the Approved ones, watch, stop any time), **Company sites**,
**Answers** (your saved form answers) and **Settings** (every health check: logins, Claude Code, the sheet).
It keeps checking that your logins still work and tells you before something expires.

Try it with nothing real involved: `nuauto gui --demo` (a fake sheet, fake NUworks pages, fake Claude).

The same things from a terminal:

```
nuauto                 list every command (plus tools like `nuauto jobs stats`)
nuauto gui             the window (--demo: everything fake)
nuauto update          find and score new jobs
nuauto approve         review the best jobs (y approve, n no, s skip, q quit)
nuauto rate            y/n on jobs to teach the ranking your taste
nuauto apply [-n 3]    submit Approved jobs (run in a real terminal)
nuauto status          sheet counts and this week's limit
nuauto login [google]  NUworks login / Google login (Google's lasts 7 days)
nuauto assist <row>    an agent fills a company-site application; it asks you before submitting
nuauto doctor [--json] health check
nuauto test            offline tests
```

## Make it yours

Your preferences (term, year, major, thresholds, priorities, and how Claude should see you) are set in the setup
screen and saved in `local/local_config.json`; the rules that use them are `src/nuauto/jobs.py` and the Claude
prompts in `prompts/`. See `docs/PIPELINE.md`. How the window works: `docs/GUI.md`.

To run updates on a server twice a day instead, fill in the server fields in
`local/local_config.json` and follow `docs/DEPLOY.md` (the server must be Linux with systemd). Leave them
empty and everything runs locally.

Check NUworks' terms of use before using this. Everything in `local/` (logins, secrets, answers) is
gitignored; keep it that way.

MIT licensed (see `LICENSE`).
