# Internship scoring: resume match % and who may apply

You score how well one student's resume matches {{term}} internship postings (read from the companies'
own job sites). Read the resume in `work/resume.txt` (in the project directory) first. Be strict and
consistent: the same job must get the same score on any run.

The student is {{standing}}.

For each job in your assigned `work/iscore_in_NNN.json`:

1. From the description, list the job's key requirements: 4-8 items, the things the employer actually
   needs (skills, tools, knowledge, experience types). Mark each as required or preferred (preferred =
   "nice to have", "plus", "bonus", "preferred"; if unclear, required). Some postings were read from the
   whole web page: ignore menus, cookie notices and other jobs listed around the posting.
2. For each requirement, check the resume for evidence:
   - met: the resume clearly shows it (a skill, course, project or job that uses it).
   - partial: related or adjacent evidence (e.g. C++ on Arduino for "embedded C";
     Python scripting for "test automation"; "other interests" alone = partial at most).
   - missing: no evidence.
3. match = round(100 * (2*req_met + req_partial + pref_met + 0.5*pref_partial)
                        / (2*req_count + pref_count)), integer 0-100.
4. Ignore year in school, graduation date, major/program restrictions, location, pay, GPA, citizenship and
   dates when scoring the match. Do not reward general enthusiasm; only resume evidence.
5. Separately, set `year_req` from what the posting text EXPLICITLY says about who may apply (year in
   school, graduation date, degree), compared with this student (not your guess from difficulty):
   - "ok": nothing stated, open to any year, or the student fits. Examples for a rising junior who
     graduates in 2029: "rising juniors and seniors", "currently a sophomore or junior", "freshmen and
     sophomores", "graduating 2028 or later", "pursuing a Bachelor's degree".
   - "one_year_up": the posting wants students one year further along than the student (e.g. "rising
     seniors" for a rising junior; "currently a junior or above" for a sophomore; a graduation window
     that ends one year before their graduation year).
   - "two_years_up": two or more years further along (e.g. final-year students only, or graduating
     within months).
   - "grad": graduate students only (Master's / PhD).
   Only requirements count: a year or graduation window the posting only prefers ("preferred", "ideally", "a
   plus", "with a preference for") is "ok". Put the sentence you used in `year_text` (copied from the posting, at
   most 200 characters; empty when nothing is stated), a preferred one too, so the student sees it. "Completed
   at least one internship" is experience, not year: it does not count.

Write `work/iscore_out_NNN.json` (same NNN): a JSON list with exactly one object per
input job, same ids, no extras:

    [{"id": "<job id>", "match": 72, "year_req": "ok", "year_text": "",
      "met": ["Python", "Linux"], "partial": ["embedded C"], "missing": ["FPGA"],
      "why": "<one sentence: the main reason for the score>"}, ...]

Output valid JSON only in that file. Do not edit any other file.
