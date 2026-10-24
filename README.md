# NUauto

Finds Northeastern co-op postings on NUworks that fit your resume, lets you approve them, and
submits the approved ones for you. Not affiliated with Northeastern, NUworks or Symplicity.

## How it works

1. NUauto checks NUworks for new postings, has Claude score them against your resume, and ranks them.
2. You review the best ones. Approving a job adds it to your Google Sheet as Approved.
3. NUauto opens NUworks in Firefox and submits each Approved job, one at a time, while you watch.

It stops and asks you when a form has a question with no saved answer, and never writes free text. Jobs that send
you to the company's own site, or aren't on NUworks at all, go to a Claude agent in a visible browser
(`nuauto assist`): it fills the form, and asks you before it submits anything. Summer internships (from
[SimplifyJobs'](https://github.com/SimplifyJobs/Summer2027-Internships) and
[vanshb03's](https://github.com/vanshb03/Summer2027-Internships) lists) can be turned on in Settings > Jobs.

## Install

You need a NUworks account, a Google account, and [Claude Code](https://claude.com/claude-code) (the setup screen
installs it if missing).

**The app:** download the AppImage (Linux) or .dmg (macOS 12+) from
[Releases](https://github.com/voidReq/NUauto/releases/latest); the release page says how to open it.

**From source** (to change it), Linux or macOS:

```sh
curl -fsSL https://raw.githubusercontent.com/voidReq/NUauto/main/install.sh | sh
```

It installs everything in your home folder (no sudo), with your files in `~/NUauto/local/`.

Either way, the setup screen walks you through Google, your sheet, your resume, the NUworks login and what you're
looking for. `nuauto assist` also needs Node.js and Google Chrome. If the browser won't start on Linux, it may need
system libraries: `sudo ~/NUauto/.venv/bin/python -m playwright install-deps firefox` (Debian/Ubuntu).

## Use it

`nuauto gui` (or the app icon) opens the window: **Today**, **Review** (approve jobs), **Apply** (submit Approved
ones, run company-site assistants), **Insights**, **Answers** and **Settings** (health checks, preferences, limits).
Try it with nothing real involved: `nuauto gui --demo`. `nuauto` alone lists every terminal command.

An optional Linux home server can run the updates twice a day and serve the same app to your phone
(Settings > Homelab > Set it up). Details are in `docs/`.

Check NUworks' terms of use before using this. Everything in `local/` (logins, secrets, answers) is
gitignored; keep it that way. MIT licensed (see `LICENSE`).
