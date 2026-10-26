JOB POOL (jobs.py; the rules in code = RULES and hard_rules in src/nuauto/jobs.py; the Summer internships pool,
intern.py: SUMMER INTERNSHIPS below)
Runs automatically on the homelab twice a day (`nuauto daily`, see docs/DEPLOY.md). Each step does
only new work. By hand (see the src/nuauto/jobs.py docstring for every step):
  nuauto jobs stats     nuauto jobs pool     nuauto jobs suggest 5

PIPELINE
Data comes from NUworks' own search/job data, not page text.
1. list (jobs.cmd_list): two server searches, both Co-op, Available, 4 or 6 month, not applied. One adds your term
   (preferences "term" / "term_id"); the other has no term filter: what it finds beyond the first the hard
   rules sort out.
2. Claude triage (Sonnet, prompts/TRIAGE_PROMPT.md, 250 jobs a batch): title, company, place and a snippet;
   drops only clearly unrelated roles.
3. details + hard rules (script; one NUworks job page each).
4. Claude match % vs your resume (Sonnet, prompts/SCORE_PROMPT.md, 20 jobs a batch). The formula is fixed in the
   prompt: 4-8 key requirements, each required or preferred, each met / partial / missing in the resume;
   match = 100 * (2 * required met + required partial + preferred met + 0.5 * preferred partial) /
   (2 * required + preferred). Class year, major, place and pay don't count; class_req (year the text asks for) is set
   separately. The description sent is capped at 15000 characters (jobs.TEXT_CAP).
   4b. Claude category (prompts/CATEGORY_PROMPT.md, 120 a batch).
5. pool (jobs.build_pool): bars + bonuses. Only jobs still in the list.
Claude runs as `claude -p --model sonnet` shut in work/ (Read/Write/Edit there only; daily.run_claude) on batch files:
work/<kind>_in_NNN.json -> work/<kind>_out_NNN.json, keyed by job id; a co-op batch that doesn't answer exactly its ids is
not imported (the internships' big batches keep the rows that are right). work/resume.txt is regenerated from the resume PDF on every export. After a resume change, `python -m
nuauto.jobs score-export-all` (then Claude, then score-import) scores everything again.
Data: data/ (list, triage, details/, scores, categories, category_sets, ratings, applications, pool). The homelab owns it
(the laptop pulls a copy); ratings are merged both ways (sync.share).

BOUNDS (preferences: local_config.json "preferences", set in the GUI's setup and Settings > Jobs. jobs.DEFAULTS holds
the values below, used for any key not set. TRIAGE_PROMPT.md's student / keep / drop lines come from the same
preferences ({{student}}, {{keep_roles}}, {{drop_roles}}, filled into work/ by jobs.render_prompt).
onboard.check_prefs validates them.)
Keys and defaults: term "2027 - Spring" + term_id; class_year sophomore; threshold 65; threshold_above 90;
category_threshold {security: 60}; major_words ["college of engineering", "electrical"]; home_state MA, home_label
Boston, home_bonus 10; category_bonus; categories; rank_last; tags; grad_year 2029; field; student, keep_roles,
drop_roles. (Defaults for the kinds, bonuses and bars come from the field: fields.py.)
- Co-op, or Internship only if it explicitly names your term. Another term = dropped; no term at all = kept, unless the
  title or text names your term; if not, flagged "term unclear".
- 4 or 6 month (no length listed: flag). Undergrad must be allowed (degree levels listed without undergraduate: dropped).
- Closed, expired or already applied: dropped.
- Class level, from the levels NUworks lists: your year or below, or no level listed -> threshold. The next year up
  -> threshold_above + a "<year>+" flag. Two or more up -> dropped.
- The scorer's class_req (from the text): one year up -> threshold_above (flag "<year>+ (in text)"), two or more up
  or grad -> dropped. The higher bar wins.
- A category's own bar (category_threshold) replaces the usual bar, but not threshold_above.
- Entry uses the RAW resume match; the home-state bonus is ranking only. A job you rated yes stays in the pool even if
  a re-score dipped it under the bar (flag "kept: rated yes").
- Citizenship/clearance: no filter.
- Flags shown, never auto-dropped: term unclear, length unclear, title says other term (jobs.term_patterns), not your
  major (targets other majors than major_words), "<year>+", prior experience required, "no first-time co-ops?",
  mentions graduation (the 4 years before grad_year), vague posting (fewer than 4 requirements listed), uncategorized,
  "apply on company site too?" (jobs.external_hint).

WHAT YOU STUDY (fields.py; preferences "field" and "categories")
- Fields: engineering (the default), business, health, sciences, social, arts. The kinds of work jobs are sorted into
  are a preference: [{key, label, description}], "other" always last. The setup wizard's "What do you study?" fills in a
  field's list (and its triage text, major words, bonuses, bars); each stays editable. CATEGORY_PROMPT.md gets the list
  as {{categories}} ({{first_category}} in its example).
- Which set of kinds a job was sorted into: data/category_sets.json {id: fingerprint of the keys}. A job sorted under
  another set (you changed field or the list's keys) counts as uncategorized and is sorted again on the next update.
  Jobs from before this file count as the old engineering set (fields.LEGACY). Changing only a name or description does
  not re-sort.
- Triage the same way: each decision records which triage text it was made with ("by" in triage.json, a hash of
  student / keep_roles / drop_roles). A job dropped under other text is triaged again; kept jobs stay kept; decisions
  from before "by" count as made with the current text.
- Saving preferences in the GUI rebuilds the pool at once (and pushes them to the homelab in homelab mode); jobs in
  kinds you no longer have show as "not sorted yet" until the next update sorts them.
- Rating rotation (RATE_ORDER), Insights labels, the GUI's labels and the terminal viewer's colors (the biggest
  bonus red, other bonuses green, rank_last dim) come from the list.

RANKING (jobs.pool_entry, pool_sort_key)
- rank = raw match + home bonus + category bonus + tag bonus. Defaults for engineering: security +20, embedded +15,
  hardware / systems / robotics-test +10, front-end / UI -20 (and always last: rank_last). Full-stack and web
  back-end rank like any software job. Other fields: no bonuses.
- Tags (preferences "tags"; name, phrases, points; up to 20): a phrase matches whole words, any case, a space or
  hyphen matching either (jobs.phrase_pattern), anywhere in the title, description, qualifications or skills. The first
  tag that matches wins (one per job, not added up). Defaults: AR/XR +5 (smart glasses, hololens...), wearables +3.
  "vision pro" does not match inside "Computer Vision Prototyping".
- Order = rank until the taste model can train (below), then half rank, half taste: (rank / 100 + taste) / 2.
  rank_last kinds always after the rest. Review's card says which (jobs.order_text); Score shows each part
  (jobs.rank_text).
- `nuauto approve` (and the GUI's Review) shows: jobs closing within 7 days first (soonest first), then your Proposed
  sheet rows (even below the pool bar, flagged; ones with no stored details are listed, not shown; y turns that row
  Approved, no new row), then pool jobs not in the sheet and not rated no. Review's search box (jobs.search_match)
  takes words or "quoted phrases".
- `nuauto apply --order`: default = closing within 7 days first, then best rank | score (rank) | match | closes | pay
  (top of the hourly range); the GUI's sort menu. Rows without the value go last. Past-deadline rows -> Needs Human.
  The daily run notifies about pool jobs closing within 3 days that aren't in the sheet and not rated no.
- `nuauto jobs suggest N`: the top N pool jobs (not in the sheet, not rated no) as Proposed rows, after you say y.

YOUR RATINGS (taste model)
- `nuauto rate` (y/n, nothing goes in the sheet) -> data/ratings.json {id: {label 0|1, date}}, merged between the
  machines per job (sync.share; jobs.ratings_locked). Approving in Review counts as yes; n as no. Sheet rows that are
  Approved, Applied, Accepted, Rejected, Needs Human or Failed also count as yes (jobs.my_labels); your explicit
  rating wins.
- Model (jobs.train): TF-IDF of title (x3), skills, function and description (words and word pairs) -> logistic
  regression, balanced. It trains once there are 5 yes and 5 no; taste is 0-1. Trained again only when the labels or
  a job's text change (jobs.taste_for keeps the last few; about a second).
- Rating order (jobs.rating_order): one job from each kind in turn (RATE_ORDER: your kinds in order, rank_last ones
  after, then not sorted), best first inside a kind; once the model trains, inside a kind it alternates the best job
  and the one it is least sure about. Only unrated pool jobs not in the sheet.
- Viewer keys: j/k (or arrows) scroll, space/b page, g/G top/bottom, y/n rate (overwrites an earlier answer), s skip,
  u back one job, o open in browser, q quit. In approve mode y = approve. Every answer is saved at once.

SUMMER INTERNSHIPS (intern.py + postings.py; `nuauto intern ...`)
A second pool for jobs that are not on NUworks, from two public lists: SimplifyJobs' and vanshb03's Summer 2027
lists (github.com/SimplifyJobs/Summer2027-Internships and github.com/vanshb03/Summer2027-Internships, dev branch,
.github/scripts/listings.json; same file format).
On when local_config.json has "internships" (an object; {} = intern.DEFAULTS): "term" "Summer 2027"; "lists"
{"simplify": url, "vansh": url} ("" = not read); "fetch_per_run" 300 postings read by plain requests;
"browser_per_run" 40 by the hidden browser; "score_per_run" 120 scored; "threshold" (None = the co-ops' bar). Set in
the GUI too (Settings > Jobs, or setup's optional step): on / off, the term, the bar, each list's address (a new
year's lists are new repos: changing the term moves the addresses with it); off saves false.
- Runs: `nuauto intern update`, on the homelab every 2 hours (nuauto-intern.timer, :40 on even hours New York) and after
  each NUworks update (daily.py); one at a time (data/intern/update.lock). Each list is downloaded only when it
  changed (ETag); one that can't be read is skipped. Each step does only new work, a limited amount per run: the first
  runs work through the backlog, best titles (fit) and newest first (intern.priority).
- Lists: a listing counts when it is for the term (Simplify's "terms"; vanshb03's has only a season, "Summer", on a
  list whose address says the term's year). One its list closes (or drops) stays, marked inactive; an approved one
  that closes is named in the next morning message (intern.closed_notice). Ids: "s..." Simplify's, "sv..." vanshb03's.
- Short links (2026-10-26, my request): vanshb03's list gives go.vansh.nyc/<code> links, not the posting's. Each update
  asks where each new one leads (intern.real_links: its redirect only, the posting is not opened; 0.2 s apart; public
  addresses only; 5 failures in a row and the rest wait for the next run) and the listing keeps the posting's own link
  (list_url: the short one). The answers are in data/intern/links.json, so each is asked once; a later download of the
  list keeps the real link while the short one is the same. So the job site's own data, the same job on two lists and
  new Other jobs rows all use the real link. A sheet row added earlier with a short link is matched through links.json
  (intern.real_url: Review leaves it out, closed_notice finds it), and the assistant opens and holds the real job site
  (assist.other_target), so several such rows run side by side. The sheet's cells are never rewritten.
- Dedupe (intern.mark_dupes): the same job on both lists is kept once: the listing seen first (the same day:
  Simplify's) stays, the other gets dup_of and is left out of everything. Once a duplicate, always. The same job =
  the same posting link (no www / case / tracking / trailing /apply / locale), or the same company and job number in
  the link on another host, or the same company ("Acme Capital" = "Acme Capital University") and title (punctuation,
  season, year and internship/intern ignored) in the same place (a shared state, or "United States" / none on one
  side) while the first one is open. A link or number several titles on one list share is a careers page, not a job.
- Steps: 1. the lists. Rules (intern.rules): not a duplicate, still open on its list, open to undergraduates
  (degrees list a Bachelor's, or none). 2. Claude triage on titles (prompts/INTERN_TRIAGE_PROMPT.md, same student /
  keep / drop text as the co-ops): drops only clearly unrelated roles; kept ones get a fit (high / medium / low),
  which only orders the reading. 3. each posting read from the company's own job site (postings.py, no login, no
  cookies), first that works: the site's public job data (Workday, Greenhouse, Lever, Ashby, SmartRecruiters, Oracle,
  Eightfold, Workable, iCIMS; a careers page with ?gh_jid= that names its Greenhouse board uses that), else the
  page's schema.org JobPosting data, else the page's text (needs 1500+ characters that read like a posting), else
  the page in a hidden browser (fresh, no profile, no images, for pages built by JavaScript). Read-only GETs, 1 s
  apart per site, never to a non-public address (checked after redirects too). Pay and deadline are read when the
  site gives them (the deadline: schema.org validThrough, Workday's endDate, iCIMS's page data, Oracle's
  ExternalPostedEndDate, Greenhouse's application_deadline; postings.end_day drops a placeholder years away). Greenhouse,
  Lever, Ashby, SmartRecruiters, Eightfold and Workable have no date in their data; dates written only in a posting's
  text are not read (12/11/2026 can be either month: no guessing). Postings read before a site's date was read
  (intern.REDATE: Oracle, 2026-10-26) are read once more for the date alone (intern.redate, 40 per run). The close date
  shows on Review and Apply, and the morning message's Closing soon line uses it. A posting that says it is gone is
  marked closed: not scored. A posting no way could read in 3 runs
  (each with the browser; not twice a day) shows in Review after the scored ones, unscored: none is dropped for being
  unreadable. 4. Claude score (prompts/INTERN_SCORE_PROMPT.md): the same match formula as the co-ops, plus year_req,
  who may apply compared with your standing DURING the internship (a sophomore now = a rising junior then): ok =
  your bar, one_year_up = threshold_above, two_years_up / grad = dropped; the sentence used is kept (year_text).
  4b. Claude category (CATEGORY_PROMPT.md). 5. pool (intern.build_pool): the internships' own bar (or the co-ops'), a
  kind's lower bar if it is lower, bonuses as above (home state, kind, tags); same "kept: rated yes". Flags: posted 45+
  days ago, US citizens only (the list's sponsorship text), mentions graduation, vague posting, the posting looks
  closed, a year further along.
- Review (GUI: the source switch; terminal: `nuauto intern approve` / `rate`): ordered like the co-ops (jobs closing
  within 7 days first, when the posting says when; then your Proposed Other jobs rows; then score, half taste once
  trained). The model learns from co-op and internship decisions together, plus Other jobs rows (Approved, Applied...)
  as yes; one data/ratings.json. Approve = an Approved row in the Other jobs tab (Notes "<term> internship (<list>);
  match ..."), so no weekly or total cap; Undo = that row back to Proposed. Apply: `nuauto assist other <row>`.
- Data: data/intern/ (listings, source, links, triage, details/, failed, scores, categories, category_sets, pool, unread,
  scans). No message per run: the morning message counts the last day's new pool internships, with the best one.
- `nuauto intern fetch <url> [--browser]` reads one posting now (nothing saved); `intern stats` shows each step.

INSIGHTS (insights.py; `nuauto insights`, the GUI's Insights): read-only pay, places and kinds of work for the pool and
for sent rows, co-ops and internships apart. Yearly pay of $1,000+ becomes hourly (2080 hours); anything not $10-$150
an hour counts as not listed, never a guess.

RESUME
- The PDF at local_config.json resume_path: the GUI keeps the one you give it as local/resume.pdf (docs/GUI.md, YOUR
  FILES). The homelab gets a copy as resume.pdf. A new resume does not re-score old jobs by itself (see score-export-all).
