"""Sheet setup.

  nuauto setup-sheet          one-time: headers, Status dropdown, frozen row 1, then the styling below.
                                 Refuses to touch the sheet if any cell already has a value.
  nuauto setup-sheet format   (re)apply the styling only: header, column widths, Status colors,
                                 alternating rows. Never changes a value; safe to re-run. Replaces this
                                 tab's conditional formats and alternating colors with ours. Styles the
                                 Other jobs tab too, if there is one.
  nuauto setup-sheet other    add the Other jobs tab (jobs not on NUworks; NUauto also makes it the first
                                 time you add one). Never touches a tab that exists.
"""
import sys

import gspread

from nuauto import config

HEADERS = ["URL", "Company", "Title", "Status", "Notes", "Date"]
STATUSES = ["Proposed", "Approved", "Applied", "Failed", "Needs Human"]
LAST_ROW = 1000  # dropdown covers D2:D1000


def rgb(hex_color):
    h = hex_color.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


HEADER_BG = "#37474F"
# Status -> (cell background, text color)
STATUS_COLORS = {
    "Proposed": ("#ECEFF1", "#455A64"),
    "Approved": ("#E6F4EA", "#137333"),
    "Applied": ("#E8F0FE", "#1967D2"),
    "Failed": ("#FCE8E6", "#C5221F"),
    "Needs Human": ("#FEF7E0", "#B06000"),
}
WIDTHS = {"URL": 110, "Company": 215, "Title": 370, "Status": 115, "Notes": 380, "Date": 95}


def format_sheet(sh, ws, title="Jobs"):
    """Styling only (see the module docstring). title: the tab's name ("Jobs", or sheet.OTHER_TAB)."""
    from nuauto import sheet  # SITE_MARK
    meta = sh.fetch_sheet_metadata()
    tab = next(t for t in meta["sheets"] if t["properties"]["sheetId"] == ws.id)
    col = {name: i for i, name in enumerate(HEADERS)}

    def cells(c0, c1, r0=1, r1=LAST_ROW):
        return {"sheetId": ws.id, "startRowIndex": r0, "endRowIndex": r1, "startColumnIndex": c0, "endColumnIndex": c1}

    def style(rng, fmt):
        return {"repeatCell": {"range": rng, "cell": {"userEnteredFormat": fmt},
                               "fields": ",".join(f"userEnteredFormat.{k}" for k in fmt)}}

    reqs = [{"deleteConditionalFormatRule": {"sheetId": ws.id, "index": 0}} for _ in tab.get("conditionalFormats", [])]
    reqs += [{"deleteBanding": {"bandedRangeId": b["bandedRangeId"]}} for b in tab.get("bandedRanges", [])]
    extra = tab["properties"]["gridProperties"]["columnCount"] - len(HEADERS)
    if extra > 0 and not any(len(r) > len(HEADERS) and any(c.strip() for c in r[len(HEADERS):]) for r in ws.get_all_values()):
        reqs.append({"deleteDimension": {"range": {"sheetId": ws.id, "dimension": "COLUMNS",
                                                   "startIndex": len(HEADERS), "endIndex": len(HEADERS) + extra}}})
    reqs.append({"updateSheetProperties": {"properties": {"sheetId": ws.id, "title": title}, "fields": "title"}})
    reqs.append(style(cells(0, len(HEADERS), 0, 1), {
        "backgroundColor": rgb(HEADER_BG), "verticalAlignment": "MIDDLE", "horizontalAlignment": "LEFT",
        "textFormat": {"bold": True, "foregroundColor": rgb("#FFFFFF"), "fontSize": 10},
        "padding": {"left": 8, "right": 8}}))
    reqs.append({"updateDimensionProperties": {"range": {"sheetId": ws.id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
                                               "properties": {"pixelSize": 34}, "fields": "pixelSize"}})
    for name, px in WIDTHS.items():
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": col[name], "endIndex": col[name] + 1},
            "properties": {"pixelSize": px}, "fields": "pixelSize"}})
    reqs.append(style(cells(0, len(HEADERS)), {"verticalAlignment": "MIDDLE", "wrapStrategy": "CLIP",
                                               "padding": {"left": 6, "right": 6, "top": 3, "bottom": 3}}))
    reqs.append(style(cells(col["URL"], col["URL"] + 1), {"textFormat": {"fontSize": 9, "foregroundColor": rgb("#5F6368")}}))
    reqs.append(style(cells(col["Company"], col["Company"] + 1), {"textFormat": {"bold": True}}))
    reqs.append(style(cells(col["Status"], col["Status"] + 1), {"horizontalAlignment": "CENTER", "textFormat": {"bold": True}}))
    reqs.append(style(cells(col["Notes"], col["Notes"] + 1), {"wrapStrategy": "WRAP", "textFormat": {"fontSize": 9}}))
    reqs.append(style(cells(col["Date"], col["Date"] + 1), {"horizontalAlignment": "CENTER"}))
    reqs.append({"addBanding": {"bandedRange": {"range": cells(0, len(HEADERS), 0, LAST_ROW), "rowProperties": {
        "headerColor": rgb(HEADER_BG), "firstBandColor": rgb("#FFFFFF"), "secondBandColor": rgb("#F6F8FA")}}}})
    for status, (bg, fg) in STATUS_COLORS.items():
        reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [cells(col["Status"], col["Status"] + 1)],
            "booleanRule": {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": status}]},
                            "format": {"backgroundColor": rgb(bg), "textFormat": {"foregroundColor": rgb(fg), "bold": True}}}}}})
    reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [cells(col["Notes"], col["Notes"] + 1)],
        "booleanRule": {"condition": {"type": "TEXT_STARTS_WITH", "values": [{"userEnteredValue": sheet.SITE_MARK}]},
                        "format": {"textFormat": {"foregroundColor": rgb("#B06000"), "bold": True}}}}}})
    sh.batch_update({"requests": reqs})


class HasData(Exception):
    """The sheet already has values; setup never writes over them."""


def setup(sh, ws, title="Jobs"):
    """One-time setup of an empty sheet: headers, Status dropdown, frozen row 1, styling. Refuses (HasData) if any
    cell already has a value. Used by main(), the GUI's setup wizard and sheet.open_other (title: the tab's name)."""
    if any(cell.strip() for row in ws.get_all_values() for cell in row):
        raise HasData("Sheet already has data. Nothing was changed. To restyle it: nuauto setup-sheet format")
    ws.update(range_name="A1:F1", values=[HEADERS])
    ws.freeze(rows=1)
    sh.batch_update({
        "requests": [{
            "setDataValidation": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 1,
                    "endRowIndex": LAST_ROW,
                    "startColumnIndex": HEADERS.index("Status"),
                    "endColumnIndex": HEADERS.index("Status") + 1,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [{"userEnteredValue": s} for s in STATUSES],
                    },
                    "strict": True,
                    "showCustomUi": True,
                },
            }
        }]
    })
    format_sheet(sh, ws, title)


def main():
    from nuauto import sheet
    sh = sheet.client().open_by_key(config.SHEET_ID)
    ws = sh.sheet1
    if sys.argv[1:] == ["format"]:
        format_sheet(sh, ws)
        try:
            format_sheet(sh, sh.worksheet(sheet.OTHER_TAB), sheet.OTHER_TAB)
        except gspread.exceptions.WorksheetNotFound:
            pass
        return print("Done: styling applied (no values changed).")
    if sys.argv[1:] == ["other"]:
        try:
            sh.worksheet(sheet.OTHER_TAB)
            return print(f"The {sheet.OTHER_TAB!r} tab is already there (nothing changed).")
        except gspread.exceptions.WorksheetNotFound:
            sheet.open_other(create=True)
            return print(f"Done: {sheet.OTHER_TAB!r} tab added (headers, Status dropdown, styled).")
    if sys.argv[1:]:
        sys.exit(__doc__)
    try:
        setup(sh, ws)
    except HasData as e:
        sys.exit(str(e))
    print("Done: headers written, Status dropdown added (D2:D1000), row 1 frozen, styled.")


if __name__ == "__main__":
    main()
