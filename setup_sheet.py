"""One-time sheet setup: headers, Status dropdown, frozen row 1.
Refuses to touch the sheet if any cell already has a value."""
import sys

import gspread

import config

HEADERS = ["URL", "Company", "Title", "Status", "Notes", "Date"]
STATUSES = ["Proposed", "Approved", "Applied", "Failed", "Needs Human"]
LAST_ROW = 1000  # dropdown covers D2:D1000


def main():
    gc = gspread.oauth(
        scopes=config.WRITE_SCOPES,
        credentials_filename=config.find_client_json(),
        authorized_user_filename=config.TOKEN_PATH,
    )
    config.lock_token()
    sh = gc.open_by_key(config.SHEET_ID)
    ws = sh.sheet1

    if any(cell.strip() for row in ws.get_all_values() for cell in row):
        sys.exit("Sheet already has data. Nothing was changed.")

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
    print("Done: headers written, Status dropdown added (D2:D1000), row 1 frozen.")


if __name__ == "__main__":
    main()
