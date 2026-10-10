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

MY BOUNDS (decided 2026-10-02; since 2026-10-05 these are preferences: local_config.json "preferences", set in the
GUI's setup. jobs.DEFAULTS holds the values below, used for any key not set, so nothing changed for an existing setup.
TRIAGE_PROMPT.md's student / keep / drop lines come from the same preferences ({{student}}, {{keep_roles}},
{{drop_roles}}, filled into work/ by jobs.render_prompt). Class year in general: your year or below = threshold,
the next year up = threshold_above + a "<year>+" flag, two or more up = dropped.)
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

WHAT YOU STUDY (fields.py, added 2026-10-10; preferences "field" and "categories")
- The kinds of work jobs are sorted into are a preference: [{key, label, description}], "other" always last. The setup
  wizard's "What do you study?" fills in a field's list (and its triage text, major words, bonuses); each stays
  editable. CATEGORY_PROMPT.md gets the list as {{categories}} ({{first_category}} in its example); with the
  engineering field (the default, = my original settings) it renders byte-identical to the old prompt.
- Which set of kinds a job was sorted into: data/category_sets.json {id: fingerprint of the keys}. A job sorted under
  another set (you changed field or the list's keys) counts as uncategorized and is sorted again on the next update
  (about one Claude call per 120 jobs). Jobs from before this file count as the engineering set. Changing only a
  name or description does not re-sort.
- Triage the same way: each decision records which triage text it was made with ("by" in triage.json, a hash of
  student / keep_roles / drop_roles). A job dropped under other text is triaged again (a new student's first update
  runs before setup's preferences step, with the engineering defaults); kept jobs stay kept; decisions from before
  "by" count as made with the current text (nothing re-triaged for an existing setup).
- Saving preferences in the GUI rebuilds the pool at once (and pushes them to the homelab in homelab mode); jobs in
  kinds you no longer have show as "not sorted yet" until the next update sorts them.
- Rating rotation (RATE_ORDER), Insights labels, the GUI's labels and the terminal viewer's colors (the biggest
  bonus red, other bonuses green, rank_last dim) come from the list.

RANKING (my settings: the engineering field)
- Cybersecurity is the top priority. Every scored job gets a category (CATEGORY_PROMPT.md).
  Security roles need 60% to enter (junior-only still 90%).
- Pool entry uses the RAW resume match (Boston +10 is ranking only).
- Ranking = match + Boston + category bonus: security +20, low-level (embedded, hardware,
  systems, robotics/controls/test) +10. Full-stack/web/front-end roles stay in the pool but
  always rank last.
- Extra boosts (past job experience): AR/XR/smart glasses +5, wearables (incl. medical) +3
  (not stacked with AR), embedded +5 more (so +15 total). Scaled down 2026-10-02 so real fit
  matters more (I need to pass technical interviews).
- Tags (since 2026-10-06 preferences "tags", editable in the GUI's setup): a name, phrases and
  points. A phrase matches whole words, any case, a space or hyphen matching either
  (jobs.phrase_pattern). The first tag that matches wins (one per job). AR/XR and wearables are
  the defaults; their phrases give the same tags as the old fixed patterns, except "vision pro"
  no longer matches inside "Computer Vision Prototyping".
- `nuauto approve` shows my Proposed sheet rows first (even below the pool bar, flagged; ones with no stored
  details are listed, not shown; y turns that row Approved, no new row), then pool jobs; closing within 7 days
  first. `nuauto apply` goes closing within 7 days first, then best score (`--order score|match|closes|pay` instead; the GUI's "Apply in this order" menu). Past-deadline rows -> Needs Human. The daily run
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
