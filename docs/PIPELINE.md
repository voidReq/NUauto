JOB POOL (jobs.py, rebuilt 2026-10-02; the rules in code = RULES in src/nuauto/jobs.py)
Runs automatically on the homelab twice a day (`nuauto daily`, see docs/DEPLOY.md). Each step does
only new work. By hand (see the src/nuauto/jobs.py docstring for every step):
  nuauto jobs stats     nuauto jobs pool     nuauto jobs suggest 5

PIPELINE
Data comes from NUworks' own search/job data, not page text.
1. list: server filters Co-op, Spring 2027, Available, 4/6 month, not applied; plus co-ops
   with no term.
2. Claude triage (Sonnet, prompts/TRIAGE_PROMPT.md): drops only clearly unrelated titles.
3. details + hard rules (script).
4. Claude match % vs my resume (Sonnet, prompts/SCORE_PROMPT.md, fixed formula).
   4b. Claude category (prompts/CATEGORY_PROMPT.md).
5. pool: thresholds + bonuses.
Claude steps work on batch files: work/<kind>_in_NNN.json -> work/<kind>_out_NNN.json, keyed by
job id; work/resume.txt is regenerated from the resume PDF on every export.
Data: data/ (list, triage, details/, scores, categories, ratings, pool). The homelab owns it;
the laptop pulls a copy.

MY BOUNDS (decided 2026-10-02)
- Co-op, or Internship only if explicitly Spring 2027. Term unclear = keep + flag.
- 4 or 6 month. Undergrad must be allowed.
- Class level: I'm a SOPHOMORE (NUworks profile says Junior; ignore that).
  Lists Sophomore or no level -> need 65%. Junior and up -> need 90%. Senior/grad only -> drop.
- The scorer also reads class-year text (class_req): junior+ -> 90%, senior/grad -> drop.
- Anywhere in the US; Massachusetts jobs get +10 for ranking (not for entry).
- Targeted majors without ECE/College of Engineering -> flag, not drop.
- Citizenship/clearance: no filter. External applications: keep.
- Flags shown, never auto-dropped: not your major, prior experience required, mentions
  graduation 2027/2028, title says other term, no first-time co-ops?, vague posting.
- Description sent to the scorer is capped at 15000 chars.

RANKING
- Cybersecurity is the top priority. Every scored job gets a category (CATEGORY_PROMPT.md).
  Security roles need 60% to enter (junior-only still 90%).
- Pool entry uses the RAW resume match (Boston +10 is ranking only).
- Ranking = match + Boston + category bonus: security +20, low-level (embedded, hardware,
  systems, robotics/controls/test) +10. Full-stack/web/front-end roles stay in the pool but
  always rank last.
- Extra boosts (past job experience): AR/XR/smart glasses +5, wearables (incl. medical) +3
  (not stacked with AR), embedded +5 more (so +15 total). Scaled down 2026-10-02 so real fit
  matters more (I need to pass technical interviews).
- `nuauto approve` shows jobs closing within 7 days first. `nuauto apply` goes closing
  within 7 days first, then best match. Past-deadline rows -> Needs Human. The daily run
  notifies about pool jobs closing within 3 days that aren't in the sheet.

MY RATINGS (taste model)
- `nuauto rate` (y/n, nothing goes in the sheet) -> data/ratings.json, which the laptop owns
  and pushes to the homelab. They train a TF-IDF model that reorders the pool (half rank,
  half my taste) once there are 5 yes + 5 no.
- Rating order rotates categories (security, embedded, hardware, systems, robotics/test,
  software, data/ML, IT, other, full-stack) so every category gets rated; once the model
  trains, it also mixes in the jobs it is least sure about.
- Viewer keys: j/k scroll, space/b page, g/G top/bottom, y/n rate (overwrites an earlier
  answer), s skip, u back one job, o open in browser, q quit.

RESUME
- The PDF at local_config.json resume_path (the only file in its folder). The homelab
  gets a copy as resume.pdf. Updated 2026-10-02 16:44 (minor: past job title/description).
  Jobs scored before that used the 10:03 version; not re-scored since the change is minor.
