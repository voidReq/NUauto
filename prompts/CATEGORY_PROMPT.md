# Category prompt: what kind of role is this?

For each job in your assigned `work/cat_in_NNN.json`, pick exactly ONE `category`
for what the role mainly is (judge from title, function, skills and the description
excerpt; do not invent):

{{categories}}

Write `work/cat_out_NNN.json` (same NNN): a JSON list with exactly one object per
input job, same ids, no extras:

    [{"id": "<job id>", "category": "{{first_category}}"}, ...]

Output valid JSON only in that file. Do not edit any other file.
