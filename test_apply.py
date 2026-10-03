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

# missing resume option stops
expect_stop("Apply to X", [], [{**RESUME, "options": ["Select a resume", "Other resume"]}], "not in the dropdown")

# no dropdown stops
expect_stop("Apply to X", [], [], "exactly one Resume")

print("All checks passed.")
