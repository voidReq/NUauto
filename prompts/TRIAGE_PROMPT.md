# Triage prompt (stage 2): drop clearly unrelated co-ops

You screen NUworks co-op postings for one student. Read the resume in
`work/resume.txt` (in the project directory) first.

Student: {{student}}

For each job in your assigned `work/triage_in_NNN.json`, decide `keep`:

- `keep: true` if a student with this resume could plausibly match at least ~50% of
  the role: {{keep_roles}}
- `keep: false` ONLY when the role is clearly outside that: e.g. {{drop_roles}}
- When unsure, keep. A wrongly kept job costs a little; a wrongly dropped one is lost.
- Judge only the title, company, location and snippet. Do not invent details.

Write `work/triage_out_NNN.json` (same NNN): a JSON list with exactly one object per
input job, same ids, no extras:

    [{"id": "<job id>", "keep": true, "why": "<5-10 words>"}, ...]

Output valid JSON only in that file. Do not edit any other file.
