# Internship triage: drop clearly unrelated internships, order the rest

You screen {{term}} internship postings (SimplifyJobs' list) for one student. Read the resume in
`work/resume.txt` (in the project directory) first.

Student: {{student}}

For each job in your assigned `work/itriage_in_NNN.json` (title, company, locations and the list's own
category), decide `keep`:

- `keep: true` if a student with this resume could plausibly match at least ~50% of
  the role: {{keep_roles}}
- `keep: false` ONLY when the role is clearly outside that: e.g. {{drop_roles}}
- When unsure, keep. A wrongly kept job costs a little; a wrongly dropped one is lost.
- For every kept job also set `fit`, your guess from the title alone: "high" (squarely the kind of role
  above), "medium", or "low" (a long shot). It only decides which postings are read first; nothing is
  dropped for it.
- Judge only the title, company, locations and category. Do not invent details.

Write `work/itriage_out_NNN.json` (same NNN): a JSON list with exactly one object per
input job, same ids, no extras:

    [{"id": "<job id>", "keep": true, "fit": "high", "why": "<5-10 words>"}, ...]

(`fit` may be null when `keep` is false.) Output valid JSON only in that file. Do not edit any other file.
