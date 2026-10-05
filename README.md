# NUauto

Finds Northeastern co-op postings on NUworks that fit your resume, lets you approve them, and
submits the approved ones for you. Not a mass-apply bot: you approve every job, and it applies
to at most 11 per week (99 total). Not affiliated with Northeastern, NUworks or Symplicity.

## How it works

1. `nuauto update` pulls new postings, has Claude score them against your resume, and ranks them.
2. `nuauto approve` shows the best ones. `y` adds a job to your Google Sheet as Approved.
3. `nuauto apply` opens NUworks in Firefox and submits each Approved job, one at a time.

`nuauto apply` stops and asks you when a form has a question with no saved answer, and never
writes free text (essays, cover letters). Jobs that send you to the company's own site are marked
Needs Human; `nuauto assist <row>` can fill those with a Claude agent in a visible browser. It drafts
longer answers for your approval and always asks you before it submits anything.

## Setup

Needs Linux (tested on Fedora and Ubuntu; macOS untested), Python 3.12 or newer, a NUworks account, a Google
account, and [Claude Code](https://claude.com/claude-code) (a Claude subscription or API key; scoring
uses Sonnet). `nuauto assist` also needs Node.js (`npx`) and Google Chrome.

```sh
git clone https://github.com/voidReq/NUauto.git && cd NUauto
python3 -m venv .venv
.venv/bin/pip install -e .                       # installs the `nuauto` command into .venv
.venv/bin/python -m playwright install firefox
ln -s "$PWD/.venv/bin/nuauto" ~/.local/bin/nuauto   # ~/.local/bin must be on your PATH
mkdir -m 700 local && cp local_config.example.json local/local_config.json
curl -fsSL https://claude.ai/install.sh | bash   # Claude Code, if you don't have it
claude                                           # log in once, then /exit
```

Your personal files all go in `local/` (gitignored).

1. **Google:** in Google Cloud, enable the Sheets API and create an OAuth client ("Desktop app").
   Save its JSON as `local/client_secret.json`.
2. **Sheet:** make a blank Google Sheet, put its ID (from the URL) in `local/local_config.json` as
   `sheet_id`, then run `nuauto setup-sheet`.
3. **Resume:** set `resume_path` in `local/local_config.json` to your resume PDF, and create
   `local/profile.json` with `{"resume_label": "..."}`: the exact name of that resume in NUworks'
   Apply popup.
4. **NUworks:** `nuauto login`, log in in the window that opens, then close it.
5. `nuauto doctor` to check everything, then `nuauto update`.

Optional: a Discord webhook URL in `local/discord_webhook.txt` for notifications.

## Commands

```
nuauto                 list every command (plus tools like `nuauto jobs stats`)
nuauto update          find and score new jobs
nuauto approve         review the best jobs (y approve, n no, s skip, q quit)
nuauto rate            y/n on jobs to teach the ranking your taste
nuauto apply [-n 3]    submit Approved jobs (run in a real terminal)
nuauto status          sheet counts and this week's limit
nuauto login [google]  NUworks login / Google login (Google's lasts 7 days)
nuauto assist <row>    an agent fills a company-site application; it asks you before submitting
nuauto doctor          health check
nuauto test            offline tests
```

## Make it yours

Your preferences live in `RULES` and the term ID in `src/nuauto/jobs.py` (term, class level, thresholds,
bonuses) and in the Claude prompts in `prompts/`. See `docs/PIPELINE.md`.

To run updates on a server twice a day instead, fill in the server fields in
`local/local_config.json` and follow `docs/DEPLOY.md`. Leave them empty and everything runs locally.

Check NUworks' terms of use before using this. Everything in `local/` (logins, secrets, answers) is
gitignored; keep it that way.

MIT licensed (see `LICENSE`).
