"""Offline checks for apply.check_popup. Run: python test_apply.py"""
from apply import NeedsHuman, check_popup

LABEL = "Doe, Jane | Spring 2027"
RESUME = {"tag": "select", "type": "select-one", "label": "Resume *",
          "options": ["Select a resume", "", LABEL]}


def expect_stop(text, links, fields, needle):
    try:
        check_popup(text, links, fields, LABEL)
    except NeedsHuman as e:
        assert needle in str(e), str(e)
        return
    raise AssertionError("expected NeedsHuman")


# plain popup passes
check_popup("Apply to X | Submit Your Application", [], [RESUME], LABEL)

# external link / "How to Apply" stops
expect_stop("How to Apply", ["https://x.icims.com/j"], [RESUME], "x.icims.com")
expect_stop("How to Apply", [], [RESUME], "External")

# extra fields are no longer rejected here; the answer bank (test_answers.py) handles them
extra = {"tag": "textarea", "type": "textarea", "label": "Why us?", "options": []}
check_popup("Apply to X", [], [RESUME, extra], LABEL)

# required items that are not fillable fields stop (e.g. Cover Letter + Transcript pickers)
POPUP = "Apply to X\nSubmit Your Application\nResume *\nor add a new resume\nCover Letter *\nAdd a new cover letter\nTranscript\n*\nAdd a new transcript\nCancel\nSubmit"
expect_stop(POPUP, [], [RESUME], "Popup requires Cover Letter, Transcript")
check_popup("Apply to X\nResume *\nCancel\nSubmit", [], [RESUME], LABEL)  # only the resume: fine
q = {"tag": "select", "type": "select-one", "label": "Work authorization *", "options": ["Yes", "No"]}
check_popup("Apply to X\nResume *\nWork authorization *\nSubmit", [], [RESUME, q], LABEL)  # a real field: answer bank handles it

# Outlook safelinks are unwrapped to the real site
SAFE = "https://eur01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fcareer55.sapsf.eu%2Fsfcareer%2Fjob%3Fid%3D1&data=x&reserved=0"
expect_stop("How to Apply", [SAFE], [RESUME], "External application: career55.sapsf.eu -> https://career55.sapsf.eu/sfcareer/job?id=1")

# company site already done (nuauto assist): an off-site link is no reason to stop, every other check stays
check_popup("How to Apply", ["https://x.icims.com/j"], [RESUME], LABEL, company_site_done=True)
try:
    check_popup(POPUP, ["https://x.icims.com/j"], [RESUME], LABEL, company_site_done=True)
    raise AssertionError("expected NeedsHuman")
except NeedsHuman as e:
    assert "Cover Letter" in str(e)

# missing resume option stops
expect_stop("Apply to X", [], [{**RESUME, "options": ["Select a resume", "Other resume"]}], "not in the dropdown")

# no dropdown stops
expect_stop("Apply to X", [], [], "exactly one Resume")

print("All checks passed.")
