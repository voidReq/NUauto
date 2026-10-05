# Scoring prompt (stage 4): resume match %

You score how well one student's resume matches NUworks co-op postings. Read the
resume in `work/resume.txt` (in the project directory) first. Be strict and
consistent: the same job must get the same score on any run.

For each job in your assigned `work/score_in_NNN.json`:

1. From the description, qualifications and skills, list the job's key requirements:
   4-8 items, the things the employer actually needs (skills, tools, knowledge,
   experience types). Mark each as required or preferred (preferred = "nice to have",
   "plus", "bonus", "preferred"; if unclear, required).
2. For each requirement, check the resume for evidence:
   - met: the resume clearly shows it (a skill, course, project or job that uses it).
   - partial: related or adjacent evidence (e.g. C++ on Arduino for "embedded C";
     Python scripting for "test automation"; "other interests" alone = partial at most).
   - missing: no evidence.
3. match = round(100 * (2*req_met + req_partial + pref_met + 0.5*pref_partial)
                        / (2*req_count + pref_count)), integer 0-100.
4. Ignore class year, major/program restrictions, location, pay, GPA, citizenship and work term when scoring.
   Those are handled by other rules. Do not reward general enthusiasm; only resume evidence.
5. Separately, set `class_req` from what the posting text EXPLICITLY says about
   year/standing (not your guess from difficulty):
   - "none": nothing stated, or open to all / any year / sophomores mentioned as OK
   - "junior_plus": requires junior standing or later (e.g. "rising junior or senior",
     "must be a junior", "3rd year or above", "completed at least one co-op" does NOT
     count; that is experience, not year)
   - "senior_plus": requires senior/final-year standing
   - "grad": graduate students only

Write `work/score_out_NNN.json` (same NNN): a JSON list with exactly one object per
input job, same ids, no extras:

    [{"id": "<job id>", "match": 72, "class_req": "none",
      "met": ["Python", "Linux"], "partial": ["embedded C"], "missing": ["FPGA"],
      "why": "<one sentence: the main reason for the score>"}, ...]

Output valid JSON only in that file. Do not edit any other file.
